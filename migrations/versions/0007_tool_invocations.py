"""Phase 3 durable tool invocations: audit records and provenance.

Revision ID: 0007_tool_invocations
Revises: 0006_phase9_approvals
Create Date: 2026-09-27

Additive only. SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_tool_invocations"
down_revision: str | tuple[str, ...] | None = "0006_phase9_approvals"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("tool_invocation_id", sa.String(36), primary_key=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("execution_id", sa.String(36), nullable=False, index=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("requesting_agent", sa.String(32), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
        sa.Column("cost_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_invocations")
