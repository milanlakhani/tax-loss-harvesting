"""Dedicated ASGI entry point for the Streamable HTTP MCP server."""

from app.container import build_container
from app.mcp.server import build_mcp


container = build_container()
mcp = build_mcp(container)
app = mcp.http_app(path="/", transport="streamable-http")
