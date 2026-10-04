"""
Unit Tests: Checkpoint repository — create/get/latest/list/verify/prune,
sequence enforcement, and retention (Phase 7 §3, §14, §20).
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from casefile.checkpoint.model import CheckpointSequenceError
from casefile.checkpoint.repository import SqlCheckpointRepository
from casefile.models.contracts import WorkflowState
from casefile.models.persistence import get_engine, get_session_factory, init_db
from tests.unit.test_checkpoint_model import _checkpoint


def _repo(tmp_path: Path) -> SqlCheckpointRepository:
    engine = get_engine(f"sqlite:///{tmp_path / 'checkpoints.db'}")
    init_db(engine)
    return SqlCheckpointRepository(engine)


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = get_engine(f"sqlite:///{tmp_path / 'checkpoints.db'}")
    init_db(engine)
    return get_session_factory(engine)


@pytest.mark.unit
class TestCheckpointRepository:
    def test_create_assigns_sequence(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        first = repo.create(_checkpoint())
        second = repo.create(_checkpoint(workflow_run_id=first.workflow_run_id))
        assert first.sequence_no == 1
        assert second.sequence_no == 2
        assert second.parent_checkpoint_id is None

    def test_get_and_verify(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        stored = repo.create(_checkpoint())
        loaded = repo.get(stored.checkpoint_id)
        assert loaded == stored
        assert repo.verify(loaded) is True

    def test_get_latest_and_listing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert repo.get_latest(uuid4()) is None
        run_id = uuid4()
        for _ in range(3):
            repo.create(_checkpoint(workflow_run_id=run_id))
        latest = repo.get_latest(run_id)
        assert latest is not None
        assert latest.sequence_no == 3
        assert [c.sequence_no for c in repo.list_for_run(run_id)] == [1, 2, 3]

    def test_explicit_sequence_must_match(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        run_id = uuid4()
        repo.create(_checkpoint(workflow_run_id=run_id))
        with pytest.raises(CheckpointSequenceError):
            repo.create(_checkpoint(workflow_run_id=run_id, sequence_no=99))

    def test_duplicate_sequence_rejected(self, tmp_path: Path) -> None:
        from sqlalchemy.exc import IntegrityError

        repo = _repo(tmp_path)
        factory = _factory(tmp_path)
        stored = repo.create(_checkpoint())
        # Direct duplicate insert at the DB level must violate constraints
        with factory() as session:
            from casefile.storage.mappings import checkpoint_to_row

            row = checkpoint_to_row(stored)
            row.checkpoint_id = str(uuid4())
            session.add(row)
            with pytest.raises(IntegrityError):
                session.commit()

    def test_cross_run_sequences_independent(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        first = repo.create(_checkpoint())
        second = repo.create(_checkpoint())
        assert first.sequence_no == 1
        assert second.sequence_no == 1
        assert first.workflow_run_id != second.workflow_run_id

    def test_prune_keeps_latest(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        run_id = uuid4()
        for _ in range(5):
            repo.create(_checkpoint(workflow_run_id=run_id))
        assert repo.prune(run_id, keep_last_n=2) == 3
        assert [c.sequence_no for c in repo.list_for_run(run_id)] == [4, 5]
        assert repo.prune(run_id, keep_last_n=5) == 0
        with pytest.raises(ValueError):
            repo.prune(run_id, keep_last_n=0)

    def test_verify_rejects_tampered(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        stored = repo.create(_checkpoint())
        tampered = stored.model_copy(update={"step_count": stored.step_count + 10})
        assert repo.verify(tampered) is False

    def test_state_mismatch_rejected_on_reload(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        stored = repo.create(_checkpoint(state=WorkflowState.REVIEW))
        loaded = repo.get(stored.checkpoint_id)
        assert loaded.state == WorkflowState.REVIEW
