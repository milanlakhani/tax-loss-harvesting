from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from app.demo_data.brokerage_pdf import (
    BrokerageStatementSpec,
    DividendSpec,
    HoldingSpec,
    LotSpec,
    PurchaseSpec,
    RealizedSummary,
    SaleSpec,
)
from app.demo_data.constants import (
    ASSET_CATALOG,
    CRYPTO_SCHEDULED_BUY_OFFSET_DAYS,
    PORTFOLIO_A_HOLDINGS,
    PORTFOLIO_B_HOLDINGS,
    PORTFOLIO_A_ID,
    PORTFOLIO_B_ID,
    USER_A_ID,
    USER_B_ID,
    WASH_EQUITY_REINVEST_OFFSET_DAYS,
    shift_from_historical,
)
from app.domain.enums import AssetType, HoldingPeriod


def catalog(symbol: str) -> tuple[str, str, AssetType, str, Decimal]:
    canonical, sym, typ, name, price = ASSET_CATALOG[symbol]
    return canonical, sym, AssetType(typ), name, price


# Current-demo lots must be losses versus live 2026 prices (VTI ~375, QQQ ~708,
# SPY ~762, VXUS ~87, AAPL ~326, MSFT ~496). Historical 2024 fixtures keep the
# catalog-relative bases used by regression tests.
CURRENT_DEMO_UNIT_BASIS: dict[str, Decimal] = {
    "A-VTI-APPROVED": Decimal("450.00"),
    "A-QQQ-APPROVED": Decimal("850.00"),
    "A-VXUS-APPROVED": Decimal("110.00"),
    "A-BTC-APPROVED": Decimal("120000.00"),
    "A-ETH-APPROVED": Decimal("5500.00"),
    "A-SPY-WASH": Decimal("920.00"),
    "A-SPY-WASH2": Decimal("920.00"),
    "A-DOGE-REBUY": Decimal("0.50"),
    "A-BND-RISK": Decimal("95.00"),
    "A-AAPL-RISK": Decimal("420.00"),
    "A-MSFT-THRESH": Decimal("520.00"),
    "A-ETH-THRESH": Decimal("3650.00"),
    "A-MSFT-IDENTICAL": Decimal("620.00"),
    "A-SOL-UNKNOWN": Decimal("250.00"),
    "A-QQQ-EXTRA": Decimal("850.00"),
    "A-VTI-EXTRA": Decimal("450.00"),
    "A-BTC-EXTRA": Decimal("120000.00"),
    "A-AAPL-EXTRA": Decimal("420.00"),
    "A-VXUS-EXTRA": Decimal("110.00"),
    "A-AGG-STALE": Decimal("130.00"),
    "A-SCHB-MIRROR": Decimal("90.00"),
    "B-QQQ-APPROVED": Decimal("850.00"),
    "B-SCHD-APPROVED": Decimal("140.00"),
    "B-VTI-APPROVED": Decimal("450.00"),
    "B-BTC-APPROVED": Decimal("120000.00"),
    "B-ETH-APPROVED": Decimal("5500.00"),
    "B-VNQ-WASH": Decimal("140.00"),
    "B-VNQ-WASH2": Decimal("140.00"),
    "B-DOGE-REBUY": Decimal("0.50"),
    "B-BND-RISK": Decimal("95.00"),
    "B-TSLA-RISK": Decimal("420.00"),
    "B-IWM-THRESH": Decimal("230.00"),
    "B-ETH-THRESH": Decimal("3650.00"),
    "B-TSLA-PROHIBITED": Decimal("420.00"),
    "B-SOL-UNKNOWN": Decimal("250.00"),
    "B-SCHD-PROFIT": Decimal("64.00"),
    "B-VNQ-PROFIT": Decimal("70.00"),
    "B-SCHA-MIRROR": Decimal("80.00"),
    "B-NVDA-STALE": Decimal("220.00"),
    "B-QQQ-EXTRA": Decimal("850.00"),
    "B-VTI-EXTRA": Decimal("450.00"),
    "B-BTC-EXTRA": Decimal("120000.00"),
    "B-NVDA-EXTRA": Decimal("220.00"),
    "B-VTI-EXTRA2": Decimal("450.00"),
}


