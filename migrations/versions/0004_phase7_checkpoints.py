"""Phase 7 checkpoint + idempotency ledger tables.

Revision ID: 0004_phase7_checkpoints
Revises: 0003_claim_details
Create Date: 2026-09-23

Additive only. SQLite-compatible. Checkpoints are append-only; the
idempotency ledger relies on its primary key for atomic claims.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_phase7_checkpoints"
down_revision: str | tuple[str, ...] | None = "0003_claim_details"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "checkpoints",
        sa.Column("checkpoint_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("recorded_tools_json", sa.Text(), nullable=False),
        sa.Column("recorded_agents_json", sa.Text(), nullable=False),
        sa.Column("path_json", sa.Text(), nullable=False),
        sa.Column("approval_json", sa.Text(), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("application_version", sa.String(16), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.UniqueConstraint("workflow_run_id", "sequence_no"),
    )
    op.create_table(
        "idempotency_ledger",
        sa.Column("idempotency_key", sa.String(160), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("result_kind", sa.String(32), nullable=False),
        sa.Column("result_ref", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_ledger")
    op.drop_table("checkpoints")
