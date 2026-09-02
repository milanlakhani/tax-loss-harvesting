from __future__ import annotations

from uuid import uuid4

from app.agents.specialists import DOC_PARSING_AGENT, EVAL_AGENT, ML_ANALYSIS_AGENT, ORCHESTRATOR_AGENT
from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES
from app.mcp.urls import AWS_MCP_SERVER_URL, COMPOSE_MCP_SERVER_URL, LOCAL_MCP_SERVER_URL

ORCHESTRATOR_INSTRUCTIONS = """
You are the Orchestrator Agent. Route work to specialist agents and explain persisted results
in plain English for a non-technical user. Never invent balances, quotes, holdings, tax lots,
or candidate statuses. Conversation memory is not source of truth.
Hand off statement parsing to the Document Parsing Agent.
Hand off spend anomalies, portfolio drift, quotes, and candidate generation to the ML Analysis Agent.
Hand off harvesting-candidate safety checks to the Eval Agent.
Never label a candidate approved or rejected unless that status is already persisted.
Only report harvesting opportunities via get_latest_candidate_decisions after Eval has finished.
Never submit, prepare, or confirm paper orders. Those tools are not available.
Do not provide individualized tax or legal advice. Never override a persisted wash-sale, risk,
freshness, or execution decision.
"""

PARSER_INSTRUCTIONS = (
    "You are the Document Parsing Agent. Invoke parse_statement for uploaded brokerage or bank PDFs. "
    "Do not reimplement parsing. After the tool returns, transfer back to the Orchestrator Agent."
)
ML_INSTRUCTIONS = (
    "You are the ML Analysis Agent. Invoke run_analysis to write pending harvesting candidates, "
    "and use anomaly, drift, quote, holdings, and transaction tools as needed. "
    "Actual machine learning is only for spend-anomaly scores. Do not reimplement models. "
    "Do not tell the user a candidate is approved or safe. Transfer back to the Orchestrator Agent "
    "so the Eval Agent can persist statuses first."
)
EVAL_INSTRUCTIONS = (
    "You are the Eval Agent. Invoke evaluate_pending_candidates (or evaluate_candidate) for every "
    "pending harvesting candidate. Never substitute LLM opinion for a deterministic rule. "
    "Persist approved or rejected status before anything is shown to the user. "
    "After tools return, transfer back to the Orchestrator Agent."
)


def agent_tool_allowlist() -> tuple[str, ...]:
    return MCP_TOOL_NAMES


def orchestrator_tool_allowlist() -> tuple[str, ...]:
    return ("get_latest_candidate_decisions", "get_holdings", "get_paper_order_status")


def parser_tool_allowlist() -> tuple[str, ...]:
    return ("parse_statement",)


def ml_tool_allowlist() -> tuple[str, ...]:
    return (
        "run_analysis",
        "get_quote",
        "get_holdings",
        "get_transactions",
        "get_spending_summary",
        "get_income_summary",
        "get_cashflow_summary",
        "compare_spending_periods",
        "get_category_breakdown",
        "get_merchant_summary",
        "get_largest_transactions",
        "get_account_balance_history",
        "get_anomalous_transactions",
        "get_portfolio_insights",
    )


def eval_tool_allowlist() -> tuple[str, ...]:
    return ("evaluate_pending_candidates", "evaluate_candidate", "get_latest_candidate_decisions")


def agents_cannot_submit() -> bool:
    return "submit_paper_order" not in MCP_TOOL_NAMES and "submit_paper_order" in FORBIDDEN_MCP_TOOLS


