from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from app.adapters.storage import LocalStatementStorage
from app.config import get_settings
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.demo_data.replace import replace_generated_demo_data
from app.demo_data.constants import (
    AS_OF,
    ASSET_CATALOG,
    BANK_A_ID,
    BANK_B_ID,
    GBP_USD,
    EUR_USD,
    PORTFOLIO_A_ID,
    PORTFOLIO_B_ID,
    REPLACEMENTS,
    USER_A_EMAIL,
    USER_A_ID,
    USER_B_EMAIL,
    USER_B_ID,
    as_of_datetime,
    parse_demo_as_of_date,
    quote_timestamps_for,
    today_is_allowed,
)
from app.domain.enums import AccountType
from app.persistence.database import session_scope
from app.persistence.models import (
    AnomalyGroundTruth,
    Asset,
    BankTransaction,
    DemoDatasetState,
    MirrorManifest,
    PaperMirrorActivity,
    PortfolioAccount,
    ReplacementRelationship,
    RiskProfile,
    TargetAllocation,
    User,
)
from app.providers.fakes import FakeCryptoQuoteProvider, FakeEquityQuoteProvider, FakeExecutionProvider, FakeFxProvider
from app.providers.protocols import ExecutionPosition, PriceObservation, ProviderRouter, Quote
from app.services.freshness import CURRENT_DEMO_DATASET, HISTORICAL_DEMO_DATASET
from app.services.ingestion import StatementIngestor


def build_fake_providers(
    as_of: datetime | None = None,
    *,
    brokerage_specs=None,
) -> ProviderRouter:
    equity = FakeEquityQuoteProvider()
    crypto = FakeCryptoQuoteProvider()
    fx = FakeFxProvider()
    execution = FakeExecutionProvider()
    stale = {"ETF:AGG", "EQUITY:NVDA"}
    zero_mirror = {"SCHB", "SCHA"}
    as_of = as_of or AS_OF
    quote_ts, stale_ts = quote_timestamps_for(as_of)
    for _symbol, (canonical, provider_symbol, asset_type, _name, price) in ASSET_CATALOG.items():
        ts = stale_ts if canonical in stale else quote_ts
        quote = Quote(
            canonical_id=canonical,
            price=price,
            currency="USD",
            provider="fake-coingecko" if asset_type == "CRYPTO" else "fake-alpha-vantage",
            provider_asset_id=provider_symbol,
            source_timestamp=ts,
            retrieved_at=as_of,
            is_mocked=True,
            tradable=True,
        )
        history = []
        for i in range(60, -1, -1):
            day = as_of - timedelta(days=i)
            drift = Decimal("1") - (Decimal(i) * Decimal("0.0005"))
            history.append(
                PriceObservation(
                    canonical_id=canonical,
                    currency="USD",
                    price=(price * drift).quantize(Decimal("0.00000001")),
                    observed_at=day.replace(hour=16, minute=0),
                    provider=quote.provider,
                    is_mocked=True,
                )
            )
        if asset_type == "CRYPTO":
            crypto.seed_quote(quote)
            crypto.seed_history(canonical, history)
        else:
            equity.seed_quote(quote)
            equity.seed_history(canonical, history)
        execution.seed_tradable(provider_symbol, True)
    specs = brokerage_specs or (portfolio_a_spec(), portfolio_b_spec())
    aliases = ("conservative-demo", "growth-demo")
    for alias, spec in zip(aliases, specs, strict=True):
        for holding in spec.holdings:
            qty = Decimal("0") if holding.symbol in zero_mirror else holding.quantity
            execution.seed_position(
                ExecutionPosition(
                    account_alias=alias,
                    symbol=holding.symbol,
                    quantity=qty,
                    tradable=True,
                    asset_class=holding.asset_type.value,
                )
            )
    fx_start = as_of.date() - timedelta(days=400)
    fx_end = as_of.date() + timedelta(days=40)
    on = fx_start
    while on <= fx_end:
        fx.seed_default_majors(on, retrieved_at=as_of)
        on += timedelta(days=1)
    return ProviderRouter(equity=equity, crypto=crypto, fx=fx, execution=execution)


