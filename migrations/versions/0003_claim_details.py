"""Phase 6 follow-up: full Claim aggregate columns for round-trip equality.

Revision ID: 0003_claim_details
Revises: 0002_phase6_repositories
Create Date: 2026-09-23

Additive only. SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_claim_details"
down_revision: str | tuple[str, ...] | None = "0002_phase6_repositories"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("reported_date", sa.String(10), nullable=True))
    op.add_column("claims", sa.Column("priority", sa.String(16), nullable=True))
    op.add_column("claims", sa.Column("submitted_by", sa.String(128), nullable=True))
    op.add_column("claims", sa.Column("submitted_at", sa.DateTime(), nullable=True))
    op.add_column("evidence_items", sa.Column("collected_at", sa.DateTime(), nullable=True))
    op.add_column("damage_estimates", sa.Column("estimate_id", sa.String(36), nullable=True))
    op.add_column("claim_documents", sa.Column("ingested_at", sa.DateTime(), nullable=True))
    op.add_column("claim_documents", sa.Column("extraction_status", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("claim_documents", "extraction_status")
    op.drop_column("claim_documents", "ingested_at")
    op.drop_column("damage_estimates", "estimate_id")
    op.drop_column("evidence_items", "collected_at")
    op.drop_column("claims", "submitted_at")
    op.drop_column("claims", "submitted_by")
    op.drop_column("claims", "priority")
    op.drop_column("claims", "reported_date")
