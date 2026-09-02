from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB, "postgresql")
UUIDType = PGUUID(as_uuid=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    accounts: Mapped[list[PortfolioAccount]] = relationship(back_populates="user")
    analysis_runs: Mapped[list[AnalysisRun]] = relationship(back_populates="user")


class PortfolioAccount(Base):
    __tablename__ = "portfolio_accounts"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alpaca_alias: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="accounts")
    statements: Mapped[list[Statement]] = relationship(
        back_populates="account",
        foreign_keys="Statement.account_id",
    )
    tax_lots: Mapped[list[TaxLot]] = relationship(
        back_populates="account",
        foreign_keys="TaxLot.account_id",
    )
    risk_profile: Mapped[RiskProfile | None] = relationship(back_populates="account", uselist=False)


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (UniqueConstraint("external_statement_id", name="uq_statements_external_id"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    external_statement_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    portfolio_id: Mapped[UUID | None] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=True)
    format: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    parsing_confidence: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False, default=Decimal("1.0"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    demo_dataset: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    account: Mapped[PortfolioAccount] = relationship(
        back_populates="statements",
        foreign_keys=[account_id],
    )
    transactions: Mapped[list[BankTransaction]] = relationship(back_populates="statement")


class BankTransaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("external_transaction_id", name="uq_transactions_external_id"),
        Index("ix_transactions_user_date", "user_id", "txn_date"),
        Index("ix_transactions_account_date", "account_id", "txn_date"),
        Index("ix_transactions_merchant", "normalized_merchant"),
        Index("ix_transactions_category", "category"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    external_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    statement_id: Mapped[UUID] = mapped_column(ForeignKey("statements.id"), nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    txn_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    txn_type: Mapped[str] = mapped_column(String(32), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    original_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    converted_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    running_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 12), nullable=True)
    fx_requested_date: Mapped[datetime | None] = mapped_column(Date(), nullable=True)
    fx_effective_date: Mapped[datetime | None] = mapped_column(Date(), nullable=True)
    fx_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsing_confidence: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, nullable=False)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    statement: Mapped[Statement] = relationship(back_populates="transactions")
    ground_truth: Mapped[AnomalyGroundTruth | None] = relationship(back_populates="transaction", uselist=False)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("canonical_id", name="uq_assets_canonical_id"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    mappings: Mapped[list[ProviderAssetMapping]] = relationship(back_populates="asset")


class ProviderAssetMapping(Base):
    __tablename__ = "provider_asset_mappings"
    __table_args__ = (
        UniqueConstraint("provider_name", "provider_symbol", name="uq_provider_asset_mapping"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    extra: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    asset: Mapped[Asset] = relationship(back_populates="mappings")


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (Index("ix_holdings_portfolio_asset", "portfolio_id", "asset_id"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    statement_id: Mapped[UUID | None] = mapped_column(ForeignKey("statements.id"), nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaxLot(Base):
    __tablename__ = "tax_lots"
    __table_args__ = (
        UniqueConstraint("external_lot_id", name="uq_tax_lots_external_id"),
        Index("ix_tax_lots_portfolio_asset", "portfolio_id", "asset_id"),
        CheckConstraint("remaining_quantity >= 0", name="ck_tax_lots_remaining_qty"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    external_lot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False)
    source_statement_id: Mapped[UUID | None] = mapped_column(ForeignKey("statements.id"), nullable=True)
    acquisition_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    per_unit_basis: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    remaining_basis: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    statement_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    missing_basis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    account: Mapped[PortfolioAccount] = relationship(
        back_populates="tax_lots",
        foreign_keys=[account_id],
    )
    asset: Mapped[Asset] = relationship()


class BrokerageSale(Base):
    __tablename__ = "brokerage_sales"
    __table_args__ = (UniqueConstraint("external_transaction_id", name="uq_sales_external_id"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    external_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False)
    acquisition_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sale_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    proceeds: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    allocated_basis: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    realized_result: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    holding_period: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BrokerageDividend(Base):
    __tablename__ = "brokerage_dividends"
    __table_args__ = (UniqueConstraint("external_transaction_id", name="uq_dividends_external_id"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    external_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    reinvested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BrokeragePurchase(Base):
    __tablename__ = "brokerage_purchases"
    __table_args__ = (UniqueConstraint("external_transaction_id", name="uq_purchases_external_id"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    external_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    is_reinvestment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_scheduled_crypto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TargetAllocation(Base):
    __tablename__ = "target_allocations"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "asset_class", "canonical_asset_id", name="uq_target_alloc"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RiskProfile(Base):
    __tablename__ = "risk_profiles"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(
        ForeignKey("portfolio_accounts.id"),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    max_crypto_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    max_single_asset_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    max_equity_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    min_bond_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    max_volatility: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    max_trade_notional: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    max_turnover: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)

    account: Mapped[PortfolioAccount] = relationship(back_populates="risk_profile")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_analysis_runs_idempotency"),
        Index("ix_analysis_runs_user_asof", "user_id", "as_of_period"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of_period: Mapped[datetime] = mapped_column(Date(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ml_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)

    user: Mapped[User] = relationship(back_populates="analysis_runs")
    candidates: Mapped[list[HarvestingCandidate]] = relationship(back_populates="analysis_run")


class PortfolioAnalysisLock(Base):
    __tablename__ = "portfolio_analysis_locks"
    __table_args__ = (
        Index(
            "uq_active_portfolio_period",
            "portfolio_id",
            "as_of_period",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    as_of_period: Mapped[datetime] = mapped_column(Date(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class HarvestingCandidate(Base):
    __tablename__ = "harvesting_candidates"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "tax_lot_id",
            name="uq_candidate_run_lot",
        ),
        Index("ix_candidates_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    tax_lot_id: Mapped[UUID] = mapped_column(ForeignKey("tax_lots.id"), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_EVALUATION")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="candidates")
    evaluations: Mapped[list[Evaluation]] = relationship(back_populates="candidate")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("harvesting_candidates.id"), nullable=False, index=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    conflict_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conflict_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    usable_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    total_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    selected_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    quote: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    quote_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    basis: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    target_before: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    target_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    risk_effect: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    drift_effect: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    replacement_canonical_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tie_breaker_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    candidate: Mapped[HarvestingCandidate] = relationship(back_populates="evaluations")


class CandidateConflictIdentity(Base):
    __tablename__ = "candidate_conflict_identities"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_conflict_fingerprint"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    tax_lot_id: Mapped[UUID] = mapped_column(ForeignKey("tax_lots.id"), nullable=False)
    canonical_asset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rejection_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_canonical_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conflict_window_start: Mapped[datetime | None] = mapped_column(Date(), nullable=True)
    conflict_window_end: Mapped[datetime | None] = mapped_column(Date(), nullable=True)
    conflicting_ids: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("candidate_conflict_identities.id"),
        nullable=True,
    )
    latest_candidate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("harvesting_candidates.id"),
        nullable=True,
    )
    latest_evaluation_id: Mapped[UUID | None] = mapped_column(ForeignKey("evaluations.id"), nullable=True)
    canonical_payload: Mapped[dict] = mapped_column(JSONType, nullable=False)


class DemoSession(Base):
    __tablename__ = "demo_sessions"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentConversationSession(Base):
    __tablename__ = "agent_conversation_sessions"
    __table_args__ = (
        Index(
            "uq_active_orchestrator_session",
            "user_id",
            "demo_session_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    demo_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("demo_sessions.id"), nullable=True, index=True)
    sdk_session_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    items: Mapped[list[AgentConversationItem]] = relationship(back_populates="session")


class AgentConversationItem(Base):
    __tablename__ = "agent_conversation_items"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversation_sessions.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    session: Mapped[AgentConversationSession] = relationship(back_populates="items")


class PaperMirrorActivity(Base):
    __tablename__ = "paper_mirror_activity"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    alpaca_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ExecutionPreparation(Base):
    __tablename__ = "execution_preparations"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("harvesting_candidates.id"), nullable=False, unique=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="SELL")
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    alpaca_alias: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    demo_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    orders: Mapped[list[PaperOrder]] = relationship(back_populates="preparation")


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    preparation_id: Mapped[UUID] = mapped_column(ForeignKey("execution_preparations.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    preparation: Mapped[ExecutionPreparation] = relationship(back_populates="orders")


class AnomalyGroundTruth(Base):
    __tablename__ = "anomaly_ground_truth"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("transactions.id"),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    anomaly_type: Mapped[str] = mapped_column(String(64), nullable=False)
    injected_reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    transaction: Mapped[BankTransaction] = relationship(back_populates="ground_truth")


class AnomalyScore(Base):
    __tablename__ = "anomaly_scores"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "transaction_id", name="uq_anomaly_score_run_txn"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey("transactions.id"), nullable=False)
    raw_decision_score: Mapped[Decimal] = mapped_column(Numeric(20, 12), nullable=False)
    normalized_score: Mapped[Decimal] = mapped_column(Numeric(20, 12), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    ml_status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    features: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ReplacementRelationship(Base):
    __tablename__ = "replacement_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_canonical_id",
            "replacement_canonical_id",
            "rule_version",
            name="uq_replacement_rel",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    source_canonical_id: Mapped[str] = mapped_column(String(128), nullable=False)
    replacement_canonical_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relationship: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RollingWindowRecord(Base):
    __tablename__ = "rolling_window_records"
    __table_args__ = (
        UniqueConstraint("logical_key", "sort_key", name="uq_rolling_window_identity"),
        Index("ix_rolling_window_logical", "logical_key"),
    )

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    logical_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MirrorManifest(Base):
    __tablename__ = "mirror_manifests"
    __table_args__ = (UniqueConstraint("portfolio_id", name="uq_mirror_portfolio"),)

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_accounts.id"), nullable=False)
    alpaca_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DemoDatasetState(Base):
    """Persisted seed clock for a generated demo dataset. Not inferred from APP_ENV."""

    __tablename__ = "demo_dataset_state"

    dataset: Mapped[str] = mapped_column(String(32), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date(), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
