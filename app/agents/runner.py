from __future__ import annotations

from uuid import UUID

from app.container import AppContainer
from app.mcp.tools import McpToolHandlers
from app.services.orchestrator_sessions import OrchestratorSessionService


async def run_orchestrator_turn(
    container: AppContainer,
    *,
    user_id: UUID,
    demo_session_id: UUID,
    message: str,
) -> dict:
    """Run one Orchestrator turn. Financial answers always come from MCP handlers, never memory."""
    sessions = OrchestratorSessionService(container.session_factory)
    active = await sessions.get_active(user_id=user_id, demo_session_id=demo_session_id)
    if active is None:
        active = await sessions.start(user_id=user_id, demo_session_id=demo_session_id)
    sdk = sessions.sdk_session(active.id)
    await sdk.add_items([{"role": "user", "content": message}])
    handlers = McpToolHandlers(container)
    reply = await _route(handlers, str(user_id), message)
    await sdk.add_items([{"role": "assistant", "content": reply}])
    return {"session_id": str(active.id), "reply": reply, "authoritative": True}


async def _route(handlers: McpToolHandlers, user_id: str, message: str) -> str:
    text = message.lower()
    if "hold" in text:
        return str(await handlers.get_holdings(user_id))
    if "spend" in text:
        return str(await handlers.get_spending_summary(user_id))
    if "anomal" in text:
        return str(await handlers.get_anomalous_transactions(user_id))
    if "analy" in text:
        return str(await handlers.run_analysis_tool(user_id, f"orch-{user_id}"))
    if "income" in text:
        return str(await handlers.get_income_summary(user_id))
    return "Ask about holdings, spending, anomalies, or analysis. Order submission is not available through agents."
