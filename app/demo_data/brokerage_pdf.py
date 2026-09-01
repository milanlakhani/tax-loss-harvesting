from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.demo_data.pdf_layout import BROKERAGE_MARKER, PagedTextDocument
from app.domain.enums import AssetType, HoldingPeriod


@dataclass(slots=True)
class HoldingSpec:
    canonical_id: str
    symbol: str
    asset_type: AssetType
    quantity: Decimal
    name: str


@dataclass(slots=True)
class LotSpec:
    lot_id: str
    canonical_id: str
    symbol: str
    asset_type: AssetType
    acquisition_date: date
    original_quantity: Decimal
    remaining_quantity: Decimal
    per_unit_basis: Decimal | None
    remaining_basis: Decimal | None
    statement_value: Decimal | None
    currency: str = "USD"
    missing_basis: bool = False
    purpose: str = ""


@dataclass(slots=True)
class SaleSpec:
    transaction_id: str
    canonical_id: str
    symbol: str
    asset_type: AssetType
    acquisition_date: date
    sale_date: date
    quantity: Decimal
    sale_price: Decimal
    proceeds: Decimal
    allocated_basis: Decimal
    realized_result: Decimal
    holding_period: HoldingPeriod
    currency: str = "USD"


@dataclass(slots=True)
class DividendSpec:
    transaction_id: str
    canonical_id: str
    symbol: str
    event_date: date
    amount: Decimal
    reinvested: bool
    quantity: Decimal | None = None


@dataclass(slots=True)
class PurchaseSpec:
    transaction_id: str
    canonical_id: str
    symbol: str
    event_date: date
    quantity: Decimal
    price: Decimal
    is_reinvestment: bool = False
    is_scheduled_crypto: bool = False


@dataclass(slots=True)
class RealizedSummary:
    st_gains: Decimal
    st_losses: Decimal
    st_net: Decimal
    lt_gains: Decimal
    lt_losses: Decimal
    lt_net: Decimal
    combined_net: Decimal


@dataclass(slots=True)
class BrokerageStatementSpec:
    statement_id: str
    account_id: UUID
    portfolio_id: UUID
    user_id: UUID
    period_start: date
    period_end: date
    is_taxable: bool
    base_currency: str
    holdings: list[HoldingSpec]
    lots: list[LotSpec]
    sales: list[SaleSpec]
    dividends: list[DividendSpec]
    purchases: list[PurchaseSpec]
    realized: RealizedSummary
    extra: dict = field(default_factory=dict)


def render_brokerage_pdf(spec: BrokerageStatementSpec) -> bytes:
    doc = PagedTextDocument(BROKERAGE_MARKER)
    doc.writeln("---HEADER---")
    doc.writeln(f"statement_id={spec.statement_id}")
    doc.writeln(f"account_id={spec.account_id}")
    doc.writeln(f"portfolio_id={spec.portfolio_id}")
    doc.writeln(f"user_id={spec.user_id}")
    doc.writeln(f"period_start={spec.period_start.isoformat()}")
    doc.writeln(f"period_end={spec.period_end.isoformat()}")
    doc.writeln(f"taxable={str(spec.is_taxable).upper()}")
    doc.writeln(f"base_currency={spec.base_currency}")
    doc.writeln("---HOLDINGS---")
    doc.writeln("CANONICAL_ID|SYMBOL|ASSET_TYPE|QUANTITY|NAME")
    for h in spec.holdings:
        doc.writeln(f"{h.canonical_id}|{h.symbol}|{h.asset_type.value}|{h.quantity}|{h.name}")
    doc.writeln("---TAX_LOTS---")
    doc.writeln(
        "LOT_ID|CANONICAL_ID|SYMBOL|ASSET_TYPE|ACQUIRED|ORIG_QTY|REMAIN_QTY|UNIT_BASIS|REMAIN_BASIS|STMT_VALUE|CCY|MISSING_BASIS"
    )
    for lot in spec.lots:
        unit = "" if lot.per_unit_basis is None else f"{lot.per_unit_basis:.4f}"
        remain = "" if lot.remaining_basis is None else f"{lot.remaining_basis:.2f}"
        value = "" if lot.statement_value is None else f"{lot.statement_value:.2f}"
        doc.writeln(
            "|".join(
                [
                    lot.lot_id,
                    lot.canonical_id,
                    lot.symbol,
                    lot.asset_type.value,
                    lot.acquisition_date.isoformat(),
                    str(lot.original_quantity),
                    str(lot.remaining_quantity),
                    unit,
                    remain,
                    value,
                    lot.currency,
                    str(lot.missing_basis).upper(),
                ]
            )
        )
    doc.writeln("---SALES---")
    doc.writeln(
        "TXN_ID|CANONICAL_ID|SYMBOL|ASSET_TYPE|ACQUIRED|SOLD|QTY|PRICE|PROCEEDS|BASIS|RESULT|PERIOD|CCY"
    )
    for s in spec.sales:
        doc.writeln(
            "|".join(
                [
                    s.transaction_id,
                    s.canonical_id,
                    s.symbol,
                    s.asset_type.value,
                    s.acquisition_date.isoformat(),
                    s.sale_date.isoformat(),
                    str(s.quantity),
                    f"{s.sale_price:.4f}",
                    f"{s.proceeds:.2f}",
                    f"{s.allocated_basis:.2f}",
                    f"{s.realized_result:.2f}",
                    s.holding_period.value,
                    s.currency,
                ]
            )
        )
    doc.writeln("---DIVIDENDS---")
    doc.writeln("TXN_ID|CANONICAL_ID|SYMBOL|DATE|AMOUNT|REINVESTED|QTY")
    for d in spec.dividends:
        qty = "" if d.quantity is None else str(d.quantity)
        doc.writeln(
            f"{d.transaction_id}|{d.canonical_id}|{d.symbol}|{d.event_date.isoformat()}|{d.amount:.2f}|{str(d.reinvested).upper()}|{qty}"
        )
    doc.writeln("---PURCHASES---")
    doc.writeln("TXN_ID|CANONICAL_ID|SYMBOL|DATE|QTY|PRICE|REINVEST|SCHEDULED_CRYPTO")
    for p in spec.purchases:
        doc.writeln(
            f"{p.transaction_id}|{p.canonical_id}|{p.symbol}|{p.event_date.isoformat()}|{p.quantity}|{p.price:.4f}|{str(p.is_reinvestment).upper()}|{str(p.is_scheduled_crypto).upper()}"
        )
    r = spec.realized
    doc.writeln("---REALIZED_SUMMARY---")
    doc.writeln(f"st_gains={r.st_gains:.2f}")
    doc.writeln(f"st_losses={r.st_losses:.2f}")
    doc.writeln(f"st_net={r.st_net:.2f}")
    doc.writeln(f"lt_gains={r.lt_gains:.2f}")
    doc.writeln(f"lt_losses={r.lt_losses:.2f}")
    doc.writeln(f"lt_net={r.lt_net:.2f}")
    doc.writeln(f"combined_net={r.combined_net:.2f}")
    doc.writeln("---END---")
    return doc.finalize()
