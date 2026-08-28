from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from app.config import Settings
from app.domain.enums import DebitCredit, MLStatus
from app.persistence.models import BankTransaction
from app.services.features import FEATURE_NAMES, FeatureCalculator, FeatureRow, base_amount

FALLBACK_VERSION = "rule_fallback_v1"


@dataclass(slots=True)
class ScoredTransaction:
    transaction_id: UUID
    raw_decision_score: float
    normalized_score: float
    is_flagged: bool
    features: dict[str, float]
    ml_status: MLStatus
    model_version: str
    feature_set_version: str


@dataclass(slots=True)
class AnomalyRunResult:
    ml_status: MLStatus
    scores: list[ScoredTransaction]


class AnomalyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.features = FeatureCalculator(settings)

    def score_user(self, transactions: list[BankTransaction]) -> AnomalyRunResult:
        feature_rows = self.features.compute(transactions)
        if len(transactions) < self.settings.min_history_threshold:
            scores = self._fallback(transactions, feature_rows)
            return AnomalyRunResult(ml_status=MLStatus.INSUFFICIENT_HISTORY, scores=scores)
        scores = self._fit_isolation_forest(transactions, feature_rows)
        return AnomalyRunResult(ml_status=MLStatus.FITTED, scores=scores)

    def _fit_isolation_forest(
        self,
        transactions: list[BankTransaction],
        feature_rows: list[FeatureRow],
    ) -> list[ScoredTransaction]:
        matrix = np.array([row.vector for row in feature_rows], dtype=float)
        scaler = StandardScaler()
        scaled = scaler.fit_transform(matrix)
        model = IsolationForest(
            n_estimators=self.settings.isolation_forest_n_estimators,
            contamination=self.settings.isolation_forest_contamination,
            random_state=self.settings.isolation_forest_seed,
            bootstrap=False,
        )
        model.fit(scaled)
        raw = model.decision_function(scaled)
        labels = model.predict(scaled)
        inverted = -raw
        vmin = float(np.min(inverted))
        vmax = float(np.max(inverted))
        span = vmax - vmin if vmax != vmin else 1.0
        out: list[ScoredTransaction] = []
        for row, decision, inv, label in zip(feature_rows, raw, inverted, labels, strict=True):
            normalized = float((inv - vmin) / span)
            out.append(
                ScoredTransaction(
                    transaction_id=row.transaction_id,
                    raw_decision_score=float(decision),
                    normalized_score=normalized,
                    is_flagged=bool(label == -1),
                    features=row.values,
                    ml_status=MLStatus.FITTED,
                    model_version=self.settings.model_version,
                    feature_set_version=self.settings.feature_set_version,
                )
            )
        return out

    def _fallback(
        self,
        transactions: list[BankTransaction],
        feature_rows: list[FeatureRow],
    ) -> list[ScoredTransaction]:
        by_id = {t.id: t for t in transactions}
        scores: list[ScoredTransaction] = []
        for row in feature_rows:
            txn = by_id[row.transaction_id]
            score, flagged = rule_based_score(txn, row.values)
            scores.append(
                ScoredTransaction(
                    transaction_id=row.transaction_id,
                    raw_decision_score=float(-score),
                    normalized_score=score,
                    is_flagged=flagged,
                    features=row.values,
                    ml_status=MLStatus.INSUFFICIENT_HISTORY,
                    model_version=FALLBACK_VERSION,
                    feature_set_version=self.settings.feature_set_version,
                )
            )
        if scores:
            vmax = max(s.normalized_score for s in scores) or 1.0
            scores = [
                ScoredTransaction(
                    transaction_id=s.transaction_id,
                    raw_decision_score=s.raw_decision_score,
                    normalized_score=s.normalized_score / vmax,
                    is_flagged=s.is_flagged,
                    features=s.features,
                    ml_status=s.ml_status,
                    model_version=s.model_version,
                    feature_set_version=s.feature_set_version,
                )
                for s in scores
            ]
        return scores


def rule_based_score(txn: BankTransaction, features: dict[str, float]) -> tuple[float, bool]:
    """Deterministic fallback. Higher score is more anomalous."""
    score = 0.0
    flagged = False
    if txn.direction == DebitCredit.DEBIT.value:
        if features["diff_user_median"] > 0 and features["abs_base_debit"] >= 4 * max(
            features["abs_base_debit"] - features["diff_user_median"], 1.0
        ):
            score += 3.0
            flagged = True
        if features["new_merchant"] >= 1.0 and features["abs_base_debit"] >= 400:
            score += 2.0
            flagged = True
        if features["duplicate_proximity"] <= 2.0:
            score += 2.5
            flagged = True
        if features["new_currency_or_intl"] >= 1.0 and features["abs_base_debit"] >= 250:
            score += 1.5
            flagged = True
        if features["similar_count_24h"] >= 3:
            score += 2.0
            flagged = True
        if features["amount_to_monthly_income"] >= 0.8:
            score += 2.0
            flagged = True
        if features["new_category"] >= 1.0 and features["abs_base_debit"] >= 300:
            score += 1.0
            flagged = True
    return score, flagged


def evaluation_metrics(
    scores: list[ScoredTransaction],
    labelled_ids: set[UUID],
) -> dict[str, float]:
    flagged_ids = {s.transaction_id for s in scores if s.is_flagged}
    tp = len(labelled_ids & flagged_ids)
    fp = len(flagged_ids - labelled_ids)
    fn = len(labelled_ids - flagged_ids)
    tn = len({s.transaction_id for s in scores} - labelled_ids - flagged_ids)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    k = max(2 * len(labelled_ids), 1)
    ranked = sorted(scores, key=lambda s: (-s.normalized_score, str(s.transaction_id)))
    top_ids = {s.transaction_id for s in ranked[:k]}
    top_k = len(labelled_ids & top_ids) / len(labelled_ids) if labelled_ids else 0.0
    percentiles: list[float] = []
    n = len(scores)
    for label_id in labelled_ids:
        target = next(s for s in scores if s.transaction_id == label_id)
        below = sum(1 for s in scores if s.normalized_score < target.normalized_score)
        percentiles.append(below / (n - 1) if n > 1 else 1.0)
    median_pct = float(np.median(percentiles)) if percentiles else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "false_positive_rate": fpr,
        "top_k_detection": top_k,
        "median_injected_percentile_rank": median_pct,
        "k": float(k),
        "true_positives": float(tp),
        "false_positives": float(fp),
        "false_negatives": float(fn),
        "true_negatives": float(tn),
    }
