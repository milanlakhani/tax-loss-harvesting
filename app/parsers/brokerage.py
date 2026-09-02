from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.demo_data.pdf_layout import BROKERAGE_MARKER
from app.domain.enums import AssetType, HoldingPeriod, ParseErrorCode, StatementFormat
from app.domain.errors import ParseError
from app.parsers.common import (
    ExtractedPdf,
    assert_columns,
    assert_row_width,
    detect_format,
    extract_pdf,
    line_source_page,
    parse_currency,
    parse_date,
    parse_decimal,
    require_header_map,
    section_rows,
    to_utc_date,
    validate_page_continuity,
)

HOLDING_COLS = ["CANONICAL_ID", "SYMBOL", "ASSET_TYPE", "QUANTITY", "NAME"]
LOT_COLS = [
    "LOT_ID",
    "CANONICAL_ID",
    "SYMBOL",
    "ASSET_TYPE",
    "ACQUIRED",
    "ORIG_QTY",
    "REMAIN_QTY",
    "UNIT_BASIS",
    "REMAIN_BASIS",
    "STMT_VALUE",
    "CCY",
    "MISSING_BASIS",
]
SALE_COLS = [
    "TXN_ID",
    "CANONICAL_ID",
    "SYMBOL",
    "ASSET_TYPE",
    "ACQUIRED",
    "SOLD",
    "QTY",
    "PRICE",
    "PROCEEDS",
    "BASIS",
    "RESULT",
    "PERIOD",
    "CCY",
]
DIV_COLS = ["TXN_ID", "CANONICAL_ID", "SYMBOL", "DATE", "AMOUNT", "REINVESTED", "QTY"]
PURCHASE_COLS = [
    "TXN_ID",
    "CANONICAL_ID",
    "SYMBOL",
    "DATE",
    "QTY",
    "PRICE",
    "REINVEST",
    "SCHEDULED_CRYPTO",
]
BROKERAGE_HEADERS = [
    "statement_id",
    "account_id",
    "portfolio_id",
    "user_id",
    "period_start",
    "period_end",
    "taxable",
    "base_currency",
]


@dataclass(slots=True)
class ParsedHolding:
    canonical_id: str
    symbol: str
    asset_type: str
    quantity: Decimal
    name: str
    source_page: int
    parsing_confidence: Decimal


@dataclass(slots=True)
class ParsedLot:
    lot_id: str
    canonical_id: str
    symbol: str
    asset_type: str
    acquisition_date: datetime
    original_quantity: Decimal
    remaining_quantity: Decimal
    per_unit_basis: Decimal | None
    remaining_basis: Decimal | None
    statement_value: Decimal | None
    currency: str
    missing_basis: bool
    source_page: int
    parsing_confidence: Decimal


@dataclass(slots=True)
class ParsedSale:
    transaction_id: str
    canonical_id: str
    symbol: str
    asset_type: str
    acquisition_date: datetime
    sale_date: datetime
    quantity: Decimal
    sale_price: Decimal
    proceeds: Decimal
    allocated_basis: Decimal
    realized_result: Decimal
    holding_period: str
    currency: str
    source_page: int
    parsing_confidence: Decimal


@dataclass(slots=True)
class ParsedDividend:
    transaction_id: str
    canonical_id: str
    symbol: str
    event_date: datetime
    amount: Decimal
    reinvested: bool
    quantity: Decimal | None
    source_page: int
    parsing_confidence: Decimal


@dataclass(slots=True)
class ParsedPurchase:
    transaction_id: str
    canonical_id: str
    symbol: str
    event_date: datetime
    quantity: Decimal
    price: Decimal
    is_reinvestment: bool
    is_scheduled_crypto: bool
    source_page: int
    parsing_confidence: Decimal


@dataclass(slots=True)
class ParsedRealizedSummary:
    st_gains: Decimal
    st_losses: Decimal
    st_net: Decimal
    lt_gains: Decimal
    lt_losses: Decimal
    lt_net: Decimal
    combined_net: Decimal


