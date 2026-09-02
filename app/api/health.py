from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.agents.mcp_client import probe_mcp
from app.agents.specialists import complete_analysis_via_agents
from app.container import AppContainer, build_container
from app.domain.enums import AnalysisTrigger
from app.domain.errors import ActiveAnalysisExistsError, IdempotencyConflictError
from app.services.analysis import run_analysis

router = APIRouter()


class AnalysisRequest(BaseModel):
    trigger: AnalysisTrigger = AnalysisTrigger.API
    idempotency_key: str = Field(min_length=1, max_length=128)


class AnalysisResponse(BaseModel):
    analysis_run_id: UUID
    user_id: UUID
    status: str
    reused: bool
    ml_status: str | None
    approved_candidate_ids: list[UUID]


def get_container() -> AppContainer:
    return build_container()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "phase": "2"}


@router.get("/health/ready")
async def ready(container: AppContainer = Depends(get_container)) -> dict[str, str]:
    async with container.session_factory() as session:
        await session.execute(text("SELECT 1"))
    if not await probe_mcp(container.settings.mcp_server_url):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": "mcp_unreachable",
            },
        )
    return {"status": "ready", "mcp": "ok"}


@router.post("/v1/users/{user_id}/analysis", response_model=AnalysisResponse)
async def create_analysis(
    user_id: UUID,
    body: AnalysisRequest,
    container: AppContainer = Depends(get_container),
    x_idempotency_key: str | None = Header(default=None),
) -> AnalysisResponse:
    key = x_idempotency_key or body.idempotency_key
    try:
        result = await complete_analysis_via_agents(
            container,
            user_id=user_id,
            idempotency_key=key,
            trigger=body.trigger if body.trigger is not AnalysisTrigger.SCHEDULED else AnalysisTrigger.API,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except ActiveAnalysisExistsError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return AnalysisResponse(
        analysis_run_id=UUID(result["analysis_run_id"]),
        user_id=UUID(result["user_id"]),
        status=result["status"],
        reused=result["reused"],
        ml_status=result.get("ml_status"),
        approved_candidate_ids=[UUID(i) for i in result.get("approved_candidate_ids") or []],
    )
