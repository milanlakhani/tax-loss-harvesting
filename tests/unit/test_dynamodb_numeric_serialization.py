from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.dynamodb_window_store import DynamoDBRollingWindowStore, _to_dynamodb_compatible
from app.adapters.rolling_window import quote_key


def _contains_float(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_float(item) for item in value)
    return False


class RecordingBotoTable:
    """Minimal boto3 Table stand-in that rejects floats the way DynamoDB does."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def put_item(self, Item):
        if _contains_float(Item):
            raise TypeError("Float types are not supported. Use Decimal types instead.")
        self.items.append(Item)


@pytest.mark.unit
def test_to_dynamodb_compatible_preserves_safe_scalars_and_converts_floats():
    payload = {
        "price": "201.5",
        "count": 3,
        "ok": True,
        "missing": None,
        "already": Decimal("1.25"),
        "freshness": 12.5,
        "nested": {"score": 0.9, "flags": [False, 1, 2.5]},
        "tuple_values": (1, 0.25, "keep"),
    }
    converted = _to_dynamodb_compatible(payload)
    assert converted["price"] == "201.5"
    assert converted["count"] == 3
    assert converted["ok"] is True
    assert converted["missing"] is None
    assert converted["already"] == Decimal("1.25")
    assert converted["freshness"] == Decimal("12.5")
    assert converted["nested"]["score"] == Decimal("0.9")
    assert converted["nested"]["flags"] == [False, 1, Decimal("2.5")]
    assert converted["tuple_values"] == [1, Decimal("0.25"), "keep"]
    assert not _contains_float(converted)


@pytest.mark.unit
def test_to_dynamodb_compatible_rejects_non_finite_floats():
    with pytest.raises(ValueError, match="NaN or infinite"):
        _to_dynamodb_compatible({"freshness": float("nan")})
    with pytest.raises(ValueError, match="NaN or infinite"):
        _to_dynamodb_compatible([float("inf")])
    with pytest.raises(ValueError, match="NaN or infinite"):
        _to_dynamodb_compatible((-float("inf"),))


@pytest.mark.unit
async def test_put_observation_does_not_send_floats_to_boto3():
    table = RecordingBotoTable()
    store = DynamoDBRollingWindowStore(table, ttl_days=180)
    retrieved = datetime(2024, 6, 10, 12, 0, tzinfo=UTC)
    source = datetime(2024, 6, 10, 11, 59, 47, tzinfo=UTC)

    await store.put_observation(
        quote_key("ETF:VTI", "USD"),
        "CURRENT",
        {
            "price": "201",
            "freshness": 13.0,
            "meta": {"score": 0.75, "samples": [1, 2.5, None]},
        },
        provider="alpha-vantage",
        source_timestamp=source,
        retrieved_at=retrieved,
    )

    assert len(table.items) == 1
    item = table.items[0]
    assert not _contains_float(item)
    assert item["freshness"] == Decimal(str((retrieved - source).total_seconds()))
    assert item["payload"]["freshness"] == Decimal("13.0")
    assert item["payload"]["meta"]["score"] == Decimal("0.75")
    assert item["payload"]["meta"]["samples"] == [1, Decimal("2.5"), None]
    assert item["payload"]["price"] == "201"
    assert item["ttl"] == int((retrieved + timedelta(days=180)).timestamp())
    assert isinstance(item["ttl"], int)
    assert item["provider"] == "alpha-vantage"
