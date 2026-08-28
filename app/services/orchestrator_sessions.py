from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import SessionAccessError
from app.persistence.models import AgentConversationItem, AgentConversationSession, DemoSession

RETENTION_MAX_ITEMS = 200
FORBIDDEN_SUBSTRINGS = ("BEGIN PDF", "%PDF", "api_key", "secret", "-----BEGIN")


class PostgresAgentSession:
    """Thin PostgreSQL-backed session compatible with the OpenAI Agents SDK Session protocol.

    Application constraints (user + demo-session ownership) are enforced by OrchestratorSessionService.
    Remembered items are never financial source of truth.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], session_id: UUID) -> None:
        self.session_factory = session_factory
        self.session_id = session_id

    async def get_items(self, limit: int | None = None) -> list[dict]:
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(AgentConversationItem)
                    .where(AgentConversationItem.session_id == self.session_id)
                    .order_by(AgentConversationItem.created_at)
                )
            )
        if limit is not None:
            rows = rows[-limit:]
        return [{"role": row.role, "content": row.content} for row in rows]

    async def add_items(self, items: list[dict]) -> None:
        async with self.session_factory() as session:
            for item in items:
                content = str(item.get("content") or "")
                if any(flag.lower() in content.lower() for flag in FORBIDDEN_SUBSTRINGS) or len(content) > 8000:
                    continue
                session.add(
                    AgentConversationItem(
                        id=uuid4(),
                        session_id=self.session_id,
                        role=str(item.get("role") or "user"),
                        content=content,
                    )
                )
            await session.commit()
        await self._enforce_retention()

    async def pop_item(self) -> dict | None:
        async with self.session_factory() as session:
            row = (
                await session.scalars(
                    select(AgentConversationItem)
                    .where(AgentConversationItem.session_id == self.session_id)
                    .order_by(AgentConversationItem.created_at.desc())
                )
            ).first()
            if row is None:
                return None
            payload = {"role": row.role, "content": row.content}
            await session.delete(row)
            await session.commit()
            return payload

    async def clear_session(self) -> None:
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(AgentConversationItem).where(AgentConversationItem.session_id == self.session_id)))
            for row in rows:
                await session.delete(row)
            await session.commit()

    async def _enforce_retention(self) -> None:
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(AgentConversationItem)
                    .where(AgentConversationItem.session_id == self.session_id)
                    .order_by(AgentConversationItem.created_at)
                )
            )
            extra = len(rows) - RETENTION_MAX_ITEMS
            for row in rows[: max(extra, 0)]:
                await session.delete(row)
            await session.commit()


class OrchestratorSessionService:
    """Only component allowed to create, resume, reset, or close Orchestrator sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def start(self, *, user_id: UUID, demo_session_id: UUID) -> AgentConversationSession:
        async with self.session_factory() as session:
            row = AgentConversationSession(
                id=uuid4(),
                user_id=user_id,
                demo_session_id=demo_session_id,
                sdk_session_id=str(uuid4()),
                status="ACTIVE",
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                existing = await self.get_active(user_id=user_id, demo_session_id=demo_session_id)
                if existing is None:
                    raise SessionAccessError() from exc
                return existing
            await session.refresh(row)
            return row

    async def get_active(self, *, user_id: UUID, demo_session_id: UUID) -> AgentConversationSession | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(AgentConversationSession).where(
                    AgentConversationSession.user_id == user_id,
                    AgentConversationSession.demo_session_id == demo_session_id,
                    AgentConversationSession.status == "ACTIVE",
                )
            )

    async def get_owned(self, *, session_id: UUID, user_id: UUID, demo_session_id: UUID) -> AgentConversationSession:
        async with self.session_factory() as session:
            row = await session.get(AgentConversationSession, session_id)
            if row is None or row.user_id != user_id or row.demo_session_id != demo_session_id:
                raise SessionAccessError()
            return row

    async def reset(self, *, user_id: UUID, demo_session_id: UUID) -> AgentConversationSession:
        await self.close(user_id=user_id, demo_session_id=demo_session_id)
        return await self.start(user_id=user_id, demo_session_id=demo_session_id)

    async def close(self, *, user_id: UUID, demo_session_id: UUID) -> AgentConversationSession | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(AgentConversationSession).where(
                    AgentConversationSession.user_id == user_id,
                    AgentConversationSession.demo_session_id == demo_session_id,
                    AgentConversationSession.status == "ACTIVE",
                )
            )
            if row is None:
                return None
            row.status = "CLOSED"
            row.closed_at = datetime.now(UTC)
            row.updated_at = row.closed_at
            await session.commit()
            return row

    def sdk_session(self, session_id: UUID) -> PostgresAgentSession:
        return PostgresAgentSession(self.session_factory, session_id)
