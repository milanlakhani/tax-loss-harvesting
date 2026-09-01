from __future__ import annotations

from uuid import UUID

from app.container import AppContainer
from app.mcp.tools import McpToolHandlers
from app.services.orchestrator_sessions import OrchestratorSessionService


LLM_INSTRUCTIONS = """
You are Northstar Wealth Copilot, a concise and approachable personal-finance assistant.
Answer in plain English for a non-technical user. For every question about the user's
financial data, call one or more provided tools on this turn; never rely on conversation
memory for balances, transactions, holdings, anomalies, or analysis status. Clearly
distinguish facts from general education. Do not provide individualized tax or legal advice.
Never prepare, confirm, or submit an order. Never override or reinterpret a persisted
wash-sale, risk, freshness, or execution decision. Explain such decisions as recorded.
Use short paragraphs and bullets where helpful. Include currency and relevant dates.
"""


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
    handlers = McpToolHandlers(container)
    sdk = sessions.sdk_session(active.id)
    if container.settings.enable_llm_orchestrator and container.settings.openai_api_key:
        try:
            reply = await _run_llm(handlers, str(user_id), message, sdk, container.settings.openai_model)
            return {"session_id": str(active.id), "reply": reply, "authoritative": True, "mode": "llm"}
        except Exception:
            # A model outage must not break access to deterministic application data.
            # Do not expose provider error details or credentials to the user.
            pass
    await sdk.add_items([{"role": "user", "content": message}])
    reply = await _route(handlers, str(user_id), message)
    await sdk.add_items([{"role": "assistant", "content": reply}])
    return {"session_id": str(active.id), "reply": reply, "authoritative": True, "mode": "deterministic_fallback"}


async def _run_llm(handlers: McpToolHandlers, user_id: str, message: str, sdk, model: str) -> str:
    from agents import Agent, Runner, function_tool

    @function_tool
    async def get_holdings() -> list[dict]:
        """Get the current user's authoritative portfolio holdings."""
        return await handlers.get_holdings(user_id)

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
    async def get_anomalous_transactions() -> list[dict]:
        """Get transactions flagged by the persisted anomaly model."""
        return await handlers.get_anomalous_transactions(user_id)

    @function_tool
    async def get_portfolio_insights() -> list[dict]:
        """Get current allocation, target drift, and configured portfolio risk limits."""
        return await handlers.get_portfolio_insights(user_id)

    @function_tool
    async def get_latest_candidate_decisions() -> dict:
        """Get only persisted final approved and protected tax-loss decisions."""
        return await handlers.get_latest_candidate_decisions(user_id)

    @function_tool
    async def run_portfolio_analysis() -> dict:
        """Run the deterministic portfolio analysis and safety evaluation pipeline."""
        from uuid import uuid4

        return await handlers.run_analysis_tool(user_id, f"agent-{uuid4().hex}")

    agent = Agent(
        name="Northstar Wealth Copilot",
        model=model,
        instructions=LLM_INSTRUCTIONS,
        tools=[
            get_holdings,
            get_spending_summary,
            get_income_summary,
            get_cashflow_summary,
            get_category_breakdown,
            get_merchant_summary,
            get_largest_transactions,
            get_anomalous_transactions,
            get_portfolio_insights,
            get_latest_candidate_decisions,
            run_portfolio_analysis,
        ],
    )
    # Persist only plain user/assistant text. Tool inputs and outputs are deliberately
    # not reused as financial truth; the instructions require fresh tool calls each turn.
    history = await sdk.get_items(limit=20)
    result = await Runner.run(agent, [*history, {"role": "user", "content": message}], max_turns=6)
    reply = str(result.final_output)
    await sdk.add_items([{"role": "user", "content": message}, {"role": "assistant", "content": reply}])
    return reply


