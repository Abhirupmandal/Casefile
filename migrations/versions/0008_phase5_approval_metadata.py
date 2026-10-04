"""Phase 5 approval durability: deadline, requested_by, and required_role.

Revision ID: 0008_phase5_approval_metadata
Revises: 0007_tool_invocations
Create Date: 2026-10-04

Additive only, all nullable/defaulted. SQLite-compatible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_phase5_approval_metadata"
down_revision: str | tuple[str, ...] | None = "0007_tool_invocations"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("human_approvals", sa.Column("deadline", sa.DateTime(), nullable=True))
    op.add_column("human_approvals", sa.Column("requested_by", sa.String(128), nullable=True))
    op.add_column("human_approvals", sa.Column("required_role", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("human_approvals", "required_role")
    op.drop_column("human_approvals", "requested_by")
    op.drop_column("human_approvals", "deadline")
