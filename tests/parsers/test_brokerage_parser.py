from __future__ import annotations

from decimal import Decimal

import pytest

from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.parsers.brokerage import parse_brokerage_pdf


@pytest.mark.parser
def test_brokerage_parser_counts_and_realized_subtotals():
    for spec in (portfolio_a_spec(), portfolio_b_spec()):
        parsed = parse_brokerage_pdf(render_brokerage_pdf(spec))
        assert parsed.is_taxable is True
        assert len(parsed.lots) == 25
        assert len(parsed.sales) == 18
        assert len(parsed.dividends) == 8
        assert len([d for d in parsed.dividends if d.reinvested]) == 4
        assert len([p for p in parsed.purchases if p.is_reinvestment]) == 4
        assert len([p for p in parsed.purchases if not p.is_reinvestment and not p.is_scheduled_crypto]) == 2
        assert len(parsed.holdings) >= 11
        cryptos = [h for h in parsed.holdings if h.asset_type == "CRYPTO"]
        equities = [h for h in parsed.holdings if h.asset_type in {"EQUITY", "ETF"}]
        assert len(cryptos) >= 3
        assert len(equities) >= 8
        assert parsed.realized.st_net == parsed.realized.st_gains + parsed.realized.st_losses
        assert parsed.realized.lt_net == parsed.realized.lt_gains + parsed.realized.lt_losses
        assert parsed.realized.combined_net == parsed.realized.st_net + parsed.realized.lt_net
        lot_ids = [lot.lot_id for lot in parsed.lots]
        assert len(lot_ids) == len(set(lot_ids))
        if spec.statement_id.endswith("-A-2024-06") or "BRK-A" in spec.statement_id:
            assert Decimal("4000") <= parsed.realized.combined_net <= Decimal("6000")
        else:
            assert Decimal("3000") <= parsed.realized.combined_net <= Decimal("5000")
