"""Phase 8 budget durability: counters, envelopes, checkpoint budgets.

Revision ID: 0005_phase8_budgets
Revises: 0004_phase7_checkpoints
Create Date: 2026-09-23

Additive only. SQLite-compatible. Extends budget_snapshots with the
Phase 8 usage dimensions; existing rows keep working (new columns
nullable or defaulted).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_phase8_budgets"
down_revision: str | tuple[str, ...] | None = "0004_phase7_checkpoints"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_counters",
        sa.Column("workflow_run_id", sa.String(36), primary_key=True),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("agent_steps", sa.Integer(), nullable=False),
        sa.Column("agent_retries", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("rework_cycles", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("wall_start", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "run_budget_envelopes",
        sa.Column("workflow_run_id", sa.String(36), primary_key=True),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.Column("terminal_reason", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "checkpoint_budgets",
        sa.Column("checkpoint_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.Column("usage_json", sa.Text(), nullable=False),
        sa.Column("terminal_reason", sa.String(32), nullable=True),
    )
    op.add_column("budget_snapshots", sa.Column("agent_steps", sa.Integer(), nullable=True))
    op.add_column("budget_snapshots", sa.Column("agent_retries", sa.Integer(), nullable=True))
    op.add_column("budget_snapshots", sa.Column("wall_start", sa.DateTime(), nullable=True))
    op.add_column("budget_snapshots", sa.Column("elapsed_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("budget_snapshots", "elapsed_seconds")
    op.drop_column("budget_snapshots", "wall_start")
    op.drop_column("budget_snapshots", "agent_retries")
    op.drop_column("budget_snapshots", "agent_steps")
    op.drop_table("checkpoint_budgets")
    op.drop_table("run_budget_envelopes")
    op.drop_table("budget_counters")
