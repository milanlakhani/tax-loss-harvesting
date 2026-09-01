from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.adapters.rolling_window import WindowMeta, WindowRecord, window_meta_key


class DynamoDBRollingWindowStore:
    """APP_ENV=aws store. Uses DynamoDB-shaped partition/sort keys matching the local contract.

    Tests inject an in-process table emulator. Production injects a boto3 Table.
    """

    def __init__(self, table) -> None:
        self.table = table

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
        await self._put(
            {
                "pk": logical_key,
                "sk": sort_key,
                "payload": payload,
                "provider": provider,
                "source_timestamp": source_timestamp.isoformat() if source_timestamp else None,
                "retrieved_at": retrieved_at.isoformat(),
            }
        )

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
            out.append(
                WindowRecord(
                    logical_key=item["pk"],
                    sort_key=item["sk"],
                    payload=item["payload"],
                    provider=item.get("provider"),
                    source_timestamp=ts,
                    retrieved_at=datetime.fromisoformat(item["retrieved_at"]),
                )
            )
        return out

    async def advance_meta(self, window_key: str, last_successful_at: datetime, extra: dict[str, Any] | None = None) -> None:
        payload = dict(extra or {})
        payload["last_successful_at"] = last_successful_at.astimezone(UTC).isoformat()
        payload["window_key"] = window_key
        payload["schema_version"] = "window_v1"
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
        if hasattr(self.table, "get_item"):
            result = self.table.get_item(Key={"pk": pk, "sk": sk})
            return result.get("Item")
        return self.table.get((pk, sk))

    async def _put(self, item: dict) -> None:
        if hasattr(self.table, "put_item"):
            self.table.put_item(Item=item)
            return
        self.table[(item["pk"], item["sk"])] = item

    async def _query(self, pk: str) -> list[dict]:
        if hasattr(self.table, "query"):
            result = self.table.query(KeyConditionExpression="pk = :pk", ExpressionAttributeValues={":pk": pk})
            return list(result.get("Items", []))
        return [v for (p, _s), v in list(self.table.items()) if p == pk]

    async def _delete(self, pk: str, sk: str) -> None:
        if hasattr(self.table, "delete_item"):
            self.table.delete_item(Key={"pk": pk, "sk": sk})
            return
        self.table.pop((pk, sk), None)


class InMemoryDynamoTable(dict):
    """Process-local DynamoDB emulator used by contract tests."""
