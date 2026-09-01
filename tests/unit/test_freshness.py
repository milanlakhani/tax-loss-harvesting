from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.config import Settings
from app.demo_data.constants import (
    AS_OF,
    DEFAULT_DEMO_AS_OF_DATE,
    parse_demo_as_of_date,
    resolve_analysis_as_of,
)
from app.domain.enums import RejectionCode
from app.providers.protocols import ExecutionPosition
from app.services.freshness import (
    brokerage_data_is_stale,
    position_mismatch_symbols,
    verify_proposed_sell_quantity,
    wash_sale_coverage_complete,
)


def test_parse_demo_as_of_date_rejects_today_outside_local_mode():
    with pytest.raises(ValueError, match="today"):
        parse_demo_as_of_date("today", allow_today=False)


def test_parse_demo_as_of_date_today_uses_injected_clock():
    assert parse_demo_as_of_date("today", allow_today=True, today=date(2026, 8, 28)) == date(2026, 8, 28)


def test_tests_use_fixed_demo_as_of_date(settings):
    assert settings.demo_as_of_date == "2026-08-28"
    assert settings.demo_mode == "historical"
    current = Settings(
        app_env="test",
        database_url=settings.database_url,
        local_data_dir=settings.local_data_dir,
        demo_mode="current",
        demo_as_of_date="2026-08-28",
    )
    assert resolve_analysis_as_of(current).date() == DEFAULT_DEMO_AS_OF_DATE
    assert resolve_analysis_as_of(settings) == AS_OF


def test_interactive_local_today_is_allowed():
    local = Settings(app_env="local", demo_mode="current", demo_as_of_date="today")
    resolved = resolve_analysis_as_of(local, today=date(2026, 8, 28))
    assert resolved.date() == date(2026, 8, 28)
    assert resolved.hour == 15


def test_brokerage_stale_and_wash_coverage():
    as_of = datetime(2026, 8, 28, 15, 0)
    assert brokerage_data_is_stale(date(2024, 6, 15), as_of) is True
    assert brokerage_data_is_stale(date(2026, 8, 28), as_of) is False
    assert wash_sale_coverage_complete(date(2026, 3, 15), date(2026, 8, 28), as_of, 30) is True
    assert wash_sale_coverage_complete(date(2026, 8, 20), date(2026, 8, 28), as_of, 30) is False


def test_position_mismatch_does_not_combine_unrelated_alpaca_holdings():
    statement = {"VTI": Decimal("17"), "QQQ": Decimal("19")}
    positions = [
        ExecutionPosition(account_alias="conservative-demo", symbol="VTI", quantity=Decimal("17"), tradable=True, asset_class="ETF"),
        ExecutionPosition(account_alias="conservative-demo", symbol="EXTRA", quantity=Decimal("3"), tradable=True, asset_class="ETF"),
    ]
    assert position_mismatch_symbols(statement, positions) == ["EXTRA"]
    matched = [
        ExecutionPosition(account_alias="conservative-demo", symbol="VTI", quantity=Decimal("17"), tradable=True, asset_class="ETF"),
        ExecutionPosition(account_alias="conservative-demo", symbol="QQQ", quantity=Decimal("19"), tradable=True, asset_class="ETF"),
        ExecutionPosition(account_alias="conservative-demo", symbol="SCHB", quantity=Decimal("0"), tradable=True, asset_class="ETF"),
    ]
    assert position_mismatch_symbols({**statement, "SCHB": Decimal("40")}, matched) == []


def test_verify_proposed_sell_quantity_fails_closed():
    position = ExecutionPosition(account_alias="a", symbol="VTI", quantity=Decimal("10"), tradable=True, asset_class="ETF")
    assert verify_proposed_sell_quantity(position, Decimal("10")) is None
    assert verify_proposed_sell_quantity(position, Decimal("10.1")) is RejectionCode.POSITION_MISMATCH
    assert verify_proposed_sell_quantity(None, Decimal("1")) is RejectionCode.POSITION_MISMATCH
