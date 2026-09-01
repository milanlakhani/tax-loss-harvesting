from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.demo_data.constants import PORTFOLIO_A_ID
from app.persistence.models import PaperMirrorActivity, TaxLot, User
from app.providers.fakes import FakeExecutionProvider
from app.providers.protocols import ExecutionPosition
from app.services.alpaca_sync import AlpacaSyncService
from app.demo_data.constants import USER_A_ID


@pytest.mark.integration
async def test_seed_purchases_are_mirror_setup_not_tax_lots(session, session_factory):
    if await session.get(User, USER_A_ID) is None:
        session.add(User(id=USER_A_ID, email="a@demo.local", display_name="A", is_synthetic=True))
        await session.commit()
    from app.domain.enums import AccountType
    from app.persistence.models import PortfolioAccount

    if await session.get(PortfolioAccount, PORTFOLIO_A_ID) is None:
        session.add(
            PortfolioAccount(
                id=PORTFOLIO_A_ID,
                user_id=USER_A_ID,
                account_type=AccountType.BROKERAGE.value,
                name="A",
                is_taxable=True,
                alpaca_alias="conservative-demo",
                is_synthetic=True,
            )
        )
        await session.commit()
    execution = FakeExecutionProvider()
    execution.seed_position(
        ExecutionPosition(
            account_alias="conservative-demo",
            symbol="VTI",
            quantity=Decimal("12"),
            tradable=True,
            asset_class="ETF",
        )
    )
    before = list(await session.scalars(select(TaxLot).where(TaxLot.portfolio_id == PORTFOLIO_A_ID)))
    svc = AlpacaSyncService(execution)
    await svc.record_seed_purchase(
        session,
        portfolio_id=PORTFOLIO_A_ID,
        alpaca_alias="conservative-demo",
        symbol="VTI",
        quantity=Decimal("12"),
    )
    await svc.sync_positions(session, portfolio_id=PORTFOLIO_A_ID, alpaca_alias="conservative-demo")
    await session.commit()
    after = list(await session.scalars(select(TaxLot).where(TaxLot.portfolio_id == PORTFOLIO_A_ID)))
    assert len(after) == len(before)
    activities = list(await session.scalars(select(PaperMirrorActivity)))
    assert any(row.activity_type == "PAPER_MIRROR_SETUP" for row in activities)
