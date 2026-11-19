"""Phase 12 advanced features: language, batch metadata, partial approval, and feedback.

Revision ID: 0009_phase12_advanced_features
Revises: 0008_phase5_approval_metadata
Create Date: 2026-11-07

Additive schema changes for:
- Claim language and batch identifier
- Human-approved partial amounts
- Batches and disaster context
- Structured human reviewer feedback (active learning)
- Counterfactual explanation scenarios

SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_phase12_advanced_features"
down_revision: str | tuple[str, ...] | None = "0008_phase5_approval_metadata"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # 1. Additive columns to existing claims table
    with op.batch_alter_table("claims") as batch_op:
        batch_op.add_column(
            sa.Column("language", sa.String(10), nullable=False, server_default="en")
        )
        batch_op.add_column(sa.Column("batch_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_claims_batch_id", ["batch_id"])

    # 2. Additive column to existing human_approvals table
    with op.batch_alter_table("human_approvals") as batch_op:
        batch_op.add_column(sa.Column("approved_amount", sa.Numeric(12, 2), nullable=True))

    # 3. Create batches table
    op.create_table(
        "batches",
        sa.Column("batch_id", sa.String(36), primary_key=True),
        sa.Column("batch_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("disaster_id", sa.String(64), nullable=True),
        sa.Column("disaster_type", sa.String(64), nullable=True),
        sa.Column("disaster_name", sa.String(128), nullable=True),
        sa.Column("shared_context_json", sa.Text(), nullable=True),
        sa.Column("total_claims", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # 4. Create approval_feedback table
    op.create_table(
        "approval_feedback",
        sa.Column("feedback_id", sa.String(36), primary_key=True),
        sa.Column("approval_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("adjuster_notes", sa.Text(), nullable=False),
        sa.Column("overridden_recommendation", sa.String(64), nullable=True),
        sa.Column("sanitized_sample_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    # 5. Create counterfactual_scenarios table
    op.create_table(
        "counterfactual_scenarios",
        sa.Column("scenario_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("factor_name", sa.String(64), nullable=False),
        sa.Column("current_value", sa.String(256), nullable=False),
        sa.Column("counterfactual_value", sa.String(256), nullable=False),
        sa.Column("hypothetical_outcome", sa.String(256), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    # 1. Drop new tables
    op.drop_table("counterfactual_scenarios")
    op.drop_table("approval_feedback")
    op.drop_table("batches")

    # 2. Revert human_approvals table
    with op.batch_alter_table("human_approvals") as batch_op:
        batch_op.drop_column("approved_amount")

    # 3. Revert claims table
    with op.batch_alter_table("claims") as batch_op:
        batch_op.drop_index("ix_claims_batch_id")
        batch_op.drop_column("batch_id")
        batch_op.drop_column("language")
