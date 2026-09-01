from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.rolling_window import RollingWindowStore, quote_key, timestamp_sort_key
from app.domain.errors import ProviderError
from app.providers.http_util import HttpJsonClient, parse_decimal, parse_timestamp
from app.providers.protocols import PriceObservation, Quote


class AlphaVantageProvider:
    provider_name = "alpha-vantage"

    def __init__(
        self,
        client: HttpJsonClient,
        api_key: str,
        *,
        windows: RollingWindowStore | None = None,
        base_url: str = "https://www.alphavantage.co/query",
    ) -> None:
        if not api_key:
            raise ProviderError("missing API key", self.provider_name)
        self.client = client
        self.api_key = api_key
        self.windows = windows
        self.base_url = base_url

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None:
        params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self.api_key}
        try:
            payload = await self.client.get_json(self.base_url, params=params)
            quote = self._parse_global_quote(payload, canonical_id, symbol, as_of)
        except ProviderError:
            cached = await self._cached_quote(canonical_id)
            if cached is None:
                raise
            cached.stale = True
            return cached
        if self.windows is not None:
            await self.windows.put_observation(
                quote_key(canonical_id, quote.currency),
                "CURRENT",
                {"price": str(quote.price), "provider": self.provider_name, "stale": False},
                provider=self.provider_name,
                source_timestamp=quote.source_timestamp,
                retrieved_at=quote.retrieved_at,
            )
        return quote

    async def get_price_history(
        self,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]:
        params = {"function": "TIME_SERIES_DAILY", "symbol": symbol, "apikey": self.api_key, "outputsize": "compact"}
        payload = await self.client.get_json(self.base_url, params=params)
        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict):
            raise ProviderError("malformed history", self.provider_name)
        out: list[PriceObservation] = []
        for day, row in series.items():
            if not isinstance(row, dict):
                continue
            observed = datetime.fromisoformat(str(day)).replace(tzinfo=UTC)
            if observed < start or observed > end:
                continue
            close = parse_decimal(row.get("4. close"), self.provider_name)
            out.append(
                PriceObservation(
                    canonical_id=canonical_id,
                    currency="USD",
                    price=close,
                    observed_at=observed,
                    provider=self.provider_name,
                    is_mocked=False,
                )
            )
        return sorted(out, key=lambda o: o.observed_at)

    def _parse_global_quote(self, payload: dict, canonical_id: str, symbol: str, as_of: datetime) -> Quote:
        body = payload.get("Global Quote")
        if not isinstance(body, dict) or not body.get("05. price"):
            note = str(payload.get("Note") or payload.get("Information") or "")
            if "rate" in note.lower() or "call frequency" in note.lower():
                raise ProviderError("rate limited", self.provider_name)
            raise ProviderError("malformed quote", self.provider_name)
        price = parse_decimal(body["05. price"], self.provider_name)
        ts = parse_timestamp(body.get("07. latest trading day"), as_of)
        return Quote(
            canonical_id=canonical_id,
            price=price,
            currency="USD",
            provider=self.provider_name,
            provider_asset_id=symbol,
            source_timestamp=ts,
            retrieved_at=datetime.now(UTC),
            is_mocked=False,
            stale=False,
            asset_type="ETF" if canonical_id.startswith("ETF:") else "EQUITY",
            symbol=symbol,
        )

    async def _cached_quote(self, canonical_id: str) -> Quote | None:
        if self.windows is None:
            return None
        rows = await self.windows.get_observations(quote_key(canonical_id, "USD"))
        if not rows:
            return None
        row = rows[-1]
        return Quote(
            canonical_id=canonical_id,
            price=Decimal(str(row.payload["price"])),
            currency="USD",
            provider=self.provider_name,
            provider_asset_id=canonical_id,
            source_timestamp=row.source_timestamp or row.retrieved_at,
            retrieved_at=row.retrieved_at,
            is_mocked=False,
            stale=True,
        )
