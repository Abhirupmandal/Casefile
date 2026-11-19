"""Phase 12.3: Fraud assessment and external intelligence audit tables.

Revision ID: 0010_fraud_assessments
Revises: 0009_phase12_advanced_features
Create Date: 2026-11-07

Creates fraud_assessments table for persistent risk scoring and external intelligence audit.
SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_fraud_assessments"
down_revision: str | tuple[str, ...] | None = "0009_phase12_advanced_features"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "fraud_assessments",
        sa.Column("assessment_id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=True, index=True),
        sa.Column("risk_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("risk_category", sa.String(32), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("indicators_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("risk_factors_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("external_matches_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("findings_summary", sa.Text(), nullable=False),
        sa.Column(
            "requires_human_scrutiny", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fraud_assessments")
