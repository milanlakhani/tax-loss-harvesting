from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CandidateStatus
from app.persistence.models import (
    AnomalyScore,
    AnalysisRun,
    Asset,
    BankTransaction,
    Evaluation,
    HarvestingCandidate,
    Holding,
    PaperOrder,
    PortfolioAccount,
    RiskProfile,
    TargetAllocation,
    TaxLot,
)
from app.services.portfolio import class_weights
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
                asset = await self.session.get(Asset, row.asset_id)
                out.append(
                    {
                        "portfolio_id": str(account.id),
                        "asset_id": str(row.asset_id),
                        "account": account.name,
                        "symbol": asset.symbol if asset else "Unknown",
                        "name": asset.name if asset else "Unknown asset",
                        "asset_type": asset.asset_type if asset else "Unknown",
                        "quantity": str(row.quantity),
                        "as_of": row.as_of.isoformat(),
                    }
                )
        return out

    async def portfolio_insights(self, user_id: UUID, providers, as_of: datetime) -> list[dict]:
        accounts = list(
            await self.session.scalars(
                select(PortfolioAccount).where(
                    PortfolioAccount.user_id == user_id,
                    PortfolioAccount.account_type == "BROKERAGE",
                )
            )
        )
        output: list[dict] = []
        for account in accounts:
            holdings = list(await self.session.scalars(select(Holding).where(Holding.portfolio_id == account.id)))
            asset_values: dict[str, Decimal] = {}
            class_of: dict[str, str] = {}
            stale_symbols: list[str] = []
            missing_symbols: list[str] = []
            for holding in holdings:
                asset = await self.session.get(Asset, holding.asset_id)
                if asset is None:
                    continue
                quote = await providers.quote_for_asset_type(asset.asset_type, asset.canonical_id, asset.symbol, as_of)
                if quote is None:
                    missing_symbols.append(asset.symbol)
                    continue
                asset_values[asset.canonical_id] = asset_values.get(asset.canonical_id, Decimal("0")) + quote.price * holding.quantity
                class_of[asset.canonical_id] = "BOND" if asset.asset_type == "ETF" and asset.symbol in {"BND", "AGG", "TLT"} else asset.asset_type
                if quote.stale:
                    stale_symbols.append(asset.symbol)
            current = class_weights(asset_values, class_of)
            targets = list(await self.session.scalars(select(TargetAllocation).where(TargetAllocation.portfolio_id == account.id)))
            target_by_class = {row.asset_class: row.target_weight for row in targets if row.canonical_asset_id is None}
            classes = sorted(set(current) | set(target_by_class))
            allocations = []
            for asset_class in classes:
                current_weight = current.get(asset_class, Decimal("0"))
                target_weight = target_by_class.get(asset_class, Decimal("0"))
                drift = current_weight - target_weight
                allocations.append(
                    {
                        "asset_class": asset_class,
                        "current_weight": str(current_weight),
                        "target_weight": str(target_weight),
                        "drift": str(drift),
                        "status": "ON_TARGET" if abs(drift) < Decimal("0.05") else ("OVERWEIGHT" if drift > 0 else "UNDERWEIGHT"),
                    }
                )
            profile = await self.session.scalar(select(RiskProfile).where(RiskProfile.portfolio_id == account.id))
            output.append(
                {
                    "portfolio_id": str(account.id),
                    "account": account.name,
                    "profile": profile.name if profile else "Not configured",
                    "total_value": str(sum(asset_values.values(), Decimal("0"))),
                    "base_currency": account.base_currency,
                    "allocations": allocations,
                    "risk_limits": {
                        "max_crypto_weight": str(profile.max_crypto_weight) if profile else None,
                        "max_single_asset_weight": str(profile.max_single_asset_weight) if profile else None,
                        "max_equity_weight": str(profile.max_equity_weight) if profile else None,
                        "min_bond_weight": str(profile.min_bond_weight) if profile else None,
                        "max_trade_notional": str(profile.max_trade_notional) if profile else None,
                        "max_turnover": str(profile.max_turnover) if profile else None,
                    },
                    "stale_symbols": sorted(set(stale_symbols)),
                    "missing_symbols": sorted(set(missing_symbols)),
                    "as_of": as_of.isoformat(),
                }
            )
        return output

    async def latest_candidate_decisions(self, user_id: UUID) -> dict:
        run = (
            await self.session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.user_id == user_id, AnalysisRun.status == "COMPLETED")
                .order_by(AnalysisRun.finished_at.desc())
            )
        ).first()
        if run is None:
            return {"found": False, "approved": [], "protected": []}
        return {
            "found": True,
            "analysis_run_id": str(run.id),
            "as_of": run.as_of.isoformat(),
            "approved": await self.candidates(run.id, approved=True),
            "protected": await self.candidates(run.id, approved=False),
        }

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
            asset = await self.session.get(Asset, row.asset_id)
            account = await self.session.get(PortfolioAccount, row.portfolio_id)
            out.append(
                {
                    "candidate_id": str(row.id),
                    "status": row.status,
                    "rejection_code": evaluation.rejection_code if evaluation else None,
                    "explanation": evaluation.explanation if evaluation else None,
                    "rank": evaluation.rank if evaluation else None,
                    "symbol": asset.symbol if asset else "Unknown",
                    "asset_type": asset.asset_type if asset else "Unknown",
                    "account": account.name if account else "Unknown",
                    "selected_quantity": str(evaluation.selected_quantity) if evaluation and evaluation.selected_quantity is not None else None,
                    "estimated_loss": str(evaluation.usable_loss) if evaluation and evaluation.usable_loss is not None else None,
                    "reference_price": str(evaluation.quote) if evaluation and evaluation.quote is not None else None,
                    "quote_provider": evaluation.quote_provider if evaluation else None,
                    "quote_feed": (evaluation.extra or {}).get("quote_feed") if evaluation and evaluation.extra else None,
                    "quote_source_timestamp": (evaluation.extra or {}).get("quote_source_timestamp") if evaluation and evaluation.extra else None,
                    "quote_retrieved_at": (evaluation.extra or {}).get("quote_retrieved_at") if evaluation and evaluation.extra else None,
                    "quote_freshness_seconds": (evaluation.extra or {}).get("quote_freshness_seconds") if evaluation and evaluation.extra else None,
                    "replacement": evaluation.replacement_canonical_id if evaluation else None,
                    "rule_version": evaluation.rule_version if evaluation else None,
                }
            )
        return out

    async def anomalous_transactions(self, user_id: UUID) -> list[dict]:
        rows = list(
            await self.session.scalars(
                select(AnomalyScore).where(AnomalyScore.user_id == user_id, AnomalyScore.is_flagged.is_(True))
            )
        )
        out = []
        for row in rows:
            txn = await self.session.get(BankTransaction, row.transaction_id)
            out.append({
                "transaction_id": str(row.transaction_id),
                "normalized_score": str(row.normalized_score),
                "ml_status": row.ml_status,
                "date": txn.txn_date.isoformat() if txn else None,
                "merchant": txn.normalized_merchant if txn else "Unknown",
                "amount": str(txn.original_amount) if txn else None,
                "currency": txn.original_currency if txn else None,
            })
        return sorted(out, key=lambda item: item["normalized_score"], reverse=True)

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
