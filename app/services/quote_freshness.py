from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.domain.enums import QuoteContext, RejectionCode
from app.domain.errors import ProviderError
from app.providers.protocols import (
    ALPACA_MARKET_DATA_PROVIDER,
    MarketClock,
    MarketSession,
    Quote,
    ensure_utc,
)


class MarketCalendar(Protocol):
    async def get_clock(self) -> MarketClock: ...

    async def get_sessions(self, start, end) -> list[MarketSession]: ...


@dataclass(frozen=True, slots=True)
class QuoteFreshness:
    ok: bool
    rejection: RejectionCode | None = None
    explanation: str = ""
    context: str | None = None


def _intraday_stale(quote: Quote, as_of, max_age_minutes: int) -> QuoteFreshness:
    age = ensure_utc(as_of) - quote.source_timestamp
    if age > timedelta(minutes=max_age_minutes):
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.STALE_QUOTE,
            explanation="Quote exceeds configured freshness",
        )
    return QuoteFreshness(ok=True)


async def assess_quote_freshness(
    *,
    quote: Quote | None,
    as_of,
    max_age_minutes: int,
    asset_type: str,
    calendar: MarketCalendar | None,
) -> QuoteFreshness:
    """Classify quote freshness without using weekday heuristics.

    Alpaca EQUITY/ETF quotes use the provider market clock/calendar. An open
    session keeps the configured intraday limit. A closed session accepts the
    latest trade from the most recently completed session and records
    MARKET_CLOSED_USING_LAST_PRICE as informational context, not a rejection.
    Crypto and non-Alpaca quotes keep the existing as-of freshness limit.
    """
    if quote is None:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.UNAVAILABLE_QUOTE,
            explanation="No quote from the required provider",
        )
    if quote.price is None or quote.price <= 0:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.UNAVAILABLE_QUOTE,
            explanation="Quote is invalid",
        )
    alpaca_equity = (
        quote.provider == ALPACA_MARKET_DATA_PROVIDER
        and asset_type in {"EQUITY", "ETF"}
    )
    if not alpaca_equity:
        return _intraday_stale(quote, as_of, max_age_minutes)
    if calendar is None:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.UNAVAILABLE_QUOTE,
            explanation="Alpaca market clock is unavailable",
        )
    try:
        clock = await calendar.get_clock()
        now = ensure_utc(clock.timestamp)
        start = now.date() - timedelta(days=14)
        end = now.date() + timedelta(days=1)
        sessions = await calendar.get_sessions(start, end)
    except ProviderError:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.UNAVAILABLE_QUOTE,
            explanation="Alpaca market clock is unavailable",
        )
    except Exception:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.UNAVAILABLE_QUOTE,
            explanation="Alpaca market clock is unavailable",
        )
    if clock.is_open:
        return _intraday_stale(quote, now, max_age_minutes)
    completed = [session for session in sessions if ensure_utc(session.close) <= now]
    if not completed:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.UNAVAILABLE_QUOTE,
            explanation="Alpaca market calendar has no completed session",
        )
    last = max(completed, key=lambda session: ensure_utc(session.close))
    last_open = ensure_utc(last.open)
    next_open = ensure_utc(clock.next_open)
    quote_ts = quote.source_timestamp
    if quote_ts < last_open or quote_ts >= next_open:
        return QuoteFreshness(
            ok=False,
            rejection=RejectionCode.STALE_QUOTE,
            explanation="Quote predates the most recently completed trading session",
        )
    return QuoteFreshness(
        ok=True,
        context=QuoteContext.MARKET_CLOSED_USING_LAST_PRICE.value,
    )
