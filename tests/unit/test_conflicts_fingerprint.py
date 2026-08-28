from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.enums import RejectionCode
from app.services.conflicts import canonical_conflict_payload, fingerprint_for


@pytest.mark.unit
def test_fingerprint_ignores_run_id_and_clock():
    base = dict(
        user_id=uuid4(),
        portfolio_id=uuid4(),
        tax_lot_id=uuid4(),
        canonical_asset_id="ETF:SPY",
        rejection_code=RejectionCode.WASH_SALE_CONFLICT,
        rule_version="harvest_gates_v1",
        replacement_canonical_id=None,
        conflicting_ids=["A-BUY-SPY-REINV"],
        window_start="2024-05-16",
        window_end="2024-07-15",
    )
    p1 = canonical_conflict_payload(**base)
    p2 = canonical_conflict_payload(**base)
    assert fingerprint_for(p1, "conflict_fp_v1") == fingerprint_for(p2, "conflict_fp_v1")
    p1["conflicting_ids"] = ["Z", "A-BUY-SPY-REINV"]
    # canonical form sorts IDs
    p3 = canonical_conflict_payload(**{**base, "conflicting_ids": ["Z", "A-BUY-SPY-REINV"]})
    p4 = canonical_conflict_payload(**{**base, "conflicting_ids": ["A-BUY-SPY-REINV", "Z"]})
    assert fingerprint_for(p3, "conflict_fp_v1") == fingerprint_for(p4, "conflict_fp_v1")
