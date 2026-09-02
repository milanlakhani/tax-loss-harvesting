"""demo dataset marker and persisted current-demo as-of

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-09-01 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("statements", sa.Column("demo_dataset", sa.String(length=32), nullable=True))
    op.create_index("ix_statements_demo_dataset", "statements", ["demo_dataset"], unique=False)
    op.create_table(
        "demo_dataset_state",
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("seeded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dataset"),
    )


def downgrade() -> None:
    op.drop_table("demo_dataset_state")
    op.drop_index("ix_statements_demo_dataset", table_name="statements")
    op.drop_column("statements", "demo_dataset")
