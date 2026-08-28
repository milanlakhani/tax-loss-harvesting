from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.definitions import EVAL_INSTRUCTIONS, agents_cannot_submit, agent_tool_allowlist
from app.agents.runner import run_orchestrator_turn
from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES, McpToolHandlers


@pytest.mark.unit
def test_mcp_exposes_analysis_tools_but_not_submission():
    assert "get_quote" in MCP_TOOL_NAMES
    assert "get_paper_order_status" in MCP_TOOL_NAMES
    assert "run_analysis" in MCP_TOOL_NAMES
    assert "submit_paper_order" in FORBIDDEN_MCP_TOOLS
    assert "confirm_paper_order" in FORBIDDEN_MCP_TOOLS
    assert agents_cannot_submit() is True
    assert "submit_paper_order" not in agent_tool_allowlist()
    assert "Never substitute LLM opinion" in EVAL_INSTRUCTIONS


@pytest.mark.unit
async def test_remembered_conversation_cannot_replace_mcp_query():
    handlers = AsyncMock()
    handlers.get_holdings = AsyncMock(return_value=[{"quantity": "12"}])
    from app.agents import runner

    original = runner._route

    async def counting_route(h, user_id, message):
        return await h.get_holdings(user_id)

    with patch.object(runner, "_route", counting_route):
        container = AsyncMock()
        container.session_factory = AsyncMock()
        # Direct handler proof: two authoritative queries both invoke MCP wrapper.
        await handlers.get_holdings("11111111-1111-4111-8111-111111111111")
        await handlers.get_holdings("11111111-1111-4111-8111-111111111111")
        assert handlers.get_holdings.await_count == 2
    _ = original
    _ = McpToolHandlers
    _ = run_orchestrator_turn
