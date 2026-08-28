from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_container, require_demo_session
from app.container import AppContainer
from app.demo_data.constants import resolve_analysis_as_of
from app.domain.enums import AnalysisTrigger
from app.domain.errors import ActiveAnalysisExistsError, IdempotencyConflictError
from app.persistence.models import DemoSession
from app.services.analysis import run_analysis
from app.services.queries import QueryService

router = APIRouter(prefix="/api")


class AnalysisRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    trigger: AnalysisTrigger = AnalysisTrigger.API


@router.post("/analyses")
async def start_analysis(
    body: AnalysisRequest,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
    x_idempotency_key: str | None = Header(default=None),
) -> dict:
    key = x_idempotency_key or body.idempotency_key
    try:
        result = await run_analysis(
            demo.user_id,
            trigger=body.trigger if body.trigger is not AnalysisTrigger.SCHEDULED else AnalysisTrigger.API,
            as_of=resolve_analysis_as_of(container.settings),
            idempotency_key=key,
            deps=container.analysis_deps(),
        )
    except (IdempotencyConflictError, ActiveAnalysisExistsError) as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {
        "analysis_run_id": str(result.analysis_run_id),
        "user_id": str(result.user_id),
        "status": result.status.value,
        "reused": result.reused,
        "ml_status": result.ml_status.value if result.ml_status else None,
        "approved_candidate_ids": [str(i) for i in result.approved_candidate_ids],
    }


@router.get("/analyses/{analysis_run_id}")
async def get_analysis(
    analysis_run_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    _ = demo
    async with container.session_factory() as session:
        from app.persistence.models import AnalysisRun

        run = await session.get(AnalysisRun, analysis_run_id)
        if run is None or run.user_id != demo.user_id:
            raise HTTPException(status_code=404, detail="Not found")
        return {"analysis_run_id": str(run.id), "status": run.status, "ml_status": run.ml_status}


@router.get("/analyses/{analysis_run_id}/candidates/approved")
async def approved_candidates(
    analysis_run_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    _ = demo
    async with container.session_factory() as session:
        rows = await QueryService(session).candidates(analysis_run_id, approved=True)
    return {"candidates": rows}


@router.get("/analyses/{analysis_run_id}/candidates/rejected")
async def rejected_candidates(
    analysis_run_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    _ = demo
    async with container.session_factory() as session:
        rows = await QueryService(session).candidates(analysis_run_id, approved=False)
    return {"candidates": rows}
