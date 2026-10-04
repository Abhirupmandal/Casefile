"""
Unit Tests: Checkpoint policy, resume, idempotency ledger, and replay
(Phase 7 §6, §7, §9–§12, §15, §22).
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from casefile.agents.providers import LLMRequest
from casefile.checkpoint.ledger import IdempotencyLedger
from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointCorruptError,
    CheckpointIncompatibleError,
    CheckpointKind,
    CheckpointNotFoundError,
    RecordedAgentOutput,
    RecordedToolOutput,
    TransitionPathEntry,
)
from casefile.checkpoint.policy import checkpoint_kind_for, should_checkpoint
from casefile.checkpoint.replay import (
    ReplayAgentProvider,
    ReplayMissingArtifactError,
    ReplayMode,
    ReplayToolRegistry,
    run_replay,
)
from casefile.checkpoint.repository import SqlCheckpointRepository
from casefile.checkpoint.resume import CheckpointResumeError, resume_from_checkpoint
from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.models.persistence import get_engine, init_db
from casefile.models.versioning import IncompatibleSchemaVersionError
from casefile.tools import create_default_registry
from casefile.tools.contracts import ToolContext, ToolErrorCode, ToolFailureError
from casefile.tools.registry import ToolRegistry
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.transitions import TransitionEvent, TransitionRecord
from casefile.workflow.triggers import Trigger
from tests.unit.test_checkpoint_model import _checkpoint


def _repo(tmp_path: Path) -> SqlCheckpointRepository:
    engine = get_engine(f"sqlite:///{tmp_path / 'cp.db'}")
    init_db(engine)
    return SqlCheckpointRepository(engine)


def _event(
    trigger: Trigger, claim_id: UUID, run_id: UUID, actor: AgentType = AgentType.SUPERVISOR
) -> TransitionEvent:
    return TransitionEvent(claim_id=claim_id, workflow_run_id=run_id, trigger=trigger, actor=actor)


def _record(run_id: UUID, claim_id: UUID, seq: int = 1) -> TransitionRecord:
    return TransitionRecord(
        transition_id=uuid4(),
        sequence_no=seq,
        claim_id=claim_id,
        workflow_run_id=run_id,
        execution_id=uuid4(),
        source=WorkflowState.RECEIVED,
        destination=WorkflowState.EXTRACTION,
        trigger=Trigger.CLAIM_VALIDATED,
        actor=AgentType.SUPERVISOR,
        idempotency_key=f"{run_id}:exec:{seq}",
        applied_at=datetime.now(UTC),
        correlation_id=uuid4(),
    )


@pytest.mark.unit
class TestCheckpointPolicy:
    def test_kind_classification(self) -> None:
        assert checkpoint_kind_for(WorkflowState.INVESTIGATION) == CheckpointKind.TRANSITION
        assert checkpoint_kind_for(WorkflowState.HUMAN_APPROVAL) == CheckpointKind.HUMAN_WAIT
        assert checkpoint_kind_for(WorkflowState.APPROVED) == CheckpointKind.TERMINAL
        assert checkpoint_kind_for(WorkflowState.FAILED) == CheckpointKind.TERMINAL

    def test_first_transition_checkpoints(self) -> None:
        run_id, claim_id = uuid4(), uuid4()
        take, kind = should_checkpoint(_record(run_id, claim_id), None)
        assert take is True
        assert kind == CheckpointKind.TRANSITION

    def test_duplicate_transition_skipped(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        run_id, claim_id = uuid4(), uuid4()
        record = _record(run_id, claim_id)
        stored = repo.create(
            _checkpoint(workflow_run_id=run_id, claim_id=claim_id).model_copy(
                update={
                    "path": (
                        TransitionPathEntry(
                            sequence_no=record.sequence_no,
                            trigger=record.trigger,
                            actor=record.actor,
                        ),
                    )
                }
            )
        )
        take, _ = should_checkpoint(record, stored)
        assert take is False

    def test_new_transition_checkpoints(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        run_id, claim_id = uuid4(), uuid4()
        stored = repo.create(_checkpoint(workflow_run_id=run_id, claim_id=claim_id))
        take, _ = should_checkpoint(_record(run_id, claim_id, seq=2), stored)
        assert take is True


@pytest.mark.unit
class TestResume:
    def test_resume_reconstructs_state(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        stored = repo.create(_checkpoint())
        result = resume_from_checkpoint(stored.checkpoint_id, checkpoints=repo)
        assert result.snapshot == stored.snapshot
        assert result.checkpoint.checkpoint_id == stored.checkpoint_id
        assert result.context.workflow_run_id == stored.workflow_run_id
        assert result.context.correlation_id == stored.correlation_id
        assert result.context.execution_id != stored.execution_id

    def test_resume_unknown_id(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(CheckpointNotFoundError):
            resume_from_checkpoint(uuid4(), checkpoints=repo)
        with pytest.raises(CheckpointNotFoundError):
            resume_from_checkpoint("not-a-uuid", checkpoints=repo)

    def test_resume_corrupt_refused(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        stored = repo.create(_checkpoint())
        # Corrupt the stored payload behind the repository's back
        engine = get_engine(f"sqlite:///{tmp_path / 'cp.db'}")
        from sqlalchemy.orm import Session as _Session

        from casefile.models.persistence import CheckpointRow

        with _Session(engine) as session:
            row = session.get(CheckpointRow, str(stored.checkpoint_id))
            assert row is not None
            row.snapshot_json = row.snapshot_json.replace("EXTRACTION", "REVIEW")
            session.commit()
        with pytest.raises(CheckpointCorruptError):
            resume_from_checkpoint(stored.checkpoint_id, checkpoints=repo)

    def test_resume_incompatible_refused(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        stored = repo.create(_checkpoint())
        stored.schema_version = "2.0.0"
        tampered = stored.sealed()
        # Bypass repository sealing to plant an incompatible version
        from casefile.models.persistence import CheckpointRow

        engine = get_engine(f"sqlite:///{tmp_path / 'cp.db'}")
        from sqlalchemy.orm import Session as _Session2

        with _Session2(engine) as session:
            session.query(CheckpointRow).filter_by(checkpoint_id=str(stored.checkpoint_id)).update(
                {"schema_version": "2.0.0", "checksum": tampered.checksum}
            )
            session.commit()
        assert tampered.checksum != ""
        with pytest.raises(CheckpointIncompatibleError):
            resume_from_checkpoint(stored.checkpoint_id, checkpoints=repo)

    def test_resume_error_types(self) -> None:
        assert issubclass(CheckpointResumeError, Exception)
        assert issubclass(IncompatibleSchemaVersionError, ValueError)


@pytest.mark.unit
class TestIdempotencyLedger:
    def _ledger(self, tmp_path: Path) -> IdempotencyLedger:
        engine = get_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
        init_db(engine)
        return IdempotencyLedger(engine)

    def test_claim_then_duplicate(self, tmp_path: Path) -> None:
        ledger = self._ledger(tmp_path)
        run_id, exec_id = uuid4(), uuid4()
        assert ledger.is_claimed("k1") is False
        assert (
            ledger.claim(
                idempotency_key="k1",
                workflow_run_id=run_id,
                execution_id=exec_id,
                sequence_no=1,
                result_kind="transition",
                result_ref="t1",
            )
            is True
        )
        assert ledger.is_claimed("k1") is True
        assert (
            ledger.claim(
                idempotency_key="k1",
                workflow_run_id=run_id,
                execution_id=exec_id,
                sequence_no=1,
                result_kind="transition",
                result_ref="t1",
            )
            is False
        )
        info = ledger.lookup("k1")
        assert info is not None
        assert info["result_ref"] == "t1"
        assert ledger.lookup("missing") is None

    def test_concurrent_claim_single_winner(self, tmp_path: Path) -> None:
        import threading

        ledger = self._ledger(tmp_path)
        run_id = uuid4()
        results: list[bool] = []

        def worker() -> None:
            results.append(
                ledger.claim(
                    idempotency_key="race",
                    workflow_run_id=run_id,
                    execution_id=uuid4(),
                    sequence_no=1,
                    result_kind="transition",
                    result_ref="t",
                )
            )

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [False] * 7 + [True]


@pytest.mark.unit
class TestReplay:
    def _checkpoint_with_recordings(self) -> tuple[Checkpoint, UUID, UUID]:
        run_id, claim_id = uuid4(), uuid4()
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.INVESTIGATION
        )
        return (
            _checkpoint(workflow_run_id=run_id, claim_id=claim_id, snapshot=snap)
            .model_copy(
                update={
                    "recorded_agents": (
                        RecordedAgentOutput(
                            agent=AgentType.REVIEWER,
                            contract_type="review_result",
                            output_json='{"ok": true}',
                            execution_id=uuid4(),
                        ),
                    ),
                    "recorded_tools": (
                        RecordedToolOutput(
                            tool_name="policy_lookup",
                            invocation_id=uuid4(),
                            input_hash="h",
                            output_json='{"found": true, "policy": null}',
                            idempotency_key="k",
                        ),
                    ),
                }
            )
            .sealed(),
            run_id,
            claim_id,
        )

    def test_provider_serves_recordings(self) -> None:
        checkpoint, _, _ = self._checkpoint_with_recordings()
        provider = ReplayAgentProvider(checkpoint)
        response = provider.complete(
            LLMRequest(model="m", messages=(), response_schema_name="review_result")
        )
        assert response.content == '{"ok": true}'
        assert response.provider == "replay"
        with pytest.raises(ReplayMissingArtifactError):
            provider.complete(LLMRequest(model="m", messages=()))

    def test_provider_rejects_non_object(self) -> None:
        checkpoint, _, _ = self._checkpoint_with_recordings()
        bad = checkpoint.model_copy(
            update={
                "recorded_agents": (
                    RecordedAgentOutput(
                        agent=AgentType.REVIEWER,
                        contract_type="x",
                        output_json="[1,2]",
                        execution_id=uuid4(),
                    ),
                )
            }
        )
        with pytest.raises(ReplayMissingArtifactError):
            ReplayAgentProvider(bad)

    def test_tool_registry_hit_and_miss(self, tmp_path: Path) -> None:
        checkpoint, run_id, claim_id = self._checkpoint_with_recordings()
        exec_id = uuid4()
        raw: dict[str, object] = {"policy_number": "POL-SYN-001"}
        key_input = json.dumps(raw, sort_keys=True, default=str)
        digest = hashlib.sha256(key_input.encode()).hexdigest()[:16]
        key = f"{run_id}:{exec_id}:policy_lookup:{digest}"
        live = create_default_registry()
        tool = live._tools["policy_lookup"]
        tool_ctx = ToolContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            execution_id=exec_id,
            agent=AgentType.INVESTIGATOR,
        )
        output = tool.run(tool.input_model.model_validate(raw), tool_ctx)
        replay: ToolRegistry = ReplayToolRegistry({key: output.model_dump_json()})
        replay.register(tool)
        hit = replay.execute("policy_lookup", "1.0.0", raw, tool_ctx)
        assert hit.succeeded is True
        with pytest.raises(ToolFailureError) as exc_info:
            replay.execute("policy_lookup", "1.0.0", {"policy_number": "OTHER"}, tool_ctx)
        assert exc_info.value.error.code == ToolErrorCode.REPLAY_ARTIFACT_MISSING

    def test_run_replay_path(self) -> None:
        checkpoint, run_id, claim_id = self._checkpoint_with_recordings()
        engine = WorkflowEngine()
        ctx = RunContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            clock=FixedClock(datetime.now(UTC)),
        )
        report = run_replay(
            checkpoint,
            [(Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR)],
            engine=engine,
            ctx=ctx,
        )
        assert report.mode == ReplayMode.REPLAY
        assert report.states == [WorkflowState.INVESTIGATION, WorkflowState.REVIEW]
        assert report.terminal_state is None
        assert report.steps == 1
