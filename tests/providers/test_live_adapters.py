from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.adapters.memory_window_store import InMemoryRollingWindowStore
from app.providers.alpha_vantage import AlphaVantageProvider
from app.providers.alpaca import ALPACA_PAPER_FORCED, AlpacaProvider
from app.providers.alpaca_market_data import AlpacaMarketDataProvider
from app.providers.coingecko import CoinGeckoProvider
from app.providers.fakes import FakeCryptoQuoteProvider, FakeEquityQuoteProvider, FakeExecutionProvider, FakeFxProvider, RecordingClock
from app.providers.frankfurter import FrankfurterProvider
from app.providers.http_util import HttpJsonClient
from app.providers.mappings import COINGECKO_IDS
from app.providers.protocols import ProviderRouter


def _client():
    return httpx.AsyncClient()


@pytest.mark.unit
def test_router_sends_equity_to_alpha_crypto_to_coingecko_fx_to_frankfurter():
    equity = FakeEquityQuoteProvider()
    crypto = FakeCryptoQuoteProvider()
    fx = FakeFxProvider()
    execution = FakeExecutionProvider()
    router = ProviderRouter(equity=equity, crypto=crypto, fx=fx, execution=execution)
    assert router.equity is equity
    assert router.crypto is crypto
    assert router.fx is fx
    assert router.execution is execution


@pytest.mark.unit
def test_explicit_coingecko_ids_never_inferred_from_ambiguous_tickers():
    assert COINGECKO_IDS["BTC/USD"] == "bitcoin"
    assert COINGECKO_IDS["ETH/USD"] == "ethereum"
    assert "BTCUSD" not in COINGECKO_IDS


@pytest.mark.unit
async def test_alpha_vantage_includes_key_and_handles_stale_rate_limit_malformed_timeout():
    clock = RecordingClock(datetime(2024, 6, 15, tzinfo=UTC))
    store = InMemoryRollingWindowStore(clock)
    async with httpx.AsyncClient() as raw:
        provider = AlphaVantageProvider(HttpJsonClient(raw, provider="alpha-vantage"), "test-key", windows=store)
        with respx.mock(assert_all_called=False) as router:
            route = router.get("https://www.alphavantage.co/query")
            route.mock(
                return_value=httpx.Response(
                    200,
                    json={"Global Quote": {"05. price": "200.00", "07. latest trading day": "2024-06-14"}},
                )
            )
            quote = await provider.get_quote("ETF:VTI", "VTI", datetime(2024, 6, 15, tzinfo=UTC))
            assert quote is not None
            assert quote.price == Decimal("200.00")
            assert "apikey=test-key" in str(route.calls.last.request.url)
            assert quote.provider == "alpha-vantage"

            route.mock(return_value=httpx.Response(200, json={"Note": "API call frequency"}))
            stale = await provider.get_quote("ETF:VTI", "VTI", datetime(2024, 6, 15, tzinfo=UTC))
            assert stale is not None and stale.stale is True

            route.mock(return_value=httpx.Response(200, json={"Global Quote": {}}))
            stale2 = await provider.get_quote("ETF:VTI", "VTI", datetime(2024, 6, 15, tzinfo=UTC))
            assert stale2 is not None and stale2.stale is True

            route.mock(side_effect=httpx.TimeoutException("timeout"))
            stale3 = await provider.get_quote("ETF:VTI", "VTI", datetime(2024, 6, 15, tzinfo=UTC))
            assert stale3 is not None and stale3.stale is True

            route.mock(return_value=httpx.Response(429, json={"Note": "rate"}))
            stale4 = await provider.get_quote("ETF:VTI", "VTI", datetime(2024, 6, 15, tzinfo=UTC))
            assert stale4 is not None and stale4.stale is True


@pytest.mark.unit
async def test_coingecko_sends_demo_key_and_explicit_id():
    async with httpx.AsyncClient() as raw:
        provider = CoinGeckoProvider(HttpJsonClient(raw, provider="coingecko"), "cg-demo")
        with respx.mock:
            route = respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
                return_value=httpx.Response(
                    200,
                    json={"bitcoin": {"usd": 60000, "gbp": 47000, "eur": 55000, "last_updated_at": 1718460000}},
                )
            )
            quote = await provider.get_quote("CRYPTO:BTC-USD", "BTC/USD", datetime(2024, 6, 15, tzinfo=UTC))
            assert quote is not None
            assert quote.provider_asset_id == "bitcoin"
            request = route.calls.last.request
            assert request.headers.get("x-cg-demo-api-key") == "cg-demo"
            assert "ids=bitcoin" in str(request.url)
            assert "usd" in str(request.url) and "gbp" in str(request.url) and "eur" in str(request.url)