def _bound_tools(handlers, user_id: str):
    from agents import function_tool

    @function_tool
    async def parse_statement(filename: str, data_hex: str) -> dict:
        """Parse an uploaded brokerage or bank statement PDF."""
        return await handlers.parse_statement(filename, data_hex)

    @function_tool
    async def get_holdings() -> list:
        """Get the current user's authoritative portfolio holdings."""
        return await handlers.get_holdings(user_id)

    @function_tool
    async def get_paper_order_status(order_id: str) -> dict:
        """Get persisted paper-order status by id."""
        return await handlers.get_paper_order_status(order_id)

    @function_tool
    async def get_latest_candidate_decisions() -> dict:
        """Get only persisted final approved and protected tax-loss decisions."""
        return await handlers.get_latest_candidate_decisions(user_id)

    @function_tool
    async def get_quote(canonical_id: str, symbol: str, asset_type: str) -> dict:
        """Get a live or last-session market quote."""
        return await handlers.get_quote(canonical_id, symbol, asset_type)

    @function_tool
    async def get_transactions() -> list:
        """Get the current user's authoritative statement transactions."""
        return await handlers.get_transactions(user_id)

    @function_tool
    async def run_portfolio_analysis() -> dict:
        """Write pending harvesting candidates and spend-anomaly scores. Does not approve trades."""
        return await handlers.run_analysis_tool(user_id, f"agent-{uuid4().hex}")

    @function_tool
    async def get_spending_summary() -> dict:
        """Calculate the current user's authoritative spending summary."""
        return await handlers.get_spending_summary(user_id)

    @function_tool
    async def get_income_summary() -> dict:
        """Calculate the current user's authoritative income summary."""
        return await handlers.get_income_summary(user_id)

    @function_tool
    async def get_cashflow_summary() -> dict:
        """Calculate authoritative income, spending, and net cash flow."""
        return await handlers.get_cashflow_summary(user_id)

    @function_tool
    async def compare_spending_periods(
        current_start: str, current_end: str, prior_start: str, prior_end: str
    ) -> dict:
        """Compare spending between two date ranges."""
        return await handlers.compare_spending_periods(
            user_id, current_start, current_end, prior_start, prior_end
        )

    @function_tool
    async def get_category_breakdown() -> dict:
        """Get authoritative spending totals grouped by category."""
        return await handlers.get_category_breakdown(user_id)

    @function_tool
    async def get_merchant_summary() -> dict:
        """Get authoritative spending totals grouped by merchant."""
        return await handlers.get_merchant_summary(user_id)

    @function_tool
    async def get_largest_transactions() -> dict:
        """Get the user's largest authoritative statement transactions."""
        return await handlers.get_largest_transactions(user_id)

    @function_tool
    async def get_account_balance_history() -> dict:
        """Get authoritative account balance history."""
        return await handlers.get_account_balance_history(user_id)

    @function_tool
    async def get_anomalous_transactions() -> list:
        """Get transactions flagged by the persisted anomaly model."""
        return await handlers.get_anomalous_transactions(user_id)

    @function_tool
    async def get_portfolio_insights() -> list:
        """Get current allocation, target drift, and configured portfolio risk limits."""
        return await handlers.get_portfolio_insights(user_id)

    @function_tool
    async def evaluate_pending_candidates(analysis_run_id: str = "") -> dict:
        """Evaluate every pending harvesting candidate against wash-sale and risk rules."""
        return await handlers.evaluate_pending_candidates_tool(user_id, analysis_run_id)

    @function_tool
    async def evaluate_candidate(candidate_id: str) -> dict:
        """Evaluate one pending harvesting candidate using deterministic rules."""
        return await handlers.evaluate_candidate_tool(candidate_id)

    parser_tools = [parse_statement]
    ml_tools = [
        run_portfolio_analysis,
        get_quote,
        get_holdings,
        get_transactions,
        get_spending_summary,
        get_income_summary,
        get_cashflow_summary,
        compare_spending_periods,
        get_category_breakdown,
        get_merchant_summary,
        get_largest_transactions,
        get_account_balance_history,
        get_anomalous_transactions,
        get_portfolio_insights,
    ]
    eval_tools = [evaluate_pending_candidates, evaluate_candidate, get_latest_candidate_decisions]
    orchestrator_tools = [get_latest_candidate_decisions, get_holdings, get_paper_order_status]
    return parser_tools, ml_tools, eval_tools, orchestrator_tools


def build_runtime_agents(*, handlers, user_id: str, model: str):
    from agents import Agent, handoff

    parser_tools, ml_tools, eval_tools, orchestrator_tools = _bound_tools(handlers, user_id)
    parser = Agent(
        name=DOC_PARSING_AGENT,
        model=model,
        instructions=PARSER_INSTRUCTIONS,
        tools=parser_tools,
        handoff_description="Parse uploaded brokerage or bank statement PDFs.",
    )
    ml = Agent(
        name=ML_ANALYSIS_AGENT,
        model=model,
        instructions=ML_INSTRUCTIONS,
        tools=ml_tools,
        handoff_description="Compute spend anomalies, portfolio drift, and pending harvesting candidates.",
    )
    evaluator = Agent(
        name=EVAL_AGENT,
        model=model,
        instructions=EVAL_INSTRUCTIONS,
        tools=eval_tools,
        handoff_description="Check harvesting candidates against wash-sale and risk rules.",
    )
    orchestrator = Agent(
        name=ORCHESTRATOR_AGENT,
        model=model,
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        tools=orchestrator_tools,
        handoffs=[
            handoff(parser, tool_description_override="Parse uploaded brokerage or bank statement PDFs into structured lots and transactions."),
            handoff(ml, tool_description_override="Compute spend anomalies, portfolio drift, and pending tax-loss-harvesting candidates."),
            handoff(evaluator, tool_description_override="Check every harvesting candidate against wash-sale and risk rules before the user sees it."),
        ],
    )
    parser.handoffs = [orchestrator]
    ml.handoffs = [orchestrator]
    evaluator.handoffs = [orchestrator]
    return orchestrator, parser, ml, evaluator


def build_agents(*, mcp_server_url: str = LOCAL_MCP_SERVER_URL, handlers=None, user_id: str = "", model: str = "gpt-4.1-mini"):
    if handlers is not None and user_id:
        return build_runtime_agents(handlers=handlers, user_id=user_id, model=model)
    from agents import Agent

    tools = []
    parser = Agent(name=DOC_PARSING_AGENT, instructions=PARSER_INSTRUCTIONS, tools=tools)
    ml = Agent(name=ML_ANALYSIS_AGENT, instructions=ML_INSTRUCTIONS, tools=tools)
    evaluator = Agent(name=EVAL_AGENT, instructions=EVAL_INSTRUCTIONS, tools=tools)
    orchestrator = Agent(
        name=ORCHESTRATOR_AGENT,
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        tools=tools,
        handoffs=[parser, ml, evaluator],
    )
    _ = mcp_server_url
    _ = (AWS_MCP_SERVER_URL, COMPOSE_MCP_SERVER_URL)
    return orchestrator, parser, ml, evaluator
