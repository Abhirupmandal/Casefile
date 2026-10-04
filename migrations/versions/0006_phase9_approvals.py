"""Phase 9 approval durability: versioning, decision keys, checkpoint links.

Revision ID: 0006_phase9_approvals
Revises: 0005_phase8_budgets
Create Date: 2026-09-23

Additive only, all nullable/defaulted. SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_phase9_approvals"
down_revision: str | tuple[str, ...] | None = "0005_phase8_budgets"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("human_approvals", sa.Column("execution_id", sa.String(36), nullable=True))
    op.add_column("human_approvals", sa.Column("checkpoint_id", sa.String(36), nullable=True))
    op.add_column("human_approvals", sa.Column("request_version", sa.Integer(), nullable=True))
    op.add_column("human_approvals", sa.Column("decision_key", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("human_approvals", "decision_key")
    op.drop_column("human_approvals", "request_version")
    op.drop_column("human_approvals", "checkpoint_id")
    op.drop_column("human_approvals", "execution_id")
