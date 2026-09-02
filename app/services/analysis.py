from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.rolling_window import RollingWindowStore
from app.adapters.window_sync import WindowSyncService
from app.config import Settings
from app.domain.enums import (
    AccountType,
    AnalysisRunStatus,
    AnalysisTrigger,
    CandidateStatus,
    MLStatus,
)
from app.domain.errors import ActiveAnalysisExistsError, IdempotencyConflictError
from app.domain.results import AnalysisRunResult
from app.persistence.models import (
    AnalysisRun,
    AnomalyScore,
    Asset,
    BankTransaction,
    Evaluation,
    HarvestingCandidate,
    Holding,
    PortfolioAccount,
    PortfolioAnalysisLock,
    TaxLot,
)
from app.providers.protocols import ProviderRouter
from app.services.anomalies import AnomalyService
from app.services.conflicts import ConflictService
from app.services.harvesting import (
    HarvestingService,
    RankInputs,
    harvesting_target,
    select_against_target,
)


@dataclass
class AnalysisDependencies:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    providers: ProviderRouter
    windows: RollingWindowStore
    clock: object


def _as_of_period(as_of: datetime):
    as_of = as_of.astimezone(UTC) if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    return as_of.date()


def _to_result(run: AnalysisRun, reused: bool, candidate_ids, evaluation_ids, approved_ids) -> AnalysisRunResult:
    return AnalysisRunResult(
        analysis_run_id=run.id,
        user_id=run.user_id,
        trigger=AnalysisTrigger(run.trigger),
        as_of=run.as_of,
        idempotency_key=run.idempotency_key,
        status=AnalysisRunStatus(run.status),
        started_at=run.started_at,
        finished_at=run.finished_at,
        failure_reason=run.failure_reason,
        reused=reused,
        candidate_ids=tuple(candidate_ids),
        evaluation_ids=tuple(evaluation_ids),
        approved_candidate_ids=tuple(approved_ids),
        ml_status=MLStatus(run.ml_status) if run.ml_status else None,
    )


async def run_analysis(
    user_id: UUID,
    *,
    trigger: AnalysisTrigger,
    as_of: datetime,
    idempotency_key: str,
    deps: AnalysisDependencies,
) -> AnalysisRunResult:
    """Facade used by CLI and tests: ML Analysis writes candidates, Eval then persists statuses."""
    proposed = await run_ml_analysis(
        user_id,
        trigger=trigger,
        as_of=as_of,
        idempotency_key=idempotency_key,
        deps=deps,
    )
    if proposed.status in {AnalysisRunStatus.COMPLETED, AnalysisRunStatus.FAILED}:
        return proposed
    return await evaluate_pending_candidates(
        user_id,
        deps=deps,
        as_of=as_of,
        analysis_run_id=proposed.analysis_run_id,
    )


