from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DebitCredit, TransactionType
from app.domain.results import StatisticalResult
from app.persistence.models import BankTransaction

LOW_CONFIDENCE = Decimal("0.85")
INCOME_TYPES = {TransactionType.DEPOSIT.value, TransactionType.INTEREST.value}
SPENDING_DIRECTION = DebitCredit.DEBIT.value


def _base_query(user_id: UUID) -> Select:
    return select(BankTransaction).where(BankTransaction.user_id == user_id)


def _in_range(stmt: Select, start: datetime | None, end: datetime | None) -> Select:
    if start is not None:
        stmt = stmt.where(BankTransaction.txn_date >= start)
    if end is not None:
        stmt = stmt.where(BankTransaction.txn_date <= end)
    return stmt


def _amount(row: BankTransaction) -> Decimal | None:
    return row.converted_amount


def _meta(rows: list[BankTransaction]) -> tuple[bool, str | None, tuple[UUID, ...], tuple[UUID, ...]]:
    low = any(row.parsing_confidence < LOW_CONFIDENCE for row in rows)
    warning = "One or more rows have low parsing confidence" if low else None
    statements = tuple(sorted({row.statement_id for row in rows}, key=str))
    accounts = tuple(sorted({row.account_id for row in rows}, key=str))
    return low, warning, statements, accounts


def _result(
    rows: list[BankTransaction],
    value: Decimal | None,
    currency: str | None,
    converted: bool,
    breakdown: dict[str, Decimal] | None = None,
) -> StatisticalResult:
    low, warning, statements, accounts = _meta(rows)
    dates = [row.txn_date for row in rows]
    return StatisticalResult(
        value=value,
        currency=currency,
        date_start=min(dates) if dates else None,
        date_end=max(dates) if dates else None,
        transaction_count=len(rows),
        statement_ids=statements,
        account_ids=accounts,
        low_confidence=low,
        warning=warning,
        breakdown=breakdown or {},
        converted=converted,
    )


class StatisticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load(
        self,
        user_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        search: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
    ) -> list[BankTransaction]:
        stmt = _in_range(_base_query(user_id), start, end)
        if category:
            stmt = stmt.where(BankTransaction.category == category)
        if merchant:
            stmt = stmt.where(BankTransaction.normalized_merchant == merchant)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                BankTransaction.description.ilike(pattern)
                | BankTransaction.normalized_merchant.ilike(pattern)
            )
        rows = list(await self.session.scalars(stmt.order_by(BankTransaction.txn_date, BankTransaction.id)))
        return rows

    def _sum_converted(self, rows: list[BankTransaction]) -> tuple[Decimal | None, str | None, bool, str | None]:
        currencies = {row.original_currency for row in rows}
        if not rows:
            return Decimal("0"), None, False, None
        if all(row.converted_amount is not None for row in rows):
            total = sum((row.converted_amount or Decimal("0") for row in rows), Decimal("0"))
            bases = {row.base_currency for row in rows}
            currency = next(iter(bases)) if len(bases) == 1 else None
            converted = any(row.original_currency != row.base_currency for row in rows)
            return total, currency, converted, None
        if len(currencies) > 1:
            return None, None, False, "Cross-currency total omitted because conversion data is missing"
        total = sum((row.original_amount for row in rows), Decimal("0"))
        return total, next(iter(currencies)), False, None

    async def spending_summary(self, user_id: UUID, start=None, end=None) -> StatisticalResult:
        rows = [r for r in await self._load(user_id, start, end) if r.direction == SPENDING_DIRECTION]
        value, ccy, converted, warning = self._sum_converted(rows)
        result = _result(rows, value, ccy, converted)
        if warning:
            result = StatisticalResult(**{**result.__dict__, "warning": warning, "value": value})
        return result

    async def income_summary(self, user_id: UUID, start=None, end=None) -> StatisticalResult:
        rows = [
            r
            for r in await self._load(user_id, start, end)
            if r.direction == DebitCredit.CREDIT.value
            and (r.txn_type in INCOME_TYPES or r.category in {"INCOME", "PAYROLL", "INTEREST"})
        ]
        value, ccy, converted, warning = self._sum_converted(rows)
        result = _result(rows, value, ccy, converted)
        if warning:
            result = StatisticalResult(**{**result.__dict__, "warning": warning})
        return result

    async def cash_flow_summary(self, user_id: UUID, start=None, end=None) -> StatisticalResult:
        rows = await self._load(user_id, start, end)
        credits = [r for r in rows if r.direction == DebitCredit.CREDIT.value]
        debits = [r for r in rows if r.direction == DebitCredit.DEBIT.value]
        c_val, ccy, converted, warning = self._sum_converted(credits)
        d_val, _, _, _ = self._sum_converted(debits)
        net = None if c_val is None or d_val is None else c_val - d_val
        breakdown = {}
        if c_val is not None:
            breakdown["credits"] = c_val
        if d_val is not None:
            breakdown["debits"] = d_val
        result = _result(rows, net, ccy, converted, breakdown)
        if warning:
            result = StatisticalResult(**{**result.__dict__, "warning": warning, "value": None if warning else net})
        return result

    async def spending_period_comparison(
        self,
        user_id: UUID,
        current_start: datetime,
        current_end: datetime,
        prior_start: datetime,
        prior_end: datetime,
    ) -> StatisticalResult:
        current = await self.spending_summary(user_id, current_start, current_end)
        prior = await self.spending_summary(user_id, prior_start, prior_end)
        delta = None
        if current.value is not None and prior.value is not None:
            delta = current.value - prior.value
        return StatisticalResult(
            value=delta,
            currency=current.currency,
            date_start=current_start,
            date_end=current_end,
            transaction_count=current.transaction_count + prior.transaction_count,
            statement_ids=tuple(sorted(set(current.statement_ids + prior.statement_ids), key=str)),
            account_ids=tuple(sorted(set(current.account_ids + prior.account_ids), key=str)),
            low_confidence=current.low_confidence or prior.low_confidence,
            warning=current.warning or prior.warning,
            breakdown={
                "current": current.value or Decimal("0"),
                "prior": prior.value or Decimal("0"),
            },
            converted=current.converted or prior.converted,
        )

    async def category_breakdown(self, user_id: UUID, start=None, end=None) -> StatisticalResult:
        rows = [r for r in await self._load(user_id, start, end) if r.direction == SPENDING_DIRECTION]
        grouped: dict[str, list[BankTransaction]] = defaultdict(list)
        for row in rows:
            grouped[row.category].append(row)
        breakdown: dict[str, Decimal] = {}
        warning = None
        for category, group in grouped.items():
            value, _, _, warn = self._sum_converted(group)
            if value is None:
                warning = warn
            else:
                breakdown[category] = value
        total = sum(breakdown.values(), Decimal("0")) if breakdown else None
        ccy = rows[0].base_currency if rows and all(r.converted_amount is not None for r in rows) else None
        return _result(rows, total, ccy, True, breakdown) if not warning else StatisticalResult(
            **{**_result(rows, None, None, False, breakdown).__dict__, "warning": warning}
        )

    async def merchant_summary(self, user_id: UUID, start=None, end=None) -> StatisticalResult:
        rows = [r for r in await self._load(user_id, start, end) if r.direction == SPENDING_DIRECTION]
        grouped: dict[str, list[BankTransaction]] = defaultdict(list)
        for row in rows:
            grouped[row.normalized_merchant].append(row)
        breakdown = {}
        for merchant, group in grouped.items():
            value, _, _, _ = self._sum_converted(group)
            if value is not None:
                breakdown[merchant] = value
        total = sum(breakdown.values(), Decimal("0")) if breakdown else Decimal("0")
        ccy = rows[0].base_currency if rows else None
        return _result(rows, total, ccy, True, breakdown)

    async def largest_transactions(self, user_id: UUID, start=None, end=None, limit: int = 10) -> StatisticalResult:
        rows = await self._load(user_id, start, end)
        ranked = sorted(rows, key=lambda r: r.converted_amount or Decimal("0"), reverse=True)[:limit]
        breakdown = {r.external_transaction_id: r.converted_amount or r.original_amount for r in ranked}
        top = ranked[0].converted_amount if ranked else Decimal("0")
        ccy = ranked[0].base_currency if ranked else None
        return _result(ranked, top, ccy, True, breakdown)

    async def account_balance_history(self, user_id: UUID, account_id: UUID | None = None) -> StatisticalResult:
        stmt = _base_query(user_id).order_by(BankTransaction.txn_date, BankTransaction.id)
        if account_id is not None:
            stmt = stmt.where(BankTransaction.account_id == account_id)
        rows = list(await self.session.scalars(stmt))
        breakdown = {r.external_transaction_id: r.running_balance for r in rows}
        last = rows[-1].running_balance if rows else Decimal("0")
        ccy = rows[-1].base_currency if rows else None
        return _result(rows, last, ccy, True, breakdown)

    async def text_search(self, user_id: UUID, query: str, start=None, end=None) -> StatisticalResult:
        rows = await self._load(user_id, start, end, search=query)
        value, ccy, converted, warning = self._sum_converted(rows)
        result = _result(rows, value, ccy, converted)
        if warning:
            result = StatisticalResult(**{**result.__dict__, "warning": warning, "value": None})
        return result
