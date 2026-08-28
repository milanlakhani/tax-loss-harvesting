from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.postgres_window_store import PostgresRollingWindowStore
from app.adapters.storage import LocalStatementStorage
from app.config import Settings, get_settings
from app.demo_data.generate import build_fake_providers
from app.persistence.database import get_session_factory
from app.providers.fakes import RecordingClock
from app.providers.protocols import ProviderRouter
from app.services.analysis import AnalysisDependencies
from app.services.ingestion import StatementIngestor


@dataclass
class AppContainer:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    providers: ProviderRouter
    storage: LocalStatementStorage
    windows: PostgresRollingWindowStore
    clock: RecordingClock
    ingestor: StatementIngestor

    def analysis_deps(self) -> AnalysisDependencies:
        return AnalysisDependencies(
            settings=self.settings,
            session_factory=self.session_factory,
            providers=self.providers,
            windows=self.windows,
            clock=self.clock,
        )


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    providers = build_fake_providers()
    storage = LocalStatementStorage(Path(settings.local_data_dir))
    windows = PostgresRollingWindowStore(factory)
    clock = RecordingClock()
    return AppContainer(
        settings=settings,
        session_factory=factory,
        providers=providers,
        storage=storage,
        windows=windows,
        clock=clock,
        ingestor=StatementIngestor(storage, providers.fx),
    )
