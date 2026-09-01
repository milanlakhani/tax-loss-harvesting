from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.adapters.storage import LocalStatementStorage
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.generate import build_fake_providers, generate
from app.demo_data.constants import USER_A_ID, USER_B_ID
from app.persistence.models import (
    AnomalyGroundTruth,
    BankTransaction,
    BrokerageDividend,
    BrokeragePurchase,
    BrokerageSale,
    Statement,
    TaxLot,
    User,
)
from app.services.ingestion import StatementIngestor


@pytest.mark.integration
async def test_generate_counts_and_user_isolation(session, settings, session_factory):
    from app.persistence.database import reset_engine
    from app.config import override_settings

    override_settings(settings)
    reset_engine()
    # Point the global session factory at the test engine by using generate against this session directly.
    storage = LocalStatementStorage(settings.local_data_dir)
    providers = build_fake_providers()
    ingestor = StatementIngestor(storage, providers.fx)
    statements, labels = build_bank_statements()
    from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
    from app.demo_data.brokerage_pdf import render_brokerage_pdf
    from app.demo_data.generate import seed_labels, seed_mirrors, seed_replacements, seed_risk_and_targets, seed_users

    await seed_users(session)
    await seed_replacements(session)
    await seed_risk_and_targets(session)
    await seed_mirrors(session, providers)
    for spec in statements:
        await ingestor.ingest(session, render_bank_pdf(spec), f"{spec.statement_id}.pdf")
    for spec in (portfolio_a_spec(), portfolio_b_spec()):
        await ingestor.ingest(session, render_brokerage_pdf(spec), f"{spec.statement_id}.pdf")
    await seed_labels(session, labels)
    await session.flush()

    users = list(await session.scalars(select(User)))
    assert len(users) == 2
    bank = list(await session.scalars(select(Statement).where(Statement.format == "SYNTHETIC_BANK_V1")))
    brk = list(await session.scalars(select(Statement).where(Statement.format == "SYNTHETIC_BROKERAGE_V1")))
    assert len(bank) == 6
    assert len(brk) == 2
    for user_id in (USER_A_ID, USER_B_ID):
        txns = list(await session.scalars(select(BankTransaction).where(BankTransaction.user_id == user_id)))
        assert len(txns) >= 225
        assert len([t for t in txns if t.original_currency == "GBP"]) >= 12
        assert len([t for t in txns if t.original_currency == "EUR"]) >= 12
        assert len([t for t in txns if t.txn_type == "REFUND"]) >= 4
        merchants = {t.normalized_merchant for t in txns}
        cats = {t.category for t in txns}
        assert len(merchants) >= 6
        assert len(cats) >= 8
        fees = [t for t in txns if t.category in {"FEE", "INTEREST"}]
        assert len(fees) >= 3
        gts = list(await session.scalars(select(AnomalyGroundTruth).where(AnomalyGroundTruth.user_id == user_id)))
        assert len(gts) >= 9
        other = list(await session.scalars(select(BankTransaction).where(BankTransaction.user_id != user_id)))
        assert {t.id for t in txns}.isdisjoint({t.id for t in other})

    lots_a = list(await session.scalars(select(TaxLot).where(TaxLot.portfolio_id == __import__("app.demo_data.constants", fromlist=["PORTFOLIO_A_ID"]).PORTFOLIO_A_ID)))
    lots_b = list(await session.scalars(select(TaxLot).where(TaxLot.portfolio_id == __import__("app.demo_data.constants", fromlist=["PORTFOLIO_B_ID"]).PORTFOLIO_B_ID)))
    assert len(lots_a) == 25
    assert len(lots_b) == 25
    sales_a = list(await session.scalars(select(BrokerageSale).where(BrokerageSale.portfolio_id == lots_a[0].portfolio_id)))
    assert len(sales_a) == 18
    st = sum((s.realized_result for s in sales_a if s.holding_period == "SHORT_TERM"), Decimal("0"))
    lt = sum((s.realized_result for s in sales_a if s.holding_period == "LONG_TERM"), Decimal("0"))
    assert st + lt == sum((s.realized_result for s in sales_a), Decimal("0"))
    dividends = list(await session.scalars(select(BrokerageDividend)))
    purchases = list(await session.scalars(select(BrokeragePurchase)))
    assert len(dividends) == 16
    assert len([d for d in dividends if d.reinvested]) == 8


@pytest.mark.integration
async def test_ingest_is_idempotent_and_rolls_back_on_parse_error(session, settings):
    storage = LocalStatementStorage(settings.local_data_dir)
    providers = build_fake_providers()
    ingestor = StatementIngestor(storage, providers.fx)
    from app.demo_data.generate import seed_users

    await seed_users(session)
    spec = build_bank_statements()[0][0]
    pdf = render_bank_pdf(spec)
    first = await ingestor.ingest(session, pdf, f"{spec.statement_id}.pdf")
    second = await ingestor.ingest(session, pdf, f"{spec.statement_id}.pdf")
    assert first.reused is False
    assert second.reused is True
    assert first.statement_id == second.statement_id
    before = list(await session.scalars(select(BankTransaction)))
    with pytest.raises(Exception):
        await ingestor.ingest(session, b"not-a-pdf", "bad.pdf")
    after = list(await session.scalars(select(BankTransaction)))
    assert len(before) == len(after)