async def seed_users(session) -> None:
    for user_id, email, name in (
        (USER_A_ID, USER_A_EMAIL, "Alex Conservative"),
        (USER_B_ID, USER_B_EMAIL, "Blair Growth"),
    ):
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(User(id=user_id, email=email, display_name=name, is_synthetic=True))
    accounts = [
        (BANK_A_ID, USER_A_ID, AccountType.BANK, "Alex Checking", False, None),
        (BANK_B_ID, USER_B_ID, AccountType.BANK, "Blair Checking", False, None),
        (PORTFOLIO_A_ID, USER_A_ID, AccountType.BROKERAGE, "Alex Taxable Brokerage", True, "conservative-demo"),
        (PORTFOLIO_B_ID, USER_B_ID, AccountType.BROKERAGE, "Blair Taxable Brokerage", True, "growth-demo"),
    ]
    for acc_id, user_id, typ, name, taxable, alias in accounts:
        if await session.get(PortfolioAccount, acc_id) is None:
            session.add(
                PortfolioAccount(
                    id=acc_id,
                    user_id=user_id,
                    account_type=typ.value,
                    name=name,
                    base_currency="USD",
                    is_taxable=taxable,
                    alpaca_alias=alias,
                    is_synthetic=True,
                )
            )
    await session.flush()


async def seed_replacements(session) -> None:
    for source, dest, kind in REPLACEMENTS:
        exists = await session.scalar(
            select(ReplacementRelationship).where(
                ReplacementRelationship.source_canonical_id == source,
                ReplacementRelationship.replacement_canonical_id == dest,
                ReplacementRelationship.rule_version == "replacement_v1",
            )
        )
        if exists is None:
            session.add(
                ReplacementRelationship(
                    id=uuid4(),
                    source_canonical_id=source,
                    replacement_canonical_id=dest,
                    relationship=kind,
                    rule_version="replacement_v1",
                )
            )


async def seed_risk_and_targets(session) -> None:
    profiles = [
        (
            PORTFOLIO_A_ID,
            "conservative",
            Decimal("0.14000000"),
            Decimal("0.45000000"),
            Decimal("0.80000000"),
            Decimal("0.22000000"),
            Decimal("0.18000000"),
            Decimal("50000.00"),
            Decimal("0.15000000"),
            [
                ("EQUITY", Decimal("0.35000000")),
                ("ETF", Decimal("0.20000000")),
                ("BOND", Decimal("0.37000000")),
                ("CRYPTO", Decimal("0.08000000")),
            ],
        ),
        (
            PORTFOLIO_B_ID,
            "growth",
            Decimal("0.16000000"),
            Decimal("0.40000000"),
            Decimal("0.90000000"),
            Decimal("0.03000000"),
            Decimal("0.35000000"),
            Decimal("100000.00"),
            Decimal("0.35000000"),
            [
                ("EQUITY", Decimal("0.45000000")),
                ("ETF", Decimal("0.37000000")),
                ("BOND", Decimal("0.05000000")),
                ("CRYPTO", Decimal("0.13000000")),
            ],
        ),
    ]
    for pid, name, max_c, max_s, max_e, min_b, max_v, max_t, max_to, allocs in profiles:
        existing = await session.scalar(select(RiskProfile).where(RiskProfile.portfolio_id == pid))
        if existing is None:
            session.add(
                RiskProfile(
                    id=uuid4(),
                    portfolio_id=pid,
                    name=name,
                    max_crypto_weight=max_c,
                    max_single_asset_weight=max_s,
                    max_equity_weight=max_e,
                    min_bond_weight=min_b,
                    max_volatility=max_v,
                    max_trade_notional=max_t,
                    max_turnover=max_to,
                    rule_version="risk_v1",
                )
            )
        else:
            existing.max_crypto_weight = max_c
            existing.max_single_asset_weight = max_s
            existing.max_equity_weight = max_e
            existing.min_bond_weight = min_b
            existing.max_volatility = max_v
            existing.max_trade_notional = max_t
            existing.max_turnover = max_to
        for asset_class, weight in allocs:
            exists = await session.scalar(
                select(TargetAllocation).where(
                    TargetAllocation.portfolio_id == pid,
                    TargetAllocation.asset_class == asset_class,
                )
            )
            if exists is None:
                session.add(
                    TargetAllocation(
                        id=uuid4(),
                        portfolio_id=pid,
                        asset_class=asset_class,
                        canonical_asset_id=None,
                        target_weight=weight,
                        rule_version="alloc_v1",
                    )
                )


def _mirror_payload(portfolio_id: UUID, alias: str, spec) -> dict:
    positions = []
    for holding in spec.holdings:
        planned = holding.quantity
        if holding.symbol in {"SCHB", "SCHA"}:
            planned = Decimal("0")
        positions.append(
            {
                "symbol": holding.symbol,
                "asset_class": holding.asset_type.value,
                "canonical_id": holding.canonical_id,
                "manifest_quantity": str(holding.quantity),
                "planned_max_sale_quantity": str(planned),
            }
        )
    return {
        "alpaca_paper_alias": alias,
        "internal_portfolio_id": str(portfolio_id),
        "tax_lot_source": "PostgreSQL remains the tax-lot source. Alpaca quantities are a paper mirror only.",
        "crypto_mappings": {
            "BTC/USD": "CRYPTO:BTC-USD",
            "ETH/USD": "CRYPTO:ETH-USD",
            "SOL/USD": "CRYPTO:SOL-USD",
            "DOGE/USD": "CRYPTO:DOGE-USD",
        },
        "positions": positions,
        "is_synthetic": True,
    }


