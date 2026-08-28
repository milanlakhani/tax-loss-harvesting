from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_container, require_demo_session
from app.container import AppContainer
from app.persistence.models import DemoSession
from app.services.demo_session import DemoSessionService

router = APIRouter(prefix="/api")


class DemoSessionRequest(BaseModel):
    user_id: UUID
    token: str | None = None


@router.post("/demo-sessions")
async def create_demo_session(body: DemoSessionRequest, container: AppContainer = Depends(get_container)) -> dict:
    token = await DemoSessionService(container.settings, container.session_factory).create(body.user_id, body.token)
    return {"demo_session_token": token, "note": "Server-bound demo session; not authentication."}


@router.post("/statements")
async def upload_statement(
    file: UploadFile = File(...),
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    data = await file.read()
    async with container.session_factory() as session:
        result = await container.ingestor.ingest(session, data, file.filename or "upload.pdf")
        await session.commit()
    _ = demo
    return {
        "statement_id": str(result.statement_id),
        "format": result.format.value,
        "reused": result.reused,
        "status": "ingested" if not result.reused else "duplicate",
    }


@router.get("/holdings")
async def get_holdings(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    from app.services.queries import QueryService

    async with container.session_factory() as session:
        rows = await QueryService(session).holdings(demo.user_id)
    return {"holdings": rows}
