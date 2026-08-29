from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.persistence.models import PaperMirrorActivity, PortfolioAccount, User
from app.providers.fakes import FakeExecutionProvider
from app.providers.protocols import ExecutionPosition
from app.seed_alpaca_paper import seed_alpaca_paper


PORTFOLIO_ID = UUID("11111111-1111-4111-8111-aaaaaaaa0002")
USER_ID = UUID("11111111-1111-4111-8111-111111111111")


async def _account(session) -> None:
    session.add(User(id=USER_ID, email="demo@example.com", display_name="Demo User"))
    session.add(
        PortfolioAccount(
            id=PORTFOLIO_ID,
            user_id=USER_ID,
            account_type="TAXABLE",
            name="Conservative taxable",
            alpaca_alias="conservative-demo",
            is_taxable=True,
        )
    )
    await session.commit()


def _manifest(path, **overrides):
    payload = {
        "alpaca_paper_alias": "conservative-demo",
        "internal_portfolio_id": str(PORTFOLIO_ID),
        "positions": [
            {"symbol": "VTI", "asset_class": "ETF", "manifest_quantity": "12"},
            {"symbol": "BTC/USD", "asset_class": "CRYPTO", "manifest_quantity": "0.07"},
            {"symbol": "SCHB", "asset_class": "ETF", "manifest_quantity": "0"},
        ],
        **overrides,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_accepts_utf8_bom(settings):
    path = settings.local_data_dir / "bom-manifest.json"
    path.write_text(json.dumps({"positions": []}), encoding="utf-8-sig")
    from app.seed_alpaca_paper import _load_manifest

    payload, digest = _load_manifest(path)
    assert payload == {"positions": []}
    assert len(digest) == 64


@pytest.mark.asyncio
async def test_preview_prints_plan_without_submission_or_enable_flag(settings, session):
    await _account(session)
    manifest = _manifest(settings.local_data_dir / "manifest.json")
    execution = FakeExecutionProvider()

    results = await seed_alpaca_paper(
        portfolio="conservative-demo",
        manifest_path=manifest,
        confirm_paper=False,
        settings=settings,
        execution=execution,
    )

    assert [row["status"] for row in results] == ["PREVIEW_ONLY", "PREVIEW_ONLY"]
    assert [row["side"] for row in results] == ["BUY", "BUY"]
    assert execution.submit_calls == []


@pytest.mark.asyncio
async def test_seeds_manifest_quantities_with_tif_and_activity(settings, session):
    await _account(session)
    settings.enable_paper_orders = True
    manifest = _manifest(settings.local_data_dir / "manifest.json")
    execution = FakeExecutionProvider()

    results = await seed_alpaca_paper(
        portfolio="conservative-demo",
        manifest_path=manifest,
        confirm_paper=True,
        settings=settings,
        execution=execution,
    )

    assert [call["symbol"] for call in execution.submit_calls] == ["VTI", "BTC/USD"]
    assert [call["quantity"] for call in execution.submit_calls] == [Decimal("12"), Decimal("0.07")]
    assert results[0]["time_in_force"] == "DAY"
    assert results[1]["time_in_force"] == "GTC"
    activities = list(await session.scalars(select(PaperMirrorActivity)))
    assert len(activities) == 2
    assert {activity.activity_type for activity in activities} == {"PAPER_MIRROR_SETUP"}
    assert {activity.payload["side"] for activity in activities} == {"BUY"}


@pytest.mark.asyncio
async def test_preview_and_submission_buy_only_missing_quantity(settings, session):
    await _account(session)
    settings.enable_paper_orders = True
    manifest = _manifest(settings.local_data_dir / "manifest.json")
    execution = FakeExecutionProvider()
    execution.seed_position(
        ExecutionPosition(
            account_alias="conservative-demo",
            symbol="VTI",
            quantity=Decimal("5"),
            tradable=True,
            asset_class="us_equity",
        )
    )
    execution.seed_position(
        ExecutionPosition(
            account_alias="conservative-demo",
            symbol="BTC/USD",
            quantity=Decimal("0.08"),
            tradable=True,
            asset_class="crypto",
        )
    )

    preview = await seed_alpaca_paper(
        portfolio="conservative-demo",
        manifest_path=manifest,
        confirm_paper=False,
        settings=settings,
        execution=execution,
    )
    assert preview[0]["symbol"] == "VTI"
    assert preview[0]["manifest_quantity"] == "12"
    assert preview[0]["current_quantity"] == "5"
    assert preview[0]["quantity"] == "7"
    assert preview[0]["status"] == "PREVIEW_ONLY"
    assert preview[1]["symbol"] == "BTC/USD"
    assert preview[1]["quantity"] == "0"
    assert preview[1]["status"] == "ALREADY_SUFFICIENT"

    results = await seed_alpaca_paper(
        portfolio="conservative-demo",
        manifest_path=manifest,
        confirm_paper=True,
        settings=settings,
        execution=execution,
    )
    assert len(execution.submit_calls) == 1
    assert execution.submit_calls[0]["symbol"] == "VTI"
    assert execution.submit_calls[0]["quantity"] == Decimal("7")
    assert {row["status"] for row in results} == {"SUBMITTED", "ALREADY_SUFFICIENT"}


@pytest.mark.asyncio
async def test_rerun_is_idempotent_and_mapping_is_validated(settings, session):
    await _account(session)
    settings.enable_paper_orders = True
    manifest = _manifest(settings.local_data_dir / "manifest.json")
    execution = FakeExecutionProvider()

    first = await seed_alpaca_paper(
        portfolio=str(PORTFOLIO_ID), manifest_path=manifest, confirm_paper=True, settings=settings, execution=execution
    )
    second = await seed_alpaca_paper(
        portfolio=str(PORTFOLIO_ID), manifest_path=manifest, confirm_paper=True, settings=settings, execution=execution
    )

    assert len(first) == 2
    assert [row["status"] for row in second] == ["SKIPPED_EXISTING", "SKIPPED_EXISTING"]
    assert len(execution.submit_calls) == 2

    bad_manifest = _manifest(settings.local_data_dir / "bad.json", alpaca_paper_alias="growth-demo")
    with pytest.raises(ValueError, match="alias"):
        await seed_alpaca_paper(
            portfolio=str(PORTFOLIO_ID), manifest_path=bad_manifest, confirm_paper=True, settings=settings, execution=execution
        )
