from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_container, require_demo_session
from app.container import AppContainer
from app.domain.errors import ParseError
from app.demo_data.constants import resolve_runtime_as_of
from app.persistence.models import DemoSession
from app.services.demo_session import DemoSessionService

router = APIRouter(prefix="/api")


def validate_pdf_upload(file_obj, *, max_bytes: int = 10 * 1024 * 1024) -> dict:
    """Validate an uploaded PDF-like file before ingesting it."""
    if file_obj is None:
        return {"ok": False, "reason": "No file provided", "size_bytes": 0, "extension": "", "mime_type": ""}

    try:
        file_obj.seek(0)
        prefix = file_obj.read(8)
        file_obj.seek(0)
    except (AttributeError, OSError):
        return {"ok": False, "reason": "Unable to read uploaded file", "size_bytes": 0, "extension": "", "mime_type": ""}

    if not prefix:
        return {"ok": False, "reason": "Empty file", "size_bytes": 0, "extension": "", "mime_type": ""}
    if prefix[:5] != b"%PDF-":
        return {"ok": False, "reason": "File does not appear to be a valid PDF", "size_bytes": 0, "extension": "", "mime_type": ""}

    try:
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(0)
    except (AttributeError, OSError):
        return {"ok": False, "reason": "Unable to determine uploaded file size", "size_bytes": 0, "extension": "", "mime_type": ""}

    name = getattr(file_obj, "name", "")
    extension = Path(name).suffix.lower() if name else ""
    if extension and extension != ".pdf":
        return {"ok": False, "reason": f"Unexpected extension: {extension}", "size_bytes": size, "extension": extension, "mime_type": "application/pdf"}
    if size <= 0:
        return {"ok": False, "reason": "File is empty", "size_bytes": size, "extension": extension or ".pdf", "mime_type": "application/pdf"}
    if size > max_bytes:
        return {"ok": False, "reason": f"File exceeds limit of {max_bytes} bytes", "size_bytes": size, "extension": extension or ".pdf", "mime_type": "application/pdf"}

    mime_type = "application/pdf"
    guessed = mimetypes.guess_type(name or "document.pdf")[0]
    if guessed:
        mime_type = guessed

    return {"ok": True, "reason": "Valid PDF upload", "size_bytes": size, "extension": extension or ".pdf", "mime_type": mime_type}


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
    validation = validate_pdf_upload(BytesIO(data), max_bytes=10 * 1024 * 1024)
    if not validation["ok"]:
        raise HTTPException(status_code=400, detail=validation["reason"])
    if file.content_type and file.content_type.lower() != "application/pdf":
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF")

    try:
        async with container.session_factory() as session:
            result = await container.ingestor.ingest(session, data, file.filename or "upload.pdf")
            await session.commit()
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
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


@router.get("/anomalies")
async def get_anomalies(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    from app.services.queries import QueryService

    async with container.session_factory() as session:
        rows = await QueryService(session).anomalous_transactions(demo.user_id)
    return {"anomalies": rows}


@router.get("/portfolio-insights")
async def get_portfolio_insights(
    container: AppContainer = Depends(get_container),
    demo: DemoSession = Depends(require_demo_session),
) -> dict:
    from app.services.queries import QueryService

    async with container.session_factory() as session:
        as_of = await resolve_runtime_as_of(session, container.settings)
        rows = await QueryService(session).portfolio_insights(
            demo.user_id,
            container.providers,
            as_of,
        )
    return {"portfolios": rows}
