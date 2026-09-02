from __future__ import annotations

from uuid import UUID

from app.container import AppContainer
from app.demo_data.constants import resolve_runtime_as_of
from app.domain.enums import AnalysisTrigger
from app.services.analysis import evaluate_pending_candidates, run_ml_analysis

DOC_PARSING_AGENT = "Document Parsing Agent"
ML_ANALYSIS_AGENT = "ML Analysis Agent"
EVAL_AGENT = "Eval Agent"
ORCHESTRATOR_AGENT = "Orchestrator Agent"


def _analysis_payload(result) -> dict:
    return {
        "analysis_run_id": str(result.analysis_run_id),
        "user_id": str(result.user_id),
        "status": result.status.value,
        "failure_reason": result.failure_reason,
        "reused": result.reused,
        "ml_status": result.ml_status.value if result.ml_status else None,
        "candidate_ids": [str(i) for i in result.candidate_ids],
        "evaluation_ids": [str(i) for i in result.evaluation_ids],
        "approved_candidate_ids": [str(i) for i in result.approved_candidate_ids],
    }


async def invoke_doc_parsing_agent(container: AppContainer, *, filename: str, data: bytes) -> dict:
    """Synchronous upload path: Orchestrator delegates to the Doc Parsing Agent pipeline."""
    async with container.session_factory() as session:
        result = await container.ingestor.ingest(session, data, filename)
        await session.commit()
    return {
        "statement_id": str(result.statement_id),
        "format": result.format.value,
        "reused": result.reused,
        "transaction_count": result.transaction_count,
        "lot_count": result.lot_count,
        "status": "ingested" if not result.reused else "duplicate",
        "agent": DOC_PARSING_AGENT,
        "agents_invoked": [DOC_PARSING_AGENT],
    }


async def invoke_ml_analysis_agent(
    container: AppContainer,
    *,
    user_id: UUID,
    idempotency_key: str,
    trigger: AnalysisTrigger = AnalysisTrigger.API,
) -> dict:
    async with container.session_factory() as session:
        as_of = await resolve_runtime_as_of(session, container.settings)
    result = await run_ml_analysis(
        user_id,
        trigger=trigger,
        as_of=as_of,
        idempotency_key=idempotency_key,
        deps=container.analysis_deps(),
    )
    payload = _analysis_payload(result)
    payload["agent"] = ML_ANALYSIS_AGENT
    payload["agents_invoked"] = [ML_ANALYSIS_AGENT]
    payload["evaluated"] = False
    return payload


async def invoke_eval_agent(
    container: AppContainer,
    *,
    user_id: UUID,
    analysis_run_id: UUID | None = None,
) -> dict:
    async with container.session_factory() as session:
        as_of = await resolve_runtime_as_of(session, container.settings)
        try:
            result = await evaluate_pending_candidates(
                user_id,
                deps=container.analysis_deps(),
                as_of=as_of,
                analysis_run_id=analysis_run_id,
            )
        except KeyError:
            return {
                "found": False,
                "agent": EVAL_AGENT,
                "agents_invoked": [EVAL_AGENT],
                "approved_candidate_ids": [],
            }
    payload = _analysis_payload(result)
    payload["found"] = True
    payload["agent"] = EVAL_AGENT
    payload["agents_invoked"] = [EVAL_AGENT]
    payload["evaluated"] = True
    return payload


async def complete_analysis_via_agents(
    container: AppContainer,
    *,
    user_id: UUID,
    idempotency_key: str,
    trigger: AnalysisTrigger = AnalysisTrigger.API,
) -> dict:
    """ML writes pending candidates; Eval must persist statuses before the user can see them."""
    ml = await invoke_ml_analysis_agent(
        container, user_id=user_id, idempotency_key=idempotency_key, trigger=trigger
    )
    if ml.get("status") == "FAILED":
        ml["agents_invoked"] = [ML_ANALYSIS_AGENT]
        return ml
    run_id = UUID(ml["analysis_run_id"])
    ev = await invoke_eval_agent(container, user_id=user_id, analysis_run_id=run_id)
    ev["ml_status"] = ml.get("ml_status")
    ev["agents_invoked"] = [ML_ANALYSIS_AGENT, EVAL_AGENT]
    return ev
