from __future__ import annotations

from app.container import AppContainer
from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES, McpToolHandlers


def build_mcp(container: AppContainer):
    from fastmcp import FastMCP

    handlers = McpToolHandlers(container)
    mcp = FastMCP("tax-loss-harvesting")

    @mcp.tool()
    async def get_quote(canonical_id: str, symbol: str, asset_type: str) -> dict:
        return await handlers.get_quote(canonical_id, symbol, asset_type)

    @mcp.tool()
    async def get_holdings(user_id: str) -> list:
        return await handlers.get_holdings(user_id)

    @mcp.tool()
    async def get_transactions(user_id: str) -> list:
        return await handlers.get_transactions(user_id)

    @mcp.tool()
    async def parse_statement(filename: str, data_hex: str) -> dict:
        return await handlers.parse_statement(filename, data_hex)

    @mcp.tool()
    async def get_portfolio_insights(user_id: str) -> list:
        """Return current allocation, targets, drift, and configured risk limits."""
        return await handlers.get_portfolio_insights(user_id)

    @mcp.tool()
    async def get_latest_candidate_decisions(user_id: str) -> dict:
        """Return only persisted final approved and protected harvesting decisions."""
        return await handlers.get_latest_candidate_decisions(user_id)

    @mcp.tool()
    async def run_analysis(user_id: str, idempotency_key: str) -> dict:
        return await handlers.run_analysis_tool(user_id, idempotency_key)

    @mcp.tool()
    async def evaluate_candidate(candidate_id: str) -> dict:
        return await handlers.evaluate_candidate_tool(candidate_id)

    @mcp.tool()
    async def get_spending_summary(user_id: str) -> dict:
        return await handlers.get_spending_summary(user_id)

    @mcp.tool()
    async def get_income_summary(user_id: str) -> dict:
        return await handlers.get_income_summary(user_id)

    @mcp.tool()
    async def get_cashflow_summary(user_id: str) -> dict:
        return await handlers.get_cashflow_summary(user_id)

    @mcp.tool()
    async def compare_spending_periods(
        user_id: str, current_start: str, current_end: str, prior_start: str, prior_end: str
    ) -> dict:
        return await handlers.compare_spending_periods(user_id, current_start, current_end, prior_start, prior_end)

    @mcp.tool()
    async def get_category_breakdown(user_id: str) -> dict:
        return await handlers.get_category_breakdown(user_id)

    @mcp.tool()
    async def get_merchant_summary(user_id: str) -> dict:
        return await handlers.get_merchant_summary(user_id)

    @mcp.tool()
    async def get_largest_transactions(user_id: str) -> dict:
        return await handlers.get_largest_transactions(user_id)

    @mcp.tool()
    async def get_account_balance_history(user_id: str) -> dict:
        return await handlers.get_account_balance_history(user_id)

    @mcp.tool()
    async def get_anomalous_transactions(user_id: str) -> list:
        return await handlers.get_anomalous_transactions(user_id)

    @mcp.tool()
    async def get_paper_order_status(order_id: str) -> dict:
        return await handlers.get_paper_order_status(order_id)

    assert set(MCP_TOOL_NAMES).isdisjoint(FORBIDDEN_MCP_TOOLS)
    return mcp
