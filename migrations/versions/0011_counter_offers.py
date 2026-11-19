"""Phase 12.4: Counter-offers and negotiation workflow tables.

Revision ID: 0011_counter_offers
Revises: 0010_fraud_assessments
Create Date: 2026-11-08

Creates counter_offers table for durable negotiation lifecycle and partial approvals.
SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_counter_offers"
down_revision: str | tuple[str, ...] | None = "0010_fraud_assessments"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "counter_offers",
        sa.Column("offer_id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.String(36), nullable=False, index=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=True, index=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("original_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("previously_approved_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("proposed_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("reduction_reason", sa.Text(), nullable=False),
        sa.Column("disputed_items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("response_action", sa.String(32), nullable=True),
        sa.Column("response_reason", sa.Text(), nullable=True),
        sa.Column("responded_by", sa.String(128), nullable=True),
        sa.Column("superseded_by_offer_id", sa.String(36), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("claim_id", "sequence_number", name="uq_counter_offer_claim_sequence"),
    )


def downgrade() -> None:
    op.drop_table("counter_offers")
