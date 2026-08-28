from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import PaperMirrorActivity, TaxLot
from app.providers.alpaca import AlpacaProvider
from app.providers.protocols import ExecutionProvider


class AlpacaSyncService:
    """Synchronize paper account state. Seed buys are PAPER_MIRROR_SETUP, never tax lots."""

    def __init__(self, execution: ExecutionProvider) -> None:
        self.execution = execution

    async def sync_positions(self, session: AsyncSession, *, portfolio_id: UUID, alpaca_alias: str) -> list[dict]:
        positions = await self.execution.list_positions(alpaca_alias)
        payload = {
            "alpaca_alias": alpaca_alias,
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": str(p.quantity),
                    "asset_class": p.asset_class,
                    "tradable": p.tradable,
                }
                for p in positions
            ],
        }
        session.add(
            PaperMirrorActivity(
                id=uuid4(),
                portfolio_id=portfolio_id,
                alpaca_alias=alpaca_alias,
                activity_type="ALPACA_SYNC",
                payload=payload,
                is_synthetic=True,
            )
        )
        return payload["positions"]

    async def record_seed_purchase(
        self,
        session: AsyncSession,
        *,
        portfolio_id: UUID,
        alpaca_alias: str,
        symbol: str,
        quantity: Decimal,
    ) -> dict:
        if isinstance(self.execution, AlpacaProvider):
            payload = self.execution.record_seed_purchase(alpaca_alias, symbol, quantity)
        else:
            payload = {
                "activity_type": "PAPER_MIRROR_SETUP",
                "alpaca_alias": alpaca_alias,
                "symbol": symbol,
                "quantity": str(quantity),
            }
        session.add(
            PaperMirrorActivity(
                id=uuid4(),
                portfolio_id=portfolio_id,
                alpaca_alias=alpaca_alias,
                activity_type="PAPER_MIRROR_SETUP",
                payload=payload,
                is_synthetic=True,
            )
        )
        lots_before = await session.scalar(select(TaxLot.id).where(TaxLot.portfolio_id == portfolio_id).limit(1))
        _ = lots_before
        return payload
