from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.mcp_client import McpGateway, list_mcp_tools_via_agents_sdk
from app.agents.runner import run_orchestrator_turn
from app.container import AppContainer
from app.demo_data.constants import USER_A_ID
from app.domain.errors import MCP_UNAVAILABLE_MESSAGE
from app.main import create_app
from app.mcp.asgi import create_mcp_app
from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES
from app.mcp.urls import MCP_PATH
from app.persistence.models import User
from app.providers.fakes import RecordingClock
from app.services.demo_session import DemoSessionService
from tests.helpers import UvicornTestServer, seed_historical_demo


async def _user(session):
    if await session.get(User, USER_A_ID) is None:
        session.add(User(id=USER_A_ID, email="a@demo.local", display_name="A", is_synthetic=True))
        await session.commit()


def _container(settings, session_factory, providers):
    from app.adapters.postgres_window_store import PostgresRollingWindowStore
    from app.adapters.storage import LocalStatementStorage
    from app.services.ingestion import StatementIngestor

    storage = LocalStatementStorage(settings.local_data_dir)
    return AppContainer(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        storage=storage,
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(),
        ingestor=StatementIngestor(storage, providers.fx),
    )


@pytest.mark.integration
async def test_standalone_mcp_serves_streamable_http(session, session_factory, settings):
    providers = await seed_historical_demo(session, settings)
    container = _container(settings, session_factory, providers)
    app = create_mcp_app(container)
    async with UvicornTestServer(app) as base:
        async with AsyncClient(base_url=base) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok", "service": "mcp"}
            initialize = await client.post(
                MCP_PATH,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                },
            )
            assert initialize.status_code == 200
            body = initialize.text.lower()
            assert "server" in body or "protocol" in body or "jsonrpc" in body


@pytest.mark.integration
async def test_agent_mocked_tool_calls_over_http(session, session_factory, settings):
    providers = await seed_historical_demo(session, settings)
    container = _container(settings, session_factory, providers)
    async with UvicornTestServer(create_mcp_app(container)) as base:
        url = f"{base}{MCP_PATH}"
        container.settings.mcp_server_url = url
        gateway = McpGateway(url)
        names = await gateway.list_tools()
        assert set(names) == set(MCP_TOOL_NAMES)
        assert set(names).isdisjoint(FORBIDDEN_MCP_TOOLS)
        sdk_names = await list_mcp_tools_via_agents_sdk(url)
        assert set(sdk_names) == set(MCP_TOOL_NAMES)
        holdings = await gateway.call_tool("get_holdings", {"user_id": str(USER_A_ID)})
        assert holdings
        demo = DemoSessionService(settings, session_factory)
        token = await demo.create(USER_A_ID)
        row = await demo.resolve(token)
        result = await run_orchestrator_turn(
            container, user_id=USER_A_ID, demo_session_id=row.id, message="holdings"
        )
        assert result["authoritative"] is True
        assert result["mode"] == "deterministic_fallback"
        assert "positions" in result["reply"].lower() or "holding" in result["reply"].lower()


@pytest.mark.integration
async def test_mcp_unavailability_is_fail_closed(session, session_factory, settings):
    await _user(session)
    from app.demo_data.generate import build_fake_providers

    container = _container(settings, session_factory, build_fake_providers())
    container.settings.mcp_server_url = "http://127.0.0.1:9/mcp"
    demo = DemoSessionService(settings, session_factory)
    token = await demo.create(USER_A_ID)
    row = await demo.resolve(token)
    with (
        patch("app.mcp.tools.run_analysis", new=AsyncMock()) as analysis,
        patch("app.mcp.tools.evaluate_candidate", new=AsyncMock()) as evaluate,
    ):
        result = await run_orchestrator_turn(
            container,
            user_id=USER_A_ID,
            demo_session_id=row.id,
            message="run analysis for tax-loss opportunities",
        )
    assert result["mode"] == "mcp_unavailable"
    assert result["authoritative"] is False
    assert result["reply"] == MCP_UNAVAILABLE_MESSAGE
    assert "approved" not in result["reply"].lower()
    analysis.assert_not_awaited()
    evaluate.assert_not_awaited()


@pytest.mark.integration
async def test_fastapi_app_still_omits_mcp_mount_with_real_container(session, session_factory, settings):
    from app.demo_data.generate import build_fake_providers

    container = _container(settings, session_factory, build_fake_providers())
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/mcp")).status_code == 404
        assert (await client.get("/health")).status_code == 200
