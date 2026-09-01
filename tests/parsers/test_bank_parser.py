from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import fitz
import pytest

from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.pdf_layout import BANK_MARKER, DEMO_BANNER
from app.domain.enums import ParseErrorCode
from app.domain.errors import ParseError
from app.parsers.bank import parse_bank_pdf
from app.parsers.common import extract_pdf


@pytest.mark.parser
def test_bank_parser_reconciles_generated_statements():
    statements, _labels = build_bank_statements()
    assert len(statements) == 6
    for spec in statements:
        parsed = parse_bank_pdf(render_bank_pdf(spec))
        assert parsed.external_statement_id == spec.statement_id
        assert len(parsed.transactions) >= 75
        assert parsed.closing_balance == spec.closing_balance
        assert parsed.transactions[-1].running_balance == spec.closing_balance
        assert all(row.parsing_confidence > 0 for row in parsed.transactions)
        assert all(row.source_page >= 1 for row in parsed.transactions)


@pytest.mark.parser
def test_rejects_unknown_format_and_banner_only():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), DEMO_BANNER)
    data = doc.tobytes()
    doc.close()
    with pytest.raises(ParseError) as exc:
        parse_bank_pdf(data)
    assert exc.value.parse_code in {ParseErrorCode.UNKNOWN_FORMAT, ParseErrorCode.INSUFFICIENT_TEXT, ParseErrorCode.MALFORMED_STATEMENT}


@pytest.mark.parser
def test_rejects_scanned_insufficient_text():
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    with pytest.raises(ParseError) as exc:
        extract_pdf(data)
    assert exc.value.parse_code is ParseErrorCode.INSUFFICIENT_TEXT


@pytest.mark.parser
def test_original_amount_not_replaced_for_fx():
    statements, _ = build_bank_statements()
    spec = statements[0]
    parsed = parse_bank_pdf(render_bank_pdf(spec))
    fx_rows = [r for r in parsed.transactions if r.original_currency != "USD"]
    assert fx_rows
    for row in fx_rows:
        assert row.original_currency in {"GBP", "EUR"}
        assert row.converted_base_amount != 0
        orig = next(t for t in spec.transactions if t.transaction_id == row.external_transaction_id)
        assert row.original_amount == orig.original_amount
