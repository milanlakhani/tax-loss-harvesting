from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.adapters.storage import LocalStatementStorage
from app.config import override_settings
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.demo_data.constants import AS_OF, DEFAULT_DEMO_AS_OF_DATE, USER_A_ID, as_of_datetime
from app.demo_data.generate import (
    build_fake_providers,
    seed_labels,
    seed_mirrors,
    seed_replacements,
    seed_risk_and_targets,
    seed_users,
)
from app.domain.enums import AnalysisTrigger, CandidateStatus, RejectionCode
from app.persistence.models import Evaluation, HarvestingCandidate
from app.providers.fakes import RecordingClock
from app.providers.protocols import ExecutionPosition
from app.services.analysis import AnalysisDependencies, run_analysis
from app.services.conflicts import ConflictService
from app.services.harvesting import HarvestingService
from app.services.ingestion import StatementIngestor


async def _seed(session, settings, *, as_of_date=None, brokerage_specs=None, providers=None):
    override_settings(settings)
    storage = LocalStatementStorage(settings.local_data_dir)
    if as_of_date is None:
        statements, labels = build_bank_statements()
        brokerage = brokerage_specs or (portfolio_a_spec(), portfolio_b_spec())
        as_of = AS_OF
    else:
        statements, labels = build_bank_statements(as_of=as_of_date, min_history=settings.min_history_threshold)
        brokerage = brokerage_specs or (portfolio_a_spec(as_of=as_of_date), portfolio_b_spec(as_of=as_of_date))
        as_of = as_of_datetime(as_of_date)
    providers = providers or build_fake_providers(as_of, brokerage_specs=brokerage)
    ingestor = StatementIngestor(storage, providers.fx)
    await seed_users(session)
    await seed_replacements(session)
    await seed_risk_and_targets(session)
    await seed_mirrors(session, providers)
    for spec in statements:
        await ingestor.ingest(session, render_bank_pdf(spec), f"{spec.statement_id}.pdf")
    for spec in brokerage:
        await ingestor.ingest(session, render_brokerage_pdf(spec), f"{spec.statement_id}.pdf")
    await seed_labels(session, labels)
    await session.commit()
    return providers


def _deps(settings, session_factory, providers, as_of):
    return AnalysisDependencies(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(as_of),
    )


@pytest.mark.integration
async def test_current_demo_analysis_keeps_gate_mix(session, session_factory, settings):
    as_of_date = DEFAULT_DEMO_AS_OF_DATE
    as_of = as_of_datetime(as_of_date)
    providers = await _seed(session, settings, as_of_date=as_of_date)
    result = await run_analysis(
        USER_A_ID,
        trigger=AnalysisTrigger.MANUAL,
        as_of=as_of,
        idempotency_key="current-demo",
        deps=_deps(settings, session_factory, providers, as_of),
    )
    assert result.status.value == "COMPLETED"
    async with session_factory() as db:
        evaluations = list(await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == result.analysis_run_id)))
        by_code = {}
        for ev in evaluations:
            by_code.setdefault(ev.rejection_code, []).append(ev)
        assert by_code.get(RejectionCode.WASH_SALE_CONFLICT.value)
        assert by_code.get(RejectionCode.CRYPTO_REPURCHASE_CONFLICT.value)
        approved = [c for c in await db.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == result.analysis_run_id)) if c.status == CandidateStatus.APPROVED.value]
        assert len(approved) >= 5
        assert RejectionCode.DATA_STALE.value not in by_code
        assert RejectionCode.POSITION_MISMATCH.value not in by_code


@pytest.mark.integration
async def test_2024_statements_are_stale_at_current_as_of(session, session_factory, settings):
    as_of = as_of_datetime(DEFAULT_DEMO_AS_OF_DATE)
    historical = (portfolio_a_spec(), portfolio_b_spec())
    providers = build_fake_providers(as_of, brokerage_specs=historical)
    await _seed(session, settings, as_of_date=None, brokerage_specs=historical, providers=providers)
    result = await run_analysis(
        USER_A_ID,
        trigger=AnalysisTrigger.MANUAL,
        as_of=as_of,
        idempotency_key="stale-2024",
        deps=_deps(settings, session_factory, providers, as_of),
    )
    async with session_factory() as db:
        evaluations = list(await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == result.analysis_run_id)))
        codes = {ev.rejection_code for ev in evaluations}
        assert RejectionCode.DATA_STALE.value in codes
        approved = [c for c in await db.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == result.analysis_run_id)) if c.status == CandidateStatus.APPROVED.value]
        assert approved == []


@pytest.mark.integration
async def test_incomplete_wash_window_fails_closed(session, session_factory, settings):
    as_of_date = DEFAULT_DEMO_AS_OF_DATE
    as_of = as_of_datetime(as_of_date)
    short_a = replace(portfolio_a_spec(as_of=as_of_date), period_start=as_of_date - timedelta(days=5))
    short_b = replace(portfolio_b_spec(as_of=as_of_date), period_start=as_of_date - timedelta(days=5))
    providers = await _seed(session, settings, as_of_date=as_of_date, brokerage_specs=(short_a, short_b))
    result = await run_analysis(
        USER_A_ID,
        trigger=AnalysisTrigger.MANUAL,
        as_of=as_of,
        idempotency_key="incomplete",
        deps=_deps(settings, session_factory, providers, as_of),
    )
    async with session_factory() as db:
        evaluations = list(await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == result.analysis_run_id)))
        codes = {ev.rejection_code for ev in evaluations}
        assert RejectionCode.INCOMPLETE_HISTORY.value in codes
        approved = [c for c in await db.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == result.analysis_run_id)) if c.status == CandidateStatus.APPROVED.value]
        assert approved == []


@pytest.mark.integration
async def test_unrelated_alpaca_holdings_are_position_mismatch(session, session_factory, settings):
    as_of_date = DEFAULT_DEMO_AS_OF_DATE
    as_of = as_of_datetime(as_of_date)
    providers = await _seed(session, settings, as_of_date=as_of_date)
    providers.execution.seed_position(
        ExecutionPosition(
            account_alias="conservative-demo",
            symbol="UNRELATED",
            quantity=Decimal("25"),
            tradable=True,
            asset_class="ETF",
        )
    )
    result = await run_analysis(
        USER_A_ID,
        trigger=AnalysisTrigger.MANUAL,
        as_of=as_of,
        idempotency_key="mismatch",
        deps=_deps(settings, session_factory, providers, as_of),
    )
    async with session_factory() as db:
        evaluations = list(await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == result.analysis_run_id)))
        codes = {ev.rejection_code for ev in evaluations}
        assert RejectionCode.POSITION_MISMATCH.value in codes
        approved = [c for c in await db.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == result.analysis_run_id)) if c.status == CandidateStatus.APPROVED.value]
        assert approved == []


@pytest.mark.integration
async def test_order_quantity_is_verified_against_current_alpaca_position(settings):
    as_of = as_of_datetime(DEFAULT_DEMO_AS_OF_DATE)
    providers = build_fake_providers(as_of)
    service = HarvestingService(settings, providers, ConflictService(settings))
    ok = await service.verify_order_quantity("conservative-demo", "VTI", Decimal("1"))
    assert ok is None
    missing = await service.verify_order_quantity("conservative-demo", "VTI", Decimal("9999"))
    assert missing is RejectionCode.POSITION_MISMATCH
    gone = await service.verify_order_quantity("conservative-demo", "NOT-HELD", Decimal("1"))
    assert gone is RejectionCode.POSITION_MISMATCH