def _with_current_demo_basis(lot: LotSpec) -> LotSpec:
    unit = CURRENT_DEMO_UNIT_BASIS.get(lot.lot_id)
    if unit is None or lot.missing_basis or lot.per_unit_basis is None:
        return lot
    return replace(lot, per_unit_basis=unit, remaining_basis=unit * lot.remaining_quantity)


def _lot(
    lot_id: str,
    symbol: str,
    acquired: date,
    qty: Decimal,
    basis_unit: Decimal | None,
    purpose: str,
    missing: bool = False,
) -> LotSpec:
    canonical, sym, typ, _name, price = catalog(symbol)
    remaining_basis = None if missing or basis_unit is None else (basis_unit * qty)
    stmt_value = None if missing else (price * qty)
    return LotSpec(
        lot_id=lot_id,
        canonical_id=canonical,
        symbol=sym,
        asset_type=typ,
        acquisition_date=acquired,
        original_quantity=qty,
        remaining_quantity=qty,
        per_unit_basis=None if missing else basis_unit,
        remaining_basis=remaining_basis,
        statement_value=stmt_value,
        missing_basis=missing,
        purpose=purpose,
    )


def _sale(
    txn: str,
    symbol: str,
    acquired: date,
    sold: date,
    qty: Decimal,
    sale_price: Decimal,
    basis_unit: Decimal,
    period: HoldingPeriod,
) -> SaleSpec:
    canonical, sym, typ, _n, _p = catalog(symbol)
    proceeds = (qty * sale_price).quantize(Decimal("0.01"))
    allocated = (qty * basis_unit).quantize(Decimal("0.01"))
    result = (proceeds - allocated).quantize(Decimal("0.01"))
    return SaleSpec(
        transaction_id=txn,
        canonical_id=canonical,
        symbol=sym,
        asset_type=typ,
        acquisition_date=acquired,
        sale_date=sold,
        quantity=qty,
        sale_price=sale_price,
        proceeds=proceeds,
        allocated_basis=allocated,
        realized_result=result,
        holding_period=period,
    )


def _holdings_from_lots(symbols: list[str], lots: list[LotSpec]) -> list[HoldingSpec]:
    qty_by_canon: dict[str, Decimal] = {}
    meta: dict[str, tuple[str, AssetType, str]] = {}
    for symbol in symbols:
        canonical, sym, typ, name, _p = catalog(symbol)
        meta[canonical] = (sym, typ, name)
        qty_by_canon.setdefault(canonical, Decimal("0"))
    for lot in lots:
        qty_by_canon[lot.canonical_id] = qty_by_canon.get(lot.canonical_id, Decimal("0")) + lot.remaining_quantity
        meta[lot.canonical_id] = (lot.symbol, lot.asset_type, lot.symbol)
    out = []
    for canonical, qty in qty_by_canon.items():
        sym, typ, name = meta[canonical]
        out.append(HoldingSpec(canonical_id=canonical, symbol=sym, asset_type=typ, quantity=qty, name=name))
    return out


def _realized(sales: list[SaleSpec]) -> RealizedSummary:
    st_gains = sum((s.realized_result for s in sales if s.holding_period is HoldingPeriod.SHORT_TERM and s.realized_result > 0), Decimal("0"))
    st_losses = sum((s.realized_result for s in sales if s.holding_period is HoldingPeriod.SHORT_TERM and s.realized_result < 0), Decimal("0"))
    lt_gains = sum((s.realized_result for s in sales if s.holding_period is HoldingPeriod.LONG_TERM and s.realized_result > 0), Decimal("0"))
    lt_losses = sum((s.realized_result for s in sales if s.holding_period is HoldingPeriod.LONG_TERM and s.realized_result < 0), Decimal("0"))
    return RealizedSummary(
        st_gains=st_gains,
        st_losses=st_losses,
        st_net=st_gains + st_losses,
        lt_gains=lt_gains,
        lt_losses=lt_losses,
        lt_net=lt_gains + lt_losses,
        combined_net=st_gains + st_losses + lt_gains + lt_losses,
    )


