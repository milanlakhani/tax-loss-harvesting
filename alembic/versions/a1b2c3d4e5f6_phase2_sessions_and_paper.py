"""phase2 sessions, paper-order snapshot columns, demo-session binding

Revision ID: a1b2c3d4e5f6
Revises: e74aa3e4d0fa
Create Date: 2026-08-28 04:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e74aa3e4d0fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_demo_sessions_user_id", "demo_sessions", ["user_id"], unique=False)

    op.add_column("agent_conversation_sessions", sa.Column("demo_session_id", sa.UUID(), nullable=True))
    op.add_column(
        "agent_conversation_sessions",
        sa.Column("sdk_session_id", sa.String(length=128), nullable=False, server_default=sa.text("gen_random_uuid()::text")),
    )
    op.add_column(
        "agent_conversation_sessions",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
    )
    op.add_column("agent_conversation_sessions", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_agent_conversation_sessions_demo_session_id",
        "agent_conversation_sessions",
        "demo_sessions",
        ["demo_session_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_agent_conversation_sessions_sdk", "agent_conversation_sessions", ["sdk_session_id"])
    op.create_index(
        "uq_active_orchestrator_session",
        "agent_conversation_sessions",
        ["user_id", "demo_session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.alter_column("agent_conversation_sessions", "sdk_session_id", server_default=None)
    op.alter_column("agent_conversation_sessions", "status", server_default=None)

    op.add_column("execution_preparations", sa.Column("side", sa.String(length=8), nullable=False, server_default="SELL"))
    op.add_column("execution_preparations", sa.Column("symbol", sa.String(length=64), nullable=True))
    op.add_column("execution_preparations", sa.Column("asset_type", sa.String(length=32), nullable=True))
    op.add_column("execution_preparations", sa.Column("alpaca_alias", sa.String(length=64), nullable=True))
    op.add_column("execution_preparations", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.add_column("execution_preparations", sa.Column("demo_session_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "execution_preparations",
        sa.Column("snapshot", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
    )
    op.add_column("execution_preparations", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("execution_preparations", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("execution_preparations", sa.Column("token_used_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("execution_preparations", "side", server_default=None)

    op.add_column("paper_orders", sa.Column("client_order_id", sa.String(length=128), nullable=True))
    op.add_column("paper_orders", sa.Column("fill_price", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("paper_orders", sa.Column("fill_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_orders", sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "paper_orders",
        sa.Column("requested", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
    )
    op.create_unique_constraint("uq_paper_orders_client_order_id", "paper_orders", ["client_order_id"])


def downgrade() -> None:
    op.drop_constraint("uq_paper_orders_client_order_id", "paper_orders", type_="unique")
    op.drop_column("paper_orders", "requested")
    op.drop_column("paper_orders", "last_refresh_at")
    op.drop_column("paper_orders", "fill_timestamp")
    op.drop_column("paper_orders", "fill_price")
    op.drop_column("paper_orders", "client_order_id")
    op.drop_column("execution_preparations", "token_used_at")
    op.drop_column("execution_preparations", "confirmed_at")
    op.drop_column("execution_preparations", "expires_at")
    op.drop_column("execution_preparations", "snapshot")
    op.drop_column("execution_preparations", "demo_session_hash")
    op.drop_column("execution_preparations", "token_hash")
    op.drop_column("execution_preparations", "alpaca_alias")
    op.drop_column("execution_preparations", "asset_type")
    op.drop_column("execution_preparations", "symbol")
    op.drop_column("execution_preparations", "side")
    op.drop_index("uq_active_orchestrator_session", table_name="agent_conversation_sessions")
    op.drop_constraint("uq_agent_conversation_sessions_sdk", "agent_conversation_sessions", type_="unique")
    op.drop_constraint("fk_agent_conversation_sessions_demo_session_id", "agent_conversation_sessions", type_="foreignkey")
    op.drop_column("agent_conversation_sessions", "closed_at")
    op.drop_column("agent_conversation_sessions", "status")
    op.drop_column("agent_conversation_sessions", "sdk_session_id")
    op.drop_column("agent_conversation_sessions", "demo_session_id")
    op.drop_index("ix_demo_sessions_user_id", table_name="demo_sessions")
    op.drop_table("demo_sessions")
