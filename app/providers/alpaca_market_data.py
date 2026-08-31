from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

from app.domain.errors import ProviderError
from app.providers.protocols import EquityQuoteProvider, PriceObservation, Quote, ensure_utc


class AlpacaMarketDataProvider:
    """Fresh equity/ETF quotes with Alpha Vantage retained for history.

    The market timestamp comes directly from Alpaca. It is deliberately never
    replaced with retrieval time, so downstream freshness gates continue to
    fail closed outside a genuinely recent trading window.
    """

    provider_name = "alpaca-market-data"

    def __init__(self, key: str, secret: str, *, history: EquityQuoteProvider) -> None:
        if not key or not secret:
            raise ProviderError("missing Alpaca market-data credentials", self.provider_name)
        self._client = StockHistoricalDataClient(key, secret)
        self._history = history

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None:
        try:
            rows = self._client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
            trade = rows.get(symbol) if hasattr(rows, "get") else None
            if trade is None:
                raise ProviderError("latest trade unavailable", self.provider_name)
            price = Decimal(str(trade.price))
            timestamp = ensure_utc(trade.timestamp)
        except ProviderError:
            raise
        except (InvalidOperation, TypeError, ValueError, AttributeError) as exc:
            raise ProviderError("malformed latest trade", self.provider_name) from exc
        except Exception as exc:
            raise ProviderError("latest trade request failed", self.provider_name) from exc
        if price <= 0:
            raise ProviderError("non-positive latest trade", self.provider_name)
        return Quote(
            canonical_id=canonical_id,
            price=price,
            currency="USD",
            provider=self.provider_name,
            provider_asset_id=symbol,
            source_timestamp=timestamp,
            retrieved_at=datetime.now(UTC),
            is_mocked=False,
            stale=False,
            asset_type="ETF" if canonical_id.startswith("ETF:") else "EQUITY",
            symbol=symbol,
        )

    async def get_price_history(
        self,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]:
        return await self._history.get_price_history(canonical_id, symbol, start, end)
