from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import (
    CandidateStatus,
    ELIGIBLE_HARVEST_ASSET_TYPES,
    INELIGIBLE_HARVEST_ASSET_TYPES,
    RejectionCode,
    ReplacementKind,
)
from app.domain.errors import InvalidStateTransitionError as StateError
from app.persistence.models import (
    Asset,
    BrokerageDividend,
    BrokeragePurchase,
    BrokerageSale,
    Evaluation,
    HarvestingCandidate,
    Holding,
    PaperOrder,
    PortfolioAccount,
    ReplacementRelationship,
    RiskProfile,
    TargetAllocation,
    TaxLot,
)
from app.providers.protocols import ProviderRouter, Quote
from app.services.conflicts import ConflictService, canonical_conflict_payload, persistable
from app.services.portfolio import class_weights, simulated_weights_after_sale

ALLOWED_TRANSITIONS = {
    CandidateStatus.PENDING_EVALUATION: {
        CandidateStatus.APPROVED,
        CandidateStatus.REJECTED,
        CandidateStatus.BELOW_THRESHOLD,
        CandidateStatus.NOT_EXECUTABLE,
    }
}


@dataclass(slots=True)
class GateContext:
    candidate: HarvestingCandidate
    lot: TaxLot
    asset: Asset
    account: PortfolioAccount
    quote: Quote | None
    replacement: tuple[str, ReplacementKind] | None
    trade_notional: Decimal
    total_loss: Decimal | None
    mirror_qty: Decimal
    tradable: bool
    conflicting_ids: list[str]
    window_start: str | None
    window_end: str | None
    risk_effect: Decimal
    drift_effect: Decimal


@dataclass(slots=True)
class RankInputs:
    candidate_id: UUID
    lot_id: UUID
    usable_loss: Decimal
    risk_improvement: Decimal
    drift_improvement: Decimal
    replacement_suitability: Decimal
    estimated_cost: Decimal
    unnecessary_turnover: Decimal
    acquisition_date: datetime
    remaining_quantity: Decimal
    per_unit_loss: Decimal
    mirror_qty: Decimal
    quote: Decimal
    provider: str
    replacement_canonical_id: str | None
    basis: Decimal
    portfolio_id: UUID
    asset_id: UUID
    canonical_id: str
    asset_type: str
    acquisition_display: datetime


def transition(current: str, target: CandidateStatus) -> None:
    allowed = ALLOWED_TRANSITIONS.get(CandidateStatus(current), set())
    if target not in allowed:
        raise StateError(f"Cannot transition candidate from {current} to {target.value}")


