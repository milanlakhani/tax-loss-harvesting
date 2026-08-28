from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.rolling_window import WindowMeta, WindowRecord, window_meta_key
from app.providers.fakes import RecordingClock


class InMemoryRollingWindowStore:
    """Clock-controlled fake used by shared contract tests."""

    def __init__(self, clock: RecordingClock | None = None) -> None:
        self.clock = clock or RecordingClock()
        self._records: dict[tuple[str, str], WindowRecord] = {}

    async def get_meta(self, window_key: str) -> WindowMeta | None:
        rec = self._records.get((window_meta_key(window_key), "META"))
        if rec is None:
            return None
        last = rec.payload.get("last_successful_at")
        last_dt = datetime.fromisoformat(last) if last else None
        if last_dt and last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        return WindowMeta(window_key=window_key, last_successful_at=last_dt, extra=rec.payload)

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
        self._records[(logical_key, sort_key)] = WindowRecord(
            logical_key=logical_key,
            sort_key=sort_key,
            payload=dict(payload),
            provider=provider,
            source_timestamp=source_timestamp,
            retrieved_at=retrieved_at,
        )

    async def get_observations(
        self,
        logical_key: str,
        *,
        cutoff: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[WindowRecord]:
        rows = [r for (lk, _sk), r in self._records.items() if lk == logical_key]
        rows.sort(key=lambda r: r.sort_key)
        out: list[WindowRecord] = []
        for row in rows:
            ts = row.source_timestamp
            if cutoff is not None and ts is not None and ts < cutoff:
                continue
            if since is not None and ts is not None and ts < since:
                continue
            if until is not None and ts is not None and ts > until:
                continue
            out.append(row)
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
        await self.put_observation(
            window_meta_key(window_key),
            "META",
            payload,
            provider=payload.get("provider"),
            source_timestamp=last_successful_at,
            retrieved_at=last_successful_at,
        )

    async def prune_outside_window(self, logical_key: str, cutoff: datetime) -> int:
        drop = [
            key
            for key, rec in self._records.items()
            if key[0] == logical_key and rec.source_timestamp is not None and rec.source_timestamp < cutoff
        ]
        for key in drop:
            del self._records[key]
        return len(drop)
