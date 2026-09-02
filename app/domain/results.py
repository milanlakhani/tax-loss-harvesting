from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.enums import (
    AnalysisRunStatus,
    AnalysisTrigger,
    CandidateStatus,
    ConflictLabel,
    MLStatus,
    RejectionCode,
)


@dataclass(frozen=True, slots=True)
class AnalysisRunResult:
    analysis_run_id: UUID
    user_id: UUID
    trigger: AnalysisTrigger
    as_of: datetime
    idempotency_key: str
    status: AnalysisRunStatus
    started_at: datetime
    finished_at: datetime | None
    failure_reason: str | None
    reused: bool
    candidate_ids: tuple[UUID, ...] = ()
    evaluation_ids: tuple[UUID, ...] = ()
    approved_candidate_ids: tuple[UUID, ...] = ()
    ml_status: MLStatus | None = None


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: UUID
    status: CandidateStatus
    rejection_code: RejectionCode | None
    explanation: str
    rule_version: str
    evaluated_at: datetime
    conflict_label: ConflictLabel | None = None
    conflict_fingerprint: str | None = None
    usable_loss: Decimal | None = None
    total_loss: Decimal | None = None
    selected_quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RankedSelection:
    candidate_id: UUID
    rank: int
    selected_quantity: Decimal
    usable_loss: Decimal
    target_before: Decimal
    target_after: Decimal
    tie_breaker_explanation: str
    risk_effect: Decimal
    drift_effect: Decimal
    replacement_canonical_id: str | None
    estimated_cost: Decimal
    quote: Decimal
    provider: str


@dataclass(frozen=True, slots=True)
class MoneyAmount:
    value: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class StatisticalResult:
    value: Decimal | None
    currency: str | None
    date_start: datetime | None
    date_end: datetime | None
    transaction_count: int
    statement_ids: tuple[UUID, ...]
    account_ids: tuple[UUID, ...]
    low_confidence: bool
    warning: str | None = None
    breakdown: dict[str, Decimal] = field(default_factory=dict)
    converted: bool = False
