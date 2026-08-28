from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

SEED = 42
AS_OF = datetime(2024, 6, 15, 15, 0, tzinfo=UTC)
HISTORICAL_AS_OF_DATE = AS_OF.date()
DEFAULT_DEMO_AS_OF_DATE = date(2026, 8, 28)
QUOTE_TS = datetime(2024, 6, 15, 14, 55, tzinfo=UTC)
STALE_QUOTE_TS = datetime(2024, 6, 1, 14, 0, tzinfo=UTC)

# Explicit offsets from DEMO_AS_OF_DATE for current-demo wash-sale coverage.
WASH_EQUITY_REINVEST_OFFSET_DAYS = -10
CRYPTO_SCHEDULED_BUY_OFFSET_DAYS = -7


def as_of_datetime(day: date, *, hour: int = 15, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def quote_timestamps_for(as_of: datetime) -> tuple[datetime, datetime]:
    """Fresh quote (~5 minutes before as_of) and stale quote (~14 days before)."""
    return as_of - timedelta(minutes=5), as_of - timedelta(days=14)


def parse_demo_as_of_date(value: str, *, allow_today: bool, today: date | None = None) -> date:
    raw = value.strip()
    if raw.lower() == "today":
        if not allow_today:
            raise ValueError("DEMO_AS_OF_DATE=today is only allowed in interactive local demo mode")
        return today or datetime.now(UTC).date()
    return date.fromisoformat(raw)


def resolve_analysis_as_of(settings, *, today: date | None = None) -> datetime:
    mode = (settings.demo_mode or "historical").strip().lower()
    if mode in {"historical", "hist", "regression"}:
        return AS_OF
    allow_today = bool(settings.is_local)
    day = parse_demo_as_of_date(settings.demo_as_of_date, allow_today=allow_today, today=today)
    return as_of_datetime(day)


def shift_from_historical(day: date, target_as_of: date) -> date:
    return day + (target_as_of - HISTORICAL_AS_OF_DATE)

USER_A_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_B_ID = UUID("22222222-2222-4222-8222-222222222222")
BANK_A_ID = UUID("11111111-1111-4111-8111-aaaaaaaa0001")
BANK_B_ID = UUID("22222222-2222-4222-8222-bbbbbbbb0001")
PORTFOLIO_A_ID = UUID("11111111-1111-4111-8111-aaaaaaaa0002")
PORTFOLIO_B_ID = UUID("22222222-2222-4222-8222-bbbbbbbb0002")

USER_A_EMAIL = "alex.conservative@demo.local"
USER_B_EMAIL = "blair.growth@demo.local"

GBP_USD = Decimal("1.270000")
EUR_USD = Decimal("1.085000")

# canonical_id, symbol, type, name, mocked_price
ASSET_CATALOG: dict[str, tuple[str, str, str, str, Decimal]] = {
    "VTI": ("ETF:VTI", "VTI", "ETF", "Vanguard Total Stock Market", Decimal("200.00")),
    "VXUS": ("ETF:VXUS", "VXUS", "ETF", "Vanguard Total International Stock", Decimal("55.00")),
    "BND": ("ETF:BND", "BND", "ETF", "Vanguard Total Bond Market", Decimal("72.00")),
    "SPY": ("ETF:SPY", "SPY", "ETF", "SPDR S&P 500", Decimal("500.00")),
    "QQQ": ("ETF:QQQ", "QQQ", "ETF", "Invesco QQQ Trust", Decimal("400.00")),
    "AAPL": ("EQUITY:AAPL", "AAPL", "EQUITY", "Apple Inc", Decimal("185.00")),
    "MSFT": ("EQUITY:MSFT", "MSFT", "EQUITY", "Microsoft Corp", Decimal("400.00")),
    "AGG": ("ETF:AGG", "AGG", "ETF", "iShares Core US Aggregate Bond", Decimal("96.00")),
    "SCHD": ("ETF:SCHD", "SCHD", "ETF", "Schwab US Dividend Equity", Decimal("80.00")),
    "VNQ": ("ETF:VNQ", "VNQ", "ETF", "Vanguard Real Estate", Decimal("84.00")),
    "IWM": ("ETF:IWM", "IWM", "ETF", "iShares Russell 2000", Decimal("200.00")),
    "TSLA": ("EQUITY:TSLA", "TSLA", "EQUITY", "Tesla Inc", Decimal("180.00")),
    "NVDA": ("EQUITY:NVDA", "NVDA", "EQUITY", "NVIDIA Corp", Decimal("120.00")),
    "BTC": ("CRYPTO:BTC-USD", "BTC/USD", "CRYPTO", "Bitcoin", Decimal("60000.00")),
    "ETH": ("CRYPTO:ETH-USD", "ETH/USD", "CRYPTO", "Ethereum", Decimal("3300.00")),
    "SOL": ("CRYPTO:SOL-USD", "SOL/USD", "CRYPTO", "Solana", Decimal("135.00")),
    "DOGE": ("CRYPTO:DOGE-USD", "DOGE/USD", "CRYPTO", "Dogecoin", Decimal("0.15000000")),
    "SCHB": ("ETF:SCHB", "SCHB", "ETF", "Schwab US Broad Market", Decimal("62.00")),
    "VEA": ("ETF:VEA", "VEA", "ETF", "Vanguard FTSE Developed Markets", Decimal("50.00")),
    "VGT": ("ETF:VGT", "VGT", "ETF", "Vanguard Information Technology", Decimal("580.00")),
    "IVV": ("ETF:IVV", "IVV", "ETF", "iShares Core S&P 500", Decimal("522.00")),
    "VYM": ("ETF:VYM", "VYM", "ETF", "Vanguard High Dividend Yield", Decimal("118.00")),
    "IYR": ("ETF:IYR", "IYR", "ETF", "iShares US Real Estate", Decimal("88.00")),
    "SCHA": ("ETF:SCHA", "SCHA", "ETF", "Schwab US Small-Cap", Decimal("48.00")),
    "SMH": ("ETF:SMH", "SMH", "ETF", "VanEck Semiconductor", Decimal("240.00")),
}

PORTFOLIO_A_HOLDINGS = ["VTI", "VXUS", "BND", "SPY", "QQQ", "AAPL", "MSFT", "AGG", "BTC", "ETH", "SOL"]
PORTFOLIO_B_HOLDINGS = ["QQQ", "SCHD", "VNQ", "VTI", "IWM", "TSLA", "NVDA", "BND", "BTC", "ETH", "SOL"]

REPLACEMENTS = [
    ("ETF:VTI", "ETF:SCHB", "ALLOWED"),
    ("ETF:QQQ", "ETF:VGT", "ALLOWED"),
    ("ETF:VXUS", "ETF:VEA", "ALLOWED"),
    ("CRYPTO:BTC-USD", "CRYPTO:ETH-USD", "ALLOWED"),
    ("CRYPTO:ETH-USD", "CRYPTO:SOL-USD", "ALLOWED"),
    ("ETF:BND", "ETF:AGG", "ALLOWED"),
    ("EQUITY:AAPL", "EQUITY:MSFT", "ALLOWED"),
    ("EQUITY:MSFT", "EQUITY:AAPL", "SUBSTANTIALLY_IDENTICAL"),
    ("ETF:SPY", "ETF:IVV", "SUBSTANTIALLY_IDENTICAL"),
    ("ETF:SCHB", "ETF:VTI", "ALLOWED"),
    ("CRYPTO:DOGE-USD", "CRYPTO:BTC-USD", "ALLOWED"),
    ("ETF:SCHD", "ETF:VYM", "ALLOWED"),
    ("ETF:VNQ", "ETF:IYR", "ALLOWED"),
    ("ETF:IWM", "ETF:SCHA", "ALLOWED"),
    ("ETF:SCHA", "ETF:IWM", "ALLOWED"),
    ("EQUITY:NVDA", "ETF:SMH", "ALLOWED"),
    ("EQUITY:TSLA", "ETF:IWM", "PROHIBITED"),
]

RECURRING_MERCHANTS = [
    "ACME PAYROLL",
    "RENTCO HOUSING",
    "GROCERYCO",
    "UTILITYCO ENERGY",
    "STREAMFLIX",
    "INSURECO",
    "TRANSITCO",
    "CAFECO",
]

CATEGORIES = [
    "INCOME",
    "HOUSING",
    "GROCERIES",
    "UTILITIES",
    "SUBSCRIPTIONS",
    "INSURANCE",
    "TRANSPORT",
    "RESTAURANTS",
    "DISCRETIONARY",
    "TRANSFER",
    "WITHDRAWAL",
    "REFUND",
    "INTEREST",
    "FEE",
]
