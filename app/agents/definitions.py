from __future__ import annotations

from app.mcp.tools import FORBIDDEN_MCP_TOOLS, MCP_TOOL_NAMES
from app.mcp.urls import AWS_MCP_SERVER_URL, COMPOSE_MCP_SERVER_URL, LOCAL_MCP_SERVER_URL

ORCHESTRATOR_INSTRUCTIONS = """
You are the Orchestrator Agent. Route questions to MCP tools and explain persisted results.
Never invent balances, quotes, holdings, tax lots, or candidate statuses.
Always call MCP tools for authoritative financial data. Conversation memory is not source of truth.
Never label a candidate approved or rejected unless that status is already persisted.
Never submit, prepare, or confirm paper orders. Those tools are not available.
"""

PARSER_INSTRUCTIONS = "Document Parsing Agent. Invoke parse_statement. Do not reimplement parsing."
ML_INSTRUCTIONS = "ML Analysis Agent. Invoke run_analysis and anomaly/statistics tools. Do not reimplement models."
EVAL_INSTRUCTIONS = (
    "Eval Agent. Invoke evaluate_candidate. Never substitute LLM opinion for a deterministic rule."
)


def agent_tool_allowlist() -> tuple[str, ...]:
    return MCP_TOOL_NAMES


def agents_cannot_submit() -> bool:
    return "submit_paper_order" not in MCP_TOOL_NAMES and "submit_paper_order" in FORBIDDEN_MCP_TOOLS


def build_agents(*, mcp_server_url: str = LOCAL_MCP_SERVER_URL):
    from agents import Agent

    # Tools are provided by the standalone FastMCP server at MCP_SERVER_URL.
    # The runtime Orchestrator binds demo-session user identity in the backend
    # and calls that server over Streamable HTTP; it does not expose unbound
    # MCP tools to the model.
    tools = []
    parser = Agent(name="Document Parsing Agent", instructions=PARSER_INSTRUCTIONS, tools=tools)
    ml = Agent(name="ML Analysis Agent", instructions=ML_INSTRUCTIONS, tools=tools)
    evaluator = Agent(name="Eval Agent", instructions=EVAL_INSTRUCTIONS, tools=tools)
    orchestrator = Agent(
        name="Orchestrator Agent",
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        tools=tools,
        handoffs=[parser, ml, evaluator],
    )
    _ = mcp_server_url
    _ = (AWS_MCP_SERVER_URL, COMPOSE_MCP_SERVER_URL)
    return orchestrator, parser, ml, evaluator
