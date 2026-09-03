from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.adapters.rolling_window import WindowMeta, WindowRecord, window_meta_key

SCHEMA_VERSION = "window_v1"
DEFAULT_TTL_DAYS = 180


def _to_dynamodb_compatible(value: Any) -> Any:
    """Convert finite floats to Decimal before boto3 put_item. Leave other scalars unchanged."""
    if value is None or isinstance(value, (str, Decimal)):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("DynamoDB cannot store NaN or infinite float values")
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamodb_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_to_dynamodb_compatible(item) for item in value]
    return value


class DynamoDBRollingWindowStore:
    """APP_ENV=aws store. Partition/sort keys match the local PostgreSQL contract.

    Tests inject an in-process table emulator. Production injects a boto3 Table.
    TTL (`ttl`) is physical cleanup only; callers must still apply query cutoffs.
    """

    def __init__(self, table, *, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        self.table = table
        self.ttl_days = ttl_days

    async def get_meta(self, window_key: str) -> WindowMeta | None:
        item = await self._get(window_meta_key(window_key), "META")
        if item is None:
            return None
        payload = item["payload"]
        last = payload.get("last_successful_at")
        last_dt = datetime.fromisoformat(last) if last else None
        if last_dt and last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        return WindowMeta(window_key=window_key, last_successful_at=last_dt, extra=payload)

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
        retrieved = retrieved_at.astimezone(UTC) if retrieved_at.tzinfo else retrieved_at.replace(tzinfo=UTC)
        expires = retrieved + timedelta(days=self.ttl_days)
        freshness = None
        if source_timestamp is not None:
            src = source_timestamp.astimezone(UTC) if source_timestamp.tzinfo else source_timestamp.replace(tzinfo=UTC)
            freshness = (retrieved - src).total_seconds()
        item = {
            "pk": logical_key,
            "sk": sort_key,
            "payload": payload,
            "provider": provider,
            "source_timestamp": source_timestamp.isoformat() if source_timestamp else None,
            "retrieved_at": retrieved.isoformat(),
            "freshness": freshness,
            "expires_at": expires.isoformat(),
            "schema_version": SCHEMA_VERSION,
            "ttl": int(expires.timestamp()),
        }
        await self._put(item)

    async def get_observations(
        self,
        logical_key: str,
        *,
        cutoff: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[WindowRecord]:
        items = await self._query(logical_key)
        items.sort(key=lambda i: i["sk"])
        out: list[WindowRecord] = []
        for item in items:
            ts = datetime.fromisoformat(item["source_timestamp"]) if item.get("source_timestamp") else None
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if cutoff is not None and ts is not None and ts < cutoff:
                continue
            if since is not None and ts is not None and ts < since:
                continue
            if until is not None and ts is not None and ts > until:
                continue
            retrieved = datetime.fromisoformat(item["retrieved_at"])
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=UTC)
            out.append(
                WindowRecord(
                    logical_key=item["pk"],
                    sort_key=item["sk"],
                    payload=item["payload"],
                    provider=item.get("provider"),
                    source_timestamp=ts,
                    retrieved_at=retrieved,
                )
            )
        return out

    async def advance_meta(self, window_key: str, last_successful_at: datetime, extra: dict[str, Any] | None = None) -> None:
        payload = dict(extra or {})
        payload["last_successful_at"] = last_successful_at.astimezone(UTC).isoformat()
        payload["window_key"] = window_key
        payload["schema_version"] = SCHEMA_VERSION
        await self.put_observation(
            window_meta_key(window_key),
            "META",
            payload,
            provider=payload.get("provider"),
            source_timestamp=last_successful_at,
            retrieved_at=last_successful_at,
        )

    async def prune_outside_window(self, logical_key: str, cutoff: datetime) -> int:
        items = await self._query(logical_key)
        removed = 0
        for item in items:
            ts = datetime.fromisoformat(item["source_timestamp"]) if item.get("source_timestamp") else None
            if ts is not None and ts < cutoff:
                await self._delete(item["pk"], item["sk"])
                removed += 1
        return removed

    async def _get(self, pk: str, sk: str) -> dict | None:
        if hasattr(self.table, "get_item") and not isinstance(self.table, dict):
            result = self.table.get_item(Key={"pk": pk, "sk": sk})
            return result.get("Item")
        return self.table.get((pk, sk))

    async def _put(self, item: dict) -> None:
        item = _to_dynamodb_compatible(item)
        if hasattr(self.table, "put_item") and not isinstance(self.table, dict):
            # Deterministic identity is (pk, sk). Overwrite is idempotent.
            self.table.put_item(Item=item)
            return
        self.table[(item["pk"], item["sk"])] = item

    async def _query(self, pk: str) -> list[dict]:
        if hasattr(self.table, "query") and not isinstance(self.table, dict):
            from boto3.dynamodb.conditions import Key

            result = self.table.query(KeyConditionExpression=Key("pk").eq(pk))
            return list(result.get("Items", []))
        return [v for (p, _s), v in list(self.table.items()) if p == pk]

    async def _delete(self, pk: str, sk: str) -> None:
        if hasattr(self.table, "delete_item") and not isinstance(self.table, dict):
            self.table.delete_item(Key={"pk": pk, "sk": sk})
            return
        self.table.pop((pk, sk), None)


class InMemoryDynamoTable(dict):
    """Process-local DynamoDB emulator used by contract tests."""
