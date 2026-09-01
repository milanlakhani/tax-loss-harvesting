from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.demo_data.constants import AS_OF, USER_A_ID
from app.domain.enums import AnalysisTrigger, CandidateStatus
from app.domain.errors import PaperExecutionError
from app.persistence.models import Asset, ExecutionPreparation, HarvestingCandidate, TaxLot
from app.providers.fakes import RecordingClock
from app.providers.protocols import ExecutionPosition
from app.services.analysis import run_analysis
from app.services.demo_session import DemoSessionService
from app.services.paper_execution import PaperExecutionService
from tests.helpers import analysis_deps, seed_historical_demo


async def _candidate(session, run_id, external_lot_id: str) -> HarvestingCandidate:
    lot = await session.scalar(select(TaxLot).where(TaxLot.external_lot_id == external_lot_id))
    candidate = await session.scalar(
        select(HarvestingCandidate).where(
            HarvestingCandidate.analysis_run_id == run_id,
            HarvestingCandidate.tax_lot_id == lot.id,
        )
    )
    assert candidate is not None
    return candidate


@pytest.mark.integration
async def test_prepare_confirm_and_guardrails(session, session_factory, settings):
    providers = await seed_historical_demo(session, settings)
    deps = analysis_deps(settings, session_factory, providers)
    result = await run_analysis(USER_A_ID, trigger=AnalysisTrigger.MANUAL, as_of=AS_OF, idempotency_key="paper-1", deps=deps)
    enabled = settings.model_copy(update={"enable_paper_orders": True})
    clock = RecordingClock(AS_OF)
    live = PaperExecutionService(enabled, session_factory, providers, clock)
    blocked = PaperExecutionService(settings, session_factory, providers, clock)
    demo_token = await DemoSessionService(settings, session_factory).create(USER_A_ID)

    async with session_factory() as db:
        equity = await _candidate(db, result.analysis_run_id, "A-VTI-APPROVED")
        crypto = await _candidate(db, result.analysis_run_id, "A-BTC-APPROVED")
        eth = await _candidate(db, result.analysis_run_id, "A-ETH-APPROVED")
        qqq = await _candidate(db, result.analysis_run_id, "A-QQQ-APPROVED")
        vxus = await _candidate(db, result.analysis_run_id, "A-VXUS-APPROVED")
        rejected = (
            await db.scalars(
                select(HarvestingCandidate).where(
                    HarvestingCandidate.analysis_run_id == result.analysis_run_id,
                    HarvestingCandidate.status != CandidateStatus.APPROVED.value,
                )
            )
        ).first()

    prepared_eq = await live.prepare(candidate_id=equity.id, demo_session_token=demo_token)
    prepared_cr = await live.prepare(candidate_id=crypto.id, demo_session_token=demo_token)
    assert prepared_eq["side"] == "SELL" and prepared_eq["asset_type"] == "ETF"
    assert prepared_cr["coingecko_id"] == "bitcoin"
    assert "SIMULATED PAPER TRADE" in prepared_eq["environment"]

    with pytest.raises(PaperExecutionError) as disabled:
        await blocked.confirm(candidate_id=equity.id, token=prepared_eq["token"], demo_session_token=demo_token)
    assert disabled.value.code == "PAPER_ORDERS_DISABLED"
    assert providers.execution.submit_calls == []

    confirmed_eq = await live.confirm(candidate_id=equity.id, token=prepared_eq["token"], demo_session_token=demo_token)
    confirmed_cr = await live.confirm(candidate_id=crypto.id, token=prepared_cr["token"], demo_session_token=demo_token)
    assert len(providers.execution.submit_calls) == 2
    assert {c["symbol"] for c in providers.execution.submit_calls} == {"VTI", "BTC/USD"}
    assert {c["asset_class"] for c in providers.execution.submit_calls} == {"us_equity", "crypto"}

    with pytest.raises(PaperExecutionError) as reused:
        await live.confirm(candidate_id=equity.id, token=prepared_eq["token"], demo_session_token=demo_token)
    assert reused.value.code == "TOKEN_REUSED"

    with pytest.raises(PaperExecutionError) as not_approved:
        await live.prepare(candidate_id=rejected.id, demo_session_token=demo_token)
    assert not_approved.value.code == "NOT_APPROVED"

    with pytest.raises(PaperExecutionError) as bad_token:
        await live.confirm(candidate_id=qqq.id, token="zzzzzzzz", demo_session_token=demo_token)
    assert bad_token.value.code == "INVALID_TOKEN"

    providers.execution.seed_fill(
        confirmed_eq["provider_order_id"],
        filled_qty=Decimal(prepared_eq["quantity"]),
        fill_price=Decimal("199.50"),
        status="FILLED",
    )
    refreshed = await live.refresh(order_id=confirmed_eq["order_id"])
    assert refreshed["fill_price"] == "199.50"
    assert refreshed["reference_price"] == prepared_eq["reference_price"]
    assert refreshed["fill_price"] != refreshed["reference_price"]
    assert refreshed["quote_provider"] == prepared_eq["quote_provider"]
    assert "fill_price" not in prepared_eq
    assert prepared_eq["reference_price"] != refreshed["fill_price"]

    providers.execution.seed_asset_class("ETH/USD", "us_equity")
    with pytest.raises(PaperExecutionError) as mismatch:
        await live.prepare(candidate_id=eth.id, demo_session_token=demo_token)
    assert mismatch.value.code == "ASSET_CLASS_MISMATCH"

    prepared_q = await live.prepare(candidate_id=qqq.id, demo_session_token=demo_token)
    async with session_factory() as db:
        prep = await db.scalar(select(ExecutionPreparation).where(ExecutionPreparation.candidate_id == qqq.id))
        mutated = dict(prep.snapshot)
        mutated["quantity"] = "1"
        mutated["side"] = "BUY"
        prep.snapshot = mutated
        await db.commit()
    with pytest.raises(PaperExecutionError) as modified:
        await live.confirm(candidate_id=qqq.id, token=prepared_q["token"], demo_session_token=demo_token)
    assert modified.value.code in {"SNAPSHOT_MODIFIED", "BUY_NOT_ALLOWED"}

    prepared_v = await live.prepare(candidate_id=vxus.id, demo_session_token=demo_token)
    providers.execution.seed_position(
        ExecutionPosition(
            account_alias="conservative-demo",
            symbol="VXUS",
            quantity=Decimal("0"),
            tradable=True,
            asset_class="ETF",
        )
    )
    with pytest.raises(PaperExecutionError) as qty:
        await live.confirm(candidate_id=vxus.id, token=prepared_v["token"], demo_session_token=demo_token)
    assert qty.value.code in {"INSUFFICIENT_QUANTITY", "INSUFFICIENT_MIRROR_QUANTITY", "NOT_APPROVED"}

    async with session_factory() as db:
        lot = await db.scalar(select(TaxLot).where(TaxLot.external_lot_id == "A-ETH-APPROVED"))
        asset = await db.get(Asset, lot.asset_id)
        asset.asset_type = "FX"
        await db.commit()
    with pytest.raises(PaperExecutionError) as fx:
        await live.prepare(candidate_id=eth.id, demo_session_token=demo_token)
    assert fx.value.code in {"INELIGIBLE_ASSET_TYPE", "NOT_APPROVED", "ASSET_CLASS_MISMATCH"}

    from app.services import paper_execution as module

    snap = {**prepared_cr, "coingecko_id": None, "side": "SELL", "quote_stale": False}
    async with session_factory() as db:
        lot = await db.scalar(select(TaxLot).where(TaxLot.external_lot_id == "A-BTC-APPROVED"))
        asset = await db.get(Asset, lot.asset_id)
        from app.persistence.models import PortfolioAccount

        account = await db.get(PortfolioAccount, lot.portfolio_id)
        original = module.coingecko_id_for
        module.coingecko_id_for = lambda *a, **k: None
        try:
            with pytest.raises(PaperExecutionError) as mapping:
                await live._validate_snapshot(snap, lot, account, asset, AS_OF)
            assert mapping.value.code == "MISSING_COINGECKO_MAPPING"
        finally:
            module.coingecko_id_for = original


@pytest.mark.integration
async def test_unavailable_quote_does_not_bypass_evaluation(session, session_factory, settings):
    providers = await seed_historical_demo(session, settings)
    providers.equity.quotes.pop("ETF:VTI", None)
    deps = analysis_deps(settings, session_factory, providers)
    result = await run_analysis(USER_A_ID, trigger=AnalysisTrigger.MANUAL, as_of=AS_OF, idempotency_key="no-quote", deps=deps)
    async with session_factory() as db:
        vti = await _candidate(db, result.analysis_run_id, "A-VTI-APPROVED")
        assert vti.status != CandidateStatus.APPROVED.value