async def run_ml_analysis(
    user_id: UUID,
    *,
    trigger: AnalysisTrigger,
    as_of: datetime,
    idempotency_key: str,
    deps: AnalysisDependencies,
) -> AnalysisRunResult:
    """ML Analysis Agent pipeline: windows, anomalies, and pending harvesting candidates only."""
    settings = deps.settings
    now = deps.clock.now()
    as_of = as_of.astimezone(UTC) if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    period = _as_of_period(as_of)
    async with deps.session_factory() as session:
        existing = await session.scalar(
            select(AnalysisRun).where(
                AnalysisRun.user_id == user_id,
                AnalysisRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.as_of != as_of or existing.trigger != trigger.value:
                raise IdempotencyConflictError()
            if existing.status in {AnalysisRunStatus.COMPLETED.value, AnalysisRunStatus.FAILED.value}:
                cids, eids, aids = await _collect_ids(session, existing.id)
                return _to_result(existing, True, cids, eids, aids)
            cids, eids, aids = await _collect_ids(session, existing.id)
            if cids:
                return _to_result(existing, True, cids, eids, aids)
            run = existing
        else:
            run = AnalysisRun(
                id=uuid4(),
                user_id=user_id,
                trigger=trigger.value,
                as_of=as_of,
                as_of_period=period,
                idempotency_key=idempotency_key,
                status=AnalysisRunStatus.PENDING.value,
                started_at=now,
                rule_version=settings.harvesting_rule_version,
            )
            session.add(run)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raced = await session.scalar(
                    select(AnalysisRun).where(
                        AnalysisRun.user_id == user_id,
                        AnalysisRun.idempotency_key == idempotency_key,
                    )
                )
                if raced is None:
                    raise
                cids, eids, aids = await _collect_ids(session, raced.id)
                return _to_result(raced, True, cids, eids, aids)
                raise exc
        run.status = AnalysisRunStatus.RUNNING.value
        await session.commit()

    conflicts = ConflictService(settings)
    harvesting = HarvestingService(settings, deps.providers, conflicts)
    anomalies = AnomalyService(settings)
    sync = WindowSyncService(deps.windows, deps.providers)
    ml_status = None
    failure: str | None = None

    async with deps.session_factory() as session:
        run = await session.get(AnalysisRun, run.id)
        assert run is not None
        portfolios = list(
            await session.scalars(
                select(PortfolioAccount).where(
                    PortfolioAccount.user_id == user_id,
                    PortfolioAccount.account_type == AccountType.BROKERAGE.value,
                )
            )
        )
        await conflicts.resolve_expired(session, now)
        await session.commit()

    for portfolio in portfolios:
        try:
            await _propose_portfolio_candidates(
                deps,
                harvesting,
                sync,
                run_id=run.id,
                portfolio=portfolio,
                as_of=as_of,
                period=period,
                now=now,
            )
        except ActiveAnalysisExistsError:
            failure = (failure or "") + f" lock:{portfolio.id}"
        except Exception as exc:  # isolate portfolio failures
            failure = (failure or "") + f" portfolio:{portfolio.id}:{exc}"

    async with deps.session_factory() as session:
        txns = list(
            await session.scalars(
                select(BankTransaction)
                .where(BankTransaction.user_id == user_id, BankTransaction.txn_date <= as_of)
                .order_by(BankTransaction.txn_date, BankTransaction.id)
            )
        )
        result = anomalies.score_user(txns)
        ml_status = result.ml_status
        for score in result.scores:
            existing_score = await session.scalar(
                select(AnomalyScore).where(
                    AnomalyScore.analysis_run_id == run.id,
                    AnomalyScore.transaction_id == score.transaction_id,
                )
            )
            if existing_score is not None:
                continue
            session.add(
                AnomalyScore(
                    id=uuid4(),
                    analysis_run_id=run.id,
                    user_id=user_id,
                    transaction_id=score.transaction_id,
                    raw_decision_score=Decimal(str(score.raw_decision_score)),
                    normalized_score=Decimal(str(score.normalized_score)),
                    model_version=score.model_version,
                    feature_set_version=score.feature_set_version,
                    ml_status=score.ml_status.value,
                    is_flagged=score.is_flagged,
                    features=score.features,
                )
            )
        run = await session.get(AnalysisRun, run.id)
        assert run is not None
        run.ml_status = ml_status.value
        if failure:
            run.finished_at = deps.clock.now()
            run.status = AnalysisRunStatus.FAILED.value
            run.failure_reason = failure[:2000]
        await session.commit()
        cids, eids, aids = await _collect_ids(session, run.id)
        return _to_result(run, False, cids, eids, aids)


async def evaluate_pending_candidates(
    user_id: UUID,
    *,
    deps: AnalysisDependencies,
    as_of: datetime,
    analysis_run_id: UUID | None = None,
) -> AnalysisRunResult:
    """Eval Agent pipeline: consume pending candidates and persist approved/rejected status."""
    as_of = as_of.astimezone(UTC) if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    now = deps.clock.now()
    harvesting = HarvestingService(deps.settings, deps.providers, ConflictService(deps.settings))
    async with deps.session_factory() as session:
        run = await _eval_target_run(session, user_id, analysis_run_id)
        if run is None:
            raise KeyError("No analysis run is waiting for evaluation")
        if run.status == AnalysisRunStatus.COMPLETED.value:
            cids, eids, aids = await _collect_ids(session, run.id)
            return _to_result(run, True, cids, eids, aids)
        if run.status == AnalysisRunStatus.FAILED.value:
            cids, eids, aids = await _collect_ids(session, run.id)
            return _to_result(run, True, cids, eids, aids)
        run_id = run.id
        run_as_of = run.as_of if run.as_of.tzinfo else run.as_of.replace(tzinfo=UTC)
        portfolio_ids = list(
            {
                row.portfolio_id
                for row in await session.scalars(
                    select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == run_id)
                )
            }
        )
        if not portfolio_ids:
            portfolio_ids = [
                row.id
                for row in await session.scalars(
                    select(PortfolioAccount).where(
                        PortfolioAccount.user_id == user_id,
                        PortfolioAccount.account_type == AccountType.BROKERAGE.value,
                    )
                )
            ]

    for portfolio_id in portfolio_ids:
        await _evaluate_and_rank_portfolio(
            deps,
            harvesting,
            run_id=run_id,
            portfolio_id=portfolio_id,
            as_of=run_as_of,
            now=now,
        )

    async with deps.session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        assert run is not None
        run.finished_at = deps.clock.now()
        run.status = AnalysisRunStatus.COMPLETED.value
        await session.commit()
        cids, eids, aids = await _collect_ids(session, run.id)
        return _to_result(run, False, cids, eids, aids)


async def _eval_target_run(session: AsyncSession, user_id: UUID, analysis_run_id: UUID | None) -> AnalysisRun | None:
    if analysis_run_id is not None:
        run = await session.get(AnalysisRun, analysis_run_id)
        if run is None or run.user_id != user_id:
            return None
        return run
    running = (
        await session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.user_id == user_id, AnalysisRun.status == AnalysisRunStatus.RUNNING.value)
            .order_by(AnalysisRun.started_at.desc())
        )
    ).first()
    if running is not None:
        return running
    return (
        await session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.user_id == user_id)
            .order_by(AnalysisRun.started_at.desc())
        )
    ).first()


