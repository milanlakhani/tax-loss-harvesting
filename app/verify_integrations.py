from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx

from app.config import get_settings
from app.providers.alpaca import AlpacaProvider
from app.providers.alpaca_market_data import AlpacaMarketDataProvider
from app.providers.alpha_vantage import AlphaVantageProvider
from app.providers.coingecko import CoinGeckoProvider
from app.providers.frankfurter import FrankfurterProvider
from app.providers.http_util import HttpJsonClient
from app.providers.mappings import COINGECKO_IDS


async def _check_alpha() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    async with httpx.AsyncClient(timeout=20.0) as client:
        provider = AlphaVantageProvider(HttpJsonClient(client, provider="alpha-vantage"), settings.alpha_vantage_api_key or "")
        rows = await provider.get_price_history("ETF:VTI", "VTI", now - timedelta(days=10), now)
        print({"provider": "alpha-vantage", "role": "equity-etf-history", "observations": len(rows)})


async def _check_alpaca_market_data() -> None:
    settings = get_settings()
    accounts = settings.alpaca_credentials()
    key, secret = next(iter(accounts.values()))
    async with httpx.AsyncClient(timeout=20.0) as client:
        history = AlphaVantageProvider(HttpJsonClient(client, provider="alpha-vantage"), settings.alpha_vantage_api_key or "")
        provider = AlpacaMarketDataProvider(key, secret, history=history, feed=settings.alpaca_market_data_feed)
        quote = await provider.get_quote("ETF:VTI", "VTI", datetime.now(UTC))
        print(
            {
                "provider": quote.provider if quote else "alpaca-market-data",
                "feed": quote.feed if quote else None,
                "role": "equity-etf-current",
                "price": str(quote.price) if quote else None,
                "source_timestamp": quote.source_timestamp.isoformat() if quote else None,
                "retrieved_at": quote.retrieved_at.isoformat() if quote else None,
            }
        )


async def _check_coingecko() -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20.0) as client:
        provider = CoinGeckoProvider(
            HttpJsonClient(client, provider="coingecko"),
            settings.coingecko_api_key or "",
            base_url=settings.coingecko_api_base_url,
        )
        quote = await provider.get_quote("CRYPTO:BTC-USD", "BTC/USD", datetime.now(UTC))
        print({"provider": "coingecko", "id": COINGECKO_IDS["BTC/USD"], "price": str(quote.price) if quote else None})


async def _check_frankfurter() -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        provider = FrankfurterProvider(HttpJsonClient(client, provider="frankfurter"))
        weekend = date(2024, 6, 16)
        rate = await provider.get_rate("GBP", "USD", weekend)
        print(
            {
                "provider": "frankfurter",
                "requested": str(rate.requested_date if rate else None),
                "effective": str(rate.effective_date if rate else None),
                "rate": str(rate.rate if rate else None),
            }
        )


async def _check_alpaca(account: str) -> None:
    settings = get_settings()
    provider = AlpacaProvider(settings.alpaca_credentials(), enable_paper_orders=False)
    positions = await provider.list_positions(account)
    print({"provider": "alpaca-trading", "account": account, "positions": len(positions), "paper": True})


async def main() -> None:
    parser = argparse.ArgumentParser(description="Live provider smoke checks (manual, credential-dependent)")
    parser.add_argument(
        "--provider",
        choices=["alpha-vantage", "alpaca-market-data", "coingecko", "frankfurter", "alpaca"],
    )
    parser.add_argument("--account", default="conservative-demo")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    targets = (
        ["alpha-vantage", "alpaca-market-data", "coingecko", "frankfurter", "alpaca"]
        if args.all
        else [args.provider]
    )
    for name in targets:
        if name == "alpha-vantage":
            await _check_alpha()
        elif name == "alpaca-market-data":
            await _check_alpaca_market_data()
        elif name == "coingecko":
            await _check_coingecko()
        elif name == "frankfurter":
            await _check_frankfurter()
        elif name == "alpaca":
            await _check_alpaca(args.account)


if __name__ == "__main__":
    asyncio.run(main())
