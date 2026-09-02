from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.adapters.rolling_window import RollingWindowStore, fx_key
from app.domain.errors import ProviderError
from app.providers.http_util import HttpJsonClient, parse_decimal
from app.providers.protocols import FxRate

SUPPORTED = {"USD", "GBP", "EUR"}


class FrankfurterProvider:
    provider_name = "frankfurter"

    def __init__(
        self,
        client: HttpJsonClient,
        *,
        windows: RollingWindowStore | None = None,
        base_url: str = "https://api.frankfurter.dev/v1",
    ) -> None:
        self.client = client
        self.windows = windows
        self.base_url = base_url.rstrip("/")

    async def get_rate(self, base: str, quote_ccy: str, on: date) -> FxRate | None:
        base = base.upper()
        quote_ccy = quote_ccy.upper()
        if base not in SUPPORTED or quote_ccy not in SUPPORTED:
            raise ProviderError("unsupported currency", self.provider_name)
        if base == quote_ccy:
            return self._rate(base, quote_ccy, Decimal("1"), on, on)
        cursor = on
        last_error: Exception | None = None
        for _ in range(10):
            path = "latest" if cursor == date.today() else cursor.isoformat()
            try:
                payload = await self.client.get_json(
                    f"{self.base_url}/{path}",
                    params={"from": base, "to": quote_ccy},
                )
            except ProviderError as exc:
                last_error = exc
                cursor = cursor - timedelta(days=1)
                continue
            rates = payload.get("rates")
            effective_raw = payload.get("date")
            if not isinstance(rates, dict) or quote_ccy not in rates:
                cursor = cursor - timedelta(days=1)
                continue
            effective = date.fromisoformat(str(effective_raw)) if effective_raw else cursor
            rate = self._rate(base, quote_ccy, parse_decimal(rates[quote_ccy], self.provider_name), on, effective)
            if self.windows is not None:
                await self.windows.put_observation(
                    fx_key(base, quote_ccy, on.isoformat()),
                    "RATE",
                    {
                        "rate": str(rate.rate),
                        "requested_date": on.isoformat(),
                        "effective_date": effective.isoformat(),
                        "provider": self.provider_name,
                    },
                    provider=self.provider_name,
                    source_timestamp=datetime(effective.year, effective.month, effective.day, tzinfo=UTC),
                    retrieved_at=datetime.now(UTC),
                )
            return rate
        if last_error:
            raise last_error
        raise ProviderError("unpublished rate", self.provider_name)

    def _rate(self, base: str, quote: str, value: Decimal, requested: date, effective: date) -> FxRate:
        now = datetime.now(UTC)
        return FxRate(
            base=base,
            quote=quote,
            rate=value,
            requested_date=requested,
            effective_date=effective,
            provider=self.provider_name,
            source_timestamp=datetime(effective.year, effective.month, effective.day, tzinfo=UTC),
            retrieved_at=now,
            is_mocked=False,
        )
