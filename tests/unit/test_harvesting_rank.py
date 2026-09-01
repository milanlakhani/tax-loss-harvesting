from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from random import shuffle
from uuid import uuid4

import pytest

from app.services.harvesting import RankInputs, rank_key, select_against_target


def _item(loss: str, acquired: str, lot=None, qty="10", unit_loss="10", mirror="10") -> RankInputs:
    return RankInputs(
        candidate_id=uuid4(),
        lot_id=lot or uuid4(),
        usable_loss=Decimal(loss),
        risk_improvement=Decimal("0.1"),
        drift_improvement=Decimal("0.1"),
        replacement_suitability=Decimal("1"),
        estimated_cost=Decimal("1.00"),
        unnecessary_turnover=Decimal("0"),
        acquisition_date=datetime.fromisoformat(acquired).replace(tzinfo=UTC),
        remaining_quantity=Decimal(qty),
        per_unit_loss=Decimal(unit_loss),
        mirror_qty=Decimal(mirror),
        quote=Decimal("50"),
        provider="fake-alpha-vantage",
        replacement_canonical_id="ETF:SCHB",
        basis=Decimal("100"),
        portfolio_id=uuid4(),
        asset_id=uuid4(),
        canonical_id="ETF:VTI",
        asset_type="ETF",
        acquisition_display=datetime.fromisoformat(acquired).replace(tzinfo=UTC),
    )


@pytest.mark.unit
def test_ranking_stable_after_shuffle():
    items = [
        _item("400", "2020-01-01"),
        _item("300", "2020-01-02"),
        _item("200", "2019-06-01"),
        _item("200", "2018-01-01"),
    ]
    expected = [str(i.lot_id) for i in sorted(items, key=rank_key)]
    mixed = list(items)
    shuffle(mixed)
    assert [str(i.lot_id) for i in sorted(mixed, key=rank_key)] == expected


@pytest.mark.unit
def test_target_reduction_and_partial_lot_exact():
    items = [
        _item("300", "2020-01-01", qty="30", unit_loss="10", mirror="30"),
        _item("80", "2021-01-01", qty="8", unit_loss="10", mirror="8"),
    ]
    selected = select_against_target(items, Decimal("250"), allow_exceed=False)
    assert selected[0][2] == Decimal("250")
    assert selected[0][1] == Decimal("25")
    assert selected[0][1] <= selected[0][0].remaining_quantity
    assert selected[0][1] <= selected[0][0].mirror_qty
    assert len(selected) == 1
    assert selected[0][4] == Decimal("0")


@pytest.mark.unit
def test_selection_respects_mirror_quantity():
    item = _item("500", "2020-01-01", qty="50", unit_loss="10", mirror="3")
    selected = select_against_target([item], Decimal("1000"), allow_exceed=False)
    assert selected[0][1] == Decimal("3")
    assert selected[0][2] == Decimal("30")
