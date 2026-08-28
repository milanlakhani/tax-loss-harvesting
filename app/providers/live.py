from __future__ import annotations

import httpx

from app.adapters.dynamodb_window_store import DynamoDBRollingWindowStore
from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.config import Settings
from app.demo_data.generate import build_fake_providers
from app.providers.alpaca import AlpacaProvider
from app.providers.alpha_vantage import AlphaVantageProvider
from app.providers.coingecko import CoinGeckoProvider
from app.providers.frankfurter import FrankfurterProvider
from app.providers.http_util import HttpJsonClient
from app.providers.protocols import ProviderRouter


def build_window_store(settings: Settings, session_factory):
    if settings.is_aws:
        import boto3

        table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.dynamodb_table)
        return DynamoDBRollingWindowStore(table)
    return PostgresRollingWindowStore(session_factory)


def build_live_providers(settings: Settings, windows=None) -> ProviderRouter:
    timeout = httpx.Timeout(15.0)
    client = httpx.AsyncClient(timeout=timeout)
    av = AlphaVantageProvider(
        HttpJsonClient(client, provider="alpha-vantage"),
        settings.alpha_vantage_api_key or "",
        windows=windows,
    )
    cg = CoinGeckoProvider(
        HttpJsonClient(client, provider="coingecko"),
        settings.coingecko_api_key or "",
        windows=windows,
        base_url=settings.coingecko_api_base_url,
    )
    fx = FrankfurterProvider(HttpJsonClient(client, provider="frankfurter"), windows=windows)
    execution = AlpacaProvider(settings.alpaca_credentials(), enable_paper_orders=settings.enable_paper_orders)
    return ProviderRouter(equity=av, crypto=cg, fx=fx, execution=execution)


def build_providers(settings: Settings, as_of, windows=None) -> ProviderRouter:
    if settings.use_live_providers:
        return build_live_providers(settings, windows=windows)
    return build_fake_providers(as_of)
