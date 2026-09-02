from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, MarketOrderRequest

from app.domain.errors import ProviderError
from app.providers.mappings import COINGECKO_IDS
from app.providers.protocols import ExecutionPosition, MarketClock, MarketSession, SubmittedOrder, ensure_utc

# Live trading is never constructed from user input or request fields.
ALPACA_PAPER_FORCED = True
EASTERN = ZoneInfo("America/New_York")


def _as_eastern_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=EASTERN).astimezone(UTC)
    return ensure_utc(value)


def _normalized_asset_class(raw: object) -> str:
    value = str(raw or "")
    return "crypto" if "crypto" in value.lower() else "us_equity"


def _normalized_position_symbol(raw_symbol: object, asset_class: str) -> str:
    symbol = str(raw_symbol)
    if asset_class != "crypto":
        return symbol
    compact = symbol.replace("/", "").upper()
    return next((mapped for mapped in COINGECKO_IDS if mapped.replace("/", "").upper() == compact), symbol)


class AlpacaProvider:
    provider_name = "alpaca"

    def __init__(self, accounts: dict[str, tuple[str, str]], *, enable_paper_orders: bool) -> None:
        self.enable_paper_orders = enable_paper_orders
        self._clients: dict[str, TradingClient] = {}
        for alias, (key, secret) in accounts.items():
            self._clients[alias] = TradingClient(key, secret, paper=ALPACA_PAPER_FORCED)
        self.seed_purchases: list[dict] = []

    def _client(self, alias: str) -> TradingClient:
        if alias not in self._clients:
            raise ProviderError(f"unknown alpaca alias {alias}", self.provider_name)
        return self._clients[alias]

    async def is_tradable(self, symbol: str, asset_class: str) -> bool:
        asset = self._find_asset(next(iter(self._clients)), symbol)
        return bool(asset and asset.tradable and asset.status == "active")

    async def available_quantity(self, account_alias: str, symbol: str) -> Decimal:
        position = await self.get_position(account_alias, symbol)
        return position.quantity if position else Decimal("0")

    async def get_position(self, account_alias: str, symbol: str) -> ExecutionPosition | None:
        client = self._client(account_alias)
        try:
            pos = client.get_open_position(symbol)
        except Exception:
            return None
        qty = Decimal(str(pos.qty))
        cls = str(getattr(pos, "asset_class", "") or "")
        return ExecutionPosition(account_alias=account_alias, symbol=symbol, quantity=qty, tradable=True, asset_class=cls)

    async def list_positions(self, account_alias: str) -> list[ExecutionPosition]:
        client = self._client(account_alias)
        rows = client.get_all_positions()
        out = []
        for pos in rows:
            asset_class = _normalized_asset_class(getattr(pos, "asset_class", ""))
            out.append(
                ExecutionPosition(
                    account_alias=account_alias,
                    symbol=_normalized_position_symbol(pos.symbol, asset_class),
                    quantity=Decimal(str(pos.qty)),
                    tradable=True,
                    asset_class=asset_class,
                )
            )
        return out

    async def submit_market_sell(
        self,
        *,
        account_alias: str,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        asset_class: str,
    ) -> SubmittedOrder:
        if not self.enable_paper_orders:
            raise ProviderError("ENABLE_PAPER_ORDERS=false", self.provider_name)
        client = self._client(account_alias)
        request = MarketOrderRequest(
            symbol=symbol,
            qty=float(quantity),
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY if asset_class != "crypto" else TimeInForce.GTC,
            client_order_id=client_order_id,
        )
        order = client.submit_order(request)
        return self._to_submitted(order, client_order_id, symbol, quantity, asset_class)

    async def submit_market_buy(
        self,
        *,
        account_alias: str,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        asset_class: str,
    ) -> SubmittedOrder:
        if not self.enable_paper_orders:
            raise ProviderError("ENABLE_PAPER_ORDERS=false", self.provider_name)
        client = self._client(account_alias)
        request = MarketOrderRequest(
            symbol=symbol,
            qty=float(quantity),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY if asset_class != "crypto" else TimeInForce.GTC,
            client_order_id=client_order_id,
        )
        order = client.submit_order(request)
        return self._to_submitted(order, client_order_id, symbol, quantity, asset_class)

    async def get_order(self, account_alias: str, provider_order_id: str) -> SubmittedOrder | None:
        client = self._client(account_alias)
        try:
            order = client.get_order_by_id(provider_order_id)
        except Exception:
            return None
        return self._to_submitted(order, str(order.client_order_id or ""), str(order.symbol), Decimal(str(order.qty or 0)), str(order.asset_class or ""))

    async def provider_asset_class(self, symbol: str) -> str | None:
        asset = self._find_asset(next(iter(self._clients)), symbol)
        if asset is None:
            return None
        raw = str(asset.asset_class)
        if "crypto" in raw.lower():
            return "crypto"
        return "us_equity"

    async def get_clock(self) -> MarketClock:
        client = next(iter(self._clients.values()), None)
        if client is None:
            raise ProviderError("market clock unavailable", self.provider_name)
        try:
            clock = client.get_clock()
        except Exception as exc:
            raise ProviderError("market clock unavailable", self.provider_name) from exc
        return MarketClock(
            timestamp=ensure_utc(clock.timestamp),
            is_open=bool(clock.is_open),
            next_open=_as_eastern_utc(clock.next_open),
            next_close=_as_eastern_utc(clock.next_close),
        )

    async def get_sessions(self, start: date, end: date) -> list[MarketSession]:
        client = next(iter(self._clients.values()), None)
        if client is None:
            raise ProviderError("market calendar unavailable", self.provider_name)
        try:
            rows = client.get_calendar(GetCalendarRequest(start=start, end=end))
        except Exception as exc:
            raise ProviderError("market calendar unavailable", self.provider_name) from exc
        sessions: list[MarketSession] = []
        for row in rows or []:
            session_date = row.date if hasattr(row.date, "year") else date.fromisoformat(str(row.date))
            sessions.append(
                MarketSession(
                    session_date=session_date,
                    open=_as_eastern_utc(row.open),
                    close=_as_eastern_utc(row.close),
                )
            )
        return sessions

    def record_seed_purchase(self, alias: str, symbol: str, quantity: Decimal) -> dict:
        payload = {
            "activity_type": "PAPER_MIRROR_SETUP",
            "alpaca_alias": alias,
            "symbol": symbol,
            "quantity": str(quantity),
            "id": str(uuid4()),
        }
        self.seed_purchases.append(payload)
        return payload

    def _find_asset(self, alias: str, symbol: str):
        client = self._client(alias)
        try:
            return client.get_asset(symbol)
        except Exception:
            return None

    def _to_submitted(self, order, client_order_id: str, symbol: str, quantity: Decimal, asset_class: str) -> SubmittedOrder:
        filled = Decimal(str(order.filled_qty)) if getattr(order, "filled_qty", None) not in {None, ""} else None
        fill_price = Decimal(str(order.filled_avg_price)) if getattr(order, "filled_avg_price", None) not in {None, ""} else None
        return SubmittedOrder(
            client_order_id=client_order_id,
            provider_order_id=str(order.id),
            status=str(order.status),
            symbol=symbol,
            quantity=quantity,
            filled_qty=filled,
            fill_price=fill_price,
            asset_class=asset_class,
            submitted_at=getattr(order, "submitted_at", None) or datetime.now(UTC),
        )