def portfolio_a_spec(*, as_of: date | None = None) -> BrokerageStatementSpec:
    lots = _portfolio_a_lots()
    sales = _portfolio_a_sales()
    dividends, purchases = _portfolio_a_income()
    if as_of is None:
        return _assemble_spec(
            statement_id="BRK-A-2024-06",
            account_id=PORTFOLIO_A_ID,
            user_id=USER_A_ID,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 6, 15),
            holdings_symbols=PORTFOLIO_A_HOLDINGS,
            lots=lots,
            sales=sales,
            dividends=dividends,
            purchases=purchases,
        )
    return _current_demo_spec(
        prefix="A",
        account_id=PORTFOLIO_A_ID,
        user_id=USER_A_ID,
        holdings_symbols=PORTFOLIO_A_HOLDINGS,
        lots=lots,
        sales=sales,
        dividends=dividends,
        purchases=purchases,
        as_of=as_of,
        wash_symbol="SPY",
    )


def portfolio_b_spec(*, as_of: date | None = None) -> BrokerageStatementSpec:
    lots = _portfolio_b_lots()
    sales = _portfolio_b_sales()
    dividends, purchases = _portfolio_b_income()
    if as_of is None:
        return _assemble_spec(
            statement_id="BRK-B-2024-06",
            account_id=PORTFOLIO_B_ID,
            user_id=USER_B_ID,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 6, 15),
            holdings_symbols=PORTFOLIO_B_HOLDINGS,
            lots=lots,
            sales=sales,
            dividends=dividends,
            purchases=purchases,
        )
    return _current_demo_spec(
        prefix="B",
        account_id=PORTFOLIO_B_ID,
        user_id=USER_B_ID,
        holdings_symbols=PORTFOLIO_B_HOLDINGS,
        lots=lots,
        sales=sales,
        dividends=dividends,
        purchases=purchases,
        as_of=as_of,
        wash_symbol="VNQ",
    )


def _assemble_spec(
    *,
    statement_id: str,
    account_id: UUID,
    user_id: UUID,
    period_start: date,
    period_end: date,
    holdings_symbols: list[str],
    lots: list[LotSpec],
    sales: list[SaleSpec],
    dividends: list[DividendSpec],
    purchases: list[PurchaseSpec],
) -> BrokerageStatementSpec:
    return BrokerageStatementSpec(
        statement_id=statement_id,
        account_id=account_id,
        portfolio_id=account_id,
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        is_taxable=True,
        base_currency="USD",
        holdings=_holdings_from_lots(holdings_symbols, lots),
        lots=lots,
        sales=sales,
        dividends=dividends,
        purchases=purchases,
        realized=_realized(sales),
    )


def _current_demo_spec(
    *,
    prefix: str,
    account_id: UUID,
    user_id: UUID,
    holdings_symbols: list[str],
    lots: list[LotSpec],
    sales: list[SaleSpec],
    dividends: list[DividendSpec],
    purchases: list[PurchaseSpec],
    as_of: date,
    wash_symbol: str,
) -> BrokerageStatementSpec:
    lots = [
        _with_current_demo_basis(replace(lot, acquisition_date=shift_from_historical(lot.acquisition_date, as_of)))
        for lot in lots
    ]
    sales = [
        replace(
            sale,
            acquisition_date=shift_from_historical(sale.acquisition_date, as_of),
            sale_date=shift_from_historical(sale.sale_date, as_of),
        )
        for sale in sales
    ]
    dividends = [replace(div, event_date=shift_from_historical(div.event_date, as_of)) for div in dividends]
    purchases = [replace(purchase, event_date=shift_from_historical(purchase.event_date, as_of)) for purchase in purchases]
    dividends, purchases = _apply_current_demo_window_dates(as_of, dividends, purchases, wash_symbol=wash_symbol)
    return _assemble_spec(
        # Current-demo snapshots can be refreshed more than once in a month.
        # Include the day so a newer snapshot is not mistaken for an older
        # statement by the ingestion idempotency guard.
        statement_id=f"BRK-{prefix}-{as_of.isoformat()}",
        account_id=account_id,
        user_id=user_id,
        period_start=shift_from_historical(date(2024, 1, 1), as_of),
        period_end=as_of,
        holdings_symbols=holdings_symbols,
        lots=lots,
        sales=sales,
        dividends=dividends,
        purchases=purchases,
    )


