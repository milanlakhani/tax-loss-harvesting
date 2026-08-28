from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_container
from app.container import AppContainer
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.constants import AS_OF, USER_A_ID
from app.domain.enums import AnalysisTrigger, CandidateStatus
from app.main import create_app
from app.mcp.tools import McpToolHandlers
from app.persistence.models import HarvestingCandidate, TaxLot
from app.providers.fakes import RecordingClock
from app.services.analysis import run_analysis
from app.services.demo_session import DemoSessionService
from app.services.paper_execution import PaperExecutionService
from app.services.queries import QueryService
from tests.helpers import analysis_deps, seed_historical_demo


@pytest.mark.integration
async def test_statement_questions_go_through_mcp_handlers(session, session_factory, settings):
    providers = await seed_historical_demo(session, settings)
    from app.adapters.postgres_window_store import PostgresRollingWindowStore
    from app.adapters.storage import LocalStatementStorage
    from app.services.ingestion import StatementIngestor

    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        storage=LocalStatementStorage(settings.local_data_dir),
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(AS_OF),
        ingestor=StatementIngestor(LocalStatementStorage(settings.local_data_dir), providers.fx),
    )
    handlers = McpToolHandlers(container)
    pdf = render_bank_pdf(build_bank_statements()[0][0])
    parsed = await handlers.parse_statement("BANK-0-dup.pdf", pdf.hex())
    assert parsed["format"]
    summary = await handlers.get_spending_summary(str(USER_A_ID))
    assert summary
    holdings = await handlers.get_holdings(str(USER_A_ID))
    assert holdings
    again = await handlers.get_holdings(str(USER_A_ID))
    assert again == holdings


@pytest.mark.integration
async def test_holdings_to_confirmed_order_and_freeform_api_rejected(session, session_factory, settings):
    providers = await seed_historical_demo(session, settings)
    deps = analysis_deps(settings, session_factory, providers)
    result = await run_analysis(USER_A_ID, trigger=AnalysisTrigger.MANUAL, as_of=AS_OF, idempotency_key="e2e-order", deps=deps)
    enabled = settings.model_copy(update={"enable_paper_orders": True})
    from app.adapters.postgres_window_store import PostgresRollingWindowStore
    from app.adapters.storage import LocalStatementStorage
    from app.services.ingestion import StatementIngestor

    container = AppContainer(
        settings=enabled,
        session_factory=session_factory,
        providers=providers,
        storage=LocalStatementStorage(settings.local_data_dir),
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(AS_OF),
        ingestor=StatementIngestor(LocalStatementStorage(settings.local_data_dir), providers.fx),
    )
    demo_token = await DemoSessionService(settings, session_factory).create(USER_A_ID)
    async with session_factory() as db:
        lot = await db.scalar(select(TaxLot).where(TaxLot.external_lot_id == "A-VTI-APPROVED"))
        candidate = await db.scalar(
            select(HarvestingCandidate).where(
                HarvestingCandidate.analysis_run_id == result.analysis_run_id,
                HarvestingCandidate.tax_lot_id == lot.id,
            )
        )
        assert candidate.status == CandidateStatus.APPROVED.value
        rows = await QueryService(db).holdings(USER_A_ID)
        assert rows

    paper = PaperExecutionService(enabled, session_factory, providers, RecordingClock(AS_OF))
    prepared = await paper.prepare(candidate_id=candidate.id, demo_session_token=demo_token)
    confirmed = await paper.confirm(candidate_id=candidate.id, token=prepared["token"], demo_session_token=demo_token)
    assert confirmed["provider_order_id"]
    assert len(providers.execution.submit_calls) == 1

    app = create_app(container)
    app.dependency_overrides[get_container] = lambda: container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/candidates/{candidate.id}/confirm",
            headers={"X-Demo-Session": demo_token},
            json={"token": prepared["token"], "symbol": "VTI", "quantity": "99", "side": "BUY"},
        )
        assert response.status_code == 422
