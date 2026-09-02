from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from uuid import UUID

from app.container import build_container
from app.demo_data.constants import resolve_runtime_as_of
from app.domain.enums import AnalysisTrigger
from app.persistence.models import User
from app.services.analysis import run_analysis
from sqlalchemy import select


def _cli_idempotency_key(user_id: UUID, as_of: datetime) -> str:
    """Scope CLI retries to the user and exact analysis clock."""
    return f"cli-{user_id}-{as_of.isoformat()}"


async def _run(user_id: UUID | None, all_users: bool) -> None:
    container = build_container()
    deps = container.analysis_deps()
    async with container.session_factory() as session:
        as_of = await resolve_runtime_as_of(session, container.settings)
        if all_users:
            users = list(await session.scalars(select(User)))
            ids = [u.id for u in users]
        else:
            assert user_id is not None
            ids = [user_id]
    for uid in ids:
        result = await run_analysis(
            uid,
            trigger=AnalysisTrigger.MANUAL,
            as_of=as_of,
            idempotency_key=_cli_idempotency_key(uid, as_of),
            deps=deps,
        )
        print(
            f"{uid} status={result.status.value} run={result.analysis_run_id} "
            f"approved={len(result.approved_candidate_ids)} reused={result.reused}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run analysis via the shared application service")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", dest="user_id")
    group.add_argument("--all-users", action="store_true")
    args = parser.parse_args()
    user_id = UUID(args.user_id) if args.user_id else None
    asyncio.run(_run(user_id, args.all_users))


if __name__ == "__main__":
    main()