def _apply_current_demo_window_dates(
    as_of: date,
    dividends: list[DividendSpec],
    purchases: list[PurchaseSpec],
    *,
    wash_symbol: str,
) -> tuple[list[DividendSpec], list[PurchaseSpec]]:
    wash_day = as_of + timedelta(days=WASH_EQUITY_REINVEST_OFFSET_DAYS)
    crypto_day = as_of + timedelta(days=CRYPTO_SCHEDULED_BUY_OFFSET_DAYS)
    patched_purchases: list[PurchaseSpec] = []
    for purchase in purchases:
        if purchase.symbol.split("/")[0] == wash_symbol and purchase.is_reinvestment:
            purchase = replace(purchase, event_date=wash_day)
        elif purchase.is_scheduled_crypto:
            purchase = replace(purchase, event_date=crypto_day)
        patched_purchases.append(purchase)
    reinvest_dates = {purchase.symbol: purchase.event_date for purchase in patched_purchases if purchase.is_reinvestment}
    patched_dividends: list[DividendSpec] = []
    for div in dividends:
        if div.reinvested and div.symbol in reinvest_dates:
            div = replace(div, event_date=reinvest_dates[div.symbol])
        patched_dividends.append(div)
    return patched_dividends, patched_purchases


def _portfolio_a_lots() -> list[LotSpec]:
    q = Decimal
    # Quotes: VTI 200, QQQ 400, VXUS 55, BTC 60000, ETH 3300, BND 72, AAPL 185, MSFT 400, AGG 96, SPY 500, SOL 135
    return [
        _lot("A-VTI-APPROVED", "VTI", date(2023, 1, 10), q("12"), q("250.00"), "approved"),
        _lot("A-QQQ-APPROVED", "QQQ", date(2023, 2, 10), q("10"), q("533.33"), "approved"),
        _lot("A-VXUS-APPROVED", "VXUS", date(2022, 6, 1), q("40"), q("67.07"), "approved"),
        _lot("A-BTC-APPROVED", "BTC", date(2023, 3, 1), q("0.05"), q("85714.29"), "approved"),
        _lot("A-ETH-APPROVED", "ETH", date(2023, 4, 1), q("0.8"), q("4230.77"), "approved"),
        _lot("A-SPY-WASH", "SPY", date(2023, 1, 15), q("8"), q("620.00"), "wash"),
        _lot("A-SPY-WASH2", "SPY", date(2023, 2, 15), q("5"), q("600.00"), "wash"),
        _lot("A-DOGE-REBUY", "DOGE", date(2023, 6, 1), q("10000"), q("0.20"), "crypto_rebuy"),
        _lot("A-BND-RISK", "BND", date(2022, 1, 1), q("400"), q("90.00"), "risk_bond"),
        _lot("A-AAPL-RISK", "AAPL", date(2022, 8, 1), q("80"), q("230.00"), "risk_crypto_weight"),
        _lot("A-MSFT-THRESH", "MSFT", date(2023, 8, 1), q("2"), q("408.16"), "below_threshold"),
        _lot("A-ETH-THRESH", "ETH", date(2023, 9, 1), q("0.3"), q("3367.35"), "below_threshold"),
        _lot("A-MSFT-IDENTICAL", "MSFT", date(2023, 1, 5), q("12"), q("512.50"), "replacement_identical"),
        _lot("A-SOL-UNKNOWN", "SOL", date(2023, 2, 1), q("15"), q("180.00"), "replacement_unknown"),
        _lot("A-QQQ-NOBASIS", "QQQ", date(2023, 3, 1), q("5"), None, "missing_basis", missing=True),
        _lot("A-VTI-NOBASIS", "VTI", date(2023, 4, 1), q("6"), None, "missing_basis", missing=True),
        _lot("A-BND-PROFIT", "BND", date(2022, 3, 1), q("20"), q("60.00"), "profitable"),
        _lot("A-SPY-PROFIT", "SPY", date(2022, 4, 1), q("6"), q("420.00"), "profitable"),
        _lot("A-SCHB-MIRROR", "SCHB", date(2023, 1, 20), q("40"), q("80.00"), "insufficient_mirror"),
        _lot("A-AGG-STALE", "AGG", date(2023, 2, 20), q("30"), q("120.00"), "stale_quote"),
        _lot("A-QQQ-EXTRA", "QQQ", date(2021, 6, 1), q("4"), q("300.00"), "multiple_lot"),
        _lot("A-VTI-EXTRA", "VTI", date(2021, 7, 1), q("5"), q("180.00"), "multiple_lot"),
        _lot("A-BTC-EXTRA", "BTC", date(2021, 8, 1), q("0.02"), q("40000.00"), "multiple_lot"),
        _lot("A-AAPL-EXTRA", "AAPL", date(2021, 9, 1), q("10"), q("150.00"), "multiple_lot"),
        _lot("A-VXUS-EXTRA", "VXUS", date(2021, 10, 1), q("10"), q("45.00"), "multiple_lot"),
    ]


