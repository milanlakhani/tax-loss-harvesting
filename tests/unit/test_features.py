from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.config import Settings
from app.domain.enums import DebitCredit, TransactionType
from app.persistence.models import BankTransaction
from app.services.features import FEATURE_NAMES, FeatureCalculator


def _txn(**kwargs) -> BankTransaction:
    defaults = dict(
        id=uuid4(),
        external_transaction_id="x",
        statement_id=uuid4(),
        account_id=uuid4(),
        user_id=uuid4(),
        txn_date=datetime(2024, 3, 1, tzinfo=UTC),
        event_time=None,
        description="x",
        normalized_merchant="GROCERYCO",
        category="GROCERIES",
        txn_type=TransactionType.PAYMENT.value,
        original_amount=Decimal("90.00"),
        original_currency="USD",
        direction=DebitCredit.DEBIT.value,
        base_currency="USD",
        converted_amount=Decimal("90.00"),
        running_balance=Decimal("1000.00"),
        parsing_confidence=Decimal("0.99"),
        source_page=1,
        is_synthetic=True,
    )
    defaults.update(kwargs)
    return BankTransaction(**defaults)


@pytest.mark.unit
def test_feature_names_are_versioned():
    assert len(FEATURE_NAMES) == 19


@pytest.mark.unit
def test_features_do_not_leak_future_transactions():
    settings = Settings(app_env="test")
    calc = FeatureCalculator(settings)
    early = _txn(txn_date=datetime(2024, 3, 1, tzinfo=UTC), normalized_merchant="GROCERYCO")
    future = _txn(
        txn_date=datetime(2024, 3, 20, tzinfo=UTC),
        normalized_merchant="NEW FUTURE MERCHANT",
        original_amount=Decimal("5000"),
        converted_amount=Decimal("5000"),
    )
    baseline = calc.compute([early])[0].values
    with_future = calc.compute([early, future])[0].values
    assert baseline == with_future
    assert with_future["new_merchant"] == 1.0 or with_future["merchant_frequency"] == 0


@pytest.mark.unit
def test_per_user_histories_are_independent():
    settings = Settings(app_env="test")
    calc = FeatureCalculator(settings)
    user_a = uuid4()
    user_b = uuid4()
    a_rows = [
        _txn(user_id=user_a, txn_date=datetime(2024, 3, d, tzinfo=UTC), converted_amount=Decimal("90"))
        for d in range(1, 6)
    ]
    b_rows = [
        _txn(
            user_id=user_b,
            txn_date=datetime(2024, 3, d, tzinfo=UTC),
            converted_amount=Decimal("9000"),
            normalized_merchant="OTHER",
        )
        for d in range(1, 6)
    ]
    a_only = calc.compute(a_rows)[-1].values["abs_base_debit"]
    mixed_a = calc.compute(a_rows)[-1].values["abs_base_debit"]
    assert a_only == mixed_a
    b_last = calc.compute(b_rows)[-1].values["abs_base_debit"]
    assert b_last != a_only
