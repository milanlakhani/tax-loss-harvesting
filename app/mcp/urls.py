from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

MCP_PATH = "/mcp"
MCP_PORT = 8001
MCP_BIND_HOST = "0.0.0.0"
LOCAL_MCP_SERVER_URL = "http://127.0.0.1:8001/mcp"
COMPOSE_MCP_SERVER_URL = "http://mcp:8001/mcp"
AWS_MCP_SERVER_URL = "http://127.0.0.1:8001/mcp"


def mcp_health_url(mcp_server_url: str) -> str:
    parts = urlsplit(mcp_server_url)
    path = parts.path.rstrip("/")
    if path.endswith(MCP_PATH):
        path = f"{path[: -len(MCP_PATH)]}/health"
    else:
        path = "/health"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
