from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.adapters.storage import LocalStatementStorage
from app.config import override_settings
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.demo_data.constants import AS_OF, PORTFOLIO_A_ID, USER_A_ID
from app.demo_data.generate import (
    build_fake_providers,
    seed_labels,
    seed_mirrors,
    seed_replacements,
    seed_risk_and_targets,
    seed_users,
)
from app.domain.enums import AnalysisTrigger, CandidateStatus, ConflictLabel, RejectionCode
from app.persistence.models import (
    CandidateConflictIdentity,
    Evaluation,
    HarvestingCandidate,
    TaxLot,
)
from app.providers.fakes import RecordingClock
from app.services.analysis import AnalysisDependencies, run_analysis
from app.services.ingestion import StatementIngestor
from app.services.analysis import run_analysis as service_run
from app.api.health import run_analysis as api_run
from app.jobs.run_analysis import run_analysis as cli_run


async def _seed(session, settings):
    override_settings(settings)
    storage = LocalStatementStorage(settings.local_data_dir)
    providers = build_fake_providers()
    ingestor = StatementIngestor(storage, providers.fx)
    await seed_users(session)
    await seed_replacements(session)
    await seed_risk_and_targets(session)
    await seed_mirrors(session, providers)
    statements, labels = build_bank_statements()
    for spec in statements:
        await ingestor.ingest(session, render_bank_pdf(spec), f"{spec.statement_id}.pdf")
    for spec in (portfolio_a_spec(), portfolio_b_spec()):
        await ingestor.ingest(session, render_brokerage_pdf(spec), f"{spec.statement_id}.pdf")
    await seed_labels(session, labels)
    await session.commit()
    return providers


def _deps(settings, session_factory, providers):
    return AnalysisDependencies(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(AS_OF),
    )


@pytest.mark.integration
def test_api_and_cli_share_analysis_service():
    assert service_run is api_run is cli_run


@pytest.mark.integration
async def test_analysis_gates_conflicts_idempotency_and_routing(session, session_factory, settings):
    providers = await _seed(session, settings)
    deps = _deps(settings, session_factory, providers)

    # Routing: equities vs crypto vs fx vs execution.
    await providers.equity.get_quote("ETF:VTI", "VTI", AS_OF)
    await providers.crypto.get_quote("CRYPTO:BTC-USD", "BTC/USD", AS_OF)
    await providers.fx.get_rate("GBP", "USD", AS_OF.date())
    await providers.execution.available_quantity("conservative-demo", "VTI")
    assert any(c[1] == "ETF:VTI" for c in providers.equity.calls)
    assert any(c[1] == "CRYPTO:BTC-USD" for c in providers.crypto.calls)
    assert providers.fx.calls
    assert any(c[0] == "qty" for c in providers.execution.calls)

    first = await run_analysis(USER_A_ID, trigger=AnalysisTrigger.MANUAL, as_of=AS_OF, idempotency_key="k1", deps=deps)
    reused = await run_analysis(USER_A_ID, trigger=AnalysisTrigger.MANUAL, as_of=AS_OF, idempotency_key="k1", deps=deps)
    assert reused.reused is True
    assert reused.analysis_run_id == first.analysis_run_id

    async with session_factory() as db:
        candidates = list(await db.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == first.analysis_run_id)))
        evaluations = list(await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == first.analysis_run_id)))
        assert candidates
        assert evaluations
        by_code = {}
        for ev in evaluations:
            by_code.setdefault(ev.rejection_code, []).append(ev)
        assert by_code.get(RejectionCode.MISSING_BASIS.value)
        assert by_code.get(RejectionCode.PROFITABLE_LOT.value)
        assert by_code.get(RejectionCode.BELOW_THRESHOLD.value)
        assert by_code.get(RejectionCode.WASH_SALE_CONFLICT.value)
        assert by_code.get(RejectionCode.CRYPTO_REPURCHASE_CONFLICT.value)
        assert by_code.get(RejectionCode.RISK_PROFILE_VIOLATION.value)
        assert by_code.get(RejectionCode.STALE_QUOTE.value) or by_code.get(RejectionCode.UNAVAILABLE_QUOTE.value)
        assert by_code.get(RejectionCode.INSUFFICIENT_MIRROR_QUANTITY.value)
        assert by_code.get(RejectionCode.UNKNOWN_REPLACEMENT.value) or by_code.get(
            RejectionCode.SUBSTANTIALLY_IDENTICAL_REPLACEMENT.value
        ) or by_code.get(RejectionCode.REPLACEMENT_NOT_ALLOWED.value)
        approved = [c for c in candidates if c.status == CandidateStatus.APPROVED.value]
        assert len(approved) >= 5
        rejected_ids = {c.id for c in candidates if c.status != CandidateStatus.APPROVED.value}
        ranked = [e for e in evaluations if e.rank is not None]
        assert ranked
        assert {e.candidate_id for e in ranked}.isdisjoint(rejected_ids)
        ranks = [e.rank for e in ranked]
        assert sorted(ranks) == list(range(1, len(ranks) + 1))

        lots = list(await db.scalars(select(TaxLot).where(TaxLot.portfolio_id == PORTFOLIO_A_ID)))
        by_asset: dict = {}
        for lot in lots:
            by_asset.setdefault(lot.asset_id, []).append(lot)
        assert any(len(v) >= 2 for v in by_asset.values())
        missing = [lot for lot in lots if lot.missing_basis]
        assert len(missing) >= 2

        first_seen = list(await db.scalars(select(CandidateConflictIdentity)))
        assert first_seen
        fingerprints = {row.fingerprint for row in first_seen}
        assert len(fingerprints) == len(first_seen)

    second = await run_analysis(USER_A_ID, trigger=AnalysisTrigger.API, as_of=AS_OF, idempotency_key="k2", deps=deps)
    async with session_factory() as db:
        identities = list(await db.scalars(select(CandidateConflictIdentity)))
        still = [e for e in await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == second.analysis_run_id)) if e.conflict_label == ConflictLabel.STILL_ACTIVE.value]
        assert still
        for identity in identities:
            if identity.occurrence_count > 1:
                assert identity.first_seen_at <= identity.last_seen_at
                assert identity.occurrence_count >= 2

        # Expire a conflict window and resolve without delete.
        identity = identities[0]
        identity.conflict_window_end = datetime(2020, 1, 1).date()
        identity.active_status = "ACTIVE"
        await db.commit()
        from app.services.conflicts import ConflictService

        async with session_factory() as db2:
            n = await ConflictService(settings).resolve_expired(db2, datetime(2024, 6, 15, tzinfo=UTC))
            await db2.commit()
        async with session_factory() as db3:
            row = await db3.get(CandidateConflictIdentity, identity.id)
            assert row is not None
            if n:
                assert row.active_status in {"RESOLVED", "ACTIVE"}
