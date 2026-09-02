from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import StatementStorage
from app.domain.enums import ParseErrorCode, StatementFormat
from app.domain.errors import ParseError
from app.parsers.bank import ParsedBankStatement, parse_bank_pdf
from app.parsers.brokerage import ParsedBrokerageStatement, parse_brokerage_pdf
from app.parsers.common import detect_format, extract_pdf
from app.persistence.models import (
    Asset,
    BankTransaction,
    BrokerageDividend,
    BrokeragePurchase,
    BrokerageSale,
    Holding,
    ProviderAssetMapping,
    Statement,
    TaxLot,
)
from app.providers.protocols import FxProvider


@dataclass(slots=True)
class IngestResult:
    statement_id: UUID
    format: StatementFormat
    reused: bool
    transaction_count: int = 0
    lot_count: int = 0
    sale_count: int = 0


class StatementIngestor:
    def __init__(self, storage: StatementStorage, fx: FxProvider) -> None:
        self.storage = storage
        self.fx = fx

    async def ingest(
        self,
        session: AsyncSession,
        data: bytes,
        filename: str,
        *,
        demo_dataset: str | None = None,
    ) -> IngestResult:
        extracted = extract_pdf(data)
        fmt = detect_format(extracted)
        if fmt is StatementFormat.SYNTHETIC_BANK_V1:
            parsed = parse_bank_pdf(data)
            return await self._ingest_bank(session, data, filename, parsed, demo_dataset=demo_dataset)
        if fmt is StatementFormat.SYNTHETIC_BROKERAGE_V1:
            parsed = parse_brokerage_pdf(data)
            return await self._ingest_brokerage(session, data, filename, parsed, demo_dataset=demo_dataset)
        raise ParseError("Unsupported format", ParseErrorCode.UNKNOWN_FORMAT)

    async def _ingest_bank(
        self,
        session: AsyncSession,
        data: bytes,
        filename: str,
        parsed: ParsedBankStatement,
        *,
        demo_dataset: str | None = None,
    ) -> IngestResult:
        existing = await session.scalar(
            select(Statement).where(Statement.external_statement_id == parsed.external_statement_id)
        )
        if existing is not None:
            return IngestResult(
                statement_id=existing.id,
                format=StatementFormat.SYNTHETIC_BANK_V1,
                reused=True,
                transaction_count=len(parsed.transactions),
            )
        path = self.storage.save(filename, data)
        statement = Statement(
            id=uuid4(),
            external_statement_id=parsed.external_statement_id,
            user_id=parsed.user_id,
            account_id=parsed.account_id,
            portfolio_id=None,
            format=parsed.format.value,
            period_start=parsed.period_start,
            period_end=parsed.period_end,
            opening_balance=parsed.opening_balance,
            closing_balance=parsed.closing_balance,
            base_currency=parsed.base_currency,
            source_path=str(path),
            parsing_confidence=parsed.parsing_confidence,
            is_synthetic=True,
            demo_dataset=demo_dataset,
        )
        session.add(statement)
        for row in parsed.transactions:
            dup = await session.scalar(
                select(BankTransaction).where(
                    BankTransaction.external_transaction_id == row.external_transaction_id
                )
            )
            if dup is not None:
                continue
            fx_rate = None
            fx_requested = None
            fx_effective = None
            fx_provider = None
            converted = row.converted_base_amount
            if row.original_currency != parsed.base_currency:
                rate = await self.fx.get_rate(
                    row.original_currency,
                    parsed.base_currency,
                    row.txn_date.date(),
                )
                if rate is not None:
                    fx_rate = rate.rate
                    fx_requested = rate.requested_date
                    fx_effective = rate.effective_date
                    fx_provider = rate.provider
            session.add(
                BankTransaction(
                    id=uuid4(),
                    external_transaction_id=row.external_transaction_id,
                    statement_id=statement.id,
                    account_id=parsed.account_id,
                    user_id=parsed.user_id,
                    txn_date=row.txn_date,
                    event_time=row.event_time,
                    description=row.description,
                    normalized_merchant=row.merchant,
                    category=row.category,
                    txn_type=row.txn_type,
                    original_amount=row.original_amount,
                    original_currency=row.original_currency,
                    direction=row.direction,
                    base_currency=parsed.base_currency,
                    converted_amount=converted,
                    running_balance=row.running_balance,
                    fx_rate=fx_rate,
                    fx_requested_date=fx_requested,
                    fx_effective_date=fx_effective,
                    fx_provider=fx_provider,
                    parsing_confidence=row.parsing_confidence,
                    source_page=row.source_page,
                    country=row.country,
                    is_synthetic=True,
                )
            )
        await session.flush()
        return IngestResult(
            statement_id=statement.id,
            format=StatementFormat.SYNTHETIC_BANK_V1,
            reused=False,
            transaction_count=len(parsed.transactions),
        )

    async def _ingest_brokerage(
        self,
        session: AsyncSession,
        data: bytes,
        filename: str,
        parsed: ParsedBrokerageStatement,
        *,
        demo_dataset: str | None = None,
    ) -> IngestResult:
        existing = await session.scalar(
            select(Statement).where(Statement.external_statement_id == parsed.external_statement_id)
        )
        if existing is not None:
            return IngestResult(
                statement_id=existing.id,
                format=StatementFormat.SYNTHETIC_BROKERAGE_V1,
                reused=True,
                lot_count=len(parsed.lots),
                sale_count=len(parsed.sales),
            )
        path = self.storage.save(filename, data)
        statement = Statement(
            id=uuid4(),
            external_statement_id=parsed.external_statement_id,
            user_id=parsed.user_id,
            account_id=parsed.account_id,
            portfolio_id=parsed.portfolio_id,
            format=parsed.format.value,
            period_start=parsed.period_start,
            period_end=parsed.period_end,
            opening_balance=None,
            closing_balance=None,
            base_currency=parsed.base_currency,
            source_path=str(path),
            parsing_confidence=parsed.parsing_confidence,
            is_synthetic=True,
            demo_dataset=demo_dataset,
        )
        session.add(statement)
        assets_by_canonical: dict[str, Asset] = {}
        for holding in parsed.holdings:
            asset = await self._upsert_asset(session, holding.canonical_id, holding.symbol, holding.asset_type, holding.name)
            assets_by_canonical[holding.canonical_id] = asset
            session.add(
                Holding(
                    id=uuid4(),
                    portfolio_id=parsed.portfolio_id,
                    asset_id=asset.id,
                    quantity=holding.quantity,
                    as_of=parsed.period_end,
                    statement_id=statement.id,
                    is_synthetic=True,
                )
            )
        for lot in parsed.lots:
            asset = assets_by_canonical.get(lot.canonical_id)
            if asset is None:
                asset = await self._upsert_asset(session, lot.canonical_id, lot.symbol, lot.asset_type, lot.symbol)
                assets_by_canonical[lot.canonical_id] = asset
            existing_lot = await session.scalar(select(TaxLot).where(TaxLot.external_lot_id == lot.lot_id))
            if existing_lot is not None:
                continue
            session.add(
                TaxLot(
                    id=uuid4(),
                    external_lot_id=lot.lot_id,
                    portfolio_id=parsed.portfolio_id,
                    account_id=parsed.account_id,
                    asset_id=asset.id,
                    source_statement_id=statement.id,
                    acquisition_date=lot.acquisition_date,
                    original_quantity=lot.original_quantity,
                    remaining_quantity=lot.remaining_quantity,
                    per_unit_basis=lot.per_unit_basis,
                    remaining_basis=lot.remaining_basis,
                    statement_value=lot.statement_value,
                    currency=lot.currency,
                    missing_basis=lot.missing_basis,
                    is_synthetic=True,
                )
            )
        for sale in parsed.sales:
            asset = assets_by_canonical.get(sale.canonical_id) or await self._upsert_asset(
                session, sale.canonical_id, sale.symbol, sale.asset_type, sale.symbol
            )
            existing_sale = await session.scalar(
                select(BrokerageSale).where(BrokerageSale.external_transaction_id == sale.transaction_id)
            )
            if existing_sale is not None:
                continue
            session.add(
                BrokerageSale(
                    id=uuid4(),
                    external_transaction_id=sale.transaction_id,
                    portfolio_id=parsed.portfolio_id,
                    account_id=parsed.account_id,
                    asset_id=asset.id,
                    acquisition_date=sale.acquisition_date,
                    sale_date=sale.sale_date,
                    quantity=sale.quantity,
                    sale_price=sale.sale_price,
                    proceeds=sale.proceeds,
                    allocated_basis=sale.allocated_basis,
                    realized_result=sale.realized_result,
                    holding_period=sale.holding_period,
                    currency=sale.currency,
                    is_synthetic=True,
                )
            )
        for div in parsed.dividends:
            asset = assets_by_canonical.get(div.canonical_id) or await self._upsert_asset(
                session, div.canonical_id, div.symbol, "ETF", div.symbol
            )
            existing_div = await session.scalar(
                select(BrokerageDividend).where(BrokerageDividend.external_transaction_id == div.transaction_id)
            )
            if existing_div is not None:
                continue
            session.add(
                BrokerageDividend(
                    id=uuid4(),
                    external_transaction_id=div.transaction_id,
                    portfolio_id=parsed.portfolio_id,
                    asset_id=asset.id,
                    event_date=div.event_date,
                    amount=div.amount,
                    reinvested=div.reinvested,
                    quantity=div.quantity,
                    currency=parsed.base_currency,
                    is_synthetic=True,
                )
            )
        for purchase in parsed.purchases:
            asset = assets_by_canonical.get(purchase.canonical_id) or await self._upsert_asset(
                session, purchase.canonical_id, purchase.symbol, "ETF", purchase.symbol
            )
            existing_p = await session.scalar(
                select(BrokeragePurchase).where(BrokeragePurchase.external_transaction_id == purchase.transaction_id)
            )
            if existing_p is not None:
                continue
            session.add(
                BrokeragePurchase(
                    id=uuid4(),
                    external_transaction_id=purchase.transaction_id,
                    portfolio_id=parsed.portfolio_id,
                    asset_id=asset.id,
                    event_date=purchase.event_date,
                    quantity=purchase.quantity,
                    price=purchase.price,
                    is_reinvestment=purchase.is_reinvestment,
                    is_scheduled_crypto=purchase.is_scheduled_crypto,
                    currency=parsed.base_currency,
                    is_synthetic=True,
                )
            )
        await session.flush()
        return IngestResult(
            statement_id=statement.id,
            format=StatementFormat.SYNTHETIC_BROKERAGE_V1,
            reused=False,
            lot_count=len(parsed.lots),
            sale_count=len(parsed.sales),
        )

    async def _upsert_asset(
        self,
        session: AsyncSession,
        canonical_id: str,
        symbol: str,
        asset_type: str,
        name: str,
    ) -> Asset:
        asset = await session.scalar(select(Asset).where(Asset.canonical_id == canonical_id))
        if asset is not None:
            return asset
        asset = Asset(
            id=uuid4(),
            canonical_id=canonical_id,
            symbol=symbol,
            asset_type=asset_type,
            name=name,
            is_synthetic=True,
        )
        session.add(asset)
        await session.flush()
        provider = "fake-alpha-vantage" if asset_type in {"EQUITY", "ETF"} else "fake-coingecko"
        if asset_type == "CRYPTO":
            provider = "fake-coingecko"
        session.add(
            ProviderAssetMapping(
                id=uuid4(),
                asset_id=asset.id,
                provider_name=provider,
                provider_symbol=symbol,
                extra={"canonical_id": canonical_id},
            )
        )
        if asset_type in {"EQUITY", "ETF"}:
            session.add(
                ProviderAssetMapping(
                    id=uuid4(),
                    asset_id=asset.id,
                    provider_name="fake-alpaca",
                    provider_symbol=symbol,
                    extra={"asset_class": "us_equity"},
                )
            )
        elif asset_type == "CRYPTO":
            session.add(
                ProviderAssetMapping(
                    id=uuid4(),
                    asset_id=asset.id,
                    provider_name="fake-alpaca",
                    provider_symbol=symbol,
                    extra={"asset_class": "crypto"},
                )
            )
        return asset
