"""
Unit Tests: SQLite persistence models (Phase 2, ADR-011).

Proves domain state can be persisted and loaded from SQLite, tables carry
the required identity/state/audit/approval/budget boundaries, and Alembic
migrations apply cleanly to a fresh database.
"""

import os
from decimal import Decimal
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from casefile.models.domain import ApprovalStatus
from casefile.models.persistence import (
    AuditEventRecord,
    BudgetSnapshotRecord,
    ClaimRecord,
    HumanApprovalRecord,
    WorkflowRunRecord,
    get_engine,
    get_session_factory,
    init_db,
)


def _sqlite_url(tmp_path: Path, name: str = "phase2.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _claim_row() -> ClaimRecord:
    return ClaimRecord(
        claim_id=str(uuid4()),
        external_claim_id="EXT-P1",
        policy_number="POL-P1",
        customer_id="CUST-P1",
        claim_type="COLLISION",
        status="SUBMITTED",
        claim_amount=Decimal("1500.00"),
        currency="USD",
        incident_date="2026-09-10",
        description="Persistence probe",
    )


@pytest.mark.unit
class TestPersistenceModels:
    def test_claim_round_trip(self, tmp_path: Path) -> None:
        engine = get_engine(_sqlite_url(tmp_path))
        init_db(engine)
        session_factory = get_session_factory(engine)
        row = _claim_row()
        with session_factory() as session:
            session.add(row)
            session.commit()
        with session_factory() as session:
            loaded = session.get(ClaimRecord, row.claim_id)
            assert loaded is not None
            assert loaded.policy_number == "POL-P1"
            assert loaded.claim_amount == Decimal("1500.00")

    def test_workflow_run_lifecycle(self, tmp_path: Path) -> None:
        engine = get_engine(_sqlite_url(tmp_path))
        init_db(engine)
        session_factory = get_session_factory(engine)
        run_id = str(uuid4())
        with session_factory() as session:
            session.add(
                WorkflowRunRecord(
                    workflow_run_id=run_id,
                    claim_id=str(uuid4()),
                    current_state="EXTRACTION",
                    trace_id="trace-persist",
                )
            )
            session.commit()
        with session_factory() as session:
            loaded = session.get(WorkflowRunRecord, run_id)
            assert loaded is not None
            assert loaded.current_state == "EXTRACTION"
            loaded.current_state = "INVESTIGATION"
            loaded.step_count = 1
            session.commit()
        with session_factory() as session:
            reloaded = session.get(WorkflowRunRecord, run_id)
            assert reloaded is not None
            assert reloaded.current_state == "INVESTIGATION"
            assert reloaded.step_count == 1

    def test_audit_event_append_and_query(self, tmp_path: Path) -> None:
        engine = get_engine(_sqlite_url(tmp_path))
        init_db(engine)
        session_factory = get_session_factory(engine)
        run_id = str(uuid4())
        claim_id = str(uuid4())
        with session_factory() as session:
            session.add(
                AuditEventRecord(
                    event_id=str(uuid4()),
                    workflow_run_id=run_id,
                    claim_id=claim_id,
                    trace_id="trace-audit",
                    event_type="STATE_TRANSITION",
                    actor_type="AGENT",
                    actor_id="supervisor",
                    action="transition",
                    summary="RECEIVED -> EXTRACTION",
                    attributes={"trigger": "validated"},
                    result="SUCCESS",
                    from_state="RECEIVED",
                    to_state="EXTRACTION",
                    correlation_id=str(uuid4()),
                )
            )
            session.commit()
        with session_factory() as session:
            rows = (
                session.query(AuditEventRecord)
                .filter_by(workflow_run_id=run_id)
                .order_by(AuditEventRecord.timestamp)
                .all()
            )
            assert len(rows) == 1
            assert rows[0].attributes == {"trigger": "validated"}
            assert rows[0].to_state == "EXTRACTION"

    def test_approval_and_budget_records(self, tmp_path: Path) -> None:
        engine = get_engine(_sqlite_url(tmp_path))
        init_db(engine)
        session_factory = get_session_factory(engine)
        run_id = str(uuid4())
        claim_id = str(uuid4())
        with session_factory() as session:
            session.add(
                HumanApprovalRecord(
                    approval_id=str(uuid4()),
                    workflow_run_id=run_id,
                    claim_id=claim_id,
                    status=ApprovalStatus.PENDING.value,
                )
            )
            session.add(
                BudgetSnapshotRecord(
                    snapshot_id=str(uuid4()),
                    workflow_run_id=run_id,
                    input_tokens_used=100,
                    output_tokens_used=50,
                    total_tokens_used=150,
                    estimated_cost_usd=Decimal("0.0100"),
                    steps_completed=2,
                    is_exhausted=False,
                )
            )
            session.commit()
        with session_factory() as session:
            approvals = session.query(HumanApprovalRecord).filter_by(workflow_run_id=run_id).all()
            budgets = session.query(BudgetSnapshotRecord).filter_by(workflow_run_id=run_id).all()
            assert len(approvals) == 1
            assert approvals[0].status == "PENDING"
            assert len(budgets) == 1
            assert budgets[0].total_tokens_used == 150

    def test_tables_match_expected_boundary(self, tmp_path: Path) -> None:
        engine = get_engine(_sqlite_url(tmp_path))
        init_db(engine)
        tables = set(inspect(engine).get_table_names())
        assert {
            "claims",
            "workflow_runs",
            "audit_events",
            "human_approvals",
            "budget_snapshots",
        } <= tables


@pytest.mark.unit
class TestAlembicMigrations:
    def test_upgrade_head_on_fresh_database(self, tmp_path: Path) -> None:
        db_file = tmp_path / "migrated.db"
        url = f"sqlite:///{db_file}"
        repo_root = Path(__file__).resolve().parents[2]
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": url}):
            command.upgrade(cfg, "head")
        engine = create_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert {
            "claims",
            "workflow_runs",
            "audit_events",
            "human_approvals",
            "budget_snapshots",
            "alembic_version",
        } <= tables
        engine.dispose()
