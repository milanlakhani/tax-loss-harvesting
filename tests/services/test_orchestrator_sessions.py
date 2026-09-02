from __future__ import annotations

from uuid import uuid4

import pytest

from app.demo_data.constants import USER_A_ID, USER_B_ID
from app.domain.errors import SessionAccessError
from app.persistence.models import User
from app.services.demo_session import DemoSessionService
from app.services.orchestrator_sessions import OrchestratorSessionService


async def _users(session):
    if await session.get(User, USER_A_ID) is None:
        session.add(User(id=USER_A_ID, email="a@demo.local", display_name="A", is_synthetic=True))
    if await session.get(User, USER_B_ID) is None:
        session.add(User(id=USER_B_ID, email="b@demo.local", display_name="B", is_synthetic=True))
    await session.commit()


@pytest.mark.integration
async def test_orchestrator_session_resume_reset_and_isolation(session, session_factory, settings):
    await _users(session)
    demo = DemoSessionService(settings, session_factory)
    token_a = await demo.create(USER_A_ID)
    token_b = await demo.create(USER_B_ID)
    row_a = await demo.resolve(token_a)
    row_b = await demo.resolve(token_b)
    svc = OrchestratorSessionService(session_factory)

    first = await svc.start(user_id=USER_A_ID, demo_session_id=row_a.id)
    sdk = svc.sdk_session(first.id)
    await sdk.add_items([{"role": "user", "content": "holdings please"}])
    await sdk.add_items([{"role": "assistant", "content": "call MCP"}])

    resumed = await svc.get_active(user_id=USER_A_ID, demo_session_id=row_a.id)
    assert resumed is not None and resumed.id == first.id
    items = await sdk.get_items()
    assert items[0]["content"] == "holdings please"
    assert len(items) == 2

    reset = await svc.reset(user_id=USER_A_ID, demo_session_id=row_a.id)
    assert reset.id != first.id
    closed = await svc.get_owned(session_id=first.id, user_id=USER_A_ID, demo_session_id=row_a.id)
    assert closed.status == "CLOSED"
    preserved = await svc.sdk_session(first.id).get_items()
    assert [row["content"] for row in preserved] == ["holdings please", "call MCP"]
    assert await svc.sdk_session(reset.id).get_items() == []

    with pytest.raises(SessionAccessError) as missing:
        await svc.get_owned(session_id=first.id, user_id=USER_B_ID, demo_session_id=row_b.id)
    assert missing.value.message == "Session not found"

    with pytest.raises(SessionAccessError) as guessed:
        await svc.get_owned(session_id=uuid4(), user_id=USER_A_ID, demo_session_id=row_a.id)
    assert guessed.value.message == "Session not found"

    await sdk.add_items([{"role": "user", "content": "%PDF-1.4 secret api_key=abc"}])
    sanitized = await svc.sdk_session(first.id).get_items()
    assert all("api_key" not in row["content"] for row in sanitized)


@pytest.mark.integration
async def test_orchestrator_turn_always_calls_mcp_for_holdings(session, session_factory, settings):
    await _users(session)
    from app.adapters.postgres_window_store import PostgresRollingWindowStore
    from app.adapters.storage import LocalStatementStorage
    from app.agents.runner import run_orchestrator_turn
    from app.container import AppContainer
    from app.demo_data.generate import build_fake_providers
    from app.providers.fakes import RecordingClock
    from app.services.ingestion import StatementIngestor

    from app.mcp.asgi import create_mcp_app
    from tests.helpers import UvicornTestServer

    providers = build_fake_providers()
    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        storage=LocalStatementStorage(settings.local_data_dir),
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(),
        ingestor=StatementIngestor(LocalStatementStorage(settings.local_data_dir), providers.fx),
    )
    demo = DemoSessionService(settings, session_factory)
    token_a = await demo.create(USER_A_ID)
    row_a = await demo.resolve(token_a)
    async with UvicornTestServer(create_mcp_app(container)) as base:
        container.settings.mcp_server_url = f"{base}/mcp"
        first = await run_orchestrator_turn(container, user_id=USER_A_ID, demo_session_id=row_a.id, message="holdings")
        second = await run_orchestrator_turn(container, user_id=USER_A_ID, demo_session_id=row_a.id, message="holdings")
    assert first["authoritative"] is True and second["authoritative"] is True
    assert first["session_id"] == second["session_id"]
    active = await OrchestratorSessionService(session_factory).get_active(user_id=USER_A_ID, demo_session_id=row_a.id)
    items = await OrchestratorSessionService(session_factory).sdk_session(active.id).get_items()
    assert sum(1 for row in items if row["role"] == "user") >= 2