@dataclass(slots=True)
class ParsedBrokerageStatement:
    format: StatementFormat
    external_statement_id: str
    account_id: UUID
    portfolio_id: UUID
    user_id: UUID
    period_start: datetime
    period_end: datetime
    is_taxable: bool
    base_currency: str
    holdings: list[ParsedHolding]
    lots: list[ParsedLot]
    sales: list[ParsedSale]
    dividends: list[ParsedDividend]
    purchases: list[ParsedPurchase]
    realized: ParsedRealizedSummary
    parsing_confidence: Decimal


def parse_brokerage_pdf(data: bytes) -> ParsedBrokerageStatement:
    extracted = extract_pdf(data)
    fmt = detect_format(extracted)
    if fmt != StatementFormat.SYNTHETIC_BROKERAGE_V1:
        raise ParseError("Not a brokerage statement", ParseErrorCode.UNKNOWN_FORMAT)
    validate_page_continuity(extracted, BROKERAGE_MARKER)
    lines = [line for line in extracted.text.splitlines() if line.strip()]
    header = require_header_map(lines, BROKERAGE_HEADERS)
    conf = Decimal("0.990000")
    holdings = _parse_holdings(extracted, lines, conf)
    lots = _parse_lots(extracted, lines, conf)
    sales = _parse_sales(extracted, lines, conf)
    dividends = _parse_dividends(extracted, lines, conf)
    purchases = _parse_purchases(extracted, lines, conf)
    realized = _parse_realized(lines, sales)
    return ParsedBrokerageStatement(
        format=fmt,
        external_statement_id=header["statement_id"],
        account_id=UUID(header["account_id"]),
        portfolio_id=UUID(header["portfolio_id"]),
        user_id=UUID(header["user_id"]),
        period_start=to_utc_date(parse_date(header["period_start"], "period_start")),
        period_end=to_utc_date(parse_date(header["period_end"], "period_end")),
        is_taxable=header["taxable"] == "TRUE",
        base_currency=parse_currency(header["base_currency"], "base_currency"),
        holdings=holdings,
        lots=lots,
        sales=sales,
        dividends=dividends,
        purchases=purchases,
        realized=realized,
        parsing_confidence=conf,
    )


def _parse_holdings(extracted: ExtractedPdf, lines: list[str], conf: Decimal) -> list[ParsedHolding]:
    header, rows = section_rows(lines, "HOLDINGS")
    assert_columns(header, HOLDING_COLS, "HOLDINGS")
    out: list[ParsedHolding] = []
    for row in rows:
        assert_row_width(row, len(HOLDING_COLS), "HOLDINGS")
        m = dict(zip(HOLDING_COLS, row, strict=True))
        out.append(
            ParsedHolding(
                canonical_id=m["CANONICAL_ID"],
                symbol=m["SYMBOL"],
                asset_type=AssetType(m["ASSET_TYPE"]).value,
                quantity=parse_decimal(m["QUANTITY"], "QUANTITY"),
                name=m["NAME"],
                source_page=line_source_page(extracted, m["CANONICAL_ID"]),
                parsing_confidence=conf,
            )
        )
    return out