async def seed_mirrors(session, providers: ProviderRouter) -> None:
    specs = [(PORTFOLIO_A_ID, "conservative-demo", portfolio_a_spec()), (PORTFOLIO_B_ID, "growth-demo", portfolio_b_spec())]
    for pid, alias, spec in specs:
        payload = _mirror_payload(pid, alias, spec)
        existing = await session.scalar(select(MirrorManifest).where(MirrorManifest.portfolio_id == pid))
        if existing is None:
            session.add(MirrorManifest(id=uuid4(), portfolio_id=pid, alpaca_alias=alias, payload=payload, is_synthetic=True))
            session.add(
                PaperMirrorActivity(
                    id=uuid4(),
                    portfolio_id=pid,
                    alpaca_alias=alias,
                    activity_type="SEED_MANIFEST",
                    payload=payload,
                    is_synthetic=True,
                )
            )
        for pos in payload["positions"]:
            qty = Decimal(pos["manifest_quantity"])
            if pos["symbol"] in {"SCHB", "SCHA"}:
                qty = Decimal("0")
            providers.execution.seed_position(
                ExecutionPosition(
                    account_alias=alias,
                    symbol=pos["symbol"],
                    quantity=qty,
                    tradable=True,
                    asset_class=pos["asset_class"],
                )
            )


async def seed_labels(session, labels: list[tuple[str, str, str]]) -> None:
    for txn_id, kind, reason in labels:
        txn = await session.scalar(select(BankTransaction).where(BankTransaction.external_transaction_id == txn_id))
        if txn is None:
            continue
        exists = await session.scalar(select(AnomalyGroundTruth).where(AnomalyGroundTruth.transaction_id == txn.id))
        if exists is None:
            session.add(
                AnomalyGroundTruth(
                    id=uuid4(),
                    transaction_id=txn.id,
                    user_id=txn.user_id,
                    anomaly_type=kind,
                    injected_reason=reason,
                    is_synthetic=True,
                )
            )


def summarize(session_data: dict) -> str:
    return json.dumps(session_data, indent=2, default=str)


async def generate(
    reset_files: bool = True,
    *,
    mode: str = "historical",
    as_of_date: date | None = None,
    min_history: int | None = None,
) -> dict:
    settings = get_settings()
    if min_history is None:
        min_history = settings.min_history_threshold
    mode = mode.lower()
    if mode == "current":
        as_of_date = as_of_date or parse_demo_as_of_date(
            settings.demo_as_of_date,
            allow_today=today_is_allowed(settings),
        )
        as_of = as_of_datetime(as_of_date)
        statements, labels = build_bank_statements(as_of=as_of_date, min_history=min_history)
        brokerage = [portfolio_a_spec(as_of=as_of_date), portfolio_b_spec(as_of=as_of_date)]
        filename_prefix = "current-demo/"
        demo_dataset = CURRENT_DEMO_DATASET
    else:
        as_of = AS_OF
        as_of_date = AS_OF.date()
        statements, labels = build_bank_statements()
        brokerage = [portfolio_a_spec(), portfolio_b_spec()]
        filename_prefix = ""
        demo_dataset = HISTORICAL_DEMO_DATASET
    storage = LocalStatementStorage(settings.local_data_dir)
    providers = build_fake_providers(as_of, brokerage_specs=brokerage)
    ingestor = StatementIngestor(storage, providers.fx)
    async with session_scope(settings) as session:
        await seed_users(session)
        await seed_replacements(session)
        await seed_risk_and_targets(session)
        await seed_mirrors(session, providers)
        await replace_generated_demo_data(session)
        for spec in statements:
            pdf = render_bank_pdf(spec)
            await ingestor.ingest(
                session,
                pdf,
                f"{filename_prefix}{spec.statement_id}.pdf",
                demo_dataset=demo_dataset,
            )
        for spec in brokerage:
            pdf = render_brokerage_pdf(spec)
            await ingestor.ingest(
                session,
                pdf,
                f"{filename_prefix}{spec.statement_id}.pdf",
                demo_dataset=demo_dataset,
            )
        await seed_labels(session, labels)
        existing_state = await session.get(DemoDatasetState, demo_dataset)
        if existing_state is None:
            session.add(
                DemoDatasetState(
                    dataset=demo_dataset,
                    as_of_date=as_of_date,
                    is_synthetic=True,
                )
            )
        else:
            existing_state.as_of_date = as_of_date
            existing_state.is_synthetic = True
        if demo_dataset == CURRENT_DEMO_DATASET:
            other = await session.get(DemoDatasetState, HISTORICAL_DEMO_DATASET)
        else:
            other = await session.get(DemoDatasetState, CURRENT_DEMO_DATASET)
        if other is not None:
            await session.delete(other)
        await session.flush()
        summary = await _build_summary(session, labels)
    summary["mode"] = mode
    summary["as_of"] = as_of_date.isoformat()
    summary_name = "current_demo_summary.json" if mode == "current" else "data_summary.json"
    summary_path = Path(settings.local_data_dir) / summary_name
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def write_statement_pdfs(*, mode: str = "historical", as_of_date: date | None = None, dest: Path | None = None) -> list[Path]:
    """Write PDFs without ingesting. Historical 2024 filenames are never reused for current-demo."""
    settings = get_settings()
    if mode == "current":
        as_of_date = as_of_date or parse_demo_as_of_date(settings.demo_as_of_date, allow_today=today_is_allowed(settings))
        dest = dest or (Path(settings.local_data_dir) / "current-demo")
        statements, _labels = build_bank_statements(as_of=as_of_date, min_history=settings.min_history_threshold)
        brokerage = [portfolio_a_spec(as_of=as_of_date), portfolio_b_spec(as_of=as_of_date)]
    else:
        dest = dest or Path(settings.local_data_dir) / "historical"
        statements, _labels = build_bank_statements()
        brokerage = [portfolio_a_spec(), portfolio_b_spec()]
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec in [*statements, *brokerage]:
        renderer = render_bank_pdf if hasattr(spec, "transactions") else render_brokerage_pdf
        path = dest / f"{spec.statement_id}.pdf"
        path.write_bytes(renderer(spec))
        written.append(path)
    return written


