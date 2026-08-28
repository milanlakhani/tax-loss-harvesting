from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import sqrt

from app.adapters.rolling_window import RollingWindowStore, price_window_key


@dataclass(slots=True)
class DriftResult:
    canonical_id: str
    current_weight: Decimal
    target_weight: Decimal
    drift: Decimal


@dataclass(slots=True)
class PriceRisk:
    canonical_id: str
    return_5d: Decimal | None
    volatility_20d: Decimal | None
    drawdown: Decimal | None
    rapid_decline_warning: bool


def portfolio_weights(values: dict[str, Decimal]) -> dict[str, Decimal]:
    total = sum(values.values(), Decimal("0"))
    if total == 0:
        return {key: Decimal("0") for key in values}
    return {key: (val / total) for key, val in values.items()}


def drift_from_targets(
    current_values: dict[str, Decimal],
    target_weights: dict[str, Decimal],
) -> list[DriftResult]:
    weights = portfolio_weights(current_values)
    keys = sorted(set(weights) | set(target_weights))
    results: list[DriftResult] = []
    for key in keys:
        current = weights.get(key, Decimal("0"))
        target = target_weights.get(key, Decimal("0"))
        results.append(DriftResult(canonical_id=key, current_weight=current, target_weight=target, drift=current - target))
    results.sort(key=lambda r: r.canonical_id)
    return results


def class_weights(asset_values: dict[str, Decimal], class_of: dict[str, str]) -> dict[str, Decimal]:
    grouped: dict[str, Decimal] = {}
    for canonical, value in asset_values.items():
        asset_class = class_of.get(canonical, "UNKNOWN")
        grouped[asset_class] = grouped.get(asset_class, Decimal("0")) + value
    return portfolio_weights(grouped)


def simulated_weights_after_sale(
    asset_values: dict[str, Decimal],
    canonical_id: str,
    sale_value: Decimal,
) -> dict[str, Decimal]:
    updated = dict(asset_values)
    updated[canonical_id] = max(updated.get(canonical_id, Decimal("0")) - sale_value, Decimal("0"))
    return portfolio_weights(updated)


async def price_risk_from_window(
    store: RollingWindowStore,
    canonical_id: str,
    currency: str,
    as_of,
    cutoff,
) -> PriceRisk:
    rows = await store.get_observations(price_window_key(canonical_id, currency), cutoff=cutoff, until=as_of)
    prices: list[tuple] = []
    for row in rows:
        if row.source_timestamp is None:
            continue
        prices.append((row.source_timestamp, Decimal(str(row.payload["price"]))))
    prices.sort(key=lambda p: p[0])
    return_5d = _return_over(prices, as_of, days=5)
    vol = _volatility(prices, days=20)
    drawdown = _drawdown(prices)
    rapid = bool(return_5d is not None and return_5d <= Decimal("-0.08"))
    return PriceRisk(
        canonical_id=canonical_id,
        return_5d=return_5d,
        volatility_20d=vol,
        drawdown=drawdown,
        rapid_decline_warning=rapid,
    )


def _return_over(prices: list[tuple], as_of, days: int) -> Decimal | None:
    if not prices:
        return None
    latest = prices[-1][1]
    target = as_of - timedelta(days=days)
    prior = None
    for ts, price in prices:
        if ts <= target:
            prior = price
    if prior in (None, 0):
        return None
    return (latest - prior) / prior


def _volatility(prices: list[tuple], days: int) -> Decimal | None:
    if len(prices) < 3:
        return None
    window = prices[-days:] if len(prices) >= days else prices
    rets: list[Decimal] = []
    for i in range(1, len(window)):
        prev = window[i - 1][1]
        if prev == 0:
            continue
        rets.append((window[i][1] - prev) / prev)
    if len(rets) < 2:
        return None
    mean = sum(rets, Decimal("0")) / Decimal(len(rets))
    var = sum(((r - mean) ** 2 for r in rets), Decimal("0")) / Decimal(len(rets) - 1)
    daily = Decimal(str(sqrt(float(var))))
    return daily * Decimal(str(sqrt(252)))


def _drawdown(prices: list[tuple]) -> Decimal | None:
    if not prices:
        return None
    peak = prices[0][1]
    max_dd = Decimal("0")
    for _, price in prices:
        if price > peak:
            peak = price
        if peak == 0:
            continue
        dd = (peak - price) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd
