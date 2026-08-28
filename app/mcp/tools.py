from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.container import AppContainer
from app.demo_data.constants import resolve_analysis_as_of
from app.domain.enums import AnalysisTrigger
from app.services.analysis import run_analysis, evaluate_candidate
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
    "get_spending_summary",
    "get_income_summary",
    "get_cashflow_summary",
    "compare_spending_periods",
    "get_category_breakdown",
    "get_merchant_summary",
    "get_largest_transactions",
    "get_account_balance_history",
    "get_anomalous_transactions",
    "get_paper_order_status",
)

FORBIDDEN_MCP_TOOLS = ("submit_paper_order", "confirm_paper_order", "prepare_paper_order")


def _json(result) -> dict:
    if hasattr(result, "__dict__"):
        data = {}
        for key, value in result.__dict__.items():
            if hasattr(value, "value"):
                data[key] = value.value
            elif hasattr(value, "isoformat"):
                data[key] = value.isoformat()
            else:
                data[key] = str(value) if value is not None and not isinstance(value, (int, float, bool, dict, list)) else value
        return data
    return {"value": result}


class McpToolHandlers:
    """Thin typed wrappers around application services. No financial logic."""

    def __init__(self, container: AppContainer) -> None:
        self.container = container

    async def get_quote(self, canonical_id: str, symbol: str, asset_type: str) -> dict:
        as_of = resolve_analysis_as_of(self.container.settings)
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
            "as_of": quote.source_timestamp.isoformat(),
            "stale": quote.stale,
        }

    async def get_holdings(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            return await QueryService(session).holdings(UUID(user_id))

    async def get_transactions(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            return await QueryService(session).transactions(UUID(user_id))

    async def parse_statement(self, filename: str, data_hex: str) -> dict:
        data = bytes.fromhex(data_hex)
        async with self.container.session_factory() as session:
            result = await self.container.ingestor.ingest(session, data, filename)
            await session.commit()
            return {
                "statement_id": str(result.statement_id),
                "format": result.format.value,
                "reused": result.reused,
                "transaction_count": result.transaction_count,
                "lot_count": result.lot_count,
            }

    async def run_analysis_tool(self, user_id: str, idempotency_key: str) -> dict:
        result = await run_analysis(
            UUID(user_id),
            trigger=AnalysisTrigger.API,
            as_of=resolve_analysis_as_of(self.container.settings),
            idempotency_key=idempotency_key,
            deps=self.container.analysis_deps(),
        )
        return {
            "analysis_run_id": str(result.analysis_run_id),
            "status": result.status.value,
            "approved_candidate_ids": [str(i) for i in result.approved_candidate_ids],
            "ml_status": result.ml_status.value if result.ml_status else None,
        }

    async def evaluate_candidate_tool(self, candidate_id: str) -> dict:
        evaluation = await evaluate_candidate(
            UUID(candidate_id),
            deps=self.container.analysis_deps(),
            as_of=resolve_analysis_as_of(self.container.settings),
        )
        return {
            "status": evaluation.status,
            "rejection_code": evaluation.rejection_code,
            "explanation": evaluation.explanation,
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
            return _json(await StatisticsService(session).largest_transactions(UUID(user_id)))

    async def get_account_balance_history(self, user_id: str) -> dict:
        async with self.container.session_factory() as session:
            return _json(await StatisticsService(session).account_balance_history(UUID(user_id)))

    async def get_anomalous_transactions(self, user_id: str) -> list[dict]:
        async with self.container.session_factory() as session:
            return await QueryService(session).anomalous_transactions(UUID(user_id))

    async def get_paper_order_status(self, order_id: str) -> dict:
        async with self.container.session_factory() as session:
            row = await QueryService(session).paper_order_status(UUID(order_id))
            return row or {"found": False}
