from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import CandidateStatus, ELIGIBLE_HARVEST_ASSET_TYPES, PaperOrderStatus, PreparationStatus, RejectionCode
from app.domain.errors import PaperExecutionError
from app.persistence.models import (
    Asset,
    Evaluation,
    ExecutionPreparation,
    HarvestingCandidate,
    Holding,
    PaperOrder,
    PortfolioAccount,
    TaxLot,
    User,
)
from app.providers.mappings import coingecko_id_for, expected_alpaca_asset_class
from app.providers.protocols import ProviderRouter, Quote
from app.services.conflicts import ConflictService
from app.services.freshness import verify_proposed_sell_quantity
from app.services.harvesting import HarvestingService


def _hash_token(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


class PaperExecutionService:
    """The only component allowed to prepare or submit an Alpaca paper SELL."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        providers: ProviderRouter,
        clock,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.providers = providers
        self.clock = clock
        self.harvesting = HarvestingService(settings, providers, ConflictService(settings))

    async def prepare(self, *, candidate_id: UUID, demo_session_token: str) -> dict:
        now = self.clock.now()
        as_of = now
        async with self.session_factory() as session:
            candidate, lot, asset, account, evaluation = await self._load_approved(session, candidate_id)
            asset_values, class_of = await self._portfolio_values(session, account, now)
            ok, code, explanation, _quote = await self.harvesting.recheck_approved(
                session, candidate_id, now, now, asset_values, class_of
            )
            if not ok:
                raise PaperExecutionError(explanation, code.value if code else "NOT_APPROVED")
            candidate, lot, asset, account, evaluation = await self._load_approved(session, candidate_id)
            existing = await session.scalar(select(ExecutionPreparation).where(ExecutionPreparation.candidate_id == candidate_id))
            if existing is not None:
                if existing.status in {PreparationStatus.CONFIRMED.value, PreparationStatus.RESERVED.value}:
                    raise PaperExecutionError("Candidate already reserved or confirmed", "ALREADY_EXECUTED")
                if existing.status == PreparationStatus.PREPARED.value and existing.expires_at and existing.expires_at > now:
                    raise PaperExecutionError("Active preparation already exists", "ALREADY_PREPARED")
            snapshot = await self._build_snapshot(session, candidate, lot, asset, account, evaluation, now)
            await self._validate_snapshot(snapshot, lot, account, asset, now)
            token = secrets.token_urlsafe(32)
            token_hash = _hash_token(token, self.settings.demo_session_signing_secret)
            session_hash = _hash_token(demo_session_token, self.settings.demo_session_signing_secret)
            expires = now + timedelta(seconds=self.settings.paper_prep_ttl_seconds)
            if existing is None:
                existing = ExecutionPreparation(
                    id=uuid4(),
                    candidate_id=candidate_id,
                    analysis_run_id=candidate.analysis_run_id,
                    status=PreparationStatus.PREPARED.value,
                    quantity=Decimal(str(snapshot["quantity"])),
                    side="SELL",
                    symbol=snapshot["symbol"],
                    asset_type=snapshot["asset_type"],
                    alpaca_alias=snapshot["alpaca_alias"],
                    token_hash=token_hash,
                    demo_session_hash=session_hash,
                    snapshot=snapshot,
                    expires_at=expires,
                )
                session.add(existing)
            else:
                existing.status = PreparationStatus.PREPARED.value
                existing.quantity = Decimal(str(snapshot["quantity"]))
                existing.side = "SELL"
                existing.symbol = snapshot["symbol"]
                existing.asset_type = snapshot["asset_type"]
                existing.alpaca_alias = snapshot["alpaca_alias"]
                existing.token_hash = token_hash
                existing.demo_session_hash = session_hash
                existing.snapshot = snapshot
                existing.expires_at = expires
                existing.token_used_at = None
                existing.confirmed_at = None
                existing.updated_at = now
            await session.commit()
            return {
                "preparation_id": str(existing.id),
                "candidate_id": str(candidate_id),
                "token": token,
                "expires_at": expires.isoformat(),
                "paper_orders_enabled": self.settings.enable_paper_orders,
                "environment": "SIMULATED PAPER TRADE - NO REAL MONEY",
                **snapshot,
            }

    async def confirm(self, *, candidate_id: UUID, token: str, demo_session_token: str) -> dict:
        now = self.clock.now()
        token_hash = _hash_token(token, self.settings.demo_session_signing_secret)
        session_hash = _hash_token(demo_session_token, self.settings.demo_session_signing_secret)
        async with self.session_factory() as session:
            candidate, lot, asset, account, evaluation = await self._load_approved(session, candidate_id)
            prep = await session.scalar(select(ExecutionPreparation).where(ExecutionPreparation.candidate_id == candidate_id))
            if prep is None or prep.token_hash != token_hash:
                raise PaperExecutionError("Invalid token", "INVALID_TOKEN")
            if prep.token_used_at is not None:
                raise PaperExecutionError("Token already used", "TOKEN_REUSED")
            if prep.expires_at is None or prep.expires_at <= now:
                prep.status = PreparationStatus.EXPIRED.value
                await session.commit()
                raise PaperExecutionError("Preparation expired", "EXPIRED")
            if prep.demo_session_hash != session_hash:
                raise PaperExecutionError("Demo session mismatch", "SESSION_MISMATCH")
            if prep.status not in {PreparationStatus.PREPARED.value}:
                raise PaperExecutionError("Preparation is not confirmable", "INVALID_STATE")
            asset_values, class_of = await self._portfolio_values(session, account, now)
            ok, code, explanation, _quote = await self.harvesting.recheck_approved(
                session, candidate_id, now, now, asset_values, class_of
            )
            if not ok:
                raise PaperExecutionError(explanation, code.value if code else "NOT_APPROVED")
            live = await self._build_snapshot(session, candidate, lot, asset, account, evaluation, now)
            stored = prep.snapshot or {}
            immutable_keys = (
                "candidate_id",
                "user_id",
                "portfolio_id",
                "alpaca_alias",
                "symbol",
                "canonical_id",
                "asset_type",
                "side",
                "quantity",
                "rule_version",
            )
            for key in immutable_keys:
                if str(stored.get(key)) != str(live.get(key)):
                    raise PaperExecutionError("Snapshot changed", "SNAPSHOT_MODIFIED")
            await self._validate_snapshot(live, lot, account, asset, now)
            if not self.settings.enable_paper_orders:
                raise PaperExecutionError("Paper orders are disabled", "PAPER_ORDERS_DISABLED")
            prep.status = PreparationStatus.RESERVED.value
            prep.token_used_at = now
            await session.flush()
            client_order_id = f"tlh-{prep.id.hex}"
            submitted = await self.providers.execution.submit_market_sell(
                account_alias=account.alpaca_alias or "",
                symbol=asset.symbol,
                quantity=prep.quantity,
                client_order_id=client_order_id,
                asset_class=expected_alpaca_asset_class(asset.asset_type),
            )
            prep.status = PreparationStatus.CONFIRMED.value
            prep.confirmed_at = now
            prep.snapshot = {**stored, "confirmed_snapshot": live}
            order = PaperOrder(
                id=uuid4(),
                preparation_id=prep.id,
                status=PaperOrderStatus.SUBMITTED.value,
                provider_order_id=submitted.provider_order_id,
                client_order_id=client_order_id,
                submitted_at=now,
                requested={
                    "symbol": asset.symbol,
                    "quantity": str(prep.quantity),
                    "side": "SELL",
                    "asset_class": expected_alpaca_asset_class(asset.asset_type),
                    "reference_price": stored.get("reference_price"),
                    "reference_provider": stored.get("quote_provider"),
                },
            )
            session.add(order)
            await session.commit()
            return {
                "order_id": str(order.id),
                "provider_order_id": submitted.provider_order_id,
                "status": order.status,
                "client_order_id": client_order_id,
                "environment": "SIMULATED PAPER TRADE - NO REAL MONEY",
            }

    async def refresh(self, *, order_id: UUID) -> dict:
        now = self.clock.now()
        async with self.session_factory() as session:
            order = await session.get(PaperOrder, order_id)
            if order is None or not order.provider_order_id:
                raise PaperExecutionError("Unknown paper order", "NOT_FOUND")
            prep = await session.get(ExecutionPreparation, order.preparation_id)
            alias = prep.alpaca_alias if prep else ""
            remote = await self.providers.execution.get_order(alias or "", order.provider_order_id)
            if remote is not None:
                order.filled_quantity = remote.filled_qty
                order.fill_price = remote.fill_price
                order.fill_timestamp = remote.submitted_at
                status = remote.status.upper()
                if "FILL" in status and (remote.filled_qty or Decimal("0")) >= (prep.quantity if prep else Decimal("0")):
                    order.status = PaperOrderStatus.FILLED.value
                elif "FILL" in status:
                    order.status = PaperOrderStatus.PARTIALLY_FILLED.value
                elif "CANCEL" in status:
                    order.status = PaperOrderStatus.CANCELLED.value
                elif "FAIL" in status or "REJECT" in status:
                    order.status = PaperOrderStatus.FAILED.value
            order.last_refresh_at = now
            await session.commit()
            return {
                "order_id": str(order.id),
                "provider_order_id": order.provider_order_id,
                "status": order.status,
                "filled_quantity": str(order.filled_quantity) if order.filled_quantity is not None else None,
                "fill_price": str(order.fill_price) if order.fill_price is not None else None,
                "reference_price": (prep.snapshot or {}).get("reference_price") if prep else None,
            }

    async def _load_approved(self, session, candidate_id: UUID):
        candidate = await session.get(HarvestingCandidate, candidate_id)
        if candidate is None:
            raise PaperExecutionError("Unknown candidate", "NOT_FOUND")
        if candidate.status != CandidateStatus.APPROVED.value:
            raise PaperExecutionError("Candidate is not approved", "NOT_APPROVED")
        lot = await session.get(TaxLot, candidate.tax_lot_id)
        asset = await session.get(Asset, candidate.asset_id)
        account = await session.get(PortfolioAccount, candidate.portfolio_id)
        evaluation = (
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id == candidate.id).order_by(Evaluation.evaluated_at.desc())
            )
        ).first()
        if not lot or not asset or not account or evaluation is None:
            raise PaperExecutionError("Incomplete candidate", "INCOMPLETE")
        return candidate, lot, asset, account, evaluation

    async def _build_snapshot(self, session, candidate, lot, asset, account, evaluation, now: datetime) -> dict:
        if asset.asset_type not in {t.value for t in ELIGIBLE_HARVEST_ASSET_TYPES}:
            raise PaperExecutionError("Ineligible asset type", "INELIGIBLE_ASSET_TYPE")
        qty = evaluation.selected_quantity or lot.remaining_quantity
        quote = await self.providers.quote_for_asset_type(asset.asset_type, asset.canonical_id, asset.symbol, now)
        proceeds = (quote.price * qty) if quote else None
        loss = None
        if quote and lot.per_unit_basis is not None:
            loss = (lot.per_unit_basis - quote.price) * qty
        return {
            "candidate_id": str(candidate.id),
            "user_id": str(account.user_id),
            "portfolio_id": str(account.id),
            "alpaca_alias": account.alpaca_alias,
            "account_name": account.name,
            "symbol": asset.symbol,
            "canonical_id": asset.canonical_id,
            "provider_asset_id": quote.provider_asset_id if quote else None,
            "coingecko_id": coingecko_id_for(asset.symbol, asset.canonical_id) if asset.asset_type == "CRYPTO" else None,
            "asset_type": asset.asset_type,
            "side": "SELL",
            "quantity": str(qty),
            "tax_lot_quantity": str(lot.remaining_quantity),
            "quote_provider": quote.provider if quote else None,
            "reference_price": str(quote.price) if quote else None,
            "reference_timestamp": quote.source_timestamp.isoformat() if quote else None,
            "quote_stale": bool(quote.stale) if quote else True,
            "estimated_proceeds": str(proceeds) if proceeds is not None else None,
            "basis": str(lot.remaining_basis) if lot.remaining_basis is not None else None,
            "estimated_loss": str(loss) if loss is not None else None,
            "rule_version": evaluation.rule_version,
            "approval_status": candidate.status,
            "rejection_code": evaluation.rejection_code,
            "explanation": evaluation.explanation,
            "prepared_at": now.isoformat(),
            "environment": "SIMULATED PAPER TRADE - NO REAL MONEY",
        }

    async def _validate_snapshot(self, snapshot: dict, lot, account, asset, now: datetime) -> None:
        if snapshot["side"] != "SELL":
            raise PaperExecutionError("Only SELL is allowed", "BUY_NOT_ALLOWED")
        if asset.asset_type in {"FX", "CURRENCY", "CASH", "BANK_BALANCE", "UNKNOWN"}:
            raise PaperExecutionError("Asset type cannot be traded", "INELIGIBLE_ASSET_TYPE")
        if asset.asset_type == "CRYPTO" and not snapshot.get("coingecko_id"):
            raise PaperExecutionError("Missing CoinGecko mapping", "MISSING_COINGECKO_MAPPING")
        expected = expected_alpaca_asset_class(asset.asset_type)
        actual = await self.providers.execution.provider_asset_class(asset.symbol)
        if actual != expected:
            raise PaperExecutionError("Asset class mismatch", "ASSET_CLASS_MISMATCH")
        tradable = await self.providers.execution.is_tradable(asset.symbol, asset.asset_type)
        if not tradable:
            raise PaperExecutionError("Asset is not tradable", "NOT_TRADABLE")
        qty = Decimal(str(snapshot["quantity"]))
        if qty > lot.remaining_quantity:
            raise PaperExecutionError("Quantity exceeds tax lot", "INSUFFICIENT_QUANTITY")
        ownership = verify_proposed_sell_quantity(
            await self.providers.execution.get_position(account.alpaca_alias or "", asset.symbol),
            qty,
        )
        if ownership:
            raise PaperExecutionError("Alpaca quantity insufficient", ownership.value)
        if snapshot.get("quote_stale") or not snapshot.get("reference_price"):
            raise PaperExecutionError("Quote is stale or missing", "STALE_QUOTE")
        quote_ts = datetime.fromisoformat(snapshot["reference_timestamp"]) if snapshot.get("reference_timestamp") else None
        if quote_ts is None or (now - quote_ts) > timedelta(minutes=self.settings.quote_max_age_minutes):
            raise PaperExecutionError("Quote exceeds freshness", "STALE_QUOTE")

    async def _portfolio_values(self, session, account, as_of) -> tuple[dict, dict]:
        holdings = list(await session.scalars(select(Holding).where(Holding.portfolio_id == account.id)))
        asset_values: dict[str, Decimal] = {}
        class_of: dict[str, str] = {}
        for holding in holdings:
            held = await session.get(Asset, holding.asset_id)
            if held is None:
                continue
            quote = await self.providers.quote_for_asset_type(held.asset_type, held.canonical_id, held.symbol, as_of)
            value = (quote.price * holding.quantity) if quote else Decimal("0")
            asset_values[held.canonical_id] = asset_values.get(held.canonical_id, Decimal("0")) + value
            class_of[held.canonical_id] = "BOND" if held.symbol in {"BND", "AGG", "TLT"} else held.asset_type
        return asset_values, class_of
