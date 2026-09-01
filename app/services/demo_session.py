from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.errors import SessionAccessError
from app.persistence.models import DemoSession, User


def hash_demo_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


class DemoSessionService:
    """Server-bound demo session. This is not authentication."""

    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_factory = session_factory

    async def create(self, user_id: UUID, token: str | None = None) -> str:
        token = token or secrets.token_urlsafe(32)
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise SessionAccessError()
            digest = hash_demo_token(token, self.settings.demo_session_signing_secret)
            existing = await session.scalar(select(DemoSession).where(DemoSession.token_hash == digest))
            if existing is not None:
                if existing.user_id != user_id:
                    raise SessionAccessError()
                return token
            session.add(
                DemoSession(
                    id=uuid4(),
                    user_id=user_id,
                    token_hash=digest,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
            await session.commit()
            return token

    async def resolve(self, token: str) -> DemoSession:
        digest = hash_demo_token(token, self.settings.demo_session_signing_secret)
        async with self.session_factory() as session:
            row = await session.scalar(select(DemoSession).where(DemoSession.token_hash == digest))
            if row is None:
                raise SessionAccessError()
            return row