class HarvestingService:
    def __init__(self, settings: Settings, providers: ProviderRouter, conflicts: ConflictService) -> None:
        self.settings = settings
        self.providers = providers
        self.conflicts = conflicts

    async def persist_pending_candidates(
        self,
        session: AsyncSession,
        analysis_run_id: UUID,
        lots: list[TaxLot],
        assets: dict[UUID, Asset],
    ) -> list[HarvestingCandidate]:
        created: list[HarvestingCandidate] = []
        for lot in lots:
            asset = assets[lot.asset_id]
            existing = await session.scalar(
                select(HarvestingCandidate).where(
                    HarvestingCandidate.analysis_run_id == analysis_run_id,
                    HarvestingCandidate.tax_lot_id == lot.id,
                )
            )
            if existing is not None:
                created.append(existing)
                continue
            row = HarvestingCandidate(
                id=uuid4(),
                analysis_run_id=analysis_run_id,
                portfolio_id=lot.portfolio_id,
                tax_lot_id=lot.id,
                asset_id=lot.asset_id,
                status=CandidateStatus.PENDING_EVALUATION.value,
            )
            session.add(row)
            created.append(row)
        await session.flush()
        return created

    async def evaluate_candidate(
        self,
        session: AsyncSession,
        candidate_id: UUID,
        as_of: datetime,
        now: datetime,
        asset_values: dict[str, Decimal],
        class_of: dict[str, str],
    ) -> Evaluation:
        candidate = await session.get(HarvestingCandidate, candidate_id)
        if candidate is None:
            raise StateError(f"Unknown candidate {candidate_id}")
        lot = await session.get(TaxLot, candidate.tax_lot_id)
        asset = await session.get(Asset, candidate.asset_id)
        account = await session.get(PortfolioAccount, candidate.portfolio_id)
        assert lot and asset and account
        quote = await self.providers.quote_for_asset_type(asset.asset_type, asset.canonical_id, asset.symbol, as_of)
        tradable = False
        mirror_qty = Decimal("0")
        if account.alpaca_alias:
            tradable = await self.providers.execution.is_tradable(asset.symbol, asset.asset_type)
            mirror_qty = await self.providers.execution.available_quantity(account.alpaca_alias, asset.symbol)
        replacement = await self._preferred_replacement(session, asset.canonical_id)
        total_loss = None
        trade_notional = Decimal("0")
        if quote is not None and lot.remaining_quantity > 0 and lot.per_unit_basis is not None:
            trade_notional = quote.price * lot.remaining_quantity
            total_loss = (lot.per_unit_basis - quote.price) * lot.remaining_quantity
        ctx = GateContext(
            candidate=candidate,
            lot=lot,
            asset=asset,
            account=account,
            quote=quote,
            replacement=replacement,
            trade_notional=trade_notional,
            total_loss=total_loss,
            mirror_qty=mirror_qty,
            tradable=tradable,
            conflicting_ids=[],
            window_start=None,
            window_end=None,
            risk_effect=Decimal("0"),
            drift_effect=Decimal("0"),
        )
        code, explanation, status = await self._apply_gates(session, ctx, as_of, asset_values, class_of)
        transition(candidate.status, status)
        evaluation = Evaluation(
            id=uuid4(),
            candidate_id=candidate.id,
            analysis_run_id=candidate.analysis_run_id,
            status=status.value,
            rejection_code=code.value if code else None,
            explanation=explanation,
            rule_version=self.settings.harvesting_rule_version,
            evaluated_at=now,
            usable_loss=total_loss if status is CandidateStatus.APPROVED else None,
            total_loss=total_loss,
            quote=quote.price if quote else None,
            quote_provider=quote.provider if quote else None,
            basis=lot.remaining_basis,
            replacement_canonical_id=replacement[0] if replacement else None,
            estimated_cost=(quote.price * Decimal("0.0005") * lot.remaining_quantity) if quote else None,
            risk_effect=ctx.risk_effect,
            drift_effect=ctx.drift_effect,
        )
        session.add(evaluation)
        await session.flush()
        candidate.status = status.value
        if code and persistable(code):
            payload = canonical_conflict_payload(
                user_id=account.user_id,
                portfolio_id=account.id,
                tax_lot_id=lot.id,
                canonical_asset_id=asset.canonical_id,
                rejection_code=code,
                rule_version=self.settings.harvesting_rule_version,
                replacement_canonical_id=replacement[0] if replacement else None,
                conflicting_ids=ctx.conflicting_ids,
                window_start=ctx.window_start,
                window_end=ctx.window_end,
            )
            identity, label = await self.conflicts.upsert(
                session,
                payload=payload,
                now=now,
                candidate_id=candidate.id,
                evaluation_id=evaluation.id,
            )
            evaluation.conflict_fingerprint = identity.fingerprint
            evaluation.conflict_label = label.value
        await session.flush()
        return evaluation

    async def _apply_gates(
        self,
        session: AsyncSession,
        ctx: GateContext,
        as_of: datetime,
        asset_values: dict[str, Decimal],
        class_of: dict[str, str],
    ) -> tuple[RejectionCode | None, str, CandidateStatus]:
        lot, asset, account = ctx.lot, ctx.asset, ctx.account
        if not account.is_taxable:
            return RejectionCode.NOT_TAXABLE_ACCOUNT, "Account is not taxable", CandidateStatus.REJECTED
        if asset.asset_type in {t.value for t in INELIGIBLE_HARVEST_ASSET_TYPES} or asset.asset_type not in {
            t.value for t in ELIGIBLE_HARVEST_ASSET_TYPES
        }:
            return RejectionCode.INELIGIBLE_ASSET_TYPE, f"{asset.asset_type} is never a harvesting candidate", CandidateStatus.REJECTED
        if lot.missing_basis or lot.per_unit_basis is None or lot.remaining_basis is None:
            return RejectionCode.MISSING_BASIS, "Missing basis is rejected rather than inferred", CandidateStatus.REJECTED
        if lot.per_unit_basis <= 0:
            return RejectionCode.INVALID_BASIS, "Cost basis must be positive", CandidateStatus.REJECTED
        if lot.remaining_quantity <= 0:
            return RejectionCode.NON_POSITIVE_QUANTITY, "Remaining quantity must be positive", CandidateStatus.REJECTED
        if ctx.quote is None:
            return RejectionCode.UNAVAILABLE_QUOTE, "No quote from the required provider", CandidateStatus.NOT_EXECUTABLE
        age = as_of - ctx.quote.source_timestamp
        if age > timedelta(minutes=self.settings.quote_max_age_minutes):
            return RejectionCode.STALE_QUOTE, "Quote exceeds configured freshness", CandidateStatus.NOT_EXECUTABLE
        if ctx.total_loss is None or ctx.total_loss <= 0:
            return RejectionCode.PROFITABLE_LOT, "Profitable lots are not harvesting candidates", CandidateStatus.REJECTED
        if ctx.total_loss < self.settings.min_loss_threshold:
            return (
                RejectionCode.BELOW_THRESHOLD,
                f"Usable loss {ctx.total_loss} below minimum {self.settings.min_loss_threshold}",
                CandidateStatus.BELOW_THRESHOLD,
            )
        wash = await self._wash_or_crypto_conflict(session, ctx, as_of)
        if wash:
            return wash
        risk = await self._risk_violation(session, ctx, asset_values, class_of)
        if risk:
            return risk
        profile = await session.scalar(select(RiskProfile).where(RiskProfile.portfolio_id == account.id))
        if profile and ctx.trade_notional > profile.max_trade_notional:
            return RejectionCode.MAX_TRADE_SIZE, "Trade exceeds max trade notional", CandidateStatus.REJECTED
        if ctx.replacement is None:
            return RejectionCode.UNKNOWN_REPLACEMENT, "No replacement relationship configured", CandidateStatus.REJECTED
        repl_id, kind = ctx.replacement
        if kind is ReplacementKind.UNKNOWN:
            return RejectionCode.UNKNOWN_REPLACEMENT, "Replacement relationship is unknown", CandidateStatus.REJECTED
        if kind is ReplacementKind.PROHIBITED:
            return RejectionCode.REPLACEMENT_NOT_ALLOWED, "Replacement is prohibited", CandidateStatus.REJECTED
        if kind is ReplacementKind.SUBSTANTIALLY_IDENTICAL:
            return (
                RejectionCode.SUBSTANTIALLY_IDENTICAL_REPLACEMENT,
                "Replacement is substantially identical",
                CandidateStatus.REJECTED,
            )
        if not ctx.tradable:
            return RejectionCode.NOT_TRADABLE, "Execution provider reports asset not tradable", CandidateStatus.NOT_EXECUTABLE
        if ctx.mirror_qty <= 0:
            return (
                RejectionCode.INSUFFICIENT_MIRROR_QUANTITY,
                "Mirrored execution quantity is insufficient",
                CandidateStatus.NOT_EXECUTABLE,
            )
        executed = await session.scalar(
            select(PaperOrder).join(PaperOrder.preparation).where(
                PaperOrder.status.in_(["SUBMITTED", "PARTIALLY_FILLED", "FILLED"])
            )
        )
        # Previously executed: any filled paper order for this lot.
        prior = await self._previously_executed(session, lot.id)
        if prior:
            return RejectionCode.PREVIOUSLY_EXECUTED, "Candidate was previously executed", CandidateStatus.REJECTED
        ctx.risk_effect = _risk_improvement(profile, asset, ctx.trade_notional, asset_values)
        ctx.drift_effect = _drift_improvement(session_targets_cache=None, asset=asset, sale_value=ctx.trade_notional, asset_values=asset_values)
        _ = executed
        _ = repl_id
        return None, "Passed all hard gates", CandidateStatus.APPROVED

    async def _wash_or_crypto_conflict(
        self,
        session: AsyncSession,
        ctx: GateContext,
        as_of: datetime,
    ) -> tuple[RejectionCode, str, CandidateStatus] | None:
        asset = ctx.asset
        start = as_of.date() - timedelta(days=self.settings.wash_sale_window_days)
        end = as_of.date() + timedelta(days=self.settings.wash_sale_window_days)
        ctx.window_start = start.isoformat()
        ctx.window_end = end.isoformat()
        if asset.asset_type in {"EQUITY", "ETF"}:
            identical = await self._substantially_identical_ids(session, asset.canonical_id)
            identical.add(asset.canonical_id)
            purchases = list(
                await session.scalars(
                    select(BrokeragePurchase).where(
                        BrokeragePurchase.portfolio_id == ctx.account.id,
                        BrokeragePurchase.event_date >= datetime(start.year, start.month, start.day, tzinfo=as_of.tzinfo),
                        BrokeragePurchase.event_date <= datetime(end.year, end.month, end.day, tzinfo=as_of.tzinfo),
                    )
                )
            )
            dividends = list(
                await session.scalars(
                    select(BrokerageDividend).where(
                        BrokerageDividend.portfolio_id == ctx.account.id,
                        BrokerageDividend.reinvested.is_(True),
                        BrokerageDividend.event_date >= datetime(start.year, start.month, start.day, tzinfo=as_of.tzinfo),
                        BrokerageDividend.event_date <= datetime(end.year, end.month, end.day, tzinfo=as_of.tzinfo),
                    )
                )
            )
            asset_ids = set()
            for canonical in identical:
                row = await session.scalar(select(Asset).where(Asset.canonical_id == canonical))
                if row:
                    asset_ids.add(row.id)
            hits = [p for p in purchases if p.asset_id in asset_ids]
            hits_d = [d for d in dividends if d.asset_id in asset_ids]
            if hits or hits_d:
                ctx.conflicting_ids = [p.external_transaction_id for p in hits] + [d.external_transaction_id for d in hits_d]
                return (
                    RejectionCode.WASH_SALE_CONFLICT,
                    "Known equity wash-sale conflict within the 30-day window",
                    CandidateStatus.REJECTED,
                )
        if asset.asset_type == "CRYPTO":
            crypto_start = as_of.date() - timedelta(days=self.settings.crypto_repurchase_window_days)
            crypto_end = as_of.date() + timedelta(days=self.settings.crypto_repurchase_window_days)
            ctx.window_start = crypto_start.isoformat()
            ctx.window_end = crypto_end.isoformat()
            purchases = list(
                await session.scalars(
                    select(BrokeragePurchase).where(
                        BrokeragePurchase.portfolio_id == ctx.account.id,
                        BrokeragePurchase.asset_id == asset.id,
                        BrokeragePurchase.event_date
                        >= datetime(crypto_start.year, crypto_start.month, crypto_start.day, tzinfo=as_of.tzinfo),
                        BrokeragePurchase.event_date
                        <= datetime(crypto_end.year, crypto_end.month, crypto_end.day, tzinfo=as_of.tzinfo),
                    )
                )
            )
            if purchases:
                ctx.conflicting_ids = [p.external_transaction_id for p in purchases]
                return (
                    RejectionCode.CRYPTO_REPURCHASE_CONFLICT,
                    "Conservative 30-day crypto repurchase policy (project policy, not tax law)",
                    CandidateStatus.REJECTED,
                )
        return None

    async def _substantially_identical_ids(self, session: AsyncSession, canonical_id: str) -> set[str]:
        rows = list(
            await session.scalars(
                select(ReplacementRelationship).where(
                    ReplacementRelationship.rule_version == self.settings.replacement_rule_version,
                    ReplacementRelationship.relationship == ReplacementKind.SUBSTANTIALLY_IDENTICAL.value,
                )
            )
        )
        out: set[str] = set()
        for row in rows:
            if row.source_canonical_id == canonical_id:
                out.add(row.replacement_canonical_id)
            if row.replacement_canonical_id == canonical_id:
                out.add(row.source_canonical_id)
        return out

    async def _preferred_replacement(
        self, session: AsyncSession, canonical_id: str
    ) -> tuple[str, ReplacementKind] | None:
        rows = list(
            await session.scalars(
                select(ReplacementRelationship).where(
                    ReplacementRelationship.source_canonical_id == canonical_id,
                    ReplacementRelationship.rule_version == self.settings.replacement_rule_version,
                )
            )
        )
        if not rows:
            return None
        allowed = [r for r in rows if r.relationship == ReplacementKind.ALLOWED.value]
        if allowed:
            chosen = sorted(allowed, key=lambda r: r.replacement_canonical_id)[0]
            return chosen.replacement_canonical_id, ReplacementKind.ALLOWED
        identical = [r for r in rows if r.relationship == ReplacementKind.SUBSTANTIALLY_IDENTICAL.value]
        if identical:
            chosen = sorted(identical, key=lambda r: r.replacement_canonical_id)[0]
            return chosen.replacement_canonical_id, ReplacementKind.SUBSTANTIALLY_IDENTICAL
        prohibited = [r for r in rows if r.relationship == ReplacementKind.PROHIBITED.value]
        if prohibited:
            chosen = sorted(prohibited, key=lambda r: r.replacement_canonical_id)[0]
            return chosen.replacement_canonical_id, ReplacementKind.PROHIBITED
        unknown = sorted(rows, key=lambda r: r.replacement_canonical_id)[0]
        return unknown.replacement_canonical_id, ReplacementKind(unknown.relationship)

    async def _risk_violation(
        self,
        session: AsyncSession,
        ctx: GateContext,
        asset_values: dict[str, Decimal],
        class_of: dict[str, str],
    ) -> tuple[RejectionCode, str, CandidateStatus] | None:
        profile = await session.scalar(select(RiskProfile).where(RiskProfile.portfolio_id == ctx.account.id))
        if profile is None or ctx.quote is None:
            return None
        sale_value = ctx.trade_notional
        after_asset = simulated_weights_after_sale(asset_values, ctx.asset.canonical_id, sale_value)
        remaining_values = {
            key: max(val - (sale_value if key == ctx.asset.canonical_id else Decimal("0")), Decimal("0"))
            for key, val in asset_values.items()
        }
        after_class = class_weights(remaining_values, class_of)
        crypto_w = after_class.get("CRYPTO", Decimal("0"))
        bond_w = after_class.get("BOND", Decimal("0"))
        equity_w = after_class.get("EQUITY", Decimal("0")) + after_class.get("ETF", Decimal("0"))
        max_single = max(after_asset.values(), default=Decimal("0"))
        if crypto_w > profile.max_crypto_weight:
            return RejectionCode.RISK_PROFILE_VIOLATION, "Sale would exceed max crypto weight", CandidateStatus.REJECTED
        if max_single > profile.max_single_asset_weight:
            return RejectionCode.RISK_PROFILE_VIOLATION, "Sale would exceed max single-asset weight", CandidateStatus.REJECTED
        if bond_w < profile.min_bond_weight and class_of.get(ctx.asset.canonical_id) == "BOND":
            return RejectionCode.RISK_PROFILE_VIOLATION, "Sale would breach minimum bond weight", CandidateStatus.REJECTED
        if equity_w > profile.max_equity_weight:
            return RejectionCode.RISK_PROFILE_VIOLATION, "Sale would exceed max equity weight", CandidateStatus.REJECTED
        return None

    async def _previously_executed(self, session: AsyncSession, lot_id: UUID) -> bool:
        from app.persistence.models import ExecutionPreparation, HarvestingCandidate as HC

        row = await session.scalar(
            select(PaperOrder)
            .join(ExecutionPreparation, PaperOrder.preparation_id == ExecutionPreparation.id)
            .join(HC, ExecutionPreparation.candidate_id == HC.id)
            .where(
                HC.tax_lot_id == lot_id,
                PaperOrder.status.in_(["SUBMITTED", "PARTIALLY_FILLED", "FILLED"]),
            )
        )
        return row is not None


