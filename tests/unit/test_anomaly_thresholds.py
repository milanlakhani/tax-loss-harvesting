from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import Settings
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.constants import USER_A_ID, USER_B_ID
from app.persistence.models import AnomalyGroundTruth, BankTransaction
from app.services.anomalies import AnomalyService, evaluation_metrics
from app.services.features import FeatureCalculator


@pytest.mark.unit
def test_insufficient_history_uses_fallback():
    settings = Settings(app_env="test", min_history_threshold=80)
    service = AnomalyService(settings)
    from tests.unit.test_features import _txn
    from datetime import UTC, datetime

    txns = [_txn(txn_date=datetime(2024, 3, i, tzinfo=UTC)) for i in range(1, 10)]
    result = service.score_user(txns)
    assert result.ml_status.value == "INSUFFICIENT_HISTORY"
    assert all(s.model_version == "rule_fallback_v1" for s in result.scores)


@pytest.mark.unit
def test_isolation_forest_normalized_score_direction_and_thresholds():
    statements, labels = build_bank_statements()
    user_rows = [row for spec in statements if spec.user_id == USER_A_ID for row in spec.transactions]
    assert len(user_rows) >= 225
    # Convert specs to ephemeral BankTransaction-like objects via parser ingest path in unit by mapping.
    from datetime import UTC, datetime
    from uuid import uuid4
    from app.domain.enums import DebitCredit
    from app.persistence.models import BankTransaction
    from app.demo_data.bank_generator import _to_usd

    txns = []
    labelled = set()
    label_ids = {txn_id for txn_id, _k, _r in labels}
    user_id = USER_A_ID
    for spec in statements:
        if spec.user_id != user_id:
            continue
        for row in spec.transactions:
            converted = _to_usd(row.original_amount, row.original_currency)
            txn = BankTransaction(
                id=uuid4(),
                external_transaction_id=row.transaction_id,
                statement_id=uuid4(),
                account_id=spec.account_id,
                user_id=spec.user_id,
                txn_date=datetime(row.txn_date.year, row.txn_date.month, row.txn_date.day, tzinfo=UTC),
                event_time=row.event_time,
                description=row.description,
                normalized_merchant=row.merchant,
                category=row.category,
                txn_type=row.txn_type.value,
                original_amount=row.original_amount,
                original_currency=row.original_currency,
                direction=row.direction.value,
                base_currency="USD",
                converted_amount=converted,
                running_balance=row.running_balance,
                parsing_confidence=Decimal("0.99"),
                source_page=1,
                country=row.country,
                is_synthetic=True,
            )
            txns.append(txn)
            if row.transaction_id in label_ids:
                labelled.add(txn.id)
    settings = Settings(app_env="test", min_history_threshold=80, isolation_forest_seed=42, isolation_forest_contamination=0.06)
    result = AnomalyService(settings).score_user(txns)
    assert result.ml_status.value == "FITTED"
    # Higher normalized score is more anomalous.
    ranked = sorted(result.scores, key=lambda s: s.normalized_score)
    assert ranked[0].normalized_score <= ranked[-1].normalized_score
    metrics = evaluation_metrics(result.scores, labelled)
    assert metrics["precision"] >= 0.50
    assert metrics["recall"] >= 0.70
    assert metrics["false_positive_rate"] <= 0.10
    assert metrics["top_k_detection"] >= 0.75
    assert metrics["median_injected_percentile_rank"] >= 0.80
