from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.rolling_window import RollingWindowStore, quote_key
from app.domain.errors import ProviderError
from app.providers.http_util import HttpJsonClient, parse_decimal
from app.providers.mappings import coingecko_id_for
from app.providers.protocols import PriceObservation, Quote


class CoinGeckoProvider:
    provider_name = "coingecko"

    def __init__(
        self,
        client: HttpJsonClient,
        api_key: str,
        *,
        windows: RollingWindowStore | None = None,
        base_url: str = "https://api.coingecko.com/api/v3",
    ) -> None:
        if not api_key:
            raise ProviderError("missing API key", self.provider_name)
        self.client = client
        self.api_key = api_key
        self.windows = windows
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"x-cg-demo-api-key": self.api_key}

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None:
        cg_id = coingecko_id_for(symbol, canonical_id)
        if cg_id is None:
            raise ProviderError("missing CoinGecko mapping", self.provider_name)
        vs = "usd"
        try:
            payload = await self.client.get_json(
                f"{self.base_url}/simple/price",
                headers=self._headers(),
                params={"ids": cg_id, "vs_currencies": "usd,gbp,eur", "include_last_updated_at": "true"},
            )
            body = payload.get(cg_id)
            if not isinstance(body, dict) or f"{vs}" not in {k.lower() for k in body}:
                raise ProviderError("malformed quote", self.provider_name)
            price = parse_decimal(body.get("usd"), self.provider_name)
            ts = datetime.fromtimestamp(int(body.get("last_updated_at") or as_of.timestamp()), tz=UTC)
        except ProviderError:
            cached = await self._cached_quote(canonical_id)
            if cached is None:
                raise
            cached.stale = True
            return cached
        quote = Quote(
            canonical_id=canonical_id,
            price=price,
            currency="USD",
            provider=self.provider_name,
            provider_asset_id=cg_id,
            source_timestamp=ts,
            retrieved_at=datetime.now(UTC),
            is_mocked=False,
            stale=False,
            asset_type="CRYPTO",
            symbol=symbol,
        )
        if self.windows is not None:
            await self.windows.put_observation(
                quote_key(canonical_id, "USD"),
                "CURRENT",
                {"price": str(quote.price), "provider": self.provider_name, "coingecko_id": cg_id, "stale": False},
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
        cg_id = coingecko_id_for(symbol, canonical_id)
        if cg_id is None:
            raise ProviderError("missing CoinGecko mapping", self.provider_name)
        payload = await self.client.get_json(
            f"{self.base_url}/coins/{cg_id}/market_chart/range",
            headers=self._headers(),
            params={"vs_currency": "usd", "from": int(start.timestamp()), "to": int(end.timestamp())},
        )
        prices = payload.get("prices")
        if not isinstance(prices, list):
            raise ProviderError("malformed history", self.provider_name)
        out: list[PriceObservation] = []
        for row in prices:
            if not isinstance(row, list) or len(row) < 2:
                continue
            observed = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
            out.append(
                PriceObservation(
                    canonical_id=canonical_id,
                    currency="USD",
                    price=parse_decimal(row[1], self.provider_name),
                    observed_at=observed,
                    provider=self.provider_name,
                    is_mocked=False,
                )
            )
        return sorted(out, key=lambda o: o.observed_at)

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
            provider_asset_id=str(row.payload.get("coingecko_id") or canonical_id),
            source_timestamp=row.source_timestamp or row.retrieved_at,
            retrieved_at=row.retrieved_at,
            is_mocked=False,
            stale=True,
            asset_type="CRYPTO",
        )
