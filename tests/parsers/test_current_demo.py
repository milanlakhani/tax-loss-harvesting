from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.demo_data.constants import (
    CRYPTO_SCHEDULED_BUY_OFFSET_DAYS,
    DEFAULT_DEMO_AS_OF_DATE,
    WASH_EQUITY_REINVEST_OFFSET_DAYS,
)
from app.demo_data.generate import write_statement_pdfs
from app.parsers.bank import parse_bank_pdf
from app.parsers.brokerage import parse_brokerage_pdf


@pytest.mark.parser
def test_historical_2024_fixtures_are_unchanged():
    bank, _ = build_bank_statements()
    assert all(spec.period_start.year == 2024 for spec in bank)
    assert {spec.statement_id for spec in bank} == {
        "BANK-0-2024-02-01",
        "BANK-0-2024-03-01",
        "BANK-0-2024-04-01",
        "BANK-1-2024-03-01",
        "BANK-1-2024-04-01",
        "BANK-1-2024-05-01",
    }
    a = portfolio_a_spec()
    b = portfolio_b_spec()
    assert a.statement_id == "BRK-A-2024-06"
    assert b.statement_id == "BRK-B-2024-06"
    assert a.period_end == date(2024, 6, 15)
    parsed = parse_brokerage_pdf(render_brokerage_pdf(a))
    assert parsed.period_end.date() == date(2024, 6, 15)
    spy_div = next(d for d in parsed.dividends if d.symbol == "SPY")
    assert spy_div.event_date.date() == date(2024, 6, 5)
    vti = next(lot for lot in parsed.lots if lot.lot_id == "A-VTI-APPROVED")
    assert vti.per_unit_basis == Decimal("250.00")


@pytest.mark.parser
def test_current_demo_bank_history_balances_and_min_threshold():
    as_of = DEFAULT_DEMO_AS_OF_DATE
    statements, labels = build_bank_statements(as_of=as_of, min_history=80)
    assert statements
    assert all(spec.period_end <= as_of for spec in statements)
    assert all(spec.period_start.year == 2026 for spec in statements)
    by_user: dict = {}
    for spec in statements:
        parsed = parse_bank_pdf(render_bank_pdf(spec))
        assert parsed.period_start.date() == spec.period_start
        assert parsed.period_end.date() == spec.period_end
        assert parsed.closing_balance == spec.closing_balance
        assert parsed.transactions[-1].running_balance == spec.closing_balance
        by_user.setdefault(spec.user_id, 0)
        by_user[spec.user_id] += len(parsed.transactions)
        for row in parsed.transactions:
            assert row.txn_date.date() <= as_of
    assert all(count >= 80 for count in by_user.values())
    assert len(labels) >= 9


@pytest.mark.parser
def test_current_demo_brokerage_offsets_reinvest_and_realized():
    as_of = DEFAULT_DEMO_AS_OF_DATE
    wash_day = as_of + timedelta(days=WASH_EQUITY_REINVEST_OFFSET_DAYS)
    crypto_day = as_of + timedelta(days=CRYPTO_SCHEDULED_BUY_OFFSET_DAYS)
    for spec, wash_symbol in ((portfolio_a_spec(as_of=as_of), "SPY"), (portfolio_b_spec(as_of=as_of), "VNQ")):
        assert spec.period_end == as_of
        assert spec.statement_id.endswith(as_of.isoformat())
        assert spec.statement_id != "BRK-A-2024-06"
        parsed = parse_brokerage_pdf(render_brokerage_pdf(spec))
        assert parsed.period_end.date() == as_of
        assert parsed.realized.st_net == parsed.realized.st_gains + parsed.realized.st_losses
        assert parsed.realized.combined_net == parsed.realized.st_net + parsed.realized.lt_net
        reinvests = [p for p in parsed.purchases if p.is_reinvestment]
        reinvested_divs = [d for d in parsed.dividends if d.reinvested]
        by_symbol_purchase = {p.symbol: p.event_date.date() for p in reinvests}
        for div in reinvested_divs:
            assert div.event_date.date() == by_symbol_purchase[div.symbol]
        wash_buys = [p for p in parsed.purchases if p.symbol == wash_symbol and p.is_reinvestment]
        assert wash_buys
        assert all(p.event_date.date() == wash_day for p in wash_buys)
        scheduled = [p for p in parsed.purchases if p.is_scheduled_crypto]
        assert scheduled
        assert all(p.event_date.date() == crypto_day for p in scheduled)
        assert all(as_of - timedelta(days=30) <= p.event_date.date() <= as_of + timedelta(days=30) for p in wash_buys + scheduled)
        if wash_symbol == "SPY":
            vti = next(lot for lot in spec.lots if lot.lot_id == "A-VTI-APPROVED")
            assert vti.per_unit_basis == Decimal("450.00")
            spy_profit = next(lot for lot in spec.lots if lot.lot_id == "A-SPY-PROFIT")
            assert spy_profit.per_unit_basis == Decimal("420.00")


@pytest.mark.parser
def test_write_current_demo_pdfs_does_not_reuse_2024_filenames(tmp_path, settings, monkeypatch):
    from app.config import override_settings

    override_settings(settings)
    written = write_statement_pdfs(mode="current", as_of_date=DEFAULT_DEMO_AS_OF_DATE, dest=tmp_path / "current-demo")
    names = {path.name for path in written}
    assert "BRK-A-2024-06.pdf" not in names
    assert "BANK-0-2024-02-01.pdf" not in names
    assert any(name.startswith("BRK-A-2026-08") for name in names)


@pytest.mark.parser
def test_current_demo_brokerage_identity_changes_each_day():
    first = portfolio_a_spec(as_of=date(2026, 8, 29))
    refreshed = portfolio_a_spec(as_of=date(2026, 8, 31))

    assert first.statement_id == "BRK-A-2026-08-29"
    assert refreshed.statement_id == "BRK-A-2026-08-31"
    assert first.statement_id != refreshed.statement_id
