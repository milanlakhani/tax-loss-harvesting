from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import cos, pi, sin
from statistics import median
from typing import Any
from uuid import UUID

from app.config import Settings
from app.domain.enums import DebitCredit, TransactionType
from app.persistence.models import BankTransaction

FEATURE_NAMES = [
    "abs_base_debit",
    "diff_user_median",
    "diff_category_median",
    "diff_merchant_median",
    "merchant_frequency",
    "category_frequency",
    "day_of_week",
    "time_sin",
    "time_cos",
    "days_since_prev",
    "similar_count_24h",
    "rolling_7d_spend",
    "rolling_30d_spend",
    "rolling_category_spend",
    "amount_to_monthly_income",
    "new_merchant",
    "new_category",
    "new_currency_or_intl",
    "duplicate_proximity",
]

INCOME_CATEGORIES = {"INCOME", "PAYROLL", "INTEREST"}
MISSING_DAYS_SENTINEL = 30.0
MISSING_DUP_SENTINEL = 999.0


@dataclass(slots=True)
class FeatureRow:
    transaction_id: UUID
    values: dict[str, float]
    vector: list[float]


def effective_ts(txn: BankTransaction):
    return txn.event_time or txn.txn_date


def base_amount(txn: BankTransaction) -> Decimal:
    if txn.converted_amount is not None:
        return txn.converted_amount
    return txn.original_amount


def signed_spend(txn: BankTransaction) -> Decimal:
    amount = base_amount(txn)
    if txn.direction == DebitCredit.DEBIT.value:
        return amount
    return Decimal("0")


class FeatureCalculator:
    """Causal, versioned Isolation Forest features. No ground-truth labels."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def compute(self, transactions: list[BankTransaction]) -> list[FeatureRow]:
        ordered = sorted(transactions, key=lambda t: (effective_ts(t), str(t.id)))
        rows: list[FeatureRow] = []
        for index, txn in enumerate(ordered):
            history = ordered[:index]
            values = self._features_for(txn, history)
            vector = [values[name] for name in FEATURE_NAMES]
            rows.append(FeatureRow(transaction_id=txn.id, values=values, vector=vector))
        return rows

    def _features_for(self, txn: BankTransaction, history: list[BankTransaction]) -> dict[str, float]:
        ts = effective_ts(txn)
        lookback_start = ts - timedelta(days=self.settings.median_lookback_days)
        window = [h for h in history if effective_ts(h) >= lookback_start]
        amount = float(base_amount(txn))
        abs_debit = amount if txn.direction == DebitCredit.DEBIT.value else 0.0
        user_median = _median([float(base_amount(h)) for h in window]) or amount or 1.0
        cat_median = _median(
            [float(base_amount(h)) for h in window if h.category == txn.category]
        ) or user_median
        merch_median = _median(
            [float(base_amount(h)) for h in window if h.normalized_merchant == txn.normalized_merchant]
        ) or user_median
        merch_freq = sum(1 for h in history if h.normalized_merchant == txn.normalized_merchant)
        cat_freq = sum(1 for h in history if h.category == txn.category)
        dow = ts.weekday()
        if txn.event_time is not None:
            frac = (txn.event_time.hour * 3600 + txn.event_time.minute * 60 + txn.event_time.second) / 86400.0
            time_sin = sin(2 * pi * frac)
            time_cos = cos(2 * pi * frac)
        else:
            time_sin = 0.0
            time_cos = 0.0
        if history:
            days_since = (ts - effective_ts(history[-1])).total_seconds() / 86400.0
        else:
            days_since = MISSING_DAYS_SENTINEL
        similar_24h = sum(
            1
            for h in history
            if ts - effective_ts(h) <= timedelta(hours=24)
            and h.normalized_merchant == txn.normalized_merchant
            and _close(base_amount(h), base_amount(txn), self.settings.similar_amount_tolerance)
        )
        spend_7 = float(sum((signed_spend(h) for h in history if ts - effective_ts(h) <= timedelta(days=7)), Decimal("0")))
        spend_30 = float(sum((signed_spend(h) for h in history if ts - effective_ts(h) <= timedelta(days=30)), Decimal("0")))
        cat_spend = float(
            sum(
                (
                    signed_spend(h)
                    for h in history
                    if h.category == txn.category and ts - effective_ts(h) <= timedelta(days=30)
                ),
                Decimal("0"),
            )
        )
        monthly_income = _normal_monthly_income(history, ts, self.settings.normal_monthly_income_lookback_months)
        income_ratio = abs_debit / monthly_income if monthly_income else 0.0
        new_merchant = 0.0 if merch_freq else 1.0
        new_category = 0.0 if cat_freq else 1.0
        intl = 1.0 if (txn.country and txn.country not in {"US", "USA", ""}) or txn.original_currency != txn.base_currency else 0.0
        dup = _duplicate_proximity_hours(txn, history, self.settings)
        return {
            "abs_base_debit": abs_debit,
            "diff_user_median": abs_debit - user_median if txn.direction == DebitCredit.DEBIT.value else 0.0,
            "diff_category_median": abs_debit - cat_median if txn.direction == DebitCredit.DEBIT.value else 0.0,
            "diff_merchant_median": abs_debit - merch_median if txn.direction == DebitCredit.DEBIT.value else 0.0,
            "merchant_frequency": float(merch_freq),
            "category_frequency": float(cat_freq),
            "day_of_week": float(dow),
            "time_sin": time_sin,
            "time_cos": time_cos,
            "days_since_prev": days_since,
            "similar_count_24h": float(similar_24h),
            "rolling_7d_spend": spend_7,
            "rolling_30d_spend": spend_30,
            "rolling_category_spend": cat_spend,
            "amount_to_monthly_income": income_ratio,
            "new_merchant": new_merchant,
            "new_category": new_category,
            "new_currency_or_intl": intl,
            "duplicate_proximity": dup,
        }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(median(values))


def _close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    if left == 0 and right == 0:
        return True
    scale = max(abs(left), abs(right))
    return abs(left - right) <= scale * tolerance


def _normal_monthly_income(history: list[BankTransaction], ts, months: int) -> float:
    start = ts - timedelta(days=30 * months)
    buckets: dict[tuple[int, int], Decimal] = {}
    for txn in history:
        if effective_ts(txn) < start:
            continue
        if txn.direction != DebitCredit.CREDIT.value:
            continue
        if txn.category not in INCOME_CATEGORIES and txn.txn_type not in {
            TransactionType.DEPOSIT.value,
            TransactionType.INTEREST.value,
        }:
            continue
        key = (effective_ts(txn).year, effective_ts(txn).month)
        buckets[key] = buckets.get(key, Decimal("0")) + base_amount(txn)
    if not buckets:
        return 0.0
    return float(median([float(v) for v in buckets.values()]))


def _duplicate_proximity_hours(txn: BankTransaction, history: list[BankTransaction], settings: Settings) -> float:
    ts = effective_ts(txn)
    best = None
    for prior in reversed(history):
        if prior.normalized_merchant != txn.normalized_merchant:
            continue
        if not _close(base_amount(prior), base_amount(txn), settings.similar_amount_tolerance):
            continue
        hours = (ts - effective_ts(prior)).total_seconds() / 3600.0
        if hours <= settings.duplicate_proximity_hours:
            best = hours if best is None else min(best, hours)
    if best is None:
        return MISSING_DUP_SENTINEL
    return float(best)


def features_to_json(values: dict[str, float]) -> dict[str, Any]:
    return {k: float(v) for k, v in values.items()}
