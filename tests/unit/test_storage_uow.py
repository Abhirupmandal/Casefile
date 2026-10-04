"""
Unit Tests: Transaction boundaries — rollback, atomicity, separate-session
visibility, and misuse rejection (Phase 6 §4, §21).

SQLite is single-writer by design; these tests prove local safety
(commit/rollback/atomicity), not distributed scaling.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType, WorkflowRun
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.errors import PersistenceError, PersistenceErrorCode
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger
from tests.unit.test_storage_repositories import _claim


def _engine(tmp_path: Path, name: str = "uow.db") -> Engine:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    return engine


def _factory_for(engine: Engine) -> sessionmaker[Session]:
    return get_session_factory(engine)


@pytest.mark.unit
class TestUnitOfWork:
    def test_failed_transaction_persists_nothing(self, tmp_path: Path) -> None:
        claim = _claim()
        factory = _factory_for(_engine(tmp_path))
        with pytest.raises(RuntimeError, match="boom"), UnitOfWork(factory) as uow:
            uow.claims.save(claim)
            raise RuntimeError("boom")
        with UnitOfWork(factory) as check:
            assert check.claims.exists(claim.claim_id) is False

    def test_atomic_snapshot_and_audit(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        run_id, claim_id = uuid4(), uuid4()
        run_ctx = RunContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            clock=FixedClock(datetime.now(UTC)),
        )
        flow = WorkflowEngine()
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
        )
        event = TransitionEvent(
            claim_id=claim_id,
            workflow_run_id=run_id,
            trigger=Trigger.CLAIM_VALIDATED,
            actor=AgentType.SUPERVISOR,
        )
        result = flow.apply(snap, event, run_ctx)
        assert result.record is not None
        store = SqliteWorkflowStore(engine)
        store.save_snapshot_and_audit(result.snapshot, result.record)
        loaded = store.load_snapshot(run_id)
        assert loaded is not None
        assert loaded.current_state == WorkflowState.EXTRACTION
        assert len(store.list_transitions(run_id)) == 1

    def test_partial_failure_rolls_back_run(self, tmp_path: Path) -> None:
        factory = _factory_for(_engine(tmp_path))
        run_id, claim_id = uuid4(), uuid4()
        run = WorkflowRun(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
        )
        with pytest.raises(PersistenceError) as exc_info, UnitOfWork(factory) as uow:
            uow.runs.save(run)
            raise PersistenceError(PersistenceErrorCode.TRANSACTION_FAILED, "injected")
        assert exc_info.value.code == PersistenceErrorCode.TRANSACTION_FAILED
        with UnitOfWork(factory) as check:
            assert check.runs.exists(run_id) is False
            assert check.audit.list_for_run(run_id) == []

    def test_separate_sessions_see_committed_state(self, tmp_path: Path) -> None:
        claim = _claim()
        factory = _factory_for(_engine(tmp_path))
        with UnitOfWork(factory) as first:
            first.claims.save(claim)
        with UnitOfWork(factory) as second:
            assert second.claims.get(claim.claim_id) == claim

    def test_use_outside_context_rejected(self, tmp_path: Path) -> None:
        uow = UnitOfWork(_factory_for(_engine(tmp_path)))
        with pytest.raises(PersistenceError) as exc_info:
            uow.claims.save(_claim())
        assert exc_info.value.code == PersistenceErrorCode.TRANSACTION_FAILED