def _portfolio_b_lots() -> list[LotSpec]:
    q = Decimal
    return [
        _lot("B-QQQ-APPROVED", "QQQ", date(2023, 1, 10), q("12"), q("533.33"), "approved"),
        _lot("B-SCHD-APPROVED", "SCHD", date(2023, 2, 10), q("25"), q("100.00"), "approved"),
        _lot("B-VTI-APPROVED", "VTI", date(2022, 6, 1), q("15"), q("250.00"), "approved"),
        _lot("B-BTC-APPROVED", "BTC", date(2023, 3, 1), q("0.06"), q("85714.29"), "approved"),
        _lot("B-ETH-APPROVED", "ETH", date(2023, 4, 1), q("1.0"), q("4230.77"), "approved"),
        _lot("B-VNQ-WASH", "VNQ", date(2023, 5, 1), q("8"), q("105.00"), "wash"),
        _lot("B-VNQ-WASH2", "VNQ", date(2023, 6, 1), q("4"), q("100.00"), "wash"),
        _lot("B-DOGE-REBUY", "DOGE", date(2023, 6, 1), q("8000"), q("0.20"), "crypto_rebuy"),
        _lot("B-BND-RISK", "BND", date(2022, 1, 1), q("30"), q("90.00"), "risk_bond"),
        _lot("B-TSLA-RISK", "TSLA", date(2022, 8, 1), q("90"), q("240.00"), "risk_crypto_weight"),
        _lot("B-IWM-THRESH", "IWM", date(2023, 8, 1), q("2"), q("204.08"), "below_threshold"),
        _lot("B-ETH-THRESH", "ETH", date(2023, 9, 1), q("0.2"), q("3367.35"), "below_threshold"),
        _lot("B-TSLA-PROHIBITED", "TSLA", date(2023, 1, 5), q("8"), q("240.00"), "replacement_prohibited"),
        _lot("B-SOL-UNKNOWN", "SOL", date(2023, 2, 1), q("18"), q("180.00"), "replacement_unknown"),
        _lot("B-NVDA-NOBASIS", "NVDA", date(2023, 3, 1), q("10"), None, "missing_basis", missing=True),
        _lot("B-IWM-NOBASIS", "IWM", date(2023, 4, 1), q("4"), None, "missing_basis", missing=True),
        _lot("B-SCHD-PROFIT", "SCHD", date(2022, 3, 1), q("20"), q("64.00"), "profitable"),
        _lot("B-VNQ-PROFIT", "VNQ", date(2022, 4, 1), q("10"), q("70.00"), "profitable"),
        _lot("B-SCHA-MIRROR", "SCHA", date(2023, 1, 20), q("40"), q("60.00"), "insufficient_mirror"),
        _lot("B-NVDA-STALE", "NVDA", date(2023, 2, 20), q("20"), q("150.00"), "stale_quote"),
        _lot("B-QQQ-EXTRA", "QQQ", date(2021, 6, 1), q("5"), q("300.00"), "multiple_lot"),
        _lot("B-VTI-EXTRA", "VTI", date(2021, 7, 1), q("6"), q("180.00"), "multiple_lot"),
        _lot("B-BTC-EXTRA", "BTC", date(2021, 8, 1), q("0.02"), q("40000.00"), "multiple_lot"),
        _lot("B-NVDA-EXTRA", "NVDA", date(2021, 9, 1), q("8"), q("90.00"), "multiple_lot"),
        _lot("B-VTI-EXTRA2", "VTI", date(2021, 10, 1), q("4"), q("160.00"), "multiple_lot"),
    ]


