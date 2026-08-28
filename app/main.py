from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.analysis import router as analysis_router
from app.api.health import router as health_router
from app.api.orders import router as orders_router
from app.api.sessions import router as sessions_router
from app.api.statements import router as statements_router
from app.container import AppContainer, build_container
from app.mcp.server import build_mcp


def create_app(container: AppContainer | None = None) -> FastAPI:
    container = container or build_container()
    mcp = build_mcp(container)
    mcp_app = mcp.http_app(path="/", transport="streamable-http")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_app.lifespan(app):
            yield

    application = FastAPI(
        title="Tax Loss Harvesting Demo",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.state.container = container
    application.include_router(health_router)
    application.include_router(statements_router)
    application.include_router(analysis_router)
    application.include_router(orders_router)
    application.include_router(sessions_router)
    application.mount("/mcp", mcp_app)
    return application


app = create_app()
