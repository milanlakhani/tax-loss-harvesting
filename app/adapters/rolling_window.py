from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

WINDOW_META_PREFIX = "WINDOW_META"


def quote_key(asset: str, currency: str) -> str:
    return f"QUOTE#{asset}#{currency}"


def price_window_key(asset: str, currency: str) -> str:
    return f"PRICE_WINDOW#{asset}#{currency}"


def anomaly_window_key(user_id: str, feature: str) -> str:
    return f"ANOMALY_WINDOW#{user_id}#{feature}"


def fx_key(base: str, quote: str, on: str) -> str:
    return f"FX#{base}#{quote}#{on}"


def window_meta_key(window_key: str) -> str:
    return f"{WINDOW_META_PREFIX}#{window_key}"


def timestamp_sort_key(observed_at: datetime, observation_id: str = "") -> str:
    ts = observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{ts}#{observation_id}" if observation_id else ts


@dataclass(slots=True)
class WindowMeta:
    window_key: str
    last_successful_at: datetime | None
    extra: dict[str, Any]


@dataclass(slots=True)
class WindowRecord:
    logical_key: str
    sort_key: str
    payload: dict[str, Any]
    provider: str | None
    source_timestamp: datetime | None
    retrieved_at: datetime


@runtime_checkable
class RollingWindowStore(Protocol):
    async def get_meta(self, window_key: str) -> WindowMeta | None: ...

    async def put_observation(
        self,
        logical_key: str,
        sort_key: str,
        payload: dict[str, Any],
        *,
        provider: str | None,
        source_timestamp: datetime | None,
        retrieved_at: datetime,
    ) -> None: ...

    async def get_observations(
        self,
        logical_key: str,
        *,
        cutoff: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[WindowRecord]: ...

    async def advance_meta(
        self,
        window_key: str,
        last_successful_at: datetime,
        extra: dict[str, Any] | None = None,
    ) -> None: ...

    async def prune_outside_window(self, logical_key: str, cutoff: datetime) -> int: ...