def _portfolio_a_sales() -> list[SaleSpec]:
    q = Decimal
    d = date
    return [
        _sale("A-SALE-STG1", "VTI", d(2023, 8, 1), d(2024, 2, 10), q("20"), q("240.00"), q("200.00"), HoldingPeriod.SHORT_TERM),  # +800
        _sale("A-SALE-STG2", "QQQ", d(2023, 9, 1), d(2024, 3, 10), q("10"), q("470.00"), q("400.00"), HoldingPeriod.SHORT_TERM),  # +700
        _sale("A-SALE-STG3", "AAPL", d(2023, 10, 1), d(2024, 4, 2), q("20"), q("200.00"), q("170.00"), HoldingPeriod.SHORT_TERM),  # +600
        _sale("A-SALE-STG4", "MSFT", d(2023, 11, 1), d(2024, 5, 2), q("10"), q("430.00"), q("380.00"), HoldingPeriod.SHORT_TERM),  # +500
        _sale("A-SALE-LTG1", "SPY", d(2022, 1, 5), d(2024, 3, 15), q("10"), q("540.00"), q("400.00"), HoldingPeriod.LONG_TERM),  # +1400
        _sale("A-SALE-LTG2", "VTI", d(2022, 2, 5), d(2024, 4, 15), q("20"), q("255.00"), q("200.00"), HoldingPeriod.LONG_TERM),  # +1100
        _sale("A-SALE-LTG3", "QQQ", d(2022, 3, 5), d(2024, 5, 15), q("10"), q("490.00"), q("400.00"), HoldingPeriod.LONG_TERM),  # +900
        _sale("A-SALE-LTG4", "VXUS", d(2022, 4, 5), d(2024, 1, 20), q("40"), q("70.00"), q("50.00"), HoldingPeriod.LONG_TERM),  # +800
        _sale("A-SALE-STL1", "AAPL", d(2023, 12, 1), d(2024, 4, 20), q("10"), q("180.00"), q("220.00"), HoldingPeriod.SHORT_TERM),  # -400
        _sale("A-SALE-STL2", "MSFT", d(2024, 1, 8), d(2024, 5, 20), q("5"), q("390.00"), q("460.00"), HoldingPeriod.SHORT_TERM),  # -350
        _sale("A-SALE-STL3", "AGG", d(2023, 12, 10), d(2024, 5, 8), q("20"), q("90.00"), q("105.00"), HoldingPeriod.SHORT_TERM),  # -300
        _sale("A-SALE-LTL1", "BND", d(2022, 1, 8), d(2024, 2, 20), q("25"), q("70.00"), q("90.00"), HoldingPeriod.LONG_TERM),  # -500
        _sale("A-SALE-LTL2", "SPY", d(2022, 2, 8), d(2024, 3, 20), q("5"), q("480.00"), q("560.00"), HoldingPeriod.LONG_TERM),  # -400
        _sale("A-SALE-LTL3", "VXUS", d(2022, 3, 8), d(2024, 4, 20), q("25"), q("52.00"), q("66.00"), HoldingPeriod.LONG_TERM),  # -350
        _sale("A-SALE-BE", "AGG", d(2023, 1, 12), d(2024, 5, 12), q("10"), q("97.00"), q("93.00"), HoldingPeriod.LONG_TERM),  # +40 near BE relative to others
        _sale("A-SALE-X1", "QQQ", d(2023, 7, 1), d(2024, 1, 12), q("5"), q("460.00"), q("400.00"), HoldingPeriod.SHORT_TERM),  # +300
        _sale("A-SALE-X2", "VTI", d(2022, 5, 1), d(2024, 2, 28), q("8"), q("225.00"), q("200.00"), HoldingPeriod.LONG_TERM),  # +200
        _sale("A-SALE-X3", "AAPL", d(2023, 8, 12), d(2024, 3, 1), q("4"), q("185.00"), q("195.00"), HoldingPeriod.SHORT_TERM),  # -40
    ]


