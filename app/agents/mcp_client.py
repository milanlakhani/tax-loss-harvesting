from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from app.domain.errors import MCP_UNAVAILABLE_MESSAGE, McpUnavailableError
from app.mcp.urls import mcp_health_url

_UNAVAILABLE_MARKERS = (
    "connect",
    "unreachable",
    "refused",
    "timed out",
    "timeout",
    "not connected",
    "server disconnected",
    "connection reset",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
)


class McpCallGateway(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def unwrap_tool_result(result: Any) -> Any:
    if getattr(result, "is_error", False):
        raise McpUnavailableError("MCP tool returned an error without a financial result")
    data = getattr(result, "data", None)
    if data is not None:
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        if "result" in structured and len(structured) == 1:
            return structured["result"]
        return structured
    content = getattr(result, "content", None) or []
    texts = [getattr(block, "text", None) for block in content if getattr(block, "text", None)]
    if not texts:
        return None
    try:
        return json.loads(texts[0])
    except json.JSONDecodeError:
        return texts[0]


def _is_unavailable(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.TimeoutException,
            ConnectionError,
            TimeoutError,
            OSError,
            McpUnavailableError,
        ),
    ):
        return True
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(marker in blob for marker in _UNAVAILABLE_MARKERS)


async def probe_mcp(mcp_server_url: str, *, timeout: float = 2.0) -> bool:
    url = mcp_health_url(mcp_server_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


class McpGateway:
    """Streamable HTTP client for the standalone FastMCP container."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        httpx_client_factory=None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.httpx_client_factory = httpx_client_factory

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        from fastmcp.exceptions import ToolError

        transport = StreamableHttpTransport(
            self.url,
            httpx_client_factory=self.httpx_client_factory,
        )
        try:
            async with Client(transport, timeout=self.timeout) as client:
                result = await client.call_tool(name, arguments)
        except ToolError:
            raise
        except Exception as exc:
            if _is_unavailable(exc):
                raise McpUnavailableError() from exc
            raise
        return unwrap_tool_result(result)

    async def list_tools(self) -> list[str]:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(
            self.url,
            httpx_client_factory=self.httpx_client_factory,
        )
        try:
            async with Client(transport, timeout=self.timeout) as client:
                tools = await client.list_tools()
        except Exception as exc:
            if _is_unavailable(exc):
                raise McpUnavailableError() from exc
            raise
        return sorted(tool.name for tool in tools)


class RemoteMcpHandlers:
    """Same call surface as McpToolHandlers; every call uses MCP_SERVER_URL over HTTP."""

    def __init__(self, gateway: McpCallGateway) -> None:
        self._gateway = gateway

    async def get_quote(self, canonical_id: str, symbol: str, asset_type: str) -> dict:
        return await self._gateway.call_tool(
            "get_quote",
            {"canonical_id": canonical_id, "symbol": symbol, "asset_type": asset_type},
        )

    async def get_holdings(self, user_id: str) -> list:
        return await self._gateway.call_tool("get_holdings", {"user_id": user_id})

    async def get_transactions(self, user_id: str) -> list:
        return await self._gateway.call_tool("get_transactions", {"user_id": user_id})

    async def parse_statement(self, filename: str, data_hex: str) -> dict:
        return await self._gateway.call_tool(
            "parse_statement", {"filename": filename, "data_hex": data_hex}
        )

    async def run_analysis_tool(self, user_id: str, idempotency_key: str) -> dict:
        return await self._gateway.call_tool(
            "run_analysis", {"user_id": user_id, "idempotency_key": idempotency_key}
        )

    async def evaluate_candidate_tool(self, candidate_id: str) -> dict:
        return await self._gateway.call_tool("evaluate_candidate", {"candidate_id": candidate_id})

    async def get_spending_summary(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_spending_summary", {"user_id": user_id})

    async def get_income_summary(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_income_summary", {"user_id": user_id})

    async def get_cashflow_summary(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_cashflow_summary", {"user_id": user_id})

    async def compare_spending_periods(
        self,
        user_id: str,
        current_start: str,
        current_end: str,
        prior_start: str,
        prior_end: str,
    ) -> dict:
        return await self._gateway.call_tool(
            "compare_spending_periods",
            {
                "user_id": user_id,
                "current_start": current_start,
                "current_end": current_end,
                "prior_start": prior_start,
                "prior_end": prior_end,
            },
        )

    async def get_category_breakdown(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_category_breakdown", {"user_id": user_id})

    async def get_merchant_summary(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_merchant_summary", {"user_id": user_id})

    async def get_largest_transactions(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_largest_transactions", {"user_id": user_id})

    async def get_account_balance_history(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_account_balance_history", {"user_id": user_id})

    async def get_anomalous_transactions(self, user_id: str) -> list:
        return await self._gateway.call_tool("get_anomalous_transactions", {"user_id": user_id})

    async def get_portfolio_insights(self, user_id: str) -> list:
        return await self._gateway.call_tool("get_portfolio_insights", {"user_id": user_id})

    async def get_latest_candidate_decisions(self, user_id: str) -> dict:
        return await self._gateway.call_tool("get_latest_candidate_decisions", {"user_id": user_id})

    async def get_paper_order_status(self, order_id: str) -> dict:
        return await self._gateway.call_tool("get_paper_order_status", {"order_id": order_id})


async def list_mcp_tools_via_agents_sdk(mcp_server_url: str) -> list[str]:
    """OpenAI Agents SDK Streamable HTTP client against MCP_SERVER_URL."""
    from agents.mcp import MCPServerStreamableHttp

    try:
        async with MCPServerStreamableHttp(
            params={"url": mcp_server_url, "timeout": 5},
            cache_tools_list=False,
            name="tax-loss-harvesting",
        ) as server:
            tools = await server.list_tools()
    except Exception as exc:
        if _is_unavailable(exc):
            raise McpUnavailableError() from exc
        raise
    return sorted(tool.name for tool in tools)