async def _propose_portfolio_candidates(
    deps: AnalysisDependencies,
    harvesting: HarvestingService,
    sync: WindowSyncService,
    *,
    run_id: UUID,
    portfolio: PortfolioAccount,
    as_of: datetime,
    period,
    now: datetime,
) -> None:
    settings = deps.settings
    overlap = timedelta(hours=settings.window_overlap_hours)
    async with deps.session_factory() as session:
        lock = PortfolioAnalysisLock(
            id=uuid4(),
            portfolio_id=portfolio.id,
            analysis_run_id=run_id,
            as_of_period=period,
            status="ACTIVE",
        )
        session.add(lock)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ActiveAnalysisExistsError() from exc

        lots = list(await session.scalars(select(TaxLot).where(TaxLot.portfolio_id == portfolio.id)))
        assets = {
            asset.id: asset
            for asset in await session.scalars(select(Asset).where(Asset.id.in_({lot.asset_id for lot in lots} or {uuid4()})))
        }
        holdings = list(await session.scalars(select(Holding).where(Holding.portfolio_id == portfolio.id)))
        for holding in holdings:
            asset = await session.get(Asset, holding.asset_id)
            if asset is None:
                continue
            try:
                await sync.sync_price_window(
                    canonical_id=asset.canonical_id,
                    symbol=asset.symbol,
                    currency="USD",
                    asset_type=asset.asset_type,
                    as_of=as_of,
                    window_days=settings.price_window_days,
                    overlap=overlap,
                    now=now,
                )
            except Exception:
                # Do not advance window meta; continue other assets.
                continue

        await harvesting.persist_pending_candidates(session, run_id, lots, assets)
        await session.commit()


async def _holding_valuations(
    session: AsyncSession,
    providers: ProviderRouter,
    portfolio_id: UUID,
    as_of: datetime,
) -> tuple[dict[str, Decimal], dict[str, str]]:
    holdings = list(await session.scalars(select(Holding).where(Holding.portfolio_id == portfolio_id)))
    asset_values: dict[str, Decimal] = {}
    class_of: dict[str, str] = {}
    for holding in holdings:
        asset = await session.get(Asset, holding.asset_id)
        if asset is None:
            continue
        quote = await providers.quote_for_asset_type(asset.asset_type, asset.canonical_id, asset.symbol, as_of)
        value = (quote.price * holding.quantity) if quote else Decimal("0")
        asset_values[asset.canonical_id] = asset_values.get(asset.canonical_id, Decimal("0")) + value
        if asset.asset_type == "ETF" and asset.symbol in {"BND", "AGG", "TLT"}:
            class_of[asset.canonical_id] = "BOND"
        else:
            class_of[asset.canonical_id] = asset.asset_type
    return asset_values, class_of