def _portfolio_b_sales() -> list[SaleSpec]:
    q = Decimal
    d = date
    return [
        _sale("B-SALE-STG1", "QQQ", d(2023, 8, 1), d(2024, 2, 10), q("10"), q("470.00"), q("400.00"), HoldingPeriod.SHORT_TERM),  # +700
        _sale("B-SALE-STG2", "NVDA", d(2023, 9, 1), d(2024, 3, 10), q("20"), q("130.00"), q("100.00"), HoldingPeriod.SHORT_TERM),  # +600
        _sale("B-SALE-STG3", "TSLA", d(2023, 10, 1), d(2024, 4, 2), q("10"), q("200.00"), q("150.00"), HoldingPeriod.SHORT_TERM),  # +500
        _sale("B-SALE-STG4", "IWM", d(2023, 11, 1), d(2024, 5, 2), q("8"), q("210.00"), q("160.00"), HoldingPeriod.SHORT_TERM),  # +400
        _sale("B-SALE-LTG1", "VTI", d(2022, 1, 5), d(2024, 3, 15), q("15"), q("260.00"), q("180.00"), HoldingPeriod.LONG_TERM),  # +1200
        _sale("B-SALE-LTG2", "SCHD", d(2022, 2, 5), d(2024, 4, 15), q("20"), q("90.00"), q("50.00"), HoldingPeriod.LONG_TERM),  # +800
        _sale("B-SALE-LTG3", "VNQ", d(2022, 3, 5), d(2024, 5, 15), q("15"), q("90.00"), q("50.00"), HoldingPeriod.LONG_TERM),  # +600
        _sale("B-SALE-LTG4", "QQQ", d(2022, 4, 5), d(2024, 1, 20), q("8"), q("450.00"), q("375.00"), HoldingPeriod.LONG_TERM),  # +600
        _sale("B-SALE-STL1", "TSLA", d(2023, 12, 1), d(2024, 4, 20), q("8"), q("170.00"), q("220.00"), HoldingPeriod.SHORT_TERM),  # -400
        _sale("B-SALE-STL2", "NVDA", d(2024, 1, 8), d(2024, 5, 20), q("10"), q("110.00"), q("140.00"), HoldingPeriod.SHORT_TERM),  # -300
        _sale("B-SALE-STL3", "IWM", d(2023, 12, 10), d(2024, 5, 8), q("6"), q("190.00"), q("230.00"), HoldingPeriod.SHORT_TERM),  # -240
        _sale("B-SALE-LTL1", "VNQ", d(2022, 1, 8), d(2024, 2, 20), q("12"), q("75.00"), q("110.00"), HoldingPeriod.LONG_TERM),  # -420
        _sale("B-SALE-LTL2", "SCHD", d(2022, 2, 8), d(2024, 3, 20), q("10"), q("70.00"), q("105.00"), HoldingPeriod.LONG_TERM),  # -350
        _sale("B-SALE-LTL3", "VTI", d(2022, 3, 8), d(2024, 4, 20), q("8"), q("190.00"), q("230.00"), HoldingPeriod.LONG_TERM),  # -320
        _sale("B-SALE-BE", "IWM", d(2023, 1, 12), d(2024, 5, 12), q("5"), q("201.00"), q("198.00"), HoldingPeriod.LONG_TERM),  # +15
        _sale("B-SALE-X1", "QQQ", d(2023, 7, 1), d(2024, 1, 12), q("4"), q("455.00"), q("400.00"), HoldingPeriod.SHORT_TERM),  # +220
        _sale("B-SALE-X2", "NVDA", d(2022, 5, 1), d(2024, 2, 28), q("6"), q("125.00"), q("95.00"), HoldingPeriod.LONG_TERM),  # +180
        _sale("B-SALE-X3", "TSLA", d(2023, 8, 12), d(2024, 3, 1), q("3"), q("175.00"), q("190.00"), HoldingPeriod.SHORT_TERM),  # -45
    ]


