from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import PaperOrderStatus, QuoteContext, RejectionCode
from app.providers.fakes import FakeExecutionProvider
from app.providers.protocols import ALPACA_MARKET_DATA_PROVIDER, MarketSession, Quote
from app.services.paper_execution import map_paper_order_status
from app.services.quote_freshness import assess_quote_freshness


def _quote(*, source: datetime, provider: str = ALPACA_MARKET_DATA_PROVIDER) -> Quote:
    return Quote(
        canonical_id="ETF:VTI",
        price=Decimal("200"),
        currency="USD",
        provider=provider,
        provider_asset_id="VTI",
        source_timestamp=source,
        retrieved_at=source + timedelta(seconds=2),
        is_mocked=True,
        asset_type="ETF",
        symbol="VTI",
        feed="iex",
    )


def _calendar(*, is_open: bool, now: datetime, sessions: list[MarketSession], next_open: datetime | None = None) -> FakeExecutionProvider:
    execution = FakeExecutionProvider()
    execution.market_open = is_open
    execution.clock_timestamp = now
    execution.next_open = next_open or now + timedelta(hours=18)
    execution.next_close = now + timedelta(hours=1)
    execution.sessions = sessions
    return execution


def _session(day: date, open_hour: int = 13, close_hour: int = 20) -> MarketSession:
    return MarketSession(
        session_date=day,
        open=datetime(day.year, day.month, day.day, open_hour, 30, tzinfo=UTC),
        close=datetime(day.year, day.month, day.day, close_hour, 0, tzinfo=UTC),
    )


@pytest.mark.unit
async def test_fresh_alpaca_quote_during_open_session():
    now = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
    quote = _quote(source=now - timedelta(minutes=5))
    result = await assess_quote_freshness(
        quote=quote,
        as_of=now,
        max_age_minutes=15,
        asset_type="ETF",
        calendar=_calendar(is_open=True, now=now, sessions=[_session(date(2026, 8, 28))]),
    )
    assert result.ok is True
    assert result.context is None
    assert result.rejection is None


@pytest.mark.unit
async def test_previous_session_quote_after_market_close():
    now = datetime(2026, 8, 28, 21, 30, tzinfo=UTC)
    quote = _quote(source=datetime(2026, 8, 28, 19, 55, tzinfo=UTC))
    result = await assess_quote_freshness(
        quote=quote,
        as_of=now,
        max_age_minutes=15,
        asset_type="ETF",
        calendar=_calendar(
            is_open=False,
            now=now,
            sessions=[_session(date(2026, 8, 28))],
            next_open=datetime(2026, 8, 31, 13, 30, tzinfo=UTC),
        ),
    )
    assert result.ok is True
    assert result.context == QuoteContext.MARKET_CLOSED_USING_LAST_PRICE.value
    assert result.rejection is None


@pytest.mark.unit
async def test_friday_quote_is_accepted_during_weekend():
    now = datetime(2026, 8, 29, 18, 0, tzinfo=UTC)
    quote = _quote(source=datetime(2026, 8, 28, 19, 50, tzinfo=UTC))
    result = await assess_quote_freshness(
        quote=quote,
        as_of=now,
        max_age_minutes=15,
        asset_type="ETF",
        calendar=_calendar(
            is_open=False,
            now=now,
            sessions=[_session(date(2026, 8, 28))],
            next_open=datetime(2026, 8, 31, 13, 30, tzinfo=UTC),
        ),
    )
    assert result.ok is True
    assert result.context == QuoteContext.MARKET_CLOSED_USING_LAST_PRICE.value


@pytest.mark.unit
async def test_quote_from_earlier_session_is_stale():
    now = datetime(2026, 8, 28, 21, 30, tzinfo=UTC)
    quote = _quote(source=datetime(2026, 8, 21, 19, 50, tzinfo=UTC))
    result = await assess_quote_freshness(
        quote=quote,
        as_of=now,
        max_age_minutes=15,
        asset_type="ETF",
        calendar=_calendar(
            is_open=False,
            now=now,
            sessions=[_session(date(2026, 8, 21)), _session(date(2026, 8, 28))],
            next_open=datetime(2026, 8, 31, 13, 30, tzinfo=UTC),
        ),
    )
    assert result.ok is False
    assert result.rejection is RejectionCode.STALE_QUOTE


@pytest.mark.unit
async def test_missing_quote_fails_closed():
    result = await assess_quote_freshness(
        quote=None,
        as_of=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        max_age_minutes=15,
        asset_type="ETF",
        calendar=FakeExecutionProvider(),
    )
    assert result.ok is False
    assert result.rejection is RejectionCode.UNAVAILABLE_QUOTE


@pytest.mark.unit
async def test_alpaca_clock_failure_fails_closed():
    now = datetime(2026, 8, 28, 21, 30, tzinfo=UTC)
    quote = _quote(source=datetime(2026, 8, 28, 19, 55, tzinfo=UTC))
    calendar = _calendar(is_open=False, now=now, sessions=[_session(date(2026, 8, 28))])
    calendar.clock_error = "clock down"
    result = await assess_quote_freshness(
        quote=quote,
        as_of=now,
        max_age_minutes=15,
        asset_type="ETF",
        calendar=calendar,
    )
    assert result.ok is False
    assert result.rejection is RejectionCode.UNAVAILABLE_QUOTE


@pytest.mark.unit
async def test_non_alpaca_quotes_keep_intraday_freshness():
    now = datetime(2026, 8, 28, 21, 30, tzinfo=UTC)
    quote = _quote(source=now - timedelta(hours=6), provider="fake-alpha-vantage")
    result = await assess_quote_freshness(
        quote=quote,
        as_of=now,
        max_age_minutes=15,
        asset_type="ETF",
        calendar=_calendar(is_open=False, now=now, sessions=[_session(date(2026, 8, 28))]),
    )
    assert result.ok is False
    assert result.rejection is RejectionCode.STALE_QUOTE


@pytest.mark.unit
def test_outside_hours_accepted_order_maps_to_queued():
    assert map_paper_order_status("accepted", market_open=False) is PaperOrderStatus.QUEUED
    assert map_paper_order_status("pending_new", market_open=False) is PaperOrderStatus.QUEUED
    assert map_paper_order_status("accepted", market_open=True) is PaperOrderStatus.SUBMITTED
    assert map_paper_order_status("filled", market_open=False) is PaperOrderStatus.FILLED
