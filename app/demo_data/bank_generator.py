from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from random import Random
from uuid import UUID

from app.demo_data.bank_pdf import BankRowSpec, BankStatementSpec
from app.demo_data.constants import (
    BANK_A_ID,
    BANK_B_ID,
    CATEGORIES,
    EUR_USD,
    GBP_USD,
    RECURRING_MERCHANTS,
    SEED,
    USER_A_ID,
    USER_B_ID,
)
from app.domain.enums import DebitCredit, TransactionType

ANOMALY_TYPES = [
    "large_amount",
    "unusual_merchant",
    "category_spike",
    "duplicate",
    "frequency_spike",
    "unusual_time",
    "weekly_spike",
    "international",
    "cash_withdrawal",
]


def month_periods_for_user(user_index: int) -> list[tuple[date, date]]:
    if user_index == 0:
        months = [(2024, 2), (2024, 3), (2024, 4)]
    else:
        months = [(2024, 3), (2024, 4), (2024, 5)]
    periods = []
    for year, month in months:
        last = monthrange(year, month)[1]
        periods.append((date(year, month, 1), date(year, month, last)))
    return periods


def build_bank_statements() -> tuple[list[BankStatementSpec], list[tuple[str, str, str]]]:
    """Return statement specs and ground-truth (txn_id, anomaly_type, reason)."""
    rng = Random(SEED)
    statements: list[BankStatementSpec] = []
    labels: list[tuple[str, str, str]] = []
    users = [
        (USER_A_ID, BANK_A_ID, 0, Decimal("8500.00")),
        (USER_B_ID, BANK_B_ID, 1, Decimal("9200.00")),
    ]
    for user_id, account_id, user_index, opening0 in users:
        opening = opening0
        for month_index, (start, end) in enumerate(month_periods_for_user(user_index)):
            spec, month_labels, closing = _one_statement(
                rng, user_id, account_id, user_index, month_index, start, end, opening
            )
            statements.append(spec)
            labels.extend(month_labels)
            opening = closing
    return statements, labels


