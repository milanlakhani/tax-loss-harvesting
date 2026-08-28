from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from app.container import build_container
from app.demo_data.constants import AS_OF
from app.domain.enums import AnalysisTrigger
from app.persistence.models import User
from app.services.analysis import run_analysis
from sqlalchemy import select


async def _run(user_id: UUID | None, all_users: bool) -> None:
    container = build_container()
    deps = container.analysis_deps()
    async with container.session_factory() as session:
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
            as_of=AS_OF,
            idempotency_key=f"cli-{uid}",
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
