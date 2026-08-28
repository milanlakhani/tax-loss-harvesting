from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.memory_window_store import InMemoryRollingWindowStore
from app.adapters.dynamodb_window_store import DynamoDBRollingWindowStore, InMemoryDynamoTable
from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.adapters.rolling_window import timestamp_sort_key
from app.providers.fakes import RecordingClock


async def _contract(store):
    key = "PRICE_WINDOW#ETF:VTI#USD"
    ts1 = datetime(2024, 6, 1, tzinfo=UTC)
    ts2 = datetime(2024, 6, 2, tzinfo=UTC)
    await store.put_observation(
        key,
        timestamp_sort_key(ts1, "ETF:VTI"),
        {"price": "200"},
        provider="fake-alpha-vantage",
        source_timestamp=ts1,
        retrieved_at=ts2,
    )
    await store.put_observation(
        key,
        timestamp_sort_key(ts1, "ETF:VTI"),
        {"price": "201"},
        provider="fake-alpha-vantage",
        source_timestamp=ts1,
        retrieved_at=ts2,
    )
    rows = await store.get_observations(key)
    assert len(rows) == 1
    assert rows[0].payload["price"] == "201"
    await store.put_observation(
        key,
        timestamp_sort_key(ts2, "ETF:VTI"),
        {"price": "202"},
        provider="fake-alpha-vantage",
        source_timestamp=ts2,
        retrieved_at=ts2,
    )
    ordered = await store.get_observations(key)
    assert [r.source_timestamp for r in ordered] == sorted([r.source_timestamp for r in ordered])
    cutoff = ts2
    visible = await store.get_observations(key, cutoff=cutoff)
    assert all(r.source_timestamp >= cutoff for r in visible if r.source_timestamp)
    await store.advance_meta(key, ts2)
    meta = await store.get_meta(key)
    assert meta.last_successful_at == ts2


@pytest.mark.unit
async def test_memory_window_matches_contract():
    await _contract(InMemoryRollingWindowStore(RecordingClock()))


@pytest.mark.unit
async def test_dynamo_emulator_window_matches_contract():
    await _contract(DynamoDBRollingWindowStore(InMemoryDynamoTable()))


@pytest.mark.integration
async def test_postgres_window_matches_contract(session_factory):
    await _contract(PostgresRollingWindowStore(session_factory))
