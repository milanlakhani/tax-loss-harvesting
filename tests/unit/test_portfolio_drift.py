from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.portfolio import drift_from_targets, portfolio_weights


@pytest.mark.unit
def test_drift_arithmetic_exact():
    values = {"ETF:VTI": Decimal("60"), "ETF:BND": Decimal("40")}
    weights = portfolio_weights(values)
    assert weights["ETF:VTI"] == Decimal("0.6")
    assert weights["ETF:BND"] == Decimal("0.4")
    drifts = drift_from_targets(values, {"ETF:VTI": Decimal("0.5"), "ETF:BND": Decimal("0.5")})
    by_id = {d.canonical_id: d.drift for d in drifts}
    assert by_id["ETF:VTI"] == Decimal("0.1")
    assert by_id["ETF:BND"] == Decimal("-0.1")