async def _route(handlers: McpToolHandlers, user_id: str, message: str) -> str:
    text = message.lower()
    if "hold" in text:
        holdings = await handlers.get_holdings(user_id)
        if not holdings:
            return "No holdings were found for this portfolio."
        symbols = ", ".join(str(item.get("symbol") or "Unknown") for item in holdings[:8])
        suffix = f" and {len(holdings) - 8} more" if len(holdings) > 8 else ""
        return f"You currently have {len(holdings)} positions. The portfolio includes {symbols}{suffix}. Open Portfolio overview for quantities and account details."
    if "anomal" in text:
        return _plain_anomalies(await handlers.get_anomalous_transactions(user_id))
    if "unusual" in text and "spend" in text:
        return _plain_anomalies(await handlers.get_anomalous_transactions(user_id))
    if "risk" in text:
        return _plain_portfolio_insights(await handlers.get_portfolio_insights(user_id), focus="risk")
    if "drift" in text or "target allocation" in text:
        return _plain_portfolio_insights(await handlers.get_portfolio_insights(user_id), focus="drift")
    if "tax-loss" in text or "harvest" in text or "opportun" in text or "wash-sale" in text or "wash sale" in text:
        return _plain_candidate_decisions(
            await handlers.get_latest_candidate_decisions(user_id),
            wash_sale_only="wash" in text,
        )
    if "spend" in text:
        return _plain_statistic("spending", await handlers.get_spending_summary(user_id))
    if "analy" in text:
        result = await handlers.run_analysis_tool(user_id, f"orch-{user_id}")
        approved = len(result.get("approved_candidate_ids") or [])
        return f"The portfolio analysis finished with status **{str(result.get('status', 'unknown')).title()}**. The model status is **{str(result.get('ml_status') or 'not available').title()}**, and {approved} candidate{'s' if approved != 1 else ''} passed every safety rule."
    if "income" in text:
        return _plain_statistic("income", await handlers.get_income_summary(user_id))
    if "cash" in text:
        return _plain_statistic("net cash flow", await handlers.get_cashflow_summary(user_id))
    if "categor" in text:
        return _plain_breakdown("Top spending categories", await handlers.get_category_breakdown(user_id))
    if "merchant" in text:
        return _plain_breakdown("Top merchants", await handlers.get_merchant_summary(user_id))
    if "largest" in text or "biggest" in text:
        return _plain_largest(await handlers.get_largest_transactions(user_id))
    return "Ask about holdings, spending, anomalies, or analysis. Order submission is not available through agents."


def _plain_statistic(label: str, result: dict) -> str:
    value = result.get("value")
    currency = result.get("currency") or "USD"
    count = result.get("transaction_count")
    start = str(result.get("date_start") or "")[:10]
    end = str(result.get("date_end") or "")[:10]
    try:
        amount = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        amount = str(value or "not available")
    period = f" from {start} to {end}" if start and end else ""
    transactions = f" across {count} transactions" if count is not None else ""
    warning = f"\n\nPlease note: {result['warning']}." if result.get("warning") else ""
    return f"Your total {label} was **{currency} {amount}**{period}{transactions}.{warning}"


def _money(value, currency: str | None = "USD") -> str:
    try:
        return f"{currency or 'USD'} {float(value):,.2f}"
    except (TypeError, ValueError):
        return "Not available"


def _plain_breakdown(title: str, result: dict, limit: int = 8) -> str:
    breakdown = result.get("breakdown") or {}
    if not breakdown:
        return f"No data is available for {title.lower()}."
    currency = result.get("currency") or "USD"
    ranked = sorted(breakdown.items(), key=lambda item: float(item[1]), reverse=True)[:limit]
    lines = [f"### {title}", "", "| Name | Amount |", "|---|---:|"]
    for name, amount in ranked:
        label = str(name).replace("_", " ").title()
        lines.append(f"| {label} | {_money(amount, currency)} |")
    lines.append("")
    lines.append(f"Based on {result.get('transaction_count', 0)} transactions.")
    if result.get("warning"):
        lines.append(f"Please note: {result['warning']}.")
    return "\n".join(lines)


def _plain_largest(result: dict) -> str:
    items = result.get("items") or []
    if not items:
        return "No transactions were found."
    lines = ["### Largest transactions", "", "| Date | Merchant | Amount |", "|---|---|---:|"]
    for item in items[:10]:
        date = str(item.get("date") or "")[:10]
        merchant = str(item.get("merchant") or "Unknown").replace("_", " ").title()
        lines.append(f"| {date} | {merchant} | {_money(item.get('amount'), item.get('currency'))} |")
    lines.extend(["", f"The largest transaction was **{_money(result.get('value'), result.get('currency'))}**."])
    if result.get("warning"):
        lines.append(f"Please note: {result['warning']}.")
    return "\n".join(lines)


def _plain_anomalies(items: list[dict]) -> str:
    if not items:
        return "No transactions are currently flagged as unusual."
    lines = [f"### Spending requiring review ({len(items)})", "", "| Date | Merchant | Amount | Review score |", "|---|---|---:|---:|"]
    for item in items[:10]:
        date = str(item.get("date") or "")[:10]
        merchant = str(item.get("merchant") or "Unknown").replace("_", " ").title()
        try:
            score = f"{float(item.get('normalized_score')):.2f}"
        except (TypeError, ValueError):
            score = "—"
        lines.append(f"| {date} | {merchant} | {_money(item.get('amount'), item.get('currency'))} | {score} |")
    lines.extend(["", "These are review signals, not proof of fraud. Open **Spending anomalies** for the complete list."])
    return "\n".join(lines)