@pytest.mark.unit
async def test_frankfurter_weekend_uses_effective_earlier_date():
    async with httpx.AsyncClient() as raw:
        provider = FrankfurterProvider(HttpJsonClient(raw, provider="frankfurter"))
        sunday = date(2024, 6, 16)
        with respx.mock:
            respx.get("https://api.frankfurter.dev/v1/2024-06-16").mock(
                return_value=httpx.Response(
                    200,
                    json={"amount": 1, "base": "GBP", "date": "2024-06-14", "rates": {"USD": 1.27}},
                )
            )
            rate = await provider.get_rate("GBP", "USD", sunday)
            assert rate is not None
            assert rate.requested_date == sunday
            assert rate.effective_date == date(2024, 6, 14)
            assert rate.rate == Decimal("1.27")
            assert rate.provider == "frankfurter"


@pytest.mark.unit
async def test_coingecko_rejects_unmapped_symbol():
    async with httpx.AsyncClient() as raw:
        provider = CoinGeckoProvider(HttpJsonClient(raw, provider="coingecko"), "cg-demo")
        from app.domain.errors import ProviderError

        with pytest.raises(ProviderError):
            await provider.get_quote("CRYPTO:UNKNOWN", "UNKNOWN", datetime(2024, 6, 15, tzinfo=UTC))


@pytest.mark.unit
def test_alpaca_live_mode_cannot_be_enabled_from_request_or_settings_flag():
    assert ALPACA_PAPER_FORCED is True
    with patch("app.providers.alpaca.TradingClient") as client:
        AlpacaProvider({"conservative-demo": ("key", "secret")}, enable_paper_orders=True)
        client.assert_called_with("key", "secret", paper=True)
        assert client.call_args.kwargs["paper"] is ALPACA_PAPER_FORCED
        assert client.call_args.kwargs["paper"] is not False


@pytest.mark.unit
async def test_alpaca_normalizes_crypto_position_symbols_at_provider_boundary():
    with patch("app.providers.alpaca.TradingClient") as client_type:
        client = client_type.return_value
        client.get_all_positions.return_value = [
            MagicMock(symbol="BTCUSD", qty="0.07", asset_class="AssetClass.CRYPTO"),
            MagicMock(symbol="QQQ", qty="10", asset_class="AssetClass.US_EQUITY"),
        ]
        provider = AlpacaProvider({"conservative-demo": ("key", "secret")}, enable_paper_orders=False)
        positions = await provider.list_positions("conservative-demo")

    assert [(p.symbol, p.asset_class) for p in positions] == [
        ("BTC/USD", "crypto"),
        ("QQQ", "us_equity"),
    ]


@pytest.mark.unit
async def test_alpaca_market_data_preserves_trade_timestamp_and_delegates_history():
    source_time = datetime(2026, 8, 31, 14, 35, tzinfo=UTC)
    history = MagicMock()
    history.get_price_history = AsyncMock(return_value=[])
    with patch("app.providers.alpaca_market_data.StockHistoricalDataClient") as client_type:
        client_type.return_value.get_stock_latest_trade.return_value = {
            "QQQ": MagicMock(price=401.25, timestamp=source_time)
        }
        provider = AlpacaMarketDataProvider("key", "secret", history=history)
        quote = await provider.get_quote("ETF:QQQ", "QQQ", source_time)
        observations = await provider.get_price_history("ETF:QQQ", "QQQ", source_time, source_time)

    assert quote is not None
    assert quote.price == Decimal("401.25")
    assert quote.source_timestamp == source_time
    assert quote.provider == "alpaca-market-data"
    assert quote.retrieved_at != quote.source_timestamp
    assert observations == []
    history.get_price_history.assert_awaited_once_with("ETF:QQQ", "QQQ", source_time, source_time)


@pytest.mark.unit
async def test_alpaca_market_data_fails_closed_when_latest_trade_is_missing():
    history = MagicMock()
    with patch("app.providers.alpaca_market_data.StockHistoricalDataClient") as client_type:
        client_type.return_value.get_stock_latest_trade.return_value = {}
        provider = AlpacaMarketDataProvider("key", "secret", history=history)
        from app.domain.errors import ProviderError

        with pytest.raises(ProviderError, match="latest trade unavailable"):
            await provider.get_quote("ETF:QQQ", "QQQ", datetime(2026, 8, 31, tzinfo=UTC))
