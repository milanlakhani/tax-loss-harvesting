from __future__ import annotations

COINGECKO_IDS: dict[str, str] = {
    "BTC/USD": "bitcoin",
    "ETH/USD": "ethereum",
    "SOL/USD": "solana",
    "DOGE/USD": "dogecoin",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "CRYPTO:BTC-USD": "bitcoin",
    "CRYPTO:ETH-USD": "ethereum",
    "CRYPTO:SOL-USD": "solana",
    "CRYPTO:DOGE-USD": "dogecoin",
}

ALPACA_CRYPTO_SYMBOLS = {"BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"}


def coingecko_id_for(symbol: str, canonical_id: str | None = None) -> str | None:
    if canonical_id and canonical_id in COINGECKO_IDS:
        return COINGECKO_IDS[canonical_id]
    return COINGECKO_IDS.get(symbol) or COINGECKO_IDS.get(symbol.upper())


def expected_alpaca_asset_class(internal_asset_type: str) -> str:
    if internal_asset_type == "CRYPTO":
        return "crypto"
    if internal_asset_type in {"EQUITY", "ETF"}:
        return "us_equity"
    raise ValueError(internal_asset_type)