def _one_statement(
    rng: Random,
    user_id: UUID,
    account_id: UUID,
    user_index: int,
    month_index: int,
    start: date,
    end: date,
    opening: Decimal,
) -> tuple[BankStatementSpec, list[tuple[str, str, str]], Decimal]:
    stmt_id = f"BANK-{user_index}-{start.isoformat()}"
    rows: list[BankRowSpec] = []
    labels: list[tuple[str, str, str]] = []
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return f"TXN-{user_index}-{start.strftime('%Y%m')}-{seq:04d}"

    def add(
        day: date,
        description: str,
        merchant: str,
        category: str,
        txn_type: TransactionType,
        amount: Decimal,
        currency: str,
        direction: DebitCredit,
        event_time: datetime | None = None,
        country: str | None = "US",
    ) -> BankRowSpec:
        row = BankRowSpec(
            transaction_id=next_id(),
            txn_date=day,
            event_time=event_time,
            description=description,
            merchant=merchant,
            category=category,
            txn_type=txn_type,
            original_amount=amount,
            original_currency=currency,
            direction=direction,
            running_balance=Decimal("0"),
            country=country,
        )
        rows.append(row)
        return row

    # Recurring income and bills.
    add(date(start.year, start.month, 1), "Salary", "ACME PAYROLL", "INCOME", TransactionType.DEPOSIT, Decimal("4500.00"), "USD", DebitCredit.CREDIT, _time(start, 9, 0))
    payday2 = min(date(start.year, start.month, 15), end)
    add(payday2, "Salary", "ACME PAYROLL", "INCOME", TransactionType.DEPOSIT, Decimal("4500.00"), "USD", DebitCredit.CREDIT, _time(payday2, 9, 0))
    add(date(start.year, start.month, 1), "Monthly rent", "RENTCO HOUSING", "HOUSING", TransactionType.PAYMENT, Decimal("1800.00"), "USD", DebitCredit.DEBIT, _time(start, 8, 0))
    add(date(start.year, start.month, 5), "Electric and gas", "UTILITYCO ENERGY", "UTILITIES", TransactionType.PAYMENT, Decimal("120.00"), "USD", DebitCredit.DEBIT)
    add(date(start.year, start.month, 7), "Streaming", "STREAMFLIX", "SUBSCRIPTIONS", TransactionType.PAYMENT, Decimal("15.99"), "USD", DebitCredit.DEBIT)
    add(date(start.year, start.month, 8), "Renter insurance", "INSURECO", "INSURANCE", TransactionType.PAYMENT, Decimal("95.00"), "USD", DebitCredit.DEBIT)

    # Weekday transit and cafe, weekly groceries/restaurants.
    day = start
    grocery_days = 0
    while day <= end:
        if day.weekday() < 5:
            add(day, "Transit fare", "TRANSITCO", "TRANSPORT", TransactionType.PAYMENT, Decimal("4.50"), "USD", DebitCredit.DEBIT, _time(day, 8, 15))
            if day.weekday() in {1, 3}:
                add(day, "Coffee", "CAFECO", "RESTAURANTS", TransactionType.PAYMENT, Decimal("12.50"), "USD", DebitCredit.DEBIT, _time(day, 7, 40))
        if day.weekday() == 5:
            grocery_days += 1
            add(day, "Weekly groceries", "GROCERYCO", "GROCERIES", TransactionType.PAYMENT, Decimal("88.00") + Decimal(day.day % 5), "USD", DebitCredit.DEBIT)
            add(day, "Dinner out", "CAFECO", "RESTAURANTS", TransactionType.PAYMENT, Decimal("44.00"), "USD", DebitCredit.DEBIT, _time(day, 19, 10))
        if day.weekday() == 2:
            add(day, "Household goods", "SHOPMART", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("36.00") + Decimal(day.day % 7), "USD", DebitCredit.DEBIT)
        day += timedelta(days=1)

    add(date(start.year, start.month, min(12, end.day)), "Internal transfer", "SELF TRANSFER", "TRANSFER", TransactionType.TRANSFER, Decimal("200.00"), "USD", DebitCredit.DEBIT)
    add(date(start.year, start.month, min(18, end.day)), "ATM cash", "CITYBANK ATM", "WITHDRAWAL", TransactionType.WITHDRAWAL, Decimal("80.00"), "USD", DebitCredit.DEBIT)

    # FX: 4 GBP and 4 EUR per month.
    gbp_days = [min(start + timedelta(days=offset), end) for offset in (3, 9, 16, 22)]
    eur_days = [min(start + timedelta(days=offset), end) for offset in (4, 11, 18, 24)]
    for d in gbp_days:
        add(d, "UK grocery", "TESCO UK", "GROCERIES", TransactionType.PAYMENT, Decimal("70.00"), "GBP", DebitCredit.DEBIT, country="GB")
    for d in eur_days:
        add(d, "EU grocery", "CARREFOUR EU", "GROCERIES", TransactionType.PAYMENT, Decimal("65.00"), "EUR", DebitCredit.DEBIT, country="FR")

    # Refunds: month 0 gets 2, others 1 → 4 over 3 months.
    refund_count = 2 if month_index == 0 else 1
    for i in range(refund_count):
        d = min(start + timedelta(days=6 + i * 5), end)
        add(d, "Merchant refund", "SHOPMART", "REFUND", TransactionType.REFUND, Decimal("36.00"), "USD", DebitCredit.CREDIT)

    # Fee / interest: one of each over months plus extra fee in month 2.
    if month_index == 0:
        add(end, "Account interest", "CITYBANK INTEREST", "INTEREST", TransactionType.INTEREST, Decimal("2.15"), "USD", DebitCredit.CREDIT)
    elif month_index == 1:
        add(end, "Monthly fee", "CITYBANK FEE", "FEE", TransactionType.FEE, Decimal("5.00"), "USD", DebitCredit.DEBIT)
    else:
        add(end, "Wire fee", "CITYBANK FEE", "FEE", TransactionType.FEE, Decimal("15.00"), "USD", DebitCredit.DEBIT)

    # Fill to at least 75 with tight discretionary spend.
    filler = 0
    cursor = start + timedelta(days=2)
    while len(rows) < 78:
        if cursor > end:
            cursor = start + timedelta(days=1)
        if cursor.weekday() < 6:
            amt = Decimal("22.00") + Decimal(filler % 4)
            add(cursor, "Misc store", "SHOPMART", "DISCRETIONARY", TransactionType.PAYMENT, amt, "USD", DebitCredit.DEBIT, _time(cursor, 12, 20))
            filler += 1
        cursor += timedelta(days=1)

    # Inject three extreme anomalies after the normal pattern exists.
    anomaly_plan = ANOMALY_TYPES[month_index * 3 : month_index * 3 + 3]
    for kind in anomaly_plan:
        row, reason = _inject_anomaly(add, start, end, month_index, kind)
        labels.append((row.transaction_id, kind, reason))

    rows.sort(key=lambda r: (r.txn_date, r.event_time or datetime(r.txn_date.year, r.txn_date.month, r.txn_date.day, tzinfo=UTC), r.transaction_id))
    running = opening
    for row in rows:
        converted = _to_usd(row.original_amount, row.original_currency)
        if row.direction is DebitCredit.CREDIT:
            running += converted
        else:
            running -= converted
        row.running_balance = running

    spec = BankStatementSpec(
        statement_id=stmt_id,
        account_id=account_id,
        user_id=user_id,
        period_start=start,
        period_end=end,
        base_currency="USD",
        opening_balance=opening,
        closing_balance=running,
        transactions=rows,
    )
    _ = rng
    _ = CATEGORIES
    _ = RECURRING_MERCHANTS
    return spec, labels, running


