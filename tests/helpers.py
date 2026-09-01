from __future__ import annotations

from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.adapters.storage import LocalStatementStorage
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.demo_data.constants import AS_OF
from app.demo_data.generate import (
    build_fake_providers,
    seed_labels,
    seed_mirrors,
    seed_replacements,
    seed_risk_and_targets,
    seed_users,
)
from app.providers.fakes import RecordingClock
from app.services.analysis import AnalysisDependencies
from app.services.ingestion import StatementIngestor


async def seed_historical_demo(session, settings):
    storage = LocalStatementStorage(settings.local_data_dir)
    providers = build_fake_providers(AS_OF)
    ingestor = StatementIngestor(storage, providers.fx)
    await seed_users(session)
    await seed_replacements(session)
    await seed_risk_and_targets(session)
    await seed_mirrors(session, providers)
    statements, labels = build_bank_statements()
    for spec in statements:
        await ingestor.ingest(session, render_bank_pdf(spec), f"{spec.statement_id}.pdf")
    for spec in (portfolio_a_spec(), portfolio_b_spec()):
        await ingestor.ingest(session, render_brokerage_pdf(spec), f"{spec.statement_id}.pdf")
    await seed_labels(session, labels)
    await session.commit()
    return providers


def analysis_deps(settings, session_factory, providers):
    return AnalysisDependencies(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(AS_OF),
    )
