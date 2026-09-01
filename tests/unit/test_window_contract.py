from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.memory_window_store import InMemoryRollingWindowStore
from app.adapters.rolling_window import price_window_key, timestamp_sort_key
from app.adapters.window_sync import WindowSyncService
from app.providers.fakes import FakeCryptoQuoteProvider, FakeEquityQuoteProvider, FakeExecutionProvider, FakeFxProvider, LiveProviderAttemptError, RecordingClock
from app.providers.protocols import PriceObservation, ProviderRouter, Quote


def _providers_with_history(fail: bool = False) -> ProviderRouter:
    equity = FakeEquityQuoteProvider()
    start = datetime(2024, 6, 1, tzinfo=UTC)
    obs = [
        PriceObservation(
            canonical_id="ETF:VTI",
            currency="USD",
            price=Decimal("200") + Decimal(i),
            observed_at=start + timedelta(days=i),
            provider="fake-alpha-vantage",
            is_mocked=True,
        )
        for i in range(10)
    ]
    equity.seed_history("ETF:VTI", obs)
    equity.seed_quote(
        Quote(
            canonical_id="ETF:VTI",
            price=Decimal("209"),
            currency="USD",
            provider="fake-alpha-vantage",
            provider_asset_id="VTI",
            source_timestamp=start + timedelta(days=9),
            retrieved_at=start + timedelta(days=9),
            is_mocked=True,
        )
    )
    fx = FakeFxProvider()
    if fail:
        fx.fail_after = 0
    return ProviderRouter(equity=equity, crypto=FakeCryptoQuoteProvider(), fx=fx, execution=FakeExecutionProvider())


@pytest.mark.unit
async def test_window_fetch_overlap_idempotent_prune_and_partial_failure():
    clock = RecordingClock(datetime(2024, 6, 10, tzinfo=UTC))
    store = InMemoryRollingWindowStore(clock)
    providers = _providers_with_history()
    sync = WindowSyncService(store, providers)
    as_of = datetime(2024, 6, 10, tzinfo=UTC)
    await sync.sync_price_window(
        canonical_id="ETF:VTI",
        symbol="VTI",
        currency="USD",
        asset_type="ETF",
        as_of=as_of,
        window_days=8,
        overlap=timedelta(hours=24),
        now=clock.now(),
    )
    key = price_window_key("ETF:VTI", "USD")
    meta = await store.get_meta(key)
    assert meta is not None
    first_calls = len(providers.equity.calls)
    rows = await store.get_observations(key, cutoff=as_of - timedelta(days=8))
    assert rows
    assert all(r.source_timestamp >= as_of - timedelta(days=8) for r in rows if r.source_timestamp)

    await sync.sync_price_window(
        canonical_id="ETF:VTI",
        symbol="VTI",
        currency="USD",
        asset_type="ETF",
        as_of=as_of,
        window_days=8,
        overlap=timedelta(hours=24),
        now=clock.now(),
    )
    assert len(providers.equity.calls) > first_calls
    rows2 = await store.get_observations(key, cutoff=as_of - timedelta(days=8))
    identities = {(r.logical_key, r.sort_key) for r in rows2}
    assert len(identities) == len(rows2)

    stale = await store.get_observations(key)
    cutoff = as_of - timedelta(days=8)
    visible = await store.get_observations(key, cutoff=cutoff)
    assert len(visible) <= len(stale)

    failing = _providers_with_history()

    class Boom(FakeEquityQuoteProvider):
        async def get_price_history(self, *args, **kwargs):
            raise LiveProviderAttemptError("partial")

    boom_router = ProviderRouter(
        equity=Boom(),
        crypto=FakeCryptoQuoteProvider(),
        fx=FakeFxProvider(),
        execution=FakeExecutionProvider(),
    )
    store2 = InMemoryRollingWindowStore(clock)
    await store2.advance_meta(key, datetime(2024, 6, 1, tzinfo=UTC))
    sync2 = WindowSyncService(store2, boom_router)
    with pytest.raises(LiveProviderAttemptError):
        await sync2.sync_price_window(
            canonical_id="ETF:VTI",
            symbol="VTI",
            currency="USD",
            asset_type="ETF",
            as_of=as_of,
            window_days=8,
            overlap=timedelta(hours=24),
            now=clock.now(),
        )
    meta_after = await store2.get_meta(key)
    assert meta_after.last_successful_at == datetime(2024, 6, 1, tzinfo=UTC)
