from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_container, require_demo_session
from app.container import AppContainer
from app.domain.errors import PaperExecutionError
from app.persistence.models import DemoSession
from app.services.paper_execution import PaperExecutionService

router = APIRouter(prefix="/api")


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8)


def _paper(container: AppContainer) -> PaperExecutionService:
    return container.paper_execution()


@router.post("/candidates/{candidate_id}/prepare")
async def prepare_order(
    candidate_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
    x_demo_session: str = Header(alias="X-Demo-Session"),
) -> dict:
    _ = demo
    try:
        return await _paper(container).prepare(candidate_id=candidate_id, demo_session_token=x_demo_session)
    except PaperExecutionError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/candidates/{candidate_id}/confirm")
async def confirm_order(
    candidate_id: UUID,
    body: ConfirmRequest,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
    x_demo_session: str = Header(alias="X-Demo-Session"),
) -> dict:
    _ = demo
    try:
        return await _paper(container).confirm(
            candidate_id=candidate_id,
            token=body.token,
            demo_session_token=x_demo_session,
        )
    except PaperExecutionError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/paper-orders/{order_id}")
async def get_paper_order(
    order_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    _ = demo
    from app.services.queries import QueryService

    async with container.session_factory() as session:
        row = await QueryService(session).paper_order_status(order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.post("/paper-orders/{order_id}/refresh")
async def refresh_paper_order(
    order_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    _ = demo
    try:
        return await _paper(container).refresh(order_id=order_id)
    except PaperExecutionError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
