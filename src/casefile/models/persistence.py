"""
CASEFILE Phase 2 SQLAlchemy persistence models (ADR-011: SQLite).

Minimal relational boundary for domain state: claims, workflow runs,
audit events, human approvals, and budget snapshots. SQLite-compatible
types only (String UUIDs, Numeric money, JSON snapshots) — no
database-specific features. Checkpoints land in Phase 7.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Engine,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for CASEFILE records."""


class ClaimRecord(Base):
    """Durable claim identity and intake state."""

    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_claim_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    policy_number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBMITTED")
    claim_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    incident_date: Mapped[str] = mapped_column(String(10), nullable=False)
    reported_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="NORMAL")
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )


class WorkflowRunRecord(Base):
    """Durable workflow run identity, state, and progress."""

    __tablename__ = "workflow_runs"

    workflow_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    current_state: Mapped[str] = mapped_column(String(32), nullable=False)
    step_count: Mapped[int] = mapped_column(nullable=False, default=0)
    rework_count: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    terminal_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditEventRecord(Base):
    """Immutable audit/event history row."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCESS")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Full structured event payload JSON"
    )


class HumanApprovalRecord(Base):
    """Durable human approval state."""

    __tablename__ = "human_approvals"

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    approver_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approver_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approver_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_override: Mapped[bool] = mapped_column(nullable=False, default=False)
    recommendation_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, doc="Recommendation snapshot JSON"
    )
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_version: Mapped[int] = mapped_column(nullable=False, default=1)
    decision_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def decision(self) -> str:
        return self.status

    @property
    def decided_by(self) -> str | None:
        return self.approver_id

    @property
    def decision_reason(self) -> str | None:
        return self.notes


class BudgetSnapshotRecord(Base):
    """Point-in-time budget state for a workflow run."""

    __tablename__ = "budget_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    input_tokens_used: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens_used: Mapped[int] = mapped_column(nullable=False, default=0)
    total_tokens_used: Mapped[int] = mapped_column(nullable=False, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0000")
    )
    steps_completed: Mapped[int] = mapped_column(nullable=False, default=0)
    tool_calls_made: Mapped[int] = mapped_column(nullable=False, default=0)
    rework_count: Mapped[int] = mapped_column(nullable=False, default=0)
    is_exhausted: Mapped[bool] = mapped_column(nullable=False, default=False)
    agent_steps: Mapped[int] = mapped_column(nullable=False, default=0)
    agent_retries: Mapped[int] = mapped_column(nullable=False, default=0)
    wall_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    elapsed_seconds: Mapped[int] = mapped_column(nullable=False, default=0)


class PolicyRecord(Base):
    """Durable policy identity, coverage, and constraints (read-only domain)."""

    __tablename__ = "policies"

    policy_number: Mapped[str] = mapped_column(String(64), primary_key=True)
    policyholder_name: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_date: Mapped[str] = mapped_column(String(10), nullable=False)
    expiration_date: Mapped[str] = mapped_column(String(10), nullable=False)
    bodily_injury_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bodily_injury_per_accident: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    property_damage: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    collision_deductible: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    comprehensive_deductible: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    exclusions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PriorClaimRecord(Base):
    """Durable prior-claim row scoped to one customer."""

    __tablename__ = "prior_claims"

    claim_reference: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    policy_number: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_date: Mapped[str] = mapped_column(String(10), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    at_fault: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class DamageEstimateRecord(Base):
    """Durable damage-estimate header scoped to one claim."""

    __tablename__ = "damage_estimates"

    estimate_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    estimate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_ref: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    estimate_date: Mapped[str] = mapped_column(String(10), nullable=False)
    labor_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    declared_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    declared_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")


class DamageLineItemRecord(Base):
    """Durable damage-estimate line item (money stays Decimal)."""

    __tablename__ = "damage_line_items"

    line_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    estimate_ref: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")


class ClaimDocumentRecord(Base):
    """Durable claim-document metadata plus bounded content."""

    __tablename__ = "claim_documents"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_ref: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    trusted_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")


class EvidenceItemRecord(Base):
    """Durable normalized evidence item with provenance."""

    __tablename__ = "evidence_items"

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_ref: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(String(256), nullable=False)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentExecutionRow(Base):
    """Durable agent execution record. No secrets, credentials, or API keys."""

    __tablename__ = "agent_executions"

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0000")
    )
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)


class FraudIndicatorRecord(Base):
    """Durable fraud indicator scoped to one claim."""

    __tablename__ = "fraud_indicators"

    indicator_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    claim_ref: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    indicator_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)


class BudgetCounterRow(Base):
    """Durable per-run budget counters for atomic reservation.

    Reservations happen through conditional single-statement UPDATEs
    (counter < limit), so exactly one concurrent worker wins.
    """

    __tablename__ = "budget_counters"

    workflow_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    steps: Mapped[int] = mapped_column(nullable=False, default=0)
    agent_steps: Mapped[int] = mapped_column(nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(nullable=False, default=0)
    rework_cycles: Mapped[int] = mapped_column(nullable=False, default=0)
    agent_retries: Mapped[int] = mapped_column(nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0000")
    )
    wall_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunBudgetEnvelopeRow(Base):
    """Immutable per-run budget envelope + monotonic terminal latch."""

    __tablename__ = "run_budget_envelopes"

    workflow_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    envelope_json: Mapped[str] = mapped_column(Text, nullable=False)
    terminal_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class CheckpointBudgetRow(Base):
    """Budget state pinned to a checkpoint for resume/replay restore."""

    __tablename__ = "checkpoint_budgets"

    checkpoint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    envelope_json: Mapped[str] = mapped_column(Text, nullable=False)
    usage_json: Mapped[str] = mapped_column(Text, nullable=False)
    terminal_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class CheckpointRow(Base):
    """Durable checkpoint snapshot for resume and deterministic replay.

    Append-only: rows are never updated. Per-run sequence enforced by a
    unique constraint; integrity is verified from the stored checksum.
    """

    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("workflow_run_id", "sequence_no"),)

    checkpoint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recorded_agents_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    path_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    approval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0.0")
    application_version: Mapped[str] = mapped_column(String(16), nullable=False, default="0.1.0")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class IdempotencyRecord(Base):
    """Durable idempotency claim: one row per applied (event, execution).

    The primary key makes claiming atomic under SQLite concurrency: the
    loser of a race gets IntegrityError and must treat its attempt as a
    duplicate.
    """

    __tablename__ = "idempotency_ledger"

    idempotency_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    result_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    result_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class ToolInvocationRecord(Base):
    """Durable tool invocation record for audit and provenance."""

    __tablename__ = "tool_invocations"

    tool_invocation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requesting_agent: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


def get_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for a CASEFILE database URL."""
    return create_engine(database_url)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to the given engine."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all Phase 2 tables (used by tests and fresh environments)."""
    Base.metadata.create_all(engine)