def _risk_improvement(profile: RiskProfile | None, asset: Asset, sale_value: Decimal, asset_values: dict[str, Decimal]) -> Decimal:
    if not asset_values:
        return Decimal("0")
    total = sum(asset_values.values(), Decimal("0"))
    if total == 0:
        return Decimal("0")
    current = asset_values.get(asset.canonical_id, Decimal("0")) / total
    # Selling a concentrated position improves risk.
    return current


def _drift_improvement(session_targets_cache, asset: Asset, sale_value: Decimal, asset_values: dict[str, Decimal]) -> Decimal:
    _ = session_targets_cache
    total = sum(asset_values.values(), Decimal("0")) or Decimal("1")
    current = asset_values.get(asset.canonical_id, Decimal("0")) / total
    return current


def rank_key(item: RankInputs) -> tuple:
    return (
        -item.usable_loss,
        -item.risk_improvement,
        -item.drift_improvement,
        -item.replacement_suitability,
        item.estimated_cost,
        item.unnecessary_turnover,
        item.acquisition_date,
        str(item.lot_id),
    )


def select_against_target(
    items: list[RankInputs],
    target: Decimal,
    allow_exceed: bool,
) -> list[tuple[RankInputs, Decimal, Decimal, Decimal, Decimal, int, str]]:
    """Return selected quantity, usable loss, target before/after, rank, explanation."""
    ordered = sorted(items, key=rank_key)
    remaining = target
    selected = []
    for rank, item in enumerate(ordered, start=1):
        if remaining <= 0 and not allow_exceed:
            break
        max_qty = min(item.remaining_quantity, item.mirror_qty)
        if max_qty <= 0 or item.per_unit_loss <= 0:
            continue
        if remaining <= 0:
            break
        needed_qty = remaining / item.per_unit_loss
        qty = min(max_qty, needed_qty)
        if qty <= 0:
            continue
        usable = (qty * item.per_unit_loss).quantize(Decimal("0.00000001"))
        if not allow_exceed and usable > remaining:
            qty = remaining / item.per_unit_loss
            if qty > max_qty:
                qty = max_qty
            usable = qty * item.per_unit_loss
            if usable > remaining:
                qty = remaining / item.per_unit_loss
                usable = remaining
        if qty > item.remaining_quantity or qty > item.mirror_qty:
            qty = min(item.remaining_quantity, item.mirror_qty)
            usable = qty * item.per_unit_loss
            if not allow_exceed and usable > remaining:
                qty = remaining / item.per_unit_loss
                usable = remaining
        before = remaining
        remaining = remaining - usable
        explanation = (
            f"rank={rank} loss={item.usable_loss} risk={item.risk_improvement} "
            f"drift={item.drift_improvement} lot={item.lot_id}"
        )
        selected.append((item, qty, usable, before, remaining, rank, explanation))
    return selected


async def harvesting_target(session: AsyncSession, portfolio_id: UUID) -> Decimal:
    sales = list(await session.scalars(select(BrokerageSale).where(BrokerageSale.portfolio_id == portfolio_id)))
    st = sum((s.realized_result for s in sales if s.holding_period == "SHORT_TERM"), Decimal("0"))
    lt = sum((s.realized_result for s in sales if s.holding_period == "LONG_TERM"), Decimal("0"))
    combined = st + lt
    return combined if combined > 0 else Decimal("0")
