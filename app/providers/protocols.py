from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Quote:
    def __init__(
        self,
        *,
        canonical_id: str,
        price: Decimal,
        currency: str,
        provider: str,
        provider_asset_id: str,
        source_timestamp: datetime,
        retrieved_at: datetime,
        is_mocked: bool,
        tradable: bool = True,
        stale: bool = False,
        asset_type: str | None = None,
        symbol: str | None = None,
    ) -> None:
        self.canonical_id = canonical_id
        self.price = price
        self.currency = currency
        self.provider = provider
        self.provider_asset_id = provider_asset_id
        self.source_timestamp = ensure_utc(source_timestamp)
        self.retrieved_at = ensure_utc(retrieved_at)
        self.is_mocked = is_mocked
        self.tradable = tradable
        self.stale = stale
        self.asset_type = asset_type
        self.symbol = symbol


class FxRate:
    def __init__(
        self,
        *,
        base: str,
        quote: str,
        rate: Decimal,
        requested_date: date,
        effective_date: date,
        provider: str,
        source_timestamp: datetime,
        retrieved_at: datetime,
        is_mocked: bool,
    ) -> None:
        self.base = base
        self.quote = quote
        self.rate = rate
        self.requested_date = requested_date
        self.effective_date = effective_date
        self.provider = provider
        self.source_timestamp = ensure_utc(source_timestamp)
        self.retrieved_at = ensure_utc(retrieved_at)
        self.is_mocked = is_mocked


class PriceObservation:
    def __init__(
        self,
        *,
        canonical_id: str,
        currency: str,
        price: Decimal,
        observed_at: datetime,
        provider: str,
        is_mocked: bool,
    ) -> None:
        self.canonical_id = canonical_id
        self.currency = currency
        self.price = price
        self.observed_at = ensure_utc(observed_at)
        self.provider = provider
        self.is_mocked = is_mocked


class ExecutionPosition:
    def __init__(
        self,
        *,
        account_alias: str,
        symbol: str,
        quantity: Decimal,
        tradable: bool,
        asset_class: str,
    ) -> None:
        self.account_alias = account_alias
        self.symbol = symbol
        self.quantity = quantity
        self.tradable = tradable
        self.asset_class = asset_class


@runtime_checkable
class EquityQuoteProvider(Protocol):
    provider_name: str

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None: ...

    async def get_price_history(
        self,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]: ...


@runtime_checkable
class CryptoQuoteProvider(Protocol):
    provider_name: str

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None: ...

    async def get_price_history(
        self,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]: ...


@runtime_checkable
class FxProvider(Protocol):
    provider_name: str

    async def get_rate(self, base: str, quote_ccy: str, on: date) -> FxRate | None: ...


class SubmittedOrder:
    def __init__(
        self,
        *,
        client_order_id: str,
        provider_order_id: str,
        status: str,
        symbol: str,
        quantity: Decimal,
        filled_qty: Decimal | None = None,
        fill_price: Decimal | None = None,
        asset_class: str | None = None,
        submitted_at: datetime | None = None,
    ) -> None:
        self.client_order_id = client_order_id
        self.provider_order_id = provider_order_id
        self.status = status
        self.symbol = symbol
        self.quantity = quantity
        self.filled_qty = filled_qty
        self.fill_price = fill_price
        self.asset_class = asset_class
        self.submitted_at = submitted_at


@runtime_checkable
class ExecutionProvider(Protocol):
    provider_name: str

    async def is_tradable(self, symbol: str, asset_class: str) -> bool: ...

    async def available_quantity(self, account_alias: str, symbol: str) -> Decimal: ...

    async def get_position(self, account_alias: str, symbol: str) -> ExecutionPosition | None: ...

    async def list_positions(self, account_alias: str) -> list[ExecutionPosition]: ...

    async def submit_market_sell(
        self,
        *,
        account_alias: str,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        asset_class: str,
    ) -> SubmittedOrder: ...

    async def get_order(self, account_alias: str, provider_order_id: str) -> SubmittedOrder | None: ...

    async def provider_asset_class(self, symbol: str) -> str | None: ...


class ProviderRouter:
    """Routes asset-class requests to the configured fake or live adapters."""

    def __init__(
        self,
        *,
        equity: EquityQuoteProvider,
        crypto: CryptoQuoteProvider,
        fx: FxProvider,
        execution: ExecutionProvider,
    ) -> None:
        self.equity = equity
        self.crypto = crypto
        self.fx = fx
        self.execution = execution

    async def quote_for_asset_type(
        self,
        asset_type: str,
        canonical_id: str,
        symbol: str,
        as_of: datetime,
    ) -> Quote | None:
        if asset_type in {"EQUITY", "ETF"}:
            return await self.equity.get_quote(canonical_id, symbol, as_of)
        if asset_type == "CRYPTO":
            return await self.crypto.get_quote(canonical_id, symbol, as_of)
        return None

    async def history_for_asset_type(
        self,
        asset_type: str,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]:
        if asset_type in {"EQUITY", "ETF"}:
            return await self.equity.get_price_history(canonical_id, symbol, start, end)
        if asset_type == "CRYPTO":
            return await self.crypto.get_price_history(canonical_id, symbol, start, end)
        return []