def _inject_anomaly(add, start: date, end: date, month_index: int, kind: str):
    day = min(start + timedelta(days=10 + month_index), end)
    if kind == "large_amount":
        row = add(day, "One-off luxury", "LUXURY UNKNOWN LLC", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("8200.00"), "USD", DebitCredit.DEBIT, _time(day, 14, 5), country="US")
        return row, "Extreme unseen merchant debit"
    if kind == "unusual_merchant":
        row = add(day, "Rare vendor", "ZEBRA YACHT RENTALS", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("5100.00"), "USD", DebitCredit.DEBIT, _time(day, 16, 40))
        return row, "Unusual merchant and large amount"
    if kind == "category_spike":
        row = add(day, "Category blowout", "MEGA DISCRETIONARY CO", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("4300.00"), "USD", DebitCredit.DEBIT)
        return row, "Category spending spike"
    if kind == "duplicate":
        row = add(date(start.year, start.month, 1), "Monthly rent duplicate", "RENTCO HOUSING", "HOUSING", TransactionType.PAYMENT, Decimal("1800.00"), "USD", DebitCredit.DEBIT, _time(start, 10, 5))
        return row, "Duplicate rent payment"
    if kind == "frequency_spike":
        row = add(day, "Burst payment", "BURST PAY VENDOR", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("2800.00"), "USD", DebitCredit.DEBIT, _time(day, 11, 0))
        return row, "Intraday frequency spike"
    if kind == "unusual_time":
        row = add(day, "Overnight wire", "NIGHTWIRE GLOBAL", "TRANSFER", TransactionType.TRANSFER, Decimal("3600.00"), "USD", DebitCredit.DEBIT, _time(day, 3, 12))
        return row, "Unusual 03:12 transfer"
    if kind == "weekly_spike":
        row = add(day, "Weekly blowout", "WEEKLY SPIKE MART", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("3900.00"), "USD", DebitCredit.DEBIT)
        return row, "Weekly spending spike"
    if kind == "international":
        row = add(day, "Foreign luxury", "BERLIN BOUTIQUE GMBH", "DISCRETIONARY", TransactionType.PAYMENT, Decimal("2800.00"), "EUR", DebitCredit.DEBIT, _time(day, 17, 45), country="DE")
        return row, "International high-value debit"
    row = add(day, "Unexpected large cash", "CITYBANK ATM", "WITHDRAWAL", TransactionType.WITHDRAWAL, Decimal("4500.00"), "USD", DebitCredit.DEBIT, _time(day, 21, 50))
    return row, "Unexpected large cash withdrawal"


def _to_usd(amount: Decimal, currency: str) -> Decimal:
    if currency == "USD":
        return amount
    if currency == "GBP":
        return (amount * GBP_USD).quantize(Decimal("0.01"))
    if currency == "EUR":
        return (amount * EUR_USD).quantize(Decimal("0.01"))
    raise ValueError(currency)


def _time(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
