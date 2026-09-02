from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.rolling_window import WindowMeta, WindowRecord, window_meta_key
from app.persistence.models import RollingWindowRecord


class PostgresRollingWindowStore:
    """Local APP_ENV=local store. Survives process restarts via PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_meta(self, window_key: str) -> WindowMeta | None:
        logical = window_meta_key(window_key)
        async with self._session_factory() as session:
            row = await session.scalar(
                select(RollingWindowRecord).where(
                    RollingWindowRecord.logical_key == logical,
                    RollingWindowRecord.sort_key == "META",
                )
            )
            if row is None:
                return None
            last = row.payload.get("last_successful_at")
            last_dt = datetime.fromisoformat(last) if last else None
            if last_dt and last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            return WindowMeta(window_key=window_key, last_successful_at=last_dt, extra=row.payload)

    async def put_observation(
        self,
        logical_key: str,
        sort_key: str,
        payload: dict[str, Any],
        *,
        provider: str | None,
        source_timestamp: datetime | None,
        retrieved_at: datetime,
    ) -> None:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(RollingWindowRecord).where(
                    RollingWindowRecord.logical_key == logical_key,
                    RollingWindowRecord.sort_key == sort_key,
                )
            )
            if existing is None:
                session.add(
                    RollingWindowRecord(
                        id=uuid4(),
                        logical_key=logical_key,
                        sort_key=sort_key,
                        payload=payload,
                        provider=provider,
                        source_timestamp=source_timestamp,
                        retrieved_at=retrieved_at,
                        is_synthetic=True,
                    )
                )
            else:
                existing.payload = payload
                existing.provider = provider
                existing.source_timestamp = source_timestamp
                existing.retrieved_at = retrieved_at
            await session.commit()

    async def get_observations(
        self,
        logical_key: str,
        *,
        cutoff: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[WindowRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(RollingWindowRecord)
                .where(RollingWindowRecord.logical_key == logical_key)
                .order_by(RollingWindowRecord.sort_key)
            )
            rows = list(result)
        out: list[WindowRecord] = []
        for row in rows:
            ts = row.source_timestamp
            if cutoff is not None and ts is not None and ts < cutoff:
                continue
            if since is not None and ts is not None and ts < since:
                continue
            if until is not None and ts is not None and ts > until:
                continue
            out.append(
                WindowRecord(
                    logical_key=row.logical_key,
                    sort_key=row.sort_key,
                    payload=row.payload,
                    provider=row.provider,
                    source_timestamp=row.source_timestamp,
                    retrieved_at=row.retrieved_at,
                )
            )
        return out

    async def advance_meta(
        self,
        window_key: str,
        last_successful_at: datetime,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(extra or {})
        payload["last_successful_at"] = last_successful_at.astimezone(UTC).isoformat()
        payload["window_key"] = window_key
        payload.setdefault("schema_version", "window_v1")
        await self.put_observation(
            window_meta_key(window_key),
            "META",
            payload,
            provider=payload.get("provider"),
            source_timestamp=last_successful_at,
            retrieved_at=last_successful_at,
        )

    async def prune_outside_window(self, logical_key: str, cutoff: datetime) -> int:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(RollingWindowRecord).where(RollingWindowRecord.logical_key == logical_key)
            )
            removed = 0
            for row in result:
                if row.source_timestamp is not None and row.source_timestamp < cutoff:
                    await session.delete(row)
                    removed += 1
            await session.commit()
            return removed
