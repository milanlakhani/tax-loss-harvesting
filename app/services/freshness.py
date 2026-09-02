from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.enums import RejectionCode
from app.providers.protocols import ExecutionPosition

IGNORED_EXECUTION_CLASSES = frozenset({"CASH", "CURRENCY", "BANK_BALANCE", "FX"})
CURRENT_DEMO_DATASET = "current"
HISTORICAL_DEMO_DATASET = "historical"


def as_of_day(as_of: datetime) -> datetime:
    if as_of.tzinfo is None:
        return as_of.replace(tzinfo=UTC)
    return as_of.astimezone(UTC)


def current_demo_freshness_applies(*, is_synthetic: bool, demo_dataset: str | None) -> bool:
    """Allowance requires an explicit persisted current-demo marker, not filename/user/env."""
    return bool(is_synthetic) and demo_dataset == CURRENT_DEMO_DATASET


def brokerage_data_is_stale(
    period_end,
    as_of: datetime,
    *,
    is_synthetic: bool = False,
    demo_dataset: str | None = None,
    max_age_days: int | None = None,
) -> bool:
    """True when the latest brokerage statement is older than the analysis as-of date.

    Generated current-demo statements may be up to `max_age_days` behind as-of.
    Uploaded and other non-demo records keep the conservative same-day policy.
    """
    end = period_end.date() if hasattr(period_end, "date") else period_end
    as_of_date = as_of_day(as_of).date()
    if current_demo_freshness_applies(is_synthetic=is_synthetic, demo_dataset=demo_dataset) and max_age_days is not None:
        return (as_of_date - end).days > max_age_days
    return end < as_of_date


def wash_sale_coverage_complete(period_start, period_end, as_of: datetime, window_days: int) -> bool:
    """Statement period must cover the lookback window through as_of for wash-sale evaluation."""
    start = period_start.date() if hasattr(period_start, "date") else period_start
    end = period_end.date() if hasattr(period_end, "date") else period_end
    as_of_date = as_of_day(as_of).date()
    lookback = as_of_date - timedelta(days=window_days)
    return start <= lookback and end >= as_of_date


def statement_quantities_by_symbol(lots_and_symbols: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    qty: dict[str, Decimal] = {}
    for symbol, remaining in lots_and_symbols:
        qty[symbol] = qty.get(symbol, Decimal("0")) + remaining
    return qty


def position_mismatch_symbols(
    statement_qty: dict[str, Decimal],
    alpaca_positions: list[ExecutionPosition],
) -> list[str]:
    """Return Alpaca symbols that are unrelated to, or exceed, statement-derived tax lots.

    Statement lots with no Alpaca quantity are insufficient-mirror, not a mismatch.
    Extra Alpaca holdings must not be combined with synthetic statement lots.
    """
    alpaca_qty: dict[str, Decimal] = {}
    for pos in alpaca_positions:
        if pos.asset_class in IGNORED_EXECUTION_CLASSES:
            continue
        if pos.quantity <= 0:
            continue
        alpaca_qty[pos.symbol] = alpaca_qty.get(pos.symbol, Decimal("0")) + pos.quantity
    mismatched: list[str] = []
    for symbol, qty in alpaca_qty.items():
        stmt = statement_qty.get(symbol, Decimal("0"))
        if stmt <= 0 or qty > stmt:
            mismatched.append(symbol)
    return sorted(mismatched)


def verify_proposed_sell_quantity(position: ExecutionPosition | None, proposed_qty: Decimal) -> RejectionCode | None:
    """Fail closed before placing an order if the mapped account no longer holds enough shares."""
    if proposed_qty <= 0:
        return RejectionCode.NON_POSITIVE_QUANTITY
    if position is None or position.quantity < proposed_qty:
        return RejectionCode.POSITION_MISMATCH
    return None
