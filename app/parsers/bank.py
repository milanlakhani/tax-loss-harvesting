from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.demo_data.bank_pdf import BANK_COLUMNS
from app.demo_data.pdf_layout import BANK_MARKER
from app.domain.enums import DebitCredit, StatementFormat, TransactionType
from app.domain.errors import ParseError
from app.domain.enums import ParseErrorCode
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

BANK_HEADERS = [
    "statement_id",
    "account_id",
    "user_id",
    "period_start",
    "period_end",
    "base_currency",
    "opening_balance",
    "closing_balance",
    "transaction_count",
]


@dataclass(slots=True)
class ParsedBankTransaction:
    external_transaction_id: str
    txn_date: datetime
    event_time: datetime | None
    description: str
    merchant: str
    category: str
    txn_type: str
    original_amount: Decimal
    original_currency: str
    direction: str
    converted_base_amount: Decimal
    running_balance: Decimal
    country: str | None
    source_page: int
    parsing_confidence: Decimal


@dataclass(slots=True)
class ParsedBankStatement:
    format: StatementFormat
    external_statement_id: str
    account_id: UUID
    user_id: UUID
    period_start: datetime
    period_end: datetime
    base_currency: str
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[ParsedBankTransaction]
    parsing_confidence: Decimal


def parse_bank_pdf(data: bytes) -> ParsedBankStatement:
    extracted = extract_pdf(data)
    fmt = detect_format(extracted)
    if fmt != StatementFormat.SYNTHETIC_BANK_V1:
        raise ParseError("Not a bank statement", ParseErrorCode.UNKNOWN_FORMAT)
    validate_page_continuity(extracted, BANK_MARKER)
    lines = [line for line in extracted.text.splitlines() if line.strip()]
    header = require_header_map(lines, BANK_HEADERS)
    columns, rows = section_rows(lines, "TRANSACTIONS")
    assert_columns(columns, BANK_COLUMNS, "TRANSACTIONS")
    expected_count = int(header["transaction_count"])
    if len(rows) != expected_count:
        raise ParseError(
            f"Transaction count {len(rows)} != header {expected_count}",
            ParseErrorCode.MALFORMED_STATEMENT,
        )
    opening = parse_decimal(header["opening_balance"], "opening_balance")
    closing = parse_decimal(header["closing_balance"], "closing_balance")
    parsed_rows: list[ParsedBankTransaction] = []
    previous_balance = opening
    base_ccy = parse_currency(header["base_currency"], "base_currency")
    for row in rows:
        assert_row_width(row, len(BANK_COLUMNS), "TRANSACTIONS")
        mapped = dict(zip(BANK_COLUMNS, row, strict=True))
        amount = parse_decimal(mapped["ORIG_AMT"], "ORIG_AMT")
        if amount <= 0:
            raise ParseError("Amounts must be positive magnitudes", ParseErrorCode.INVALID_NUMERIC)
        direction = DebitCredit(mapped["DIRECTION"])
        txn_date = parse_date(mapped["DATE"], "DATE")
        event_time = None
        if mapped["TIME"]:
            hour, minute, second = (int(p) for p in mapped["TIME"].split(":"))
            event_time = datetime(
                txn_date.year,
                txn_date.month,
                txn_date.day,
                hour,
                minute,
                second,
                tzinfo=to_utc_date(txn_date).tzinfo,
            )
        parsed_balance = parse_decimal(mapped["RUNNING_BAL"], "RUNNING_BAL")
        delta = parsed_balance - previous_balance
        if direction is DebitCredit.CREDIT and delta <= 0:
            raise ParseError(
                f"Credit must increase running balance for {mapped['TXN_ID']}",
                ParseErrorCode.RECONCILIATION_FAILED,
            )
        if direction is DebitCredit.DEBIT and delta >= 0:
            raise ParseError(
                f"Debit must decrease running balance for {mapped['TXN_ID']}",
                ParseErrorCode.RECONCILIATION_FAILED,
            )
        orig_ccy = parse_currency(mapped["ORIG_CCY"], "ORIG_CCY")
        if orig_ccy == base_ccy and abs(delta) != amount:
            raise ParseError(
                f"Base-currency amount mismatch for {mapped['TXN_ID']}",
                ParseErrorCode.RECONCILIATION_FAILED,
            )
        previous_balance = parsed_balance
        parsed_rows.append(
            ParsedBankTransaction(
                external_transaction_id=mapped["TXN_ID"],
                txn_date=to_utc_date(txn_date),
                event_time=event_time,
                description=mapped["DESCRIPTION"],
                merchant=mapped["MERCHANT"],
                category=mapped["CATEGORY"],
                txn_type=TransactionType(mapped["TYPE"]).value,
                original_amount=amount,
                original_currency=orig_ccy,
                direction=direction.value,
                converted_base_amount=abs(delta),
                running_balance=parsed_balance,
                country=mapped["COUNTRY"] or None,
                source_page=line_source_page(extracted, mapped["TXN_ID"]),
                parsing_confidence=Decimal("0.990000"),
            )
        )
    if previous_balance != closing:
        raise ParseError(
            f"Closing balance {closing} does not match reconciled {running}",
            ParseErrorCode.RECONCILIATION_FAILED,
        )
    return ParsedBankStatement(
        format=fmt,
        external_statement_id=header["statement_id"],
        account_id=UUID(header["account_id"]),
        user_id=UUID(header["user_id"]),
        period_start=to_utc_date(parse_date(header["period_start"], "period_start")),
        period_end=to_utc_date(parse_date(header["period_end"], "period_end")),
        base_currency=parse_currency(header["base_currency"], "base_currency"),
        opening_balance=opening,
        closing_balance=closing,
        transactions=parsed_rows,
        parsing_confidence=Decimal("0.990000"),
    )