def _parse_lots(extracted: ExtractedPdf, lines: list[str], conf: Decimal) -> list[ParsedLot]:
    header, rows = section_rows(lines, "TAX_LOTS")
    assert_columns(header, LOT_COLS, "TAX_LOTS")
    out: list[ParsedLot] = []
    for row in rows:
        assert_row_width(row, len(LOT_COLS), "TAX_LOTS")
        m = dict(zip(LOT_COLS, row, strict=True))
        missing = m["MISSING_BASIS"] == "TRUE"
        unit = parse_decimal(m["UNIT_BASIS"], "UNIT_BASIS") if m["UNIT_BASIS"] else None
        remain = parse_decimal(m["REMAIN_BASIS"], "REMAIN_BASIS") if m["REMAIN_BASIS"] else None
        value = parse_decimal(m["STMT_VALUE"], "STMT_VALUE") if m["STMT_VALUE"] else None
        if missing and unit is not None:
            raise ParseError("Missing-basis lot must not include unit basis", ParseErrorCode.MALFORMED_STATEMENT)
        out.append(
            ParsedLot(
                lot_id=m["LOT_ID"],
                canonical_id=m["CANONICAL_ID"],
                symbol=m["SYMBOL"],
                asset_type=AssetType(m["ASSET_TYPE"]).value,
                acquisition_date=to_utc_date(parse_date(m["ACQUIRED"], "ACQUIRED")),
                original_quantity=parse_decimal(m["ORIG_QTY"], "ORIG_QTY"),
                remaining_quantity=parse_decimal(m["REMAIN_QTY"], "REMAIN_QTY"),
                per_unit_basis=unit,
                remaining_basis=remain,
                statement_value=value,
                currency=parse_currency(m["CCY"], "CCY"),
                missing_basis=missing,
                source_page=line_source_page(extracted, m["LOT_ID"]),
                parsing_confidence=conf,
            )
        )
    return out


def _parse_sales(extracted: ExtractedPdf, lines: list[str], conf: Decimal) -> list[ParsedSale]:
    header, rows = section_rows(lines, "SALES")
    assert_columns(header, SALE_COLS, "SALES")
    out: list[ParsedSale] = []
    for row in rows:
        assert_row_width(row, len(SALE_COLS), "SALES")
        m = dict(zip(SALE_COLS, row, strict=True))
        qty = parse_decimal(m["QTY"], "QTY")
        price = parse_decimal(m["PRICE"], "PRICE")
        proceeds = parse_decimal(m["PROCEEDS"], "PROCEEDS")
        basis = parse_decimal(m["BASIS"], "BASIS")
        result = parse_decimal(m["RESULT"], "RESULT")
        if proceeds != qty * price:
            # Allow 2-decimal rounding on proceeds.
            if proceeds.quantize(Decimal("0.01")) != (qty * price).quantize(Decimal("0.01")):
                raise ParseError(f"Sale proceeds mismatch {m['TXN_ID']}", ParseErrorCode.RECONCILIATION_FAILED)
        if result != (proceeds - basis).quantize(Decimal("0.01")) and result != proceeds - basis:
            raise ParseError(f"Sale result mismatch {m['TXN_ID']}", ParseErrorCode.RECONCILIATION_FAILED)
        out.append(
            ParsedSale(
                transaction_id=m["TXN_ID"],
                canonical_id=m["CANONICAL_ID"],
                symbol=m["SYMBOL"],
                asset_type=AssetType(m["ASSET_TYPE"]).value,
                acquisition_date=to_utc_date(parse_date(m["ACQUIRED"], "ACQUIRED")),
                sale_date=to_utc_date(parse_date(m["SOLD"], "SOLD")),
                quantity=qty,
                sale_price=price,
                proceeds=proceeds,
                allocated_basis=basis,
                realized_result=result,
                holding_period=HoldingPeriod(m["PERIOD"]).value,
                currency=parse_currency(m["CCY"], "CCY"),
                source_page=line_source_page(extracted, m["TXN_ID"]),
                parsing_confidence=conf,
            )
        )
    return out


def _parse_dividends(extracted: ExtractedPdf, lines: list[str], conf: Decimal) -> list[ParsedDividend]:
    header, rows = section_rows(lines, "DIVIDENDS")
    assert_columns(header, DIV_COLS, "DIVIDENDS")
    out: list[ParsedDividend] = []
    for row in rows:
        assert_row_width(row, len(DIV_COLS), "DIVIDENDS")
        m = dict(zip(DIV_COLS, row, strict=True))
        out.append(
            ParsedDividend(
                transaction_id=m["TXN_ID"],
                canonical_id=m["CANONICAL_ID"],
                symbol=m["SYMBOL"],
                event_date=to_utc_date(parse_date(m["DATE"], "DATE")),
                amount=parse_decimal(m["AMOUNT"], "AMOUNT"),
                reinvested=m["REINVESTED"] == "TRUE",
                quantity=parse_decimal(m["QTY"], "QTY") if m["QTY"] else None,
                source_page=line_source_page(extracted, m["TXN_ID"]),
                parsing_confidence=conf,
            )
        )
    return out