async def _evaluate_and_rank_portfolio(
    deps: AnalysisDependencies,
    harvesting: HarvestingService,
    *,
    run_id: UUID,
    portfolio_id: UUID,
    as_of: datetime,
    now: datetime,
) -> None:
    async with deps.session_factory() as session:
        asset_values, class_of = await _holding_valuations(session, deps.providers, portfolio_id, as_of)
        pending = list(
            await session.scalars(
                select(HarvestingCandidate).where(
                    HarvestingCandidate.analysis_run_id == run_id,
                    HarvestingCandidate.portfolio_id == portfolio_id,
                    HarvestingCandidate.status == CandidateStatus.PENDING_EVALUATION.value,
                )
            )
        )
        for candidate in pending:
            existing_eval = await session.scalar(
                select(Evaluation).where(Evaluation.candidate_id == candidate.id)
            )
            if existing_eval is not None:
                continue
            await harvesting.evaluate_candidate(session, candidate.id, as_of, now, asset_values, class_of)
        await session.commit()

    async with deps.session_factory() as session:
        approved = list(
            await session.scalars(
                select(HarvestingCandidate).where(
                    HarvestingCandidate.analysis_run_id == run_id,
                    HarvestingCandidate.portfolio_id == portfolio_id,
                    HarvestingCandidate.status == CandidateStatus.APPROVED.value,
                )
            )
        )
        rank_items: list[RankInputs] = []
        for candidate in approved:
            evaluation = (
                await session.scalars(
                    select(Evaluation)
                    .where(Evaluation.candidate_id == candidate.id)
                    .order_by(Evaluation.evaluated_at.desc())
                )
            ).first()
            lot = await session.get(TaxLot, candidate.tax_lot_id)
            asset = await session.get(Asset, candidate.asset_id)
            account = await session.get(PortfolioAccount, candidate.portfolio_id)
            if evaluation is None or lot is None or asset is None or evaluation.quote is None or account is None:
                continue
            per_unit_loss = (lot.per_unit_basis or Decimal("0")) - evaluation.quote
            mirror = await deps.providers.execution.available_quantity(account.alpaca_alias or "", asset.symbol)
            rank_items.append(
                RankInputs(
                    candidate_id=candidate.id,
                    lot_id=lot.id,
                    usable_loss=evaluation.total_loss or Decimal("0"),
                    risk_improvement=evaluation.risk_effect or Decimal("0"),
                    drift_improvement=evaluation.drift_effect or Decimal("0"),
                    replacement_suitability=Decimal("1") if evaluation.replacement_canonical_id else Decimal("0"),
                    estimated_cost=evaluation.estimated_cost or Decimal("0"),
                    unnecessary_turnover=Decimal("0"),
                    acquisition_date=lot.acquisition_date,
                    remaining_quantity=lot.remaining_quantity,
                    per_unit_loss=per_unit_loss,
                    mirror_qty=mirror,
                    quote=evaluation.quote,
                    provider=evaluation.quote_provider or "",
                    replacement_canonical_id=evaluation.replacement_canonical_id,
                    basis=evaluation.basis or Decimal("0"),
                    portfolio_id=portfolio_id,
                    asset_id=asset.id,
                    canonical_id=asset.canonical_id,
                    asset_type=asset.asset_type,
                    acquisition_display=lot.acquisition_date,
                )
            )
        target = await harvesting_target(session, portfolio_id)
        selected = select_against_target(rank_items, target, deps.settings.harvest_allow_exceed_target)
        for item, qty, usable, before, after, rank, explanation in selected:
            evaluation = (
                await session.scalars(
                    select(Evaluation)
                    .where(Evaluation.candidate_id == item.candidate_id)
                    .order_by(Evaluation.evaluated_at.desc())
                )
            ).first()
            if evaluation is None:
                continue
            evaluation.selected_quantity = qty
            evaluation.usable_loss = usable
            evaluation.target_before = before
            evaluation.target_after = after
            evaluation.rank = rank
            evaluation.tie_breaker_explanation = explanation
        lock_row = await session.scalar(
            select(PortfolioAnalysisLock).where(
                PortfolioAnalysisLock.analysis_run_id == run_id,
                PortfolioAnalysisLock.portfolio_id == portfolio_id,
                PortfolioAnalysisLock.status == "ACTIVE",
            )
        )
        if lock_row is not None:
            lock_row.status = "RELEASED"
        await session.commit()


async def _collect_ids(session: AsyncSession, run_id: UUID) -> tuple[list[UUID], list[UUID], list[UUID]]:
    candidates = list(await session.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == run_id)))
    evaluations = list(await session.scalars(select(Evaluation).where(Evaluation.analysis_run_id == run_id)))
    approved = [c.id for c in candidates if c.status == CandidateStatus.APPROVED.value]
    return [c.id for c in candidates], [e.id for e in evaluations], approved


async def evaluate_candidate(candidate_id: UUID, *, deps: AnalysisDependencies, as_of: datetime) -> Evaluation:
    harvesting = HarvestingService(deps.settings, deps.providers, ConflictService(deps.settings))
    now = deps.clock.now()
    async with deps.session_factory() as session:
        candidate = await session.get(HarvestingCandidate, candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        asset_values, class_of = await _holding_valuations(
            session, deps.providers, candidate.portfolio_id, as_of
        )
        evaluation = await harvesting.evaluate_candidate(session, candidate_id, as_of, now, asset_values, class_of)
        await session.commit()
        return evaluation
