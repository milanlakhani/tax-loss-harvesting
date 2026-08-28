from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.domain.enums import ConflictActiveStatus, ConflictLabel, PERSISTABLE_CONFLICT_CODES, RejectionCode
from app.persistence.models import CandidateConflictIdentity


def canonical_conflict_payload(
    *,
    user_id: UUID,
    portfolio_id: UUID,
    tax_lot_id: UUID,
    canonical_asset_id: str,
    rejection_code: RejectionCode,
    rule_version: str,
    replacement_canonical_id: str | None,
    conflicting_ids: list[str],
    window_start: str | None,
    window_end: str | None,
    extra_inputs: dict[str, str] | None = None,
) -> dict:
    payload = {
        "user_id": str(user_id),
        "portfolio_id": str(portfolio_id),
        "tax_lot_id": str(tax_lot_id),
        "canonical_asset_id": canonical_asset_id,
        "rejection_code": rejection_code.value,
        "rule_version": rule_version,
        "replacement_canonical_id": replacement_canonical_id,
        "conflicting_ids": sorted(conflicting_ids),
        "conflict_window_start": window_start,
        "conflict_window_end": window_end,
        "extra_inputs": extra_inputs or {},
    }
    return payload


def fingerprint_for(payload: dict, version: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{version}|{canonical}".encode("utf-8")).hexdigest()


class ConflictService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def upsert(
        self,
        session: AsyncSession,
        *,
        payload: dict,
        now: datetime,
        candidate_id: UUID,
        evaluation_id: UUID,
    ) -> tuple[CandidateConflictIdentity, ConflictLabel]:
        fp = fingerprint_for(payload, self.settings.conflict_fingerprint_version)
        existing = await session.scalar(
            select(CandidateConflictIdentity).where(CandidateConflictIdentity.fingerprint == fp).with_for_update()
        )
        if existing is None:
            row = CandidateConflictIdentity(
                id=uuid4(),
                fingerprint=fp,
                user_id=UUID(payload["user_id"]),
                portfolio_id=UUID(payload["portfolio_id"]),
                tax_lot_id=UUID(payload["tax_lot_id"]),
                canonical_asset_id=payload["canonical_asset_id"],
                rejection_code=payload["rejection_code"],
                rule_version=payload["rule_version"],
                replacement_canonical_id=payload.get("replacement_canonical_id"),
                conflict_window_start=payload.get("conflict_window_start"),
                conflict_window_end=payload.get("conflict_window_end"),
                conflicting_ids={"ids": payload.get("conflicting_ids", [])},
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
                active_status=ConflictActiveStatus.ACTIVE.value,
                latest_candidate_id=candidate_id,
                latest_evaluation_id=evaluation_id,
                canonical_payload=payload,
            )
            session.add(row)
            await session.flush()
            return row, ConflictLabel.NEW
        existing.last_seen_at = now
        existing.occurrence_count += 1
        existing.latest_candidate_id = candidate_id
        existing.latest_evaluation_id = evaluation_id
        if existing.active_status != ConflictActiveStatus.ACTIVE.value:
            existing.active_status = ConflictActiveStatus.ACTIVE.value
            existing.resolved_at = None
        await session.flush()
        return existing, ConflictLabel.STILL_ACTIVE

    async def resolve_expired(self, session: AsyncSession, now: datetime) -> int:
        rows = list(
            await session.scalars(
                select(CandidateConflictIdentity).where(
                    CandidateConflictIdentity.active_status == ConflictActiveStatus.ACTIVE.value
                )
            )
        )
        resolved = 0
        today = now.date()
        for row in rows:
            end = row.conflict_window_end
            if end is not None and end < today:
                row.active_status = ConflictActiveStatus.RESOLVED.value
                row.resolved_at = now
                resolved += 1
        await session.flush()
        return resolved

    async def supersede(
        self,
        session: AsyncSession,
        identity: CandidateConflictIdentity,
        successor: CandidateConflictIdentity,
        now: datetime,
    ) -> None:
        identity.active_status = ConflictActiveStatus.SUPERSEDED.value
        identity.resolved_at = now
        identity.superseded_by_id = successor.id
        await session.flush()


def persistable(code: RejectionCode) -> bool:
    return code in PERSISTABLE_CONFLICT_CODES