def _plain_portfolio_insights(portfolios: list[dict], focus: str) -> str:
    if not portfolios:
        return "No brokerage portfolio is available for risk and drift analysis."
    lines: list[str] = []
    for portfolio in portfolios:
        account = portfolio.get("account") or "Portfolio"
        lines.extend([f"### {account}", f"Risk profile: **{_friendly_label(portfolio.get('profile'))}**"])
        if focus == "risk":
            limits = portfolio.get("risk_limits") or {}
            allocations = portfolio.get("allocations") or []
            outside_target = [row for row in allocations if str(row.get("status") or "").upper() not in {"ON_TARGET", "WITHIN_TOLERANCE"}]
            if outside_target:
                affected = ", ".join(_friendly_label(row.get("asset_class")) for row in outside_target)
                lines.extend(["", f"**Needs review:** {affected} is outside its configured target range. Proposed trades will still be checked against every hard risk gate."])
            else:
                lines.extend(["", "**Within configured allocation ranges:** no current asset class is outside its target tolerance. Every proposed trade is still checked separately."])
            lines.extend(
                [
                    "",
                    "| Safety guardrail | Limit |",
                    "|---|---:|",
                    f"| Maximum crypto | {_as_percent(limits.get('max_crypto_weight'))} |",
                    f"| Maximum equities and equity ETFs | {_as_percent(limits.get('max_equity_weight'))} |",
                    f"| Maximum single asset | {_as_percent(limits.get('max_single_asset_weight'))} |",
                    f"| Minimum bonds | {_as_percent(limits.get('min_bond_weight'))} |",
                    f"| Maximum turnover | {_as_percent(limits.get('max_turnover'))} |",
                    f"| Maximum trade size | {_money(limits.get('max_trade_notional'), portfolio.get('base_currency'))} |",
                ]
            )
        else:
            lines.extend(["", "| Asset class | Current | Target | Difference | Status |", "|---|---:|---:|---:|---|"])
            for row in portfolio.get("allocations") or []:
                lines.append(
                    f"| {_friendly_label(row.get('asset_class'))} | {_as_percent(row.get('current_weight'))} | {_as_percent(row.get('target_weight'))} | {_as_percent(row.get('drift'), signed=True)} | {_friendly_label(row.get('status'))} |"
                )
        if portfolio.get("stale_symbols"):
            lines.extend(["", "Latest available prices are older for: " + ", ".join(portfolio["stale_symbols"]) + "."])
        lines.append("")
    lines.append("These values explain the configured controls; every proposed sale is evaluated separately and fails closed.")
    return "\n".join(lines)


def _plain_candidate_decisions(result: dict, wash_sale_only: bool = False) -> str:
    if not result.get("found"):
        return "No completed portfolio analysis is available. Open **Portfolio analysis** and run it first."
    approved = result.get("approved") or []
    protected = result.get("protected") or []
    if wash_sale_only:
        conflicts = [item for item in protected if "WASH_SALE" in str(item.get("rejection_code") or "")]
        if not conflicts:
            return "The latest completed analysis contains no persisted wash-sale conflicts."
        lines = [f"### Wash-sale protection ({len(conflicts)})", "", "| Symbol | Decision | Explanation |", "|---|---|---|"]
        for item in conflicts:
            lines.append(f"| {item.get('symbol') or 'Unknown'} | Blocked | {item.get('explanation') or 'Wash-sale safety rule triggered'} |")
        return "\n".join(lines)
    lines = [f"The latest analysis found **{len(approved)} safe tax-loss opportunities** and protected you from **{len(protected)} other candidates**."]
    if approved:
        lines.extend(["", "| Rank | Symbol | Quantity | Estimated usable loss |", "|---:|---|---:|---:|"])
        for item in sorted(approved, key=lambda row: row.get("rank") or 999):
            lines.append(f"| {item.get('rank') or '—'} | {item.get('symbol') or 'Unknown'} | {item.get('selected_quantity') or '—'} | {_money(item.get('estimated_loss'))} |")
    else:
        lines.extend(["", "No candidate passed every hard gate. This is a valid fail-closed result, not an application error."])
    reasons: dict[str, int] = {}
    for item in protected:
        reason = _friendly_label(item.get("rejection_code"))
        reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        lines.extend(["", "Most important protections: " + ", ".join(f"{name} ({count})" for name, count in sorted(reasons.items(), key=lambda pair: -pair[1])[:4]) + "."])
    return "\n".join(lines)


def _friendly_label(value) -> str:
    return str(value or "Not configured").replace("_", " ").title()


def _as_percent(value, signed: bool = False) -> str:
    try:
        number = float(value) * 100
        return f"{number:+.1f}%" if signed else f"{number:.1f}%"
    except (TypeError, ValueError):
        return "—"
