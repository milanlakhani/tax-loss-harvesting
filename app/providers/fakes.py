from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.errors import ProviderError
from app.providers.protocols import (
    CryptoQuoteProvider,
    EquityQuoteProvider,
    ExecutionPosition,
    ExecutionProvider,
    FxProvider,
    FxRate,
    MarketClock,
    MarketSession,
    PriceObservation,
    Quote,
    SubmittedOrder,
)


class LiveProviderAttemptError(RuntimeError):
    """Raised when a test or Phase 1 path attempts a live network provider."""


class FakeEquityQuoteProvider:
    provider_name = "fake-alpha-vantage"

    def __init__(self) -> None:
        self.quotes: dict[str, Quote] = {}
        self.history: dict[str, list[PriceObservation]] = {}
        self.calls: list[tuple[str, str]] = []

    def seed_quote(self, quote: Quote) -> None:
        self.quotes[quote.canonical_id] = quote

    def seed_history(self, canonical_id: str, observations: list[PriceObservation]) -> None:
        self.history[canonical_id] = sorted(observations, key=lambda o: o.observed_at)

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None:
        self.calls.append(("quote", canonical_id, as_of))
        return self.quotes.get(canonical_id)

    async def get_price_history(
        self,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]:
        self.calls.append(("history", canonical_id))
        rows = self.history.get(canonical_id, [])
        return [row for row in rows if start <= row.observed_at <= end]


