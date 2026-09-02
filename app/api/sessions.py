from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents.runner import run_orchestrator_turn
from app.api.deps import get_container, require_demo_session
from app.container import AppContainer
from app.domain.errors import SessionAccessError
from app.persistence.models import DemoSession
from app.services.orchestrator_sessions import OrchestratorSessionService

router = APIRouter(prefix="/api/orchestrator-sessions")


class ChatRequest(BaseModel):
    message: str


def _svc(container: AppContainer) -> OrchestratorSessionService:
    return OrchestratorSessionService(container.session_factory)


@router.post("")
async def start_session(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    row = await _svc(container).get_active(user_id=demo.user_id, demo_session_id=demo.id)
    if row is None:
        row = await _svc(container).start(user_id=demo.user_id, demo_session_id=demo.id)
    return {"session_id": str(row.id), "status": row.status}


@router.get("/active")
async def active_session(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    row = await _svc(container).get_active(user_id=demo.user_id, demo_session_id=demo.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": str(row.id), "status": row.status}


@router.get("/{session_id}")
async def get_session(
    session_id: UUID,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    try:
        row = await _svc(container).get_owned(session_id=session_id, user_id=demo.user_id, demo_session_id=demo.id)
    except SessionAccessError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return {"session_id": str(row.id), "status": row.status}


@router.post("/reset")
async def reset_session(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    row = await _svc(container).reset(user_id=demo.user_id, demo_session_id=demo.id)
    return {"session_id": str(row.id), "status": row.status}


@router.post("/close")
async def close_session(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    row = await _svc(container).close(user_id=demo.user_id, demo_session_id=demo.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": str(row.id), "status": row.status}


@router.post("/chat")
async def chat(
    body: ChatRequest,
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    return await run_orchestrator_turn(
        container,
        user_id=demo.user_id,
        demo_session_id=demo.id,
        message=body.message,
    )