def _portfolio_a_income() -> tuple[list[DividendSpec], list[PurchaseSpec]]:
    dividends = [
        DividendSpec("A-DIV-VTI-1", *catalog("VTI")[:2], date(2024, 3, 20), Decimal("45.00"), True, Decimal("0.18")),
        DividendSpec("A-DIV-SPY-1", *catalog("SPY")[:2], date(2024, 6, 5), Decimal("38.00"), True, Decimal("0.07")),
        DividendSpec("A-DIV-QQQ-1", *catalog("QQQ")[:2], date(2024, 3, 22), Decimal("22.00"), True, Decimal("0.05")),
        DividendSpec("A-DIV-BND-1", *catalog("BND")[:2], date(2024, 4, 2), Decimal("30.00"), True, Decimal("0.40")),
        DividendSpec("A-DIV-VXUS-C", *catalog("VXUS")[:2], date(2024, 3, 25), Decimal("18.00"), False, None),
        DividendSpec("A-DIV-AGG-C", *catalog("AGG")[:2], date(2024, 4, 8), Decimal("12.00"), False, None),
        DividendSpec("A-DIV-MSFT-C", *catalog("MSFT")[:2], date(2024, 5, 10), Decimal("9.00"), False, None),
        DividendSpec("A-DIV-AAPL-C", *catalog("AAPL")[:2], date(2024, 5, 16), Decimal("8.00"), False, None),
    ]
    purchases = [
        PurchaseSpec("A-BUY-VTI-REINV", *catalog("VTI")[:2], date(2024, 3, 20), Decimal("0.18"), Decimal("200.00"), True, False),
        PurchaseSpec("A-BUY-SPY-REINV", *catalog("SPY")[:2], date(2024, 6, 5), Decimal("0.07"), Decimal("500.00"), True, False),
        PurchaseSpec("A-BUY-QQQ-REINV", *catalog("QQQ")[:2], date(2024, 3, 22), Decimal("0.05"), Decimal("400.00"), True, False),
        PurchaseSpec("A-BUY-BND-REINV", *catalog("BND")[:2], date(2024, 4, 2), Decimal("0.40"), Decimal("72.00"), True, False),
        PurchaseSpec("A-BUY-MSFT-ORD", *catalog("MSFT")[:2], date(2024, 1, 15), Decimal("4"), Decimal("380.00"), False, False),
        PurchaseSpec("A-BUY-AAPL-ORD", *catalog("AAPL")[:2], date(2024, 2, 15), Decimal("5"), Decimal("175.00"), False, False),
        PurchaseSpec("A-BUY-DOGE-SCHED", *catalog("DOGE")[:2], date(2024, 6, 8), Decimal("500"), Decimal("0.14"), False, True),
    ]
    return dividends, purchases


def _portfolio_b_income() -> tuple[list[DividendSpec], list[PurchaseSpec]]:
    dividends = [
        DividendSpec("B-DIV-QQQ-1", *catalog("QQQ")[:2], date(2024, 3, 20), Decimal("40.00"), True, Decimal("0.09")),
        DividendSpec("B-DIV-VTI-1", *catalog("VTI")[:2], date(2024, 6, 5), Decimal("35.00"), True, Decimal("0.14")),
        DividendSpec("B-DIV-SCHD-1", *catalog("SCHD")[:2], date(2024, 3, 22), Decimal("28.00"), True, Decimal("0.35")),
        DividendSpec("B-DIV-VNQ-1", *catalog("VNQ")[:2], date(2024, 4, 2), Decimal("20.00"), True, Decimal("0.24")),
        DividendSpec("B-DIV-IWM-C", *catalog("IWM")[:2], date(2024, 3, 25), Decimal("16.00"), False, None),
        DividendSpec("B-DIV-NVDA-C", *catalog("NVDA")[:2], date(2024, 4, 8), Decimal("4.00"), False, None),
        DividendSpec("B-DIV-TSLA-C", *catalog("TSLA")[:2], date(2024, 5, 10), Decimal("3.00"), False, None),
        DividendSpec("B-DIV-BND-C", *catalog("BND")[:2], date(2024, 5, 16), Decimal("11.00"), False, None),
    ]
    purchases = [
        PurchaseSpec("B-BUY-QQQ-REINV", *catalog("QQQ")[:2], date(2024, 3, 20), Decimal("0.09"), Decimal("400.00"), True, False),
        PurchaseSpec("B-BUY-VTI-REINV", *catalog("VTI")[:2], date(2024, 3, 18), Decimal("0.14"), Decimal("200.00"), True, False),
        PurchaseSpec("B-BUY-SCHD-REINV", *catalog("SCHD")[:2], date(2024, 3, 22), Decimal("0.35"), Decimal("80.00"), True, False),
        PurchaseSpec("B-BUY-VNQ-REINV", *catalog("VNQ")[:2], date(2024, 6, 5), Decimal("0.24"), Decimal("84.00"), True, False),
        PurchaseSpec("B-BUY-NVDA-ORD", *catalog("NVDA")[:2], date(2024, 1, 15), Decimal("6"), Decimal("90.00"), False, False),
        PurchaseSpec("B-BUY-IWM-ORD", *catalog("IWM")[:2], date(2024, 2, 15), Decimal("5"), Decimal("190.00"), False, False),
        PurchaseSpec("B-BUY-DOGE-SCHED", *catalog("DOGE")[:2], date(2024, 6, 8), Decimal("400"), Decimal("0.14"), False, True),
    ]
    return dividends, purchases