def _parse_purchases(extracted: ExtractedPdf, lines: list[str], conf: Decimal) -> list[ParsedPurchase]:
    header, rows = section_rows(lines, "PURCHASES")
    assert_columns(header, PURCHASE_COLS, "PURCHASES")
    out: list[ParsedPurchase] = []
    for row in rows:
        assert_row_width(row, len(PURCHASE_COLS), "PURCHASES")
        m = dict(zip(PURCHASE_COLS, row, strict=True))
        out.append(
            ParsedPurchase(
                transaction_id=m["TXN_ID"],
                canonical_id=m["CANONICAL_ID"],
                symbol=m["SYMBOL"],
                event_date=to_utc_date(parse_date(m["DATE"], "DATE")),
                quantity=parse_decimal(m["QTY"], "QTY"),
                price=parse_decimal(m["PRICE"], "PRICE"),
                is_reinvestment=m["REINVEST"] == "TRUE",
                is_scheduled_crypto=m["SCHEDULED_CRYPTO"] == "TRUE",
                source_page=line_source_page(extracted, m["TXN_ID"]),
                parsing_confidence=conf,
            )
        )
    return out


def _parse_realized(lines: list[str], sales: list[ParsedSale]) -> ParsedRealizedSummary:
    values: dict[str, Decimal] = {}
    capturing = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---REALIZED_SUMMARY---":
            capturing = True
            continue
        if capturing and stripped.startswith("---"):
            break
        if capturing and "=" in stripped:
            key, raw = stripped.split("=", 1)
            values[key] = parse_decimal(raw, key)
    required = ["st_gains", "st_losses", "st_net", "lt_gains", "lt_losses", "lt_net", "combined_net"]
    missing = [key for key in required if key not in values]
    if missing:
        raise ParseError(f"Missing realized summary fields {missing}", ParseErrorCode.MISSING_HEADER)
    st_gains = sum((s.realized_result for s in sales if s.holding_period == "SHORT_TERM" and s.realized_result > 0), Decimal("0"))
    st_losses = sum((s.realized_result for s in sales if s.holding_period == "SHORT_TERM" and s.realized_result < 0), Decimal("0"))
    lt_gains = sum((s.realized_result for s in sales if s.holding_period == "LONG_TERM" and s.realized_result > 0), Decimal("0"))
    lt_losses = sum((s.realized_result for s in sales if s.holding_period == "LONG_TERM" and s.realized_result < 0), Decimal("0"))
    if values["st_gains"] != st_gains or values["st_losses"] != st_losses:
        raise ParseError("Short-term realized summary does not match sales", ParseErrorCode.RECONCILIATION_FAILED)
    if values["lt_gains"] != lt_gains or values["lt_losses"] != lt_losses:
        raise ParseError("Long-term realized summary does not match sales", ParseErrorCode.RECONCILIATION_FAILED)
    if values["st_net"] != st_gains + st_losses:
        raise ParseError("Short-term net mismatch", ParseErrorCode.RECONCILIATION_FAILED)
    if values["lt_net"] != lt_gains + lt_losses:
        raise ParseError("Long-term net mismatch", ParseErrorCode.RECONCILIATION_FAILED)
    if values["combined_net"] != values["st_net"] + values["lt_net"]:
        raise ParseError("Combined net mismatch", ParseErrorCode.RECONCILIATION_FAILED)
    return ParsedRealizedSummary(**values)
