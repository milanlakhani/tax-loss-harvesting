from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz

from app.demo_data.pdf_layout import BANK_MARKER, BROKERAGE_MARKER, DEMO_BANNER
from app.domain.enums import ParseErrorCode, StatementFormat
from app.domain.errors import ParseError

MIN_EXTRACTABLE_CHARS = 120
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(slots=True)
class ExtractedPdf:
    text: str
    page_texts: list[str]
    page_count: int


def extract_pdf(data: bytes | Path) -> ExtractedPdf:
    if isinstance(data, Path):
        data = data.read_bytes()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    text = "\n".join(pages)
    if len(re.sub(r"\s+", "", text)) < MIN_EXTRACTABLE_CHARS:
        raise ParseError(
            "Insufficient extractable text; scanned or image-only PDFs are rejected",
            ParseErrorCode.INSUFFICIENT_TEXT,
        )
    return ExtractedPdf(text=text, page_texts=pages, page_count=len(pages))


def detect_format(extracted: ExtractedPdf) -> StatementFormat:
    joined = extracted.text
    has_demo = DEMO_BANNER in joined
    bank = BANK_MARKER in joined
    brokerage = BROKERAGE_MARKER in joined
    if not has_demo:
        raise ParseError("Missing demo banner", ParseErrorCode.UNKNOWN_FORMAT)
    if bank and not brokerage:
        if "---HEADER---" not in joined or "---TRANSACTIONS---" not in joined:
            raise ParseError("Bank marker present but structure missing", ParseErrorCode.MALFORMED_STATEMENT)
        return StatementFormat.SYNTHETIC_BANK_V1
    if brokerage and not bank:
        if "---HEADER---" not in joined or "---TAX_LOTS---" not in joined:
            raise ParseError("Brokerage marker present but structure missing", ParseErrorCode.MALFORMED_STATEMENT)
        return StatementFormat.SYNTHETIC_BROKERAGE_V1
    raise ParseError("Unknown or ambiguous statement format", ParseErrorCode.UNKNOWN_FORMAT)


def require_header_map(lines: list[str], required: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    in_header = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---HEADER---":
            in_header = True
            continue
        if in_header and stripped.startswith("---") and stripped.endswith("---"):
            break
        if in_header and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    missing = [key for key in required if key not in values]
    if missing:
        raise ParseError(f"Missing required headers: {missing}", ParseErrorCode.MISSING_HEADER)
    return values


def parse_decimal(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ParseError(f"Invalid numeric value for {field}: {value}", ParseErrorCode.INVALID_NUMERIC) from exc


def parse_date(value: str, field: str) -> date:
    if not ISO_DATE.match(value):
        raise ParseError(f"Invalid date for {field}: {value}", ParseErrorCode.INVALID_DATE)
    return date.fromisoformat(value)


def parse_currency(value: str, field: str) -> str:
    if not CURRENCY.match(value):
        raise ParseError(f"Invalid currency for {field}: {value}", ParseErrorCode.INVALID_CURRENCY)
    return value


def validate_page_continuity(extracted: ExtractedPdf, marker: str) -> None:
    for i, page_text in enumerate(extracted.page_texts):
        expected = f"PAGE {i + 1} OF {extracted.page_count}"
        if expected not in page_text:
            raise ParseError(
                f"Page continuity failed on page {i + 1}",
                ParseErrorCode.PAGE_CONTINUITY,
            )
        if marker not in page_text or DEMO_BANNER not in page_text:
            raise ParseError(
                f"Format marker or demo banner missing on page {i + 1}",
                ParseErrorCode.MALFORMED_STATEMENT,
            )


def section_rows(lines: list[str], section: str) -> tuple[list[str], list[list[str]]]:
    capturing = False
    header: list[str] | None = None
    rows: list[list[str]] = []
    start = f"---{section}---"
    for line in lines:
        stripped = line.strip()
        if stripped == start:
            capturing = True
            continue
        if capturing and stripped.startswith("---") and stripped.endswith("---"):
            break
        if capturing and stripped:
            if stripped.startswith("PAGE ") or stripped.startswith("FORMAT:") or stripped.startswith("DEMO DATA"):
                continue
            if header is None:
                header = stripped.split("|")
            else:
                rows.append(stripped.split("|"))
    if header is None:
        raise ParseError(f"Missing section {section}", ParseErrorCode.MALFORMED_STATEMENT)
    return header, rows


def assert_columns(header: list[str], expected: list[str], section: str) -> None:
    if header != expected:
        raise ParseError(
            f"Column mismatch in {section}: {header} != {expected}",
            ParseErrorCode.COLUMN_MISMATCH,
        )


def assert_row_width(row: list[str], expected: int, section: str) -> None:
    if len(row) != expected:
        raise ParseError(
            f"Row field count {len(row)} != {expected} in {section}",
            ParseErrorCode.ROW_FIELD_COUNT,
        )


def line_source_page(extracted: ExtractedPdf, needle: str) -> int:
    for i, page_text in enumerate(extracted.page_texts):
        if needle in page_text:
            return i + 1
    return 1


def to_utc_date(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)