class FakeCryptoQuoteProvider:
    provider_name = "fake-coingecko"

    def __init__(self) -> None:
        self.quotes: dict[str, Quote] = {}
        self.history: dict[str, list[PriceObservation]] = {}
        self.calls: list[tuple[str, str]] = []

    def seed_quote(self, quote: Quote) -> None:
        self.quotes[quote.canonical_id] = quote

    def seed_history(self, canonical_id: str, observations: list[PriceObservation]) -> None:
        self.history[canonical_id] = sorted(observations, key=lambda o: o.observed_at)

    async def get_quote(self, canonical_id: str, symbol: str, as_of: datetime) -> Quote | None:
        self.calls.append(("quote", canonical_id, as_of))
        return self.quotes.get(canonical_id)

    async def get_price_history(
        self,
        canonical_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[PriceObservation]:
        self.calls.append(("history", canonical_id))
        rows = self.history.get(canonical_id, [])
        return [row for row in rows if start <= row.observed_at <= end]


class FakeFxProvider:
    provider_name = "fake-frankfurter"

    def __init__(self) -> None:
        self.rates: dict[tuple[str, str, date], FxRate] = {}
        self.calls: list[tuple[str, str, date]] = []
        self.fail_after: int | None = None

    def seed_rate(self, rate: FxRate) -> None:
        self.rates[(rate.base, rate.quote, rate.requested_date)] = rate

    def seed_default_majors(self, on: date, retrieved_at: datetime | None = None) -> None:
        retrieved_at = retrieved_at or datetime(2024, 6, 15, tzinfo=UTC)
        defaults = {
            ("GBP", "USD"): Decimal("1.270000"),
            ("EUR", "USD"): Decimal("1.085000"),
            ("USD", "USD"): Decimal("1"),
        }
        for (base, quote), value in defaults.items():
            self.seed_rate(
                FxRate(
                    base=base,
                    quote=quote,
                    rate=value,
                    requested_date=on,
                    effective_date=on,
                    provider=self.provider_name,
                    source_timestamp=datetime(on.year, on.month, on.day, tzinfo=UTC),
                    retrieved_at=retrieved_at,
                    is_mocked=True,
                )
            )

    async def get_rate(self, base: str, quote_ccy: str, on: date) -> FxRate | None:
        self.calls.append((base, quote_ccy, on))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise LiveProviderAttemptError("configured fake FX failure")
        if base == quote_ccy:
            now = datetime(on.year, on.month, on.day, tzinfo=UTC)
            return FxRate(
                base=base,
                quote=quote_ccy,
                rate=Decimal("1"),
                requested_date=on,
                effective_date=on,
                provider=self.provider_name,
                source_timestamp=now,
                retrieved_at=now,
                is_mocked=True,
            )
        return self.rates.get((base, quote_ccy, on))


class FakeExecutionProvider:
    provider_name = "fake-alpaca"

    def __init__(self) -> None:
        self.positions: dict[tuple[str, str], ExecutionPosition] = {}
        self.tradable: dict[str, bool] = {}
        self.asset_classes: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.orders: dict[str, SubmittedOrder] = {}
        self.submit_calls: list[dict] = []
        self.reject_submit: str | None = None
        self.submit_status: str = "SUBMITTED"
        self.market_open: bool = True
        self.clock_timestamp: datetime | None = None
        self.next_open: datetime | None = None
        self.next_close: datetime | None = None
        self.sessions: list[MarketSession] = []
        self.clock_error: str | None = None
        self.calendar_error: str | None = None

    def seed_position(self, position: ExecutionPosition) -> None:
        self.positions[(position.account_alias, position.symbol)] = position
        self.tradable[position.symbol] = position.tradable
        self.asset_classes[position.symbol] = position.asset_class

    def seed_tradable(self, symbol: str, tradable: bool) -> None:
        self.tradable[symbol] = tradable

    def seed_asset_class(self, symbol: str, asset_class: str) -> None:
        self.asset_classes[symbol] = asset_class

    async def is_tradable(self, symbol: str, asset_class: str) -> bool:
        self.calls.append(("tradable", symbol))
        return self.tradable.get(symbol, False)

    async def available_quantity(self, account_alias: str, symbol: str) -> Decimal:
        self.calls.append(("qty", symbol))
        position = self.positions.get((account_alias, symbol))
        return position.quantity if position else Decimal("0")

    async def get_position(self, account_alias: str, symbol: str) -> ExecutionPosition | None:
        self.calls.append(("position", symbol))
        return self.positions.get((account_alias, symbol))

    async def list_positions(self, account_alias: str) -> list[ExecutionPosition]:
        self.calls.append(("list", account_alias))
        return [pos for (alias, _symbol), pos in self.positions.items() if alias == account_alias]

    async def submit_market_sell(
        self,
        *,
        account_alias: str,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        asset_class: str,
    ) -> SubmittedOrder:
        self.calls.append(("submit", symbol))
        payload = {
            "account_alias": account_alias,
            "symbol": symbol,
            "quantity": quantity,
            "client_order_id": client_order_id,
            "asset_class": asset_class,
        }
        self.submit_calls.append(payload)
        if self.reject_submit:
            raise LiveProviderAttemptError(self.reject_submit)
        existing = next((o for o in self.orders.values() if o.client_order_id == client_order_id), None)
        if existing:
            return existing
        order = SubmittedOrder(
            client_order_id=client_order_id,
            provider_order_id=f"alpaca-{client_order_id}",
            status=self.submit_status,
            symbol=symbol,
            quantity=quantity,
            filled_qty=None,
            fill_price=None,
            asset_class=asset_class,
        )
        self.orders[order.provider_order_id] = order
        return order

    async def submit_market_buy(
        self,
        *,
        account_alias: str,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        asset_class: str,
    ) -> SubmittedOrder:
        self.calls.append(("submit_buy", symbol))
        payload = {
            "account_alias": account_alias,
            "symbol": symbol,
            "quantity": quantity,
            "client_order_id": client_order_id,
            "asset_class": asset_class,
        }
        self.submit_calls.append(payload)
        if self.reject_submit:
            raise LiveProviderAttemptError(self.reject_submit)
        existing = next((o for o in self.orders.values() if o.client_order_id == client_order_id), None)
        if existing:
            return existing
        order = SubmittedOrder(
            client_order_id=client_order_id,
            provider_order_id=f"alpaca-{client_order_id}",
            status="SUBMITTED",
            symbol=symbol,
            quantity=quantity,
            asset_class=asset_class,
        )
        self.orders[order.provider_order_id] = order
        return order

    def seed_fill(self, provider_order_id: str, *, filled_qty: Decimal, fill_price: Decimal, status: str = "FILLED") -> None:
        order = self.orders[provider_order_id]
        order.filled_qty = filled_qty
        order.fill_price = fill_price
        order.status = status

    async def get_order(self, account_alias: str, provider_order_id: str) -> SubmittedOrder | None:
        self.calls.append(("get_order", provider_order_id))
        return self.orders.get(provider_order_id)

    async def provider_asset_class(self, symbol: str) -> str | None:
        self.calls.append(("asset_class", symbol))
        mapped = self.asset_classes.get(symbol)
        if mapped in {"crypto", "us_equity"}:
            return mapped
        if mapped == "CRYPTO":
            return "crypto"
        if mapped in {"EQUITY", "ETF"}:
            return "us_equity"
        if symbol.endswith("/USD"):
            return "crypto"
        return mapped or "us_equity"

    async def get_clock(self) -> MarketClock:
        self.calls.append(("clock", ""))
        if self.clock_error:
            raise ProviderError(self.clock_error, self.provider_name)
        timestamp = self.clock_timestamp or datetime.now(UTC)
        next_open = self.next_open or timestamp
        next_close = self.next_close or timestamp
        return MarketClock(
            timestamp=timestamp,
            is_open=self.market_open,
            next_open=next_open,
            next_close=next_close,
        )

    async def get_sessions(self, start: date, end: date) -> list[MarketSession]:
        self.calls.append(("sessions", f"{start}:{end}"))
        if self.calendar_error:
            raise ProviderError(self.calendar_error, self.provider_name)
        return [session for session in self.sessions if start <= session.session_date <= end]


class RecordingClock:
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2024, 6, 15, 16, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, **kwargs: int) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now
