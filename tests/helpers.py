from __future__ import annotations

from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.adapters.storage import LocalStatementStorage
from app.demo_data.bank_generator import build_bank_statements
from app.demo_data.bank_pdf import render_bank_pdf
from app.demo_data.brokerage_generator import portfolio_a_spec, portfolio_b_spec
from app.demo_data.brokerage_pdf import render_brokerage_pdf
from app.demo_data.constants import AS_OF
from app.demo_data.generate import (
    build_fake_providers,
    seed_labels,
    seed_mirrors,
    seed_replacements,
    seed_risk_and_targets,
    seed_users,
)
from app.providers.fakes import RecordingClock
from app.services.analysis import AnalysisDependencies
from app.services.ingestion import StatementIngestor


class UvicornTestServer:
    """Serve an ASGI app on an ephemeral loopback port for HTTP MCP tests."""

    def __init__(self, app, host: str = "127.0.0.1") -> None:
        self.app = app
        self.host = host
        self.port: int | None = None
        self._server = None
        self._task = None

    async def __aenter__(self) -> str:
        import asyncio
        import socket

        import uvicorn

        sock = socket.socket()
        sock.bind((self.host, 0))
        self.port = int(sock.getsockname()[1])
        sock.close()
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="error",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(100):
            if self._server.started:
                break
            await asyncio.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("uvicorn failed to start")
        return f"http://{self.host}:{self.port}"

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task


async def seed_historical_demo(session, settings):
    storage = LocalStatementStorage(settings.local_data_dir)
    providers = build_fake_providers(AS_OF)
    ingestor = StatementIngestor(storage, providers.fx)
    await seed_users(session)
    await seed_replacements(session)
    await seed_risk_and_targets(session)
    await seed_mirrors(session, providers)
    statements, labels = build_bank_statements()
    for spec in statements:
        await ingestor.ingest(session, render_bank_pdf(spec), f"{spec.statement_id}.pdf")
    for spec in (portfolio_a_spec(), portfolio_b_spec()):
        await ingestor.ingest(session, render_brokerage_pdf(spec), f"{spec.statement_id}.pdf")
    await seed_labels(session, labels)
    await session.commit()
    return providers


def analysis_deps(settings, session_factory, providers):
    return AnalysisDependencies(
        settings=settings,
        session_factory=session_factory,
        providers=providers,
        windows=PostgresRollingWindowStore(session_factory),
        clock=RecordingClock(AS_OF),
    )
