from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.agents.definitions import (
    build_runtime_agents,
    eval_tool_allowlist,
    ml_tool_allowlist,
    orchestrator_tool_allowlist,
    parser_tool_allowlist,
)
from app.agents.specialists import (
    DOC_PARSING_AGENT,
    EVAL_AGENT,
    ML_ANALYSIS_AGENT,
    ORCHESTRATOR_AGENT,
    complete_analysis_via_agents,
    invoke_doc_parsing_agent,
)
from app.demo_data.constants import AS_OF, USER_A_ID
from app.domain.enums import AnalysisRunStatus, AnalysisTrigger, CandidateStatus
from app.persistence.models import Evaluation, HarvestingCandidate
from app.providers.fakes import RecordingClock
from app.services.analysis import evaluate_pending_candidates, run_ml_analysis
from app.services.queries import QueryService
from tests.services.test_analysis import _deps, _seed


@pytest.mark.unit
def test_specialist_tool_allowlists_keep_eval_adversarial():
    assert "parse_statement" in parser_tool_allowlist()
    assert "run_analysis" in ml_tool_allowlist()
    assert "evaluate_pending_candidates" not in ml_tool_allowlist()
    assert "evaluate_pending_candidates" in eval_tool_allowlist()
    assert "run_analysis" not in orchestrator_tool_allowlist()
    assert "evaluate_candidate" not in orchestrator_tool_allowlist()
    assert "get_latest_candidate_decisions" in orchestrator_tool_allowlist()


@pytest.mark.unit
def test_runtime_graph_has_three_specialists_and_handoffs():
    handlers = AsyncMock()
    orchestrator, parser, ml, evaluator = build_runtime_agents(
        handlers=handlers, user_id=str(USER_A_ID), model="gpt-4.1-mini"
    )
    assert orchestrator.name == ORCHESTRATOR_AGENT
    assert parser.name == DOC_PARSING_AGENT
    assert ml.name == ML_ANALYSIS_AGENT
    assert evaluator.name == EVAL_AGENT
    orch_tools = {getattr(tool, "name", "") for tool in orchestrator.tools}
    ml_tools = {getattr(tool, "name", "") for tool in ml.tools}
    eval_tools = {getattr(tool, "name", "") for tool in evaluator.tools}
    assert "run_portfolio_analysis" not in orch_tools
    assert "evaluate_pending_candidates" not in orch_tools
    assert "run_portfolio_analysis" in ml_tools
    assert "evaluate_pending_candidates" in eval_tools
    assert parser.tools
    assert orchestrator.handoffs
    assert parser.handoffs
    assert ml.handoffs
    assert evaluator.handoffs


@pytest.mark.integration
async def test_ml_leaves_pending_candidates_until_eval(session, session_factory, settings):
    providers = await _seed(session, settings)
    deps = _deps(settings, session_factory, providers)
    proposed = await run_ml_analysis(
        USER_A_ID, trigger=AnalysisTrigger.MANUAL, as_of=AS_OF, idempotency_key="ml-only", deps=deps
    )
    assert proposed.status == AnalysisRunStatus.RUNNING
    async with session_factory() as db:
        candidates = list(
            await db.scalars(select(HarvestingCandidate).where(HarvestingCandidate.analysis_run_id == proposed.analysis_run_id))
        )
        evaluations = list(
            await db.scalars(select(Evaluation).where(Evaluation.analysis_run_id == proposed.analysis_run_id))
        )
        assert candidates
        assert all(row.status == CandidateStatus.PENDING_EVALUATION.value for row in candidates)
        assert evaluations == []
        hidden = await QueryService(db).candidates(proposed.analysis_run_id, approved=False)
        assert hidden == []
        decisions = await QueryService(db).latest_candidate_decisions(USER_A_ID)
        assert decisions["found"] is False

    evaluated = await evaluate_pending_candidates(
        USER_A_ID, deps=deps, as_of=AS_OF, analysis_run_id=proposed.analysis_run_id
    )
    assert evaluated.status == AnalysisRunStatus.COMPLETED
    assert evaluated.evaluation_ids
    async with session_factory() as db:
        leftover = list(
            await db.scalars(
                select(HarvestingCandidate).where(
                    HarvestingCandidate.analysis_run_id == proposed.analysis_run_id,
                    HarvestingCandidate.status == CandidateStatus.PENDING_EVALUATION.value,
                )
            )
        )
        assert leftover == []
        decisions = await QueryService(db).latest_candidate_decisions(USER_A_ID)
        assert decisions["found"] is True
        assert all(row["status"] != CandidateStatus.PENDING_EVALUATION.value for row in decisions["approved"] + decisions["protected"])


@pytest.mark.integration
async def test_upload_and_analysis_api_paths_invoke_named_agents(session, session_factory, settings):
    from app.adapters.storage import LocalStatementStorage
    from app.container import AppContainer
    from app.demo_data.bank_generator import build_bank_statements
    from app.demo_data.bank_pdf import render_bank_pdf
    from app.services.ingestion import StatementIngestor

    providers = await _seed(session, settings)
    storage = LocalStatementStorage(settings.local_data_dir)
    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        storage=storage,
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(AS_OF),
        ingestor=StatementIngestor(storage, providers.fx),
    )
    pdf = render_bank_pdf(build_bank_statements()[0][0])
    parsed = await invoke_doc_parsing_agent(container, filename=f"agent-{uuid4().hex}.pdf", data=pdf)
    assert parsed["agent"] == DOC_PARSING_AGENT
    assert DOC_PARSING_AGENT in parsed["agents_invoked"]

    result = await complete_analysis_via_agents(
        container, user_id=USER_A_ID, idempotency_key="agents-1", trigger=AnalysisTrigger.API
    )
    assert result["status"] == AnalysisRunStatus.COMPLETED.value
    assert result["agents_invoked"] == [ML_ANALYSIS_AGENT, EVAL_AGENT]
    assert result.get("evaluated") is True
