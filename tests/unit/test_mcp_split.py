from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.health import get_container
from app.config import Settings
from app.main import create_app
from app.mcp.server import build_mcp
from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES, MCP_TOOL_PARAMETERS
from app.mcp.urls import AWS_MCP_SERVER_URL, COMPOSE_MCP_SERVER_URL, LOCAL_MCP_SERVER_URL, MCP_PATH, MCP_PORT

REPO = Path(__file__).resolve().parents[2]


def _compose_services(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    services: dict[str, list[str]] = {}
    current: str | None = None
    in_services = False
    for line in lines:
        if line.startswith("services:"):
            in_services = True
            current = None
            continue
        if in_services and line and not line.startswith(" ") and not line.startswith("#"):
            break
        if not in_services:
            continue
        if (
            line.startswith("  ")
            and not line.startswith("    ")
            and line.rstrip().endswith(":")
            and not line.strip().startswith("#")
        ):
            current = line.strip()[:-1]
            services[current] = []
            continue
        if current is not None:
            services[current].append(line)
    return {name: "\n".join(body) for name, body in services.items()}


@pytest.mark.unit
def test_fastapi_does_not_serve_mcp():
    app = create_app(MagicMock())
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok", "phase": "2"}
    assert client.get("/mcp").status_code == 404
    assert client.post("/mcp").status_code == 404
    assert client.get("/mcp/").status_code == 404


@pytest.mark.unit
def test_ready_reports_mcp_unavailability_without_bypassing_rules(monkeypatch):
    class FakeSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            return None

    monkeypatch.setattr("app.api.health.probe_mcp", AsyncMock(return_value=False))
    container = MagicMock()
    container.settings.mcp_server_url = COMPOSE_MCP_SERVER_URL
    container.session_factory = FakeSessionFactory()
    app = create_app(container)
    app.dependency_overrides[get_container] = lambda: container
    client = TestClient(app)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "mcp_unreachable"
    assert client.get("/health").status_code == 200


@pytest.mark.unit
async def test_approved_mcp_tool_schemas_unchanged_and_forbidden_absent():
    mcp = build_mcp(MagicMock())
    tools = await mcp.get_tools()
    assert set(tools) == set(MCP_TOOL_NAMES)
    assert set(tools).isdisjoint(FORBIDDEN_MCP_TOOLS)
    for name in ("submit_paper_order", "confirm_paper_order", "prepare_paper_order", "query_sql"):
        assert name not in tools
    assert not any("alpaca" in name.lower() or "secret" in name.lower() for name in tools)
    for name, tool in tools.items():
        properties = tuple((tool.parameters.get("properties") or {}).keys())
        assert properties == MCP_TOOL_PARAMETERS[name]


@pytest.mark.unit
def test_local_and_aws_select_internal_mcp_urls(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    local = Settings(app_env="local", _env_file=None)
    aws = Settings(app_env="aws", _env_file=None)
    assert local.mcp_server_url == LOCAL_MCP_SERVER_URL
    assert aws.mcp_server_url == AWS_MCP_SERVER_URL
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"MCP_SERVER_URL: {COMPOSE_MCP_SERVER_URL}" in compose
    assert AWS_MCP_SERVER_URL in (REPO / "infrastructure" / "stacks" / "tlh_stack.py").read_text(
        encoding="utf-8"
    )


@pytest.mark.unit
def test_compose_has_healthy_mcp_service_without_host_port():
    services = _compose_services(REPO / "docker-compose.yml")
    assert set(services) >= {"postgres", "mcp", "backend", "ui"}
    mcp = services["mcp"]
    backend = services["backend"]
    ui = services["ui"]
    assert "healthcheck:" in mcp
    assert f"--port {MCP_PORT}" in mcp or f"--port {MCP_PORT}\n" in mcp
    assert f'"{MCP_PORT}"' in mcp or f"- \"{MCP_PORT}\"" in mcp or f'- "{MCP_PORT}"' in mcp
    assert "ports:" not in mcp
    assert "8001:8001" not in mcp
    assert f"MCP_SERVER_URL: {COMPOSE_MCP_SERVER_URL}" in backend
    assert "8001:8001" not in backend
    assert "MCP_SERVER_URL" not in ui
    assert "8001" not in ui
    debug = (REPO / "docker-compose.debug-mcp.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:8001:8001" in debug


@pytest.mark.unit
def test_streamlit_never_calls_mcp_directly():
    text = (REPO / "app" / "ui" / "streamlit_app.py").read_text(encoding="utf-8")
    assert "MCP_SERVER_URL" not in text
    assert MCP_PATH not in text
    assert "8001" not in text
    assert "BACKEND_URL" in text
