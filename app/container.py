from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.rolling_window import RollingWindowStore
from app.adapters.storage import LocalStatementStorage
from app.config import Settings, get_settings
from app.demo_data.constants import resolve_analysis_as_of
from app.persistence.database import get_session_factory
from app.providers.fakes import RecordingClock
from app.providers.live import build_providers, build_window_store
from app.providers.protocols import ProviderRouter
from app.services.analysis import AnalysisDependencies
from app.services.ingestion import StatementIngestor
from app.services.paper_execution import PaperExecutionService


@dataclass
class AppContainer:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    providers: ProviderRouter
    storage: LocalStatementStorage
    windows: RollingWindowStore
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

    def paper_execution(self) -> PaperExecutionService:
        return PaperExecutionService(self.settings, self.session_factory, self.providers, self.clock)


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    as_of = resolve_analysis_as_of(settings)
    windows = build_window_store(settings, factory)
    providers = build_providers(settings, as_of, windows=windows)
    storage = LocalStatementStorage(Path(settings.local_data_dir))
    clock = RecordingClock(as_of)
    return AppContainer(
        settings=settings,
        session_factory=factory,
        providers=providers,
        storage=storage,
        windows=windows,
        clock=clock,
        ingestor=StatementIngestor(storage, providers.fx),
    )
