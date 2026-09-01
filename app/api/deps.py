from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from app.container import AppContainer, build_container
from app.domain.errors import SessionAccessError
from app.services.demo_session import DemoSessionService


def get_container() -> AppContainer:
    return build_container()


async def require_demo_session(
    x_demo_session: str | None = Header(default=None, alias="X-Demo-Session"),
    container: AppContainer = Depends(get_container),
):
    if not x_demo_session:
        raise HTTPException(status_code=401, detail="Missing demo session")
    try:
        return await DemoSessionService(container.settings, container.session_factory).resolve(x_demo_session)
    except SessionAccessError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
