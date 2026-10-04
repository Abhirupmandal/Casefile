"""Phase 2 initial tables: claims, workflow_runs, audit_events, human_approvals, budget_snapshots.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-09-22

SQLite-compatible only (String UUIDs, Numeric money, JSON snapshots).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | tuple[str, ...] | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("claim_id", sa.String(36), primary_key=True),
        sa.Column("external_claim_id", sa.String(100), unique=True, nullable=False),
        sa.Column("policy_number", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.String(100), nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("claim_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("incident_date", sa.String(10), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("workflow_run_id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("current_state", sa.String(32), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("rework_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("terminal_state", sa.String(32), nullable=True),
        sa.Column("termination_reason", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("from_state", sa.String(32), nullable=True),
        sa.Column("to_state", sa.String(32), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
    )
    op.create_table(
        "human_approvals",
        sa.Column("approval_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approver_id", sa.String(128), nullable=True),
        sa.Column("approver_name", sa.String(128), nullable=True),
        sa.Column("approver_role", sa.String(64), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_override", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "budget_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("input_tokens_used", sa.Integer(), nullable=False),
        sa.Column("output_tokens_used", sa.Integer(), nullable=False),
        sa.Column("total_tokens_used", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("steps_completed", sa.Integer(), nullable=False),
        sa.Column("tool_calls_made", sa.Integer(), nullable=False),
        sa.Column("rework_count", sa.Integer(), nullable=False),
        sa.Column("is_exhausted", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("budget_snapshots")
    op.drop_table("human_approvals")
    op.drop_table("audit_events")
    op.drop_table("workflow_runs")
    op.drop_table("claims")
