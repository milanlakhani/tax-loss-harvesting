from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from app.container import AppContainer
from app.demo_data.constants import resolve_runtime_as_of
from app.domain.enums import AnalysisTrigger
from app.services.analysis import evaluate_candidate, evaluate_pending_candidates, run_ml_analysis
from app.services.ingestion import StatementIngestor
from app.services.queries import QueryService
from app.services.statistics import StatisticsService

MCP_TOOL_NAMES = (
    "get_quote",
    "get_holdings",
    "get_transactions",
    "parse_statement",
    "run_analysis",
    "evaluate_candidate",
    "evaluate_pending_candidates",
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
    "get_latest_candidate_decisions",
    "get_paper_order_status",
)

FORBIDDEN_MCP_TOOLS = ("submit_paper_order", "confirm_paper_order", "prepare_paper_order")

MCP_TOOL_PARAMETERS = {
    "get_quote": ("canonical_id", "symbol", "asset_type"),
    "get_holdings": ("user_id",),
    "get_transactions": ("user_id",),
    "parse_statement": ("filename", "data_hex"),
    "run_analysis": ("user_id", "idempotency_key"),
    "evaluate_candidate": ("candidate_id",),
    "evaluate_pending_candidates": ("user_id", "analysis_run_id"),
    "get_spending_summary": ("user_id",),
    "get_income_summary": ("user_id",),
    "get_cashflow_summary": ("user_id",),
    "compare_spending_periods": ("user_id", "current_start", "current_end", "prior_start", "prior_end"),
    "get_category_breakdown": ("user_id",),
    "get_merchant_summary": ("user_id",),
    "get_largest_transactions": ("user_id",),
    "get_account_balance_history": ("user_id",),
    "get_anomalous_transactions": ("user_id",),
    "get_portfolio_insights": ("user_id",),
    "get_latest_candidate_decisions": ("user_id",),
    "get_paper_order_status": ("order_id",),
}


def _json_value(value):
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    return value


def _json(result) -> dict:
    value = _json_value(result)
    return value if isinstance(value, dict) else {"value": value}


class McpToolHandlers:
    """Thin typed wrappers around application services. No financial logic."""

    def __init__(self, container: AppContainer) -> None:
        self.container = container

    async def get_quote(self, canonical_id: str, symbol: str, asset_type: str) -> dict:
        async with self.container.session_factory() as session:
            as_of = await resolve_runtime_as_of(session, self.container.settings)
        quote = await self.container.providers.quote_for_asset_type(asset_type, canonical_id, symbol, as_of)
        if quote is None:
            return {"found": False}
        return {
            "found": True,
            "canonical_id": quote.canonical_id,
            "symbol": quote.symbol or symbol,
            "asset_type": quote.asset_type or asset_type,
            "price": str(quote.price),
            "currency": quote.currency,
            "provider": quote.provider,
            "feed": quote.feed,
            "source_timestamp": quote.source_timestamp.isoformat(),
            "retrieved_at": quote.retrieved_at.isoformat(),
            "freshness_seconds": quote.freshness_seconds,
            "stale": quote.stale,
        }

    async def get_holdings(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            return await QueryService(session).holdings(UUID(user_id))

    async def get_transactions(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            return await QueryService(session).transactions(UUID(user_id))

    async def parse_statement(self, filename: str, data_hex: str) -> dict:
        from app.agents.specialists import invoke_doc_parsing_agent

        data = bytes.fromhex(data_hex)
        return await invoke_doc_parsing_agent(self.container, filename=filename, data=data)

    async def run_analysis_tool(self, user_id: str, idempotency_key: str) -> dict:
        async with self.container.session_factory() as session:
            as_of = await resolve_runtime_as_of(session, self.container.settings)
        result = await run_ml_analysis(
            UUID(user_id),
            trigger=AnalysisTrigger.API,
            as_of=as_of,
            idempotency_key=idempotency_key,
            deps=self.container.analysis_deps(),
        )
        return {
            "analysis_run_id": str(result.analysis_run_id),
            "status": result.status.value,
            "candidate_ids": [str(i) for i in result.candidate_ids],
            "approved_candidate_ids": [str(i) for i in result.approved_candidate_ids],
            "ml_status": result.ml_status.value if result.ml_status else None,
            "evaluated": False,
        }

    async def evaluate_candidate_tool(self, candidate_id: str) -> dict:
        async with self.container.session_factory() as session:
            as_of = await resolve_runtime_as_of(session, self.container.settings)
        evaluation = await evaluate_candidate(
            UUID(candidate_id),
            deps=self.container.analysis_deps(),
            as_of=as_of,
        )
        return {
            "status": evaluation.status,
            "rejection_code": evaluation.rejection_code,
            "explanation": evaluation.explanation,
        }

    async def evaluate_pending_candidates_tool(self, user_id: str, analysis_run_id: str = "") -> dict:
        run_id = UUID(analysis_run_id) if analysis_run_id else None
        async with self.container.session_factory() as session:
            as_of = await resolve_runtime_as_of(session, self.container.settings)
        try:
            result = await evaluate_pending_candidates(
                UUID(user_id),
                deps=self.container.analysis_deps(),
                as_of=as_of,
                analysis_run_id=run_id,
            )
        except KeyError:
            return {"found": False, "approved_candidate_ids": [], "evaluated": False}
        return {
            "found": True,
            "analysis_run_id": str(result.analysis_run_id),
            "status": result.status.value,
            "approved_candidate_ids": [str(i) for i in result.approved_candidate_ids],
            "ml_status": result.ml_status.value if result.ml_status else None,
            "evaluated": True,
            "reused": result.reused,
        }

    async def get_spending_summary(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).spending_summary(UUID(user_id)))

    async def get_income_summary(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).income_summary(UUID(user_id)))

    async def get_cashflow_summary(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).cash_flow_summary(UUID(user_id)))

    async def compare_spending_periods(self, user_id: str, current_start: str, current_end: str, prior_start: str, prior_end: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(
                await StatisticsService(session).spending_period_comparison(
                    UUID(user_id),
                    datetime.fromisoformat(current_start),
                    datetime.fromisoformat(current_end),
                    datetime.fromisoformat(prior_start),
                    datetime.fromisoformat(prior_end),
                )
            )

    async def get_category_breakdown(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).category_breakdown(UUID(user_id)))

    async def get_merchant_summary(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).merchant_summary(UUID(user_id)))

    async def get_largest_transactions(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            query = QueryService(session)
            result = _json(await StatisticsService(session).largest_transactions(UUID(user_id)))
            transactions = await query.transactions(UUID(user_id))
            by_external = {row["external_id"]: row for row in transactions}
            result["items"] = [
                by_external[external_id]
                for external_id in result.get("breakdown", {})
                if external_id in by_external
            ]
            return result

    async def get_account_balance_history(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).account_balance_history(UUID(user_id)))

    async def get_anomalous_transactions(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            return await QueryService(session).anomalous_transactions(UUID(user_id))

    async def get_portfolio_insights(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            as_of = await resolve_runtime_as_of(session, self.container.settings)
            return await QueryService(session).portfolio_insights(
                UUID(user_id),
                self.container.providers,
                as_of,
            )

    async def get_latest_candidate_decisions(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return await QueryService(session).latest_candidate_decisions(UUID(user_id))

    async def get_paper_order_status(self, order_id: str) -> dict:
        async with self.container.session_factory() as session:
            row = await QueryService(session).paper_order_status(UUID(order_id))
            return row or {"found": False}
