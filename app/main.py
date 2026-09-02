from __future__ import annotations

from fastapi import FastAPI

from app.api.analysis import router as analysis_router
from app.api.health import router as health_router
from app.api.orders import router as orders_router
from app.api.sessions import router as sessions_router
from app.api.statements import router as statements_router
from app.api.whatsapp import router as whatsapp_router
from app.container import AppContainer, build_container


def create_app(container: AppContainer | None = None) -> FastAPI:
    container = container or build_container()
    application = FastAPI(
        title="Tax Loss Harvesting Demo",
        version="0.2.0",
    )
    application.state.container = container
    application.include_router(health_router)
    application.include_router(statements_router)
    application.include_router(analysis_router)
    application.include_router(orders_router)
    application.include_router(sessions_router)
    application.include_router(whatsapp_router)
    return application


app = create_app()
