from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import (
    AnomalyGroundTruth,
    AnomalyScore,
    BrokerageDividend,
    BrokeragePurchase,
    BrokerageSale,
    CandidateConflictIdentity,
    Evaluation,
    ExecutionPreparation,
    HarvestingCandidate,
    Holding,
    PaperOrder,
    Statement,
    TaxLot,
    BankTransaction,
)
from app.services.freshness import CURRENT_DEMO_DATASET, HISTORICAL_DEMO_DATASET


GENERATED_DEMO_DATASETS = (CURRENT_DEMO_DATASET, HISTORICAL_DEMO_DATASET)


async def replace_generated_demo_data(
    session: AsyncSession,
    datasets: tuple[str, ...] = GENERATED_DEMO_DATASETS,
) -> int:
    """Delete previously generated demo statements and dependents.

    Uploaded statements (`demo_dataset` IS NULL) are left untouched. Status is
    never inferred from filename, user ID, or APP_ENV.
    """
    statements = list(
        await session.scalars(
            select(Statement).where(
                Statement.demo_dataset.in_(datasets),
                Statement.is_synthetic.is_(True),
            )
        )
    )
    if not statements:
        return 0
    statement_ids = [row.id for row in statements]
    portfolio_ids = {row.portfolio_id for row in statements if row.portfolio_id is not None}
    txns = list(await session.scalars(select(BankTransaction).where(BankTransaction.statement_id.in_(statement_ids))))
    txn_ids = [row.id for row in txns]
    lots = list(await session.scalars(select(TaxLot).where(TaxLot.source_statement_id.in_(statement_ids))))
    lot_ids = [row.id for row in lots]
    candidates = []
    if lot_ids:
        candidates = list(
            await session.scalars(select(HarvestingCandidate).where(HarvestingCandidate.tax_lot_id.in_(lot_ids)))
        )
    candidate_ids = [row.id for row in candidates]
    prep_ids = []
    if candidate_ids:
        preps = list(
            await session.scalars(select(ExecutionPreparation).where(ExecutionPreparation.candidate_id.in_(candidate_ids)))
        )
        prep_ids = [row.id for row in preps]
    eval_ids = []
    if candidate_ids:
        evals = list(await session.scalars(select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))))
        eval_ids = [row.id for row in evals]

    if txn_ids:
        await session.execute(delete(AnomalyScore).where(AnomalyScore.transaction_id.in_(txn_ids)))
        await session.execute(delete(AnomalyGroundTruth).where(AnomalyGroundTruth.transaction_id.in_(txn_ids)))
    if lot_ids:
        await session.execute(
            update(CandidateConflictIdentity)
            .where(CandidateConflictIdentity.tax_lot_id.in_(lot_ids))
            .values(superseded_by_id=None, latest_candidate_id=None, latest_evaluation_id=None)
        )
        await session.execute(delete(CandidateConflictIdentity).where(CandidateConflictIdentity.tax_lot_id.in_(lot_ids)))
    if prep_ids:
        await session.execute(delete(PaperOrder).where(PaperOrder.preparation_id.in_(prep_ids)))
        await session.execute(delete(ExecutionPreparation).where(ExecutionPreparation.id.in_(prep_ids)))
    if eval_ids:
        await session.execute(delete(Evaluation).where(Evaluation.id.in_(eval_ids)))
    if candidate_ids:
        await session.execute(delete(HarvestingCandidate).where(HarvestingCandidate.id.in_(candidate_ids)))
    await session.execute(delete(Holding).where(Holding.statement_id.in_(statement_ids)))
    if lot_ids:
        await session.execute(delete(TaxLot).where(TaxLot.id.in_(lot_ids)))
    if txn_ids:
        await session.execute(delete(BankTransaction).where(BankTransaction.id.in_(txn_ids)))

    for portfolio_id in portfolio_ids:
        remaining_upload = await session.scalar(
            select(Statement.id).where(
                Statement.portfolio_id == portfolio_id,
                Statement.format == "SYNTHETIC_BROKERAGE_V1",
                Statement.demo_dataset.is_(None),
                Statement.id.notin_(statement_ids),
            )
        )
        if remaining_upload is None:
            await session.execute(delete(BrokeragePurchase).where(BrokeragePurchase.portfolio_id == portfolio_id))
            await session.execute(delete(BrokerageSale).where(BrokerageSale.portfolio_id == portfolio_id))
            await session.execute(delete(BrokerageDividend).where(BrokerageDividend.portfolio_id == portfolio_id))

    await session.execute(delete(Statement).where(Statement.id.in_(statement_ids)))
    await session.flush()
    return len(statement_ids)
