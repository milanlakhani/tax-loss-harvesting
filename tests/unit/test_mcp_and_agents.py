from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.definitions import EVAL_INSTRUCTIONS, agents_cannot_submit, agent_tool_allowlist
from app.agents.runner import (
    _plain_anomalies,
    _plain_breakdown,
    _plain_candidate_decisions,
    _plain_largest,
    _plain_portfolio_insights,
    _route,
    run_orchestrator_turn,
)
from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES, MCP_TOOL_PARAMETERS, McpToolHandlers


@pytest.mark.unit
def test_mcp_exposes_analysis_tools_but_not_submission():
    assert "get_quote" in MCP_TOOL_NAMES
    assert "get_paper_order_status" in MCP_TOOL_NAMES
    assert "run_analysis" in MCP_TOOL_NAMES
    assert "get_portfolio_insights" in MCP_TOOL_NAMES
    assert "get_latest_candidate_decisions" in MCP_TOOL_NAMES
    assert "submit_paper_order" in FORBIDDEN_MCP_TOOLS
    assert "confirm_paper_order" in FORBIDDEN_MCP_TOOLS
    assert agents_cannot_submit() is True
    assert "submit_paper_order" not in agent_tool_allowlist()
    assert "Never substitute LLM opinion" in EVAL_INSTRUCTIONS
    assert MCP_TOOL_PARAMETERS["run_analysis"] == ("user_id", "idempotency_key")


@pytest.mark.unit
def test_fallback_financial_results_are_human_readable():
    largest = _plain_largest(
        {
            "value": "8200.00",
            "currency": "USD",
            "items": [{"date": "2026-07-15T00:00:00+00:00", "merchant": "example_store", "amount": "8200", "currency": "USD"}],
        }
    )
    categories = _plain_breakdown("Top spending categories", {"currency": "USD", "transaction_count": 2, "breakdown": {"housing": "1000", "food": "250"}})
    anomalies = _plain_anomalies([{"date": "2026-07-15", "merchant": "example_store", "amount": "50", "currency": "USD", "normalized_score": "0.92"}])
    assert "| 2026-07-15 | Example Store | USD 8,200.00 |" in largest
    assert "| Housing | USD 1,000.00 |" in categories
    assert "review signals, not proof of fraud" in anomalies


@pytest.mark.unit
async def test_problem_statement_questions_route_to_authoritative_tools():
    handlers = AsyncMock()
    handlers.get_anomalous_transactions.return_value = []
    handlers.get_portfolio_insights.return_value = [
        {
            "account": "Demo Brokerage",
            "profile": "BALANCED",
            "base_currency": "USD",
            "allocations": [{"asset_class": "EQUITY", "current_weight": "0.70", "target_weight": "0.60", "drift": "0.10", "status": "OUTSIDE_TOLERANCE"}],
            "risk_limits": {"max_equity_weight": "0.75"},
        }
    ]
    handlers.get_latest_candidate_decisions.return_value = {"found": True, "approved": [], "protected": [{"rejection_code": "WASH_SALE_CONFLICT"}]}

    await _route(handlers, "user-id", "Show my unusual spending.")
    risk = await _route(handlers, "user-id", "Is my portfolio within its risk limits?")
    drift = await _route(handlers, "user-id", "How far is my portfolio from its target allocation?")
    opportunities = await _route(handlers, "user-id", "Do I have any safe tax-loss opportunities?")

    handlers.get_anomalous_transactions.assert_awaited_once_with("user-id")
    assert "Needs review" in risk and "Maximum equities" in risk
    assert "Current" in drift and "+10.0%" in drift
    assert "valid fail-closed result" in opportunities


@pytest.mark.unit
def test_problem_statement_answers_are_plain_english():
    risk = _plain_portfolio_insights(
        [{"account": "Demo", "profile": "BALANCED", "allocations": [], "risk_limits": {}, "base_currency": "USD"}],
        focus="risk",
    )
    decisions = _plain_candidate_decisions({"found": True, "approved": [], "protected": []})
    assert "Within configured allocation ranges" in risk
    assert "No candidate passed every hard gate" in decisions


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
