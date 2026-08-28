from __future__ import annotations

import asyncio
import os
import socket
import sys
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, override_settings
from app.persistence.models import Base

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.hookimpl(tryfirst=True)
def pytest_configure() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, override_settings
from app.persistence.models import Base

LIVE_HOST_FRAGMENTS = (
    "alphavantage",
    "coingecko",
    "frankfurter",
    "alpaca.markets",
    "openai.com",
    "api.openai",
)


class BlockedSocket(socket.socket):
    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        host_s = str(host).lower()
        if any(part in host_s for part in LIVE_HOST_FRAGMENTS):
            raise AssertionError(f"Unexpected live provider network call to {address}")
        return super().connect(address)


@pytest.fixture(scope="session")
def blocked_live_network():
    original = socket.socket
    socket.socket = BlockedSocket  # type: ignore[misc]
    yield
    socket.socket = original  # type: ignore[misc]


@pytest.fixture
def settings(tmp_path) -> Settings:
    url = os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get("DATABASE_URL", "postgresql+psycopg://finance:finance@localhost:5432/finance"),
    )
    cfg = Settings(
        app_env="test",
        database_url=url,
        local_data_dir=tmp_path,
        isolation_forest_seed=42,
        isolation_forest_contamination=0.06,
        min_history_threshold=80,
        quote_max_age_minutes=15,
        demo_mode="historical",
        demo_as_of_date="2026-08-28",
    )
    override_settings(cfg)
    return cfg


@pytest_asyncio.fixture
async def db_engine(settings, blocked_live_network):
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
        await session.rollback()
