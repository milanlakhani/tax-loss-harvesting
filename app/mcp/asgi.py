from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.container import AppContainer, build_container
from app.mcp.server import build_mcp
from app.mcp.urls import MCP_PATH


def create_mcp_app(container: AppContainer | None = None):
    """Standalone FastMCP Streamable HTTP ASGI app. Not mounted on FastAPI."""
    container = container or build_container()
    mcp = build_mcp(container)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp"})

    application = mcp.http_app(path=MCP_PATH, transport="streamable-http")
    application.state.container = container
    return application


app = create_mcp_app()
