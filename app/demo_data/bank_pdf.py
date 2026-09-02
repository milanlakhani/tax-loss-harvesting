from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.demo_data.pdf_layout import BANK_MARKER, PagedTextDocument
from app.domain.enums import DebitCredit, TransactionType

BANK_COLUMNS = [
    "TXN_ID",
    "DATE",
    "TIME",
    "DESCRIPTION",
    "MERCHANT",
    "CATEGORY",
    "TYPE",
    "ORIG_AMT",
    "ORIG_CCY",
    "DIRECTION",
    "RUNNING_BAL",
    "COUNTRY",
]


@dataclass(slots=True)
class BankStatementSpec:
    statement_id: str
    account_id: UUID
    user_id: UUID
    period_start: date
    period_end: date
    base_currency: str
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[BankRowSpec]


@dataclass(slots=True)
class BankRowSpec:
    transaction_id: str
    txn_date: date
    event_time: datetime | None
    description: str
    merchant: str
    category: str
    txn_type: TransactionType
    original_amount: Decimal
    original_currency: str
    direction: DebitCredit
    running_balance: Decimal
    country: str | None = None


def render_bank_pdf(spec: BankStatementSpec) -> bytes:
    doc = PagedTextDocument(BANK_MARKER)
    doc.writeln("---HEADER---")
    doc.writeln(f"statement_id={spec.statement_id}")
    doc.writeln(f"account_id={spec.account_id}")
    doc.writeln(f"user_id={spec.user_id}")
    doc.writeln(f"period_start={spec.period_start.isoformat()}")
    doc.writeln(f"period_end={spec.period_end.isoformat()}")
    doc.writeln(f"base_currency={spec.base_currency}")
    doc.writeln(f"opening_balance={spec.opening_balance:.2f}")
    doc.writeln(f"closing_balance={spec.closing_balance:.2f}")
    doc.writeln(f"transaction_count={len(spec.transactions)}")
    doc.writeln("---TRANSACTIONS---")
    doc.writeln("|".join(BANK_COLUMNS))
    for row in spec.transactions:
        time_part = row.event_time.strftime("%H:%M:%S") if row.event_time else ""
        country = row.country or ""
        fields = [
            row.transaction_id,
            row.txn_date.isoformat(),
            time_part,
            _clean(row.description),
            _clean(row.merchant),
            row.category,
            row.txn_type.value,
            f"{row.original_amount:.2f}",
            row.original_currency,
            row.direction.value,
            f"{row.running_balance:.2f}",
            country,
        ]
        doc.writeln("|".join(fields))
    doc.writeln("---END---")
    return doc.finalize()


def _clean(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")
