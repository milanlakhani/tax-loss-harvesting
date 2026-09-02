"""python -m app.jobs.presentation_check"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.container import build_container
from app.demo_data.constants import resolve_runtime_as_of
from app.persistence.models import DemoDatasetState, Statement, TaxLot
from app.services.freshness import (
    CURRENT_DEMO_DATASET,
    HISTORICAL_DEMO_DATASET,
    brokerage_data_is_stale,
    wash_sale_coverage_complete,
)


async def check_presentation_readiness(session, settings) -> dict:
    as_of = await resolve_runtime_as_of(session, settings)
    state = await session.get(DemoDatasetState, CURRENT_DEMO_DATASET)
    current_statements = list(
        await session.scalars(
            select(Statement).where(
                Statement.demo_dataset == CURRENT_DEMO_DATASET,
                Statement.is_synthetic.is_(True),
            )
        )
    )
    historical_statements = list(
        await session.scalars(
            select(Statement).where(
                Statement.demo_dataset == HISTORICAL_DEMO_DATASET,
                Statement.is_synthetic.is_(True),
            )
        )
    )
    brokerage = [row for row in current_statements if row.format == "SYNTHETIC_BROKERAGE_V1"]
    issues: list[str] = []
    if state is None:
        issues.append("No persisted current-demo as-of. Run python -m app.jobs.seed --mode current --as-of today.")
    elif state.as_of_date != as_of.date():
        issues.append(f"Analysis as-of {as_of.date().isoformat()} does not match seeded {state.as_of_date.isoformat()}.")
    if not current_statements:
        issues.append("No generated current-demo statements are persisted.")
    if historical_statements:
        issues.append("Historical generated fixtures are still present; current-demo analysis would mix datasets.")
    if brokerage:
        stale = any(
            brokerage_data_is_stale(
                row.period_end,
                as_of,
                is_synthetic=row.is_synthetic,
                demo_dataset=row.demo_dataset,
                max_age_days=settings.demo_statement_max_age_days,
            )
            for row in brokerage
        )
        if stale:
            issues.append("Current-demo brokerage statements exceed DEMO_STATEMENT_MAX_AGE_DAYS.")
        incomplete = any(
            not wash_sale_coverage_complete(
                row.period_start,
                row.period_end,
                as_of,
                settings.wash_sale_window_days,
            )
            for row in brokerage
        )
        if incomplete:
            issues.append("Current-demo brokerage statements do not cover the full wash-sale window through as-of.")
    current_portfolios = {row.portfolio_id for row in brokerage if row.portfolio_id is not None}
    if current_portfolios:
        mixed_lots = list(
            await session.scalars(
                select(TaxLot).where(
                    TaxLot.portfolio_id.in_(current_portfolios),
                    TaxLot.source_statement_id.in_(
                        select(Statement.id).where(Statement.demo_dataset == HISTORICAL_DEMO_DATASET)
                    ),
                )
            )
        )
        if mixed_lots:
            issues.append("Historical generated tax lots remain on current-demo portfolios.")
    return {
        "ok": not issues,
        "as_of": as_of.isoformat(),
        "seeded_as_of": state.as_of_date.isoformat() if state else None,
        "current_demo_statements": len(current_statements),
        "checked_at": datetime.now(UTC).isoformat(),
        "issues": issues,
    }


async def _run() -> dict:
    container = build_container()
    async with container.session_factory() as session:
        return await check_presentation_readiness(session, container.settings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify current-demo data is ready for presentation")
    parser.parse_args()
    result = asyncio.run(_run())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
