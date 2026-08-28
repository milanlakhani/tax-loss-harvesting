from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CandidateStatus
from app.persistence.models import (
    AnomalyScore,
    BankTransaction,
    Evaluation,
    HarvestingCandidate,
    Holding,
    PaperOrder,
    PortfolioAccount,
    TaxLot,
)
from app.services.statistics import StatisticsService


class QueryService:
    """Read models for MCP/API. No harvesting math lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.stats = StatisticsService(session)

    async def holdings(self, user_id: UUID) -> list[dict]:
        accounts = list(await self.session.scalars(select(PortfolioAccount).where(PortfolioAccount.user_id == user_id)))
        out = []
        for account in accounts:
            rows = list(await self.session.scalars(select(Holding).where(Holding.portfolio_id == account.id)))
            for row in rows:
                out.append(
                    {
                        "portfolio_id": str(account.id),
                        "asset_id": str(row.asset_id),
                        "quantity": str(row.quantity),
                        "as_of": row.as_of.isoformat(),
                    }
                )
        return out

    async def transactions(self, user_id: UUID, start=None, end=None) -> list[dict]:
        stmt = select(BankTransaction).where(BankTransaction.user_id == user_id)
        if start is not None:
            stmt = stmt.where(BankTransaction.txn_date >= start)
        if end is not None:
            stmt = stmt.where(BankTransaction.txn_date <= end)
        rows = list(await self.session.scalars(stmt.order_by(BankTransaction.txn_date)))
        return [
            {
                "id": str(row.id),
                "external_id": row.external_transaction_id,
                "date": row.txn_date.isoformat(),
                "merchant": row.normalized_merchant,
                "amount": str(row.original_amount),
                "currency": row.original_currency,
            }
            for row in rows
        ]

    async def candidates(self, analysis_run_id: UUID, *, approved: bool) -> list[dict]:
        status = CandidateStatus.APPROVED.value if approved else None
        rows = list(await self.session.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == analysis_run_id)))
        out = []
        for row in rows:
            if approved and row.status != CandidateStatus.APPROVED.value:
                continue
            if not approved and row.status == CandidateStatus.APPROVED.value:
                continue
            evaluation = (
                await self.session.scalars(
                    select(Evaluation).where(Evaluation.candidate_id == row.id).order_by(Evaluation.evaluated_at.desc())
                )
            ).first()
            out.append(
                {
                    "candidate_id": str(row.id),
                    "status": row.status,
                    "rejection_code": evaluation.rejection_code if evaluation else None,
                    "explanation": evaluation.explanation if evaluation else None,
                    "rank": evaluation.rank if evaluation else None,
                }
            )
        return out

    async def anomalous_transactions(self, user_id: UUID) -> list[dict]:
        rows = list(
            await self.session.scalars(
                select(AnomalyScore).where(AnomalyScore.user_id == user_id, AnomalyScore.is_flagged.is_(True))
            )
        )
        return [
            {
                "transaction_id": str(row.transaction_id),
                "normalized_score": str(row.normalized_score),
                "ml_status": row.ml_status,
            }
            for row in rows
        ]

    async def paper_order_status(self, order_id: UUID) -> dict | None:
        order = await self.session.get(PaperOrder, order_id)
        if order is None:
            return None
        return {
            "order_id": str(order.id),
            "status": order.status,
            "provider_order_id": order.provider_order_id,
            "filled_quantity": str(order.filled_quantity) if order.filled_quantity is not None else None,
            "fill_price": str(order.fill_price) if order.fill_price is not None else None,
        }

    async def tax_lots(self, portfolio_id: UUID) -> list[dict]:
        rows = list(await self.session.scalars(select(TaxLot).where(TaxLot.portfolio_id == portfolio_id)))
        return [{"lot_id": str(row.id), "remaining": str(row.remaining_quantity), "external": row.external_lot_id} for row in rows]