async def _build_summary(session, labels) -> dict:
    from app.persistence.models import BrokerageDividend, BrokeragePurchase, BrokerageSale, Statement, TaxLot

    users = list(await session.scalars(select(User)))
    statements = list(await session.scalars(select(Statement)))
    txns = list(await session.scalars(select(BankTransaction)))
    lots = list(await session.scalars(select(TaxLot)))
    sales = list(await session.scalars(select(BrokerageSale)))
    dividends = list(await session.scalars(select(BrokerageDividend)))
    purchases = list(await session.scalars(select(BrokeragePurchase)))
    by_user = {}
    for user in users:
        user_txns = [t for t in txns if t.user_id == user.id]
        user_labels = [l for l in labels if any(t.external_transaction_id == l[0] for t in user_txns)]
        by_user[str(user.id)] = {
            "email": user.email,
            "statements": len([s for s in statements if s.user_id == user.id]),
            "transactions": len(user_txns),
            "anomaly_labels": len(user_labels),
            "currencies": sorted({t.original_currency for t in user_txns}),
            "gbp": len([t for t in user_txns if t.original_currency == "GBP"]),
            "eur": len([t for t in user_txns if t.original_currency == "EUR"]),
        }
    return {
        "users": len(users),
        "bank_statements": len([s for s in statements if s.format == "SYNTHETIC_BANK_V1"]),
        "brokerage_statements": len([s for s in statements if s.format == "SYNTHETIC_BROKERAGE_V1"]),
        "transactions": len(txns),
        "tax_lots": len(lots),
        "sales": len(sales),
        "dividends": len(dividends),
        "purchases": len(purchases),
        "anomaly_labels": len(labels),
        "by_user": by_user,
        "fx_note": f"GBP/USD={GBP_USD} EUR/USD={EUR_USD} mocked",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic Phase 1 demo data")
    parser.add_argument("--mode", choices=["historical", "current"], default="historical")
    parser.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="ISO date or 'today' for current-demo mode. Tests must pass a fixed date.",
    )
    parser.add_argument("--pdfs-only", action="store_true", help="Write PDFs without database ingest")
    args = parser.parse_args()
    as_of_date = None
    if args.as_of:
        as_of_date = parse_demo_as_of_date(args.as_of, allow_today=True)
    if args.pdfs_only:
        paths = write_statement_pdfs(mode=args.mode, as_of_date=as_of_date)
        print(json.dumps({"mode": args.mode, "pdfs": [str(p) for p in paths]}, indent=2))
        return
    summary = asyncio.run(generate(mode=args.mode, as_of_date=as_of_date))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
