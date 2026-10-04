"""
Unit Tests: Checkpoint model — creation, canonical serialization,
integrity hashing, kind shapes, and version validation (Phase 7 §2, §4,
§5, §16, §17).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointKind,
    RecordedAgentOutput,
    RecordedToolOutput,
    TransitionPathEntry,
)
from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    AgentType,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.versioning import IncompatibleSchemaVersionError
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.triggers import Trigger


def _snapshot(state: WorkflowState = WorkflowState.EXTRACTION) -> WorkflowSnapshot:
    return WorkflowSnapshot(
        workflow_run_id=uuid4(), claim_id=uuid4(), current_state=state, step_count=1
    )


def _checkpoint(**overrides: Any) -> Checkpoint:
    claim_id, run_id = uuid4(), uuid4()
    snap = _snapshot()
    snap = snap.model_copy(update={"workflow_run_id": run_id, "claim_id": claim_id})
    base: dict[str, Any] = {
        "claim_id": claim_id,
        "workflow_run_id": run_id,
        "state": WorkflowState.EXTRACTION,
        "snapshot": snap,
        "step_count": 1,
        "rework_count": 0,
    }
    base.update(overrides)
    return Checkpoint(**base)


def _approval_request(run_id: UUID, claim_id: UUID) -> HumanApprovalRequest:
    now = datetime.now(UTC)
    return HumanApprovalRequest(
        workflow_run_id=run_id,
        claim_id=claim_id,
        recommendation=Recommendation(
            claim_id=claim_id,
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("100.00")),
            notes="ok",
            confidence=0.9,
        ),
        reviewer_summary="ready",
        deadline=now + timedelta(hours=24),
    )


@pytest.mark.unit
class TestCheckpointCreation:
    def test_minimal_checkpoint_seals(self) -> None:
        checkpoint = _checkpoint().sealed()
        assert checkpoint.checksum != ""
        assert checkpoint.verify_integrity() is True
        assert checkpoint.schema_version == "1.0.0"
        assert checkpoint.kind == CheckpointKind.TRANSITION

    def test_terminal_shape_enforced(self) -> None:
        run_id, claim_id = uuid4(), uuid4()
        snap = _snapshot(WorkflowState.APPROVED)
        snap = snap.model_copy(update={"workflow_run_id": run_id, "claim_id": claim_id})
        terminal = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.APPROVED,
            kind=CheckpointKind.TERMINAL,
            snapshot=snap,
            step_count=5,
            rework_count=0,
            terminal_reason="Human approved",
        ).sealed()
        assert terminal.verify_integrity() is True

    def test_terminal_requires_reason_and_state(self) -> None:
        with pytest.raises(ValidationError):
            _checkpoint(kind=CheckpointKind.TERMINAL, state=WorkflowState.APPROVED)
        with pytest.raises(ValidationError):
            _checkpoint(
                kind=CheckpointKind.TERMINAL,
                state=WorkflowState.REVIEW,
                terminal_reason="x",
            )

    def test_human_wait_requires_request(self) -> None:
        run_id, claim_id = uuid4(), uuid4()
        snap = _snapshot(WorkflowState.HUMAN_APPROVAL)
        snap = snap.model_copy(update={"workflow_run_id": run_id, "claim_id": claim_id})
        with pytest.raises(ValidationError):
            Checkpoint(
                claim_id=claim_id,
                workflow_run_id=run_id,
                state=WorkflowState.HUMAN_APPROVAL,
                kind=CheckpointKind.HUMAN_WAIT,
                snapshot=snap,
                step_count=4,
                rework_count=0,
            )
        waiting = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.HUMAN_APPROVAL,
            kind=CheckpointKind.HUMAN_WAIT,
            snapshot=snap,
            step_count=4,
            rework_count=0,
            approval_request=_approval_request(run_id, claim_id),
        ).sealed()
        assert waiting.verify_integrity() is True


@pytest.mark.unit
class TestCanonicalSerialization:
    def test_same_state_same_bytes(self) -> None:
        first, second = _checkpoint(), _checkpoint()
        assert first.canonical_bytes() != second.canonical_bytes()  # ids differ
        clone = Checkpoint.model_validate_json(first.model_dump_json(exclude={"checksum"}))
        assert clone.canonical_bytes() == first.canonical_bytes()

    def test_deterministic_key_order(self) -> None:
        import json as _json

        checkpoint = _checkpoint().sealed()
        raw = _json.loads(checkpoint.canonical_bytes().decode("utf-8"))
        assert list(raw) == sorted(raw)

    def test_payload_change_breaks_integrity(self) -> None:
        checkpoint = _checkpoint().sealed()
        tampered = checkpoint.model_copy(update={"step_count": 999})
        assert tampered.verify_integrity() is False

    def test_hash_tamper_breaks_integrity(self) -> None:
        checkpoint = _checkpoint().sealed()
        tampered = checkpoint.model_copy(update={"checksum": "0" * 64})
        assert tampered.verify_integrity() is False

    def test_empty_checksum_never_valid(self) -> None:
        assert _checkpoint().verify_integrity() is False

    def test_no_pickle_or_live_objects(self) -> None:
        import json as _json

        checkpoint = _checkpoint().sealed()
        payload_text = checkpoint.canonical_bytes().decode("utf-8")
        parsed = _json.loads(payload_text)  # proves JSON, not pickle
        assert isinstance(parsed, dict)
        lowered = payload_text.lower()
        for token in ("api_key", "password", "secret"):
            assert token not in lowered


@pytest.mark.unit
class TestCheckpointVersioning:
    def test_compatible_version_accepted(self) -> None:
        checkpoint = _checkpoint()
        assert checkpoint.check_compatible() == "1.0.0"

    def test_incompatible_version_rejected(self) -> None:
        checkpoint = _checkpoint()
        checkpoint.schema_version = "2.0.0"
        with pytest.raises(IncompatibleSchemaVersionError):
            checkpoint.check_compatible()

    def test_malformed_snapshot_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Checkpoint(
                claim_id=uuid4(),
                workflow_run_id=uuid4(),
                state="NOT_A_STATE",  # type: ignore[arg-type]
                snapshot=_snapshot(),
                step_count=0,
                rework_count=0,
            )

    def test_recorded_outputs_typed(self) -> None:
        tool = RecordedToolOutput(
            tool_name="policy_lookup",
            invocation_id=uuid4(),
            input_hash="abc",
            output_json="{}",
            idempotency_key="k",
        )
        agent = RecordedAgentOutput(
            agent=AgentType.EXTRACTOR,
            contract_type="extraction_result",
            output_json="{}",
            execution_id=uuid4(),
        )
        path = TransitionPathEntry(
            sequence_no=1, trigger=Trigger.CLAIM_VALIDATED, actor=AgentType.SUPERVISOR
        )
        checkpoint = _checkpoint(recorded_tools=(tool,), recorded_agents=(agent,), path=(path,))
        assert checkpoint.sealed().verify_integrity() is True
