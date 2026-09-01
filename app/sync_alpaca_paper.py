from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import or_, select

from app.container import build_container
from app.persistence.models import PortfolioAccount
from app.services.alpaca_sync import AlpacaSyncService


async def sync_portfolio(portfolio: str) -> dict:
    container = build_container()
    try:
        portfolio_id = UUID(portfolio)
    except ValueError:
        portfolio_id = None
    conditions = [PortfolioAccount.alpaca_alias == portfolio, PortfolioAccount.name == portfolio]
    if portfolio_id is not None:
        conditions.insert(0, PortfolioAccount.id == portfolio_id)
    async with container.session_factory() as session:
        account = await session.scalar(select(PortfolioAccount).where(or_(*conditions)))
        if account is None or not account.alpaca_alias:
            raise ValueError(f"portfolio is not mapped to an Alpaca account: {portfolio}")
        positions = await AlpacaSyncService(container.providers.execution).sync_positions(
            session,
            portfolio_id=account.id,
            alpaca_alias=account.alpaca_alias,
        )
        await session.commit()
    return {
        "portfolio_id": str(account.id),
        "account": account.alpaca_alias,
        "positions": len(positions),
        "paper": True,
        "activity": "ALPACA_SYNC",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize one mapped Alpaca paper portfolio")
    parser.add_argument("--portfolio", required=True, help="portfolio UUID, name, or Alpaca alias")
    args = parser.parse_args()
    print(asyncio.run(sync_portfolio(args.portfolio)))


if __name__ == "__main__":
    main()
