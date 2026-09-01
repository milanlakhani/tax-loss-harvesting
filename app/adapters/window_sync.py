from __future__ import annotations

from datetime import timedelta

from app.adapters.rolling_window import (
    RollingWindowStore,
    price_window_key,
    quote_key,
    timestamp_sort_key,
)
from app.providers.protocols import ProviderRouter, Quote


class WindowSyncService:
    """Fetch only observations missing after the saved timestamp, then advance meta."""

    def __init__(self, store: RollingWindowStore, providers: ProviderRouter) -> None:
        self.store = store
        self.providers = providers

    async def sync_price_window(
        self,
        *,
        canonical_id: str,
        symbol: str,
        currency: str,
        asset_type: str,
        as_of,
        window_days: int,
        overlap,
        now,
    ) -> None:
        window_key = price_window_key(canonical_id, currency)
        cutoff = as_of - timedelta(days=window_days)
        meta = await self.store.get_meta(window_key)
        fetch_start = cutoff
        if meta and meta.last_successful_at is not None:
            fetch_start = meta.last_successful_at - overlap
            if fetch_start < cutoff:
                fetch_start = cutoff
        try:
            observations = await self.providers.history_for_asset_type(
                asset_type, canonical_id, symbol, fetch_start, as_of
            )
            for obs in observations:
                payload = {
                    "canonical_id": obs.canonical_id,
                    "currency": obs.currency,
                    "price": str(obs.price),
                    "provider": obs.provider,
                    "is_mocked": obs.is_mocked,
                    "freshness": (now - obs.observed_at).total_seconds(),
                }
                await self.store.put_observation(
                    window_key,
                    timestamp_sort_key(obs.observed_at, obs.canonical_id),
                    payload,
                    provider=obs.provider,
                    source_timestamp=obs.observed_at,
                    retrieved_at=now,
                )
            quote = await self.providers.quote_for_asset_type(asset_type, canonical_id, symbol, as_of)
            if quote is not None:
                await self._store_quote(quote, currency, now)
            await self.store.advance_meta(
                window_key,
                as_of,
                extra={"provider": "router", "cutoff": cutoff.isoformat()},
            )
        except Exception:
            # Partial failure must not advance last-successful timestamp.
            raise

    async def _store_quote(self, quote: Quote, currency: str, now) -> None:
        await self.store.put_observation(
            quote_key(quote.canonical_id, currency),
            "CURRENT",
            {
                "price": str(quote.price),
                "provider": quote.provider,
                "provider_asset_id": quote.provider_asset_id,
                "is_mocked": quote.is_mocked,
                "tradable": quote.tradable,
                "source_timestamp": quote.source_timestamp.isoformat(),
            },
            provider=quote.provider,
            source_timestamp=quote.source_timestamp,
            retrieved_at=now,
        )
