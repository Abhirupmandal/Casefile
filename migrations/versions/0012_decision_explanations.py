"""Phase 12.5: Decision explanations table.

Revision ID: 0012_decision_explanations
Revises: 0011_counter_offers
Create Date: 2026-11-08

Creates decision_explanations table for durable explainability records and counterfactuals.
SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_decision_explanations"
down_revision: str | tuple[str, ...] | None = "0011_counter_offers"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_explanations",
        sa.Column("explanation_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("recommendation", sa.String(32), nullable=False, server_default="APPROVE"),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False, server_default="1.0000"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_factors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("positive_factors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("negative_factors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_citations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("factor_contributions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("missing_information_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "model_name",
            sa.String(64),
            nullable=False,
            server_default="casefile-explanation-engine",
        ),
        sa.Column("model_version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("assessment_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("decision_explanations")
