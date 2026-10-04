"""Phase 6 durable repositories: policies, priors, estimates, documents,
evidence, agent executions, fraud indicators + audit/approval JSON columns.

Revision ID: 0002_phase6_repositories
Revises: 0001_initial
Create Date: 2026-09-23

Additive only. SQLite-compatible. No checkpoint tables (Phase 7).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_phase6_repositories"
down_revision: str | tuple[str, ...] | None = "0001_initial"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("policy_number", sa.String(64), primary_key=True),
        sa.Column("policyholder_name", sa.String(128), nullable=False),
        sa.Column("policy_type", sa.String(32), nullable=False),
        sa.Column("effective_date", sa.String(10), nullable=False),
        sa.Column("expiration_date", sa.String(10), nullable=False),
        sa.Column("bodily_injury_per_person", sa.Numeric(12, 2), nullable=False),
        sa.Column("bodily_injury_per_accident", sa.Numeric(12, 2), nullable=False),
        sa.Column("property_damage", sa.Numeric(12, 2), nullable=False),
        sa.Column("collision_deductible", sa.Numeric(12, 2), nullable=True),
        sa.Column("comprehensive_deductible", sa.Numeric(12, 2), nullable=True),
        sa.Column("exclusions", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "prior_claims",
        sa.Column("claim_reference", sa.String(100), primary_key=True),
        sa.Column("customer_id", sa.String(100), nullable=False, index=True),
        sa.Column("policy_number", sa.String(64), nullable=False),
        sa.Column("claim_date", sa.String(10), nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("at_fault", sa.Boolean(), nullable=True),
    )
    op.create_table(
        "damage_estimates",
        sa.Column("estimate_ref", sa.String(100), primary_key=True),
        sa.Column("claim_ref", sa.String(100), nullable=False, index=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("estimate_date", sa.String(10), nullable=False),
        sa.Column("labor_hours", sa.Float(), nullable=False),
        sa.Column("declared_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("declared_currency", sa.String(3), nullable=False),
    )
    op.create_table(
        "damage_line_items",
        sa.Column("line_id", sa.String(140), primary_key=True),
        sa.Column("estimate_ref", sa.String(100), nullable=False, index=True),
        sa.Column("description", sa.String(256), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_currency", sa.String(3), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_currency", sa.String(3), nullable=False),
    )
    op.create_table(
        "claim_documents",
        sa.Column("document_id", sa.String(36), primary_key=True),
        sa.Column("claim_ref", sa.String(100), nullable=False, index=True),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(256), nullable=True),
        sa.Column("content_type", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("trusted_source", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "evidence_items",
        sa.Column("evidence_id", sa.String(36), primary_key=True),
        sa.Column("claim_ref", sa.String(100), nullable=False, index=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(256), nullable=False),
    )
    op.create_table(
        "agent_executions",
        sa.Column("execution_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, index=True),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("agent", sa.String(16), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
    )
    op.create_table(
        "fraud_indicators",
        sa.Column("indicator_id", sa.String(140), primary_key=True),
        sa.Column("claim_ref", sa.String(100), nullable=False, index=True),
        sa.Column("indicator_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
    )
    op.add_column("audit_events", sa.Column("payload_json", sa.Text(), nullable=True))
    op.add_column("human_approvals", sa.Column("recommendation_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("human_approvals", "recommendation_json")
    op.drop_column("audit_events", "payload_json")
    op.drop_table("fraud_indicators")
    op.drop_table("agent_executions")
    op.drop_table("evidence_items")
    op.drop_table("claim_documents")
    op.drop_table("damage_line_items")
    op.drop_table("damage_estimates")
    op.drop_table("prior_claims")
    op.drop_table("policies")
