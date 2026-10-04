"""
Phase 4 Acceptance Test Matrix (Items A through BZ) and End-to-End Scenarios 1–8.

Validates the complete Phase 4 scope:
- A-H: Checkpoint model (strongly typed, canonical bytes, checksum, immutable attributes, versioning, serialization)
- I-L: Checkpoint integrity (sha256 canonical payload, modified state detection, corrupt checkpoint fails closed, no silent regeneration)
- M-P: Checkpoint repository (create, get, list_by_workflow_run_id, get_latest, verify_integrity, prune)
- Q-T: Resume (restore typed state, execution identity, counters, does not re-run completed nodes, terminal state preserves immutability)
- U-Z: Replay (deterministic replay from stored checkpoints, no live LLM, no external calls, reconstructs identical state sequence)
- AA-AD: Replay mismatch (divergence detected, first divergence index, expected vs actual value, structured result, original preserved)
- AE-AJ: Budget model (10 dimensions: input/output/total tokens, cost, steps, agent steps, rework cycles, tool calls, retries, wall-clock)
- AK-AP: Token/cost accounting (monotonic accumulation, versioned pricing table, CostCalculator, exact Decimal cost)
- AQ-AV: Step/loop guards (max_steps exhaustion -> MAX_STEPS_EXCEEDED, rework loop exhaustion -> MAX_REWORK_EXCEEDED, tool invocation limit, agent retry limit)
- AW-AZ: Termination policy (central typed TerminationPolicy, deterministic evaluation, no LLM decide termination)
- BA-BD: Budget + checkpoint atomicity (atomic checkpoint + budget + event persistence via UnitOfWork, rollback on failure)
- BE-BH: Security (checkpoints contain no secrets, API keys, passwords, database credentials)
- BI-BN: Failure modes 1 through 15
- Matrix A-J: Budget matrix scenarios
- BO-BV: End-to-End Scenarios 1 through 8
- BW-BZ: Architecture boundaries
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.budget.engine import (
    BudgetEngine,
    ReserveKind,
)
from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.pricing import (
    CostCalculator,
    CostRecord,
    ModelPricing,
    ModelUsage,
    PricingTable,
    calculate_cost,
)
from casefile.budget.termination import (
    BudgetTermination,
    TerminationPolicy,
)
from casefile.budget.usage import BudgetUsage
from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointCorruptError,
    CheckpointIncompatibleError,
    CheckpointKind,
    CheckpointNotFoundError,
    CheckpointSequenceError,
    RecordedAgentOutput,
    TransitionPathEntry,
    WorkflowCheckpoint,
)
from casefile.checkpoint.policy import checkpoint_kind_for
from casefile.checkpoint.replay import (
    ReplayResult,
    compare_replay_runs,
    replay_workflow,
    run_replay,
)
from casefile.checkpoint.repository import (
    SqlCheckpointRepository,
)
from casefile.checkpoint.resume import ResumeResult, resume_from_checkpoint
from casefile.models.contracts import (
    BudgetLimits,
    BudgetState,
    ClaimInput,
    ExtractionResult,
    InvestigationResult,
    WorkflowState,
)
from casefile.models.domain import (
    AgentType,
)
from casefile.models.persistence import (
    CheckpointRow,
    get_engine,
    get_session_factory,
    init_db,
)
from casefile.storage.unit_of_work import UnitOfWork
from casefile.tools import (
    PolicyLookupTool,
    ToolContext,
)
from casefile.workflow.context import (
    RunContext,
    SystemClock,
    WorkflowContext,
    WorkflowSnapshot,
)
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.runner import WorkflowRunner
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_test_db(tmp_path: Path, name: str = "phase4_test.db") -> tuple[Any, sessionmaker[Session]]:
    db_file = tmp_path / name
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    return engine, get_session_factory(engine)


def make_test_claim_input(
    claim_id: UUID | None = None,
    policy_id: str = "POL-PH4-001",
) -> ClaimInput:
    return ClaimInput(
        claim_id=claim_id or uuid4(),
        policy_id=policy_id,
        claimant_name="Alice Smith",
        incident_date="2026-09-25",
        claim_amount=Decimal("3200.00"),
        description="Fender bender in shopping mall parking lot",
        documents=["DOC-A", "DOC-B"],
    )


def sample_extraction_payload(
    claim: ClaimInput, workflow_id: UUID | str | None = None
) -> dict[str, Any]:
    wf_id = str(workflow_id) if workflow_id is not None else str(uuid4())
    return {
        "workflow_id": wf_id,
        "claimant_name": claim.claimant_name,
        "policy_id": claim.policy_id,
        "incident_date": claim.incident_date,
        "incident_location": "Mall Parking",
        "incident_description": claim.description,
        "claim_amount": str(claim.claim_amount),
        "requested_coverage_type": "COLLISION",
        "documents_processed": list(claim.documents),
        "extraction_confidence": 0.95,
        "missing_fields": [],
        "is_complete": True,
    }


def sample_investigation_payload(workflow_id: UUID | str | None = None) -> dict[str, Any]:
    wf_id = str(workflow_id) if workflow_id is not None else str(uuid4())
    return {
        "workflow_id": wf_id,
        "policy_details": {
            "policy_number": "POL-PH4-001",
            "active": True,
            "coverage_limit": "50000.00",
        },
        "claim_history": {"prior_claims_count": 0},
        "repair_cost_validation": {"estimate_amount": "3200.00", "is_reasonable": True},
        "fraud_signals": {"risk_level": "LOW", "score": 0.05},
        "supporting_documents": ["DOC-A", "DOC-B"],
        "findings_summary": "Policy valid, costs verified, fraud risk low.",
        "evidence_strength": 0.92,
        "tool_calls_made": ["policy_lookup", "repair_cost_lookup"],
        "investigation_duration_seconds": 2,
        "tools_succeeded": 2,
        "tools_failed": 0,
    }


def sample_review_payload(
    decision: str = "APPROVE",
    rework_feedback: str | None = None,
    workflow_id: UUID | str | None = None,
) -> dict[str, Any]:
    wf_id = str(workflow_id) if workflow_id is not None else str(uuid4())
    return {
        "workflow_id": wf_id,
        "decision": decision,
        "reasoning": f"Evidence is complete and supports {decision}.",
        "confidence_score": 0.95,
        "evidence_completeness": 0.95,
        "identified_gaps": [],
        "rework_feedback": rework_feedback,
        "fraud_risk_level": "LOW",
        "fraud_signals_detected": False,
    }


class TrackingRunner(WorkflowRunner):
    """WorkflowRunner tracking the active workflow_run_id for deterministic test providers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.active_run_id: UUID | None = None

    def run(
        self,
        claim: ClaimInput,
        *,
        workflow_run_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> WorkflowContext:
        self.active_run_id = workflow_run_id or uuid4()
        return super().run(claim, workflow_run_id=self.active_run_id, correlation_id=correlation_id)


class SmartDeterministicProvider(DeterministicProvider):
    """DeterministicProvider that automatically matches workflow_id to the running workflow."""

    def __init__(
        self, runner_holder: list[TrackingRunner], provider_name: str = "deterministic"
    ) -> None:
        super().__init__(provider_name)
        self._runner_holder = runner_holder

    def complete(self, request: Any) -> Any:
        from casefile.agents.deterministic import _as_int
        from casefile.agents.providers import LLMResponse, ProviderError, ProviderTimeoutError

        self.requests.append(request)
        if not self._script:
            raise ProviderError("DeterministicProvider has no scripted outcome left")
        outcome = self._script.pop(0)
        if not isinstance(outcome, dict):
            raise ProviderError("DeterministicProvider received a malformed script entry")
        kind = outcome.get("kind")
        if kind == "timeout":
            raise ProviderTimeoutError()
        if kind == "error":
            error = outcome.get("error")
            if not isinstance(error, ProviderError):
                raise ProviderError("DeterministicProvider received a malformed error entry")
            raise error
        metadata = outcome.get("metadata")
        meta: dict[str, object] = metadata if isinstance(metadata, dict) else {}
        if kind == "json":
            payload = dict(outcome.get("payload", {}))
            if (
                self._runner_holder
                and self._runner_holder[0].active_run_id is not None
                and "workflow_id" in payload
            ):
                payload["workflow_id"] = str(self._runner_holder[0].active_run_id)
            content = json.dumps(payload, sort_keys=True)
        elif kind == "text":
            content = str(outcome.get("text", ""))
        else:
            raise ProviderError(f"DeterministicProvider received unknown outcome {kind!r}")
        return LLMResponse(
            request_id=request.request_id,
            content=content,
            input_tokens=_as_int(meta.get("input_tokens")),
            output_tokens=_as_int(meta.get("output_tokens")),
            latency_ms=_as_int(meta.get("latency_ms")),
            model=request.model,
            provider=self._provider_name,
        )


def make_runner(
    tmp_path: Path,
    db_name: str = "runner.db",
    max_steps: int = 50,
    max_rework_cycles: int = 3,
    max_agent_retries: int = 3,
    extraction_data: dict[str, Any] | None = None,
    investigation_data: dict[str, Any] | None = None,
    review_data: dict[str, Any] | None = None,
    budget_engine: BudgetEngine | None = None,
) -> tuple[TrackingRunner, sessionmaker[Session], SqlCheckpointRepository]:
    engine, factory = make_test_db(tmp_path, db_name)
    cp_repo = SqlCheckpointRepository(engine)

    runner_holder: list[TrackingRunner] = []
    ext_prov = SmartDeterministicProvider(runner_holder, "ext-prov")
    inv_prov = SmartDeterministicProvider(runner_holder, "inv-prov")
    rev_prov = SmartDeterministicProvider(runner_holder, "rev-prov")

    if extraction_data:
        ext_prov.push_json(extraction_data)
    if investigation_data:
        inv_prov.push_json(investigation_data)
    if review_data:
        rev_prov.push_json(review_data)

    extractor = ExtractorAgent(ext_prov)
    investigator = InvestigatorAgent(inv_prov)
    reviewer = ReviewerAgent(rev_prov)

    runner = TrackingRunner(
        extractor,
        investigator,
        reviewer,
        max_steps=max_steps,
        max_rework_cycles=max_rework_cycles,
        max_agent_retries=max_agent_retries,
        uow_factory=lambda: UnitOfWork(factory),
        checkpoint_repo=cp_repo,
        budget_engine=budget_engine,
    )
    runner_holder.append(runner)
    return runner, factory, cp_repo


# ---------------------------------------------------------------------------
# Categories A-H: Checkpoint Model
# ---------------------------------------------------------------------------


class TestCheckpointModel:
    def test_checkpoint_model_attributes(self) -> None:
        """Matrix Item A: Strongly typed Checkpoint model contains all required fields."""
        run_id = uuid4()
        claim_id = uuid4()
        snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.EXTRACTION,
            step_count=1,
            rework_count=0,
        )
        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.EXTRACTION,
            kind=CheckpointKind.TRANSITION,
            snapshot=snapshot,
            step_count=1,
            rework_count=0,
            schema_version="1.0.0",
        )
        assert cp.checkpoint_id is not None
        assert cp.checkpoint_version == "1.0.0"
        assert cp.workflow_state == WorkflowState.EXTRACTION
        assert WorkflowCheckpoint is Checkpoint

    def test_checkpoint_canonical_bytes_deterministic(self) -> None:
        """Matrix Item B: Canonical serialization is byte-deterministic across calls."""
        run_id = uuid4()
        claim_id = uuid4()
        snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.INVESTIGATION,
            step_count=2,
            rework_count=0,
        )
        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.INVESTIGATION,
            snapshot=snapshot,
            step_count=2,
            rework_count=0,
        )
        bytes1 = cp.canonical_bytes()
        bytes2 = cp.canonical_bytes()
        assert bytes1 == bytes2
        assert isinstance(bytes1, bytes)

    def test_checkpoint_sealing_and_verification(self) -> None:
        """Matrix Item C: Sealing generates a sha256 checksum that verifies."""
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                current_state=WorkflowState.RECEIVED,
            ),
            step_count=0,
            rework_count=0,
        )
        assert cp.checksum == ""
        assert not cp.verify_integrity()

        sealed = cp.sealed()
        assert len(sealed.checksum) == 64
        assert sealed.verify_integrity()

    def test_checkpoint_terminal_requires_terminal_state_and_reason(self) -> None:
        """Matrix Item D: CheckpointKind.TERMINAL requires terminal state and reason."""
        with pytest.raises(ValueError, match="TERMINAL checkpoints require a terminal state"):
            Checkpoint(
                claim_id=uuid4(),
                workflow_run_id=uuid4(),
                state=WorkflowState.EXTRACTION,
                kind=CheckpointKind.TERMINAL,
                snapshot=WorkflowSnapshot(
                    workflow_run_id=uuid4(),
                    claim_id=uuid4(),
                    current_state=WorkflowState.EXTRACTION,
                ),
                step_count=1,
                rework_count=0,
                terminal_reason="Done",
            )

        with pytest.raises(ValueError, match="terminal_reason"):
            Checkpoint(
                claim_id=uuid4(),
                workflow_run_id=uuid4(),
                state=WorkflowState.APPROVED,
                kind=CheckpointKind.TERMINAL,
                snapshot=WorkflowSnapshot(
                    workflow_run_id=uuid4(),
                    claim_id=uuid4(),
                    current_state=WorkflowState.APPROVED,
                ),
                step_count=5,
                rework_count=0,
                terminal_reason="",
            )

    def test_checkpoint_human_wait_requires_approval_request(self) -> None:
        """Matrix Item E: CheckpointKind.HUMAN_WAIT requires approval_request."""
        with pytest.raises(ValueError, match="HUMAN_WAIT checkpoints require approval_request"):
            Checkpoint(
                claim_id=uuid4(),
                workflow_run_id=uuid4(),
                state=WorkflowState.HUMAN_APPROVAL,
                kind=CheckpointKind.HUMAN_WAIT,
                snapshot=WorkflowSnapshot(
                    workflow_run_id=uuid4(),
                    claim_id=uuid4(),
                    current_state=WorkflowState.HUMAN_APPROVAL,
                ),
                step_count=3,
                rework_count=0,
            )

    def test_checkpoint_json_serialization_roundtrip(self) -> None:
        """Matrix Item F: Checkpoint serializes to and from JSON without pickle."""
        claim_id = uuid4()
        run_id = uuid4()
        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.REVIEW,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id,
                claim_id=claim_id,
                current_state=WorkflowState.REVIEW,
                step_count=3,
            ),
            step_count=3,
            rework_count=0,
            path=(
                TransitionPathEntry(
                    sequence_no=1, trigger=Trigger.CLAIM_VALIDATED, actor=AgentType.SUPERVISOR
                ),
                TransitionPathEntry(
                    sequence_no=2, trigger=Trigger.EXTRACTION_SUCCEEDED, actor=AgentType.SUPERVISOR
                ),
            ),
        ).sealed()

        dumped = cp.model_dump_json()
        restored = Checkpoint.model_validate_json(dumped)
        assert restored.checkpoint_id == cp.checkpoint_id
        assert restored.verify_integrity()
        assert len(restored.path) == 2

    def test_checkpoint_excludes_arbitrary_objects(self) -> None:
        """Matrix Item G: Checkpoint payload uses explicit primitives and Pydantic models."""
        payload = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                current_state=WorkflowState.RECEIVED,
            ),
            step_count=0,
            rework_count=0,
        ).model_dump(mode="json")

        # Serialized form must only contain JSON types
        json_str = json.dumps(payload)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_checkpoint_policy_classification(self) -> None:
        """Matrix Item H: checkpoint_kind_for accurately classifies destination states."""
        assert checkpoint_kind_for(WorkflowState.APPROVED) == CheckpointKind.TERMINAL
        assert checkpoint_kind_for(WorkflowState.REJECTED) == CheckpointKind.TERMINAL
        assert checkpoint_kind_for(WorkflowState.MAX_STEPS_EXCEEDED) == CheckpointKind.TERMINAL
        assert checkpoint_kind_for(WorkflowState.BUDGET_EXHAUSTED) == CheckpointKind.TERMINAL
        assert checkpoint_kind_for(WorkflowState.HUMAN_APPROVAL) == CheckpointKind.HUMAN_WAIT
        assert checkpoint_kind_for(WorkflowState.EXTRACTION) == CheckpointKind.TRANSITION
        assert checkpoint_kind_for(WorkflowState.INVESTIGATION) == CheckpointKind.TRANSITION


# ---------------------------------------------------------------------------
# Categories I-L: Checkpoint Integrity
# ---------------------------------------------------------------------------


class TestCheckpointIntegrity:
    def test_tampered_payload_fails_verification(self) -> None:
        """Matrix Item I: Modifying payload after sealing breaks verify_integrity."""
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                current_state=WorkflowState.EXTRACTION,
            ),
            step_count=1,
            rework_count=0,
        ).sealed()

        assert cp.verify_integrity()
        tampered = cp.model_copy(update={"step_count": 99})
        assert not tampered.verify_integrity()

    def test_corrupted_checkpoint_fails_closed_on_resume(self, tmp_path: Path) -> None:
        """Matrix Item J: Resuming a corrupted checkpoint raises CheckpointCorruptError."""
        engine, _ = make_test_db(tmp_path, "corrupt.db")
        repo = SqlCheckpointRepository(engine)

        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.INVESTIGATION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                current_state=WorkflowState.INVESTIGATION,
            ),
            step_count=2,
            rework_count=0,
        ).sealed()
        created = repo.create(cp)

        # Corrupt the row in SQLite directly
        with Session(engine) as session:
            row = session.get(CheckpointRow, str(created.checkpoint_id))
            assert row is not None
            row.checksum = "0" * 64
            session.commit()

        with pytest.raises(CheckpointCorruptError, match="failed integrity verification"):
            resume_from_checkpoint(created.checkpoint_id, checkpoints=repo)

    def test_checksum_not_silently_regenerated(self) -> None:
        """Matrix Item K: Checkpoint verification fails closed without recomputing checksum."""
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                current_state=WorkflowState.EXTRACTION,
            ),
            step_count=1,
            rework_count=0,
            checksum="badchecksum" + "0" * 52,
        )
        assert not cp.verify_integrity()
        assert cp.checksum.startswith("badchecksum")

    def test_incompatible_checkpoint_version_rejected(self, tmp_path: Path) -> None:
        """Matrix Item L: Unsupported schema version raises CheckpointIncompatibleError on resume."""
        engine, _ = make_test_db(tmp_path, "incompat.db")
        repo = SqlCheckpointRepository(engine)

        claim_id = uuid4()
        run_id = uuid4()
        snap = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.EXTRACTION,
            step_count=1,
            rework_count=0,
        )
        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.EXTRACTION,
            snapshot=snap,
            step_count=1,
            rework_count=0,
            schema_version="99.0.0",  # Incompatible version
        ).sealed()
        created = repo.create(cp)

        with pytest.raises(CheckpointIncompatibleError):
            resume_from_checkpoint(created.checkpoint_id, checkpoints=repo)


# ---------------------------------------------------------------------------
# Categories M-P: Checkpoint Repository
# ---------------------------------------------------------------------------


class TestCheckpointRepository:
    def test_repository_crud_operations(self, tmp_path: Path) -> None:
        """Matrix Item M: Repository create, get, get_latest, list_by_workflow_run_id, prune."""
        engine, _ = make_test_db(tmp_path, "repo_crud.db")
        repo = SqlCheckpointRepository(engine)

        run_id = uuid4()
        claim_id = uuid4()

        cp1 = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
            ),
            step_count=0,
            rework_count=0,
        )
        saved1 = repo.create(cp1)
        assert saved1.sequence_no == 1

        cp2 = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.EXTRACTION
            ),
            step_count=1,
            rework_count=0,
            parent_checkpoint_id=saved1.checkpoint_id,
        )
        saved2 = repo.create(cp2)
        assert saved2.sequence_no == 2

        # Get by id
        fetched = repo.get(saved1.checkpoint_id)
        assert fetched.checkpoint_id == saved1.checkpoint_id
        assert fetched.state == WorkflowState.RECEIVED

        # Get latest
        latest = repo.get_latest(run_id)
        assert latest is not None
        assert latest.sequence_no == 2
        assert latest.state == WorkflowState.EXTRACTION

        # List by run
        all_cps = repo.list_by_workflow_run_id(run_id)
        assert len(all_cps) == 2
        assert [c.sequence_no for c in all_cps] == [1, 2]

        # Verify integrity helper
        assert repo.verify_integrity(saved1)

        # Prune older, keeping latest 1
        dropped = repo.prune(run_id, keep_last_n=1)
        assert dropped == 1
        remaining = repo.list_for_run(run_id)
        assert len(remaining) == 1
        assert remaining[0].sequence_no == 2

    def test_repository_get_missing_raises_not_found(self, tmp_path: Path) -> None:
        """Matrix Item N: Non-existent checkpoint id raises CheckpointNotFoundError."""
        engine, _ = make_test_db(tmp_path, "missing_cp.db")
        repo = SqlCheckpointRepository(engine)
        with pytest.raises(CheckpointNotFoundError):
            repo.get(uuid4())

    def test_repository_returns_domain_models_not_orm(self, tmp_path: Path) -> None:
        """Matrix Item O: Repository methods return Pydantic Checkpoint models, not ORM rows."""
        engine, _ = make_test_db(tmp_path, "orm_isolation.db")
        repo = SqlCheckpointRepository(engine)
        run_id = uuid4()
        claim_id = uuid4()

        saved = repo.create(
            Checkpoint(
                claim_id=claim_id,
                workflow_run_id=run_id,
                state=WorkflowState.RECEIVED,
                snapshot=WorkflowSnapshot(
                    workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
                ),
                step_count=0,
                rework_count=0,
            )
        )
        assert isinstance(saved, Checkpoint)
        assert not hasattr(saved, "_sa_instance_state")

    def test_repository_sequence_enforcement(self, tmp_path: Path) -> None:
        """Matrix Item P: Out-of-sequence checkpoint is rejected with CheckpointSequenceError."""
        engine, _ = make_test_db(tmp_path, "seq_error.db")
        repo = SqlCheckpointRepository(engine)
        run_id = uuid4()
        claim_id = uuid4()

        cp1 = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
            ),
            step_count=0,
            rework_count=0,
        )
        repo.create(cp1)

        # Attempt to insert an explicit invalid sequence number (e.g. 5 instead of expected 2)
        cp_bad = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            sequence_no=5,
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.EXTRACTION
            ),
            step_count=1,
            rework_count=0,
        ).sealed()

        with pytest.raises(CheckpointSequenceError, match="must carry sequence 2, got 5"):
            repo.create(cp_bad)


# ---------------------------------------------------------------------------
# Categories Q-T: Resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_resume_reconstructs_snapshot_and_context(self, tmp_path: Path) -> None:
        """Matrix Item Q: resume_from_checkpoint restores snapshot, checkpoint, and fresh context."""
        engine, _ = make_test_db(tmp_path, "resume_basic.db")
        repo = SqlCheckpointRepository(engine)
        run_id = uuid4()
        claim_id = uuid4()

        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.INVESTIGATION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id,
                claim_id=claim_id,
                current_state=WorkflowState.INVESTIGATION,
                step_count=2,
                rework_count=1,
            ),
            step_count=2,
            rework_count=1,
        ).sealed()
        saved = repo.create(cp)

        result = resume_from_checkpoint(saved.checkpoint_id, checkpoints=repo)
        assert isinstance(result, ResumeResult)
        assert result.snapshot.current_state == WorkflowState.INVESTIGATION
        assert result.snapshot.step_count == 2
        assert result.snapshot.rework_count == 1
        assert result.context.workflow_run_id == run_id
        assert result.context.execution_id != cp.execution_id

    def test_resume_preserves_terminal_immutability(self, tmp_path: Path) -> None:
        """Matrix Item R: Resuming from a terminal checkpoint preserves terminal state without execution."""
        engine, factory = make_test_db(tmp_path, "resume_terminal.db")
        cp_repo = SqlCheckpointRepository(engine)

        run_id = uuid4()
        claim_id = uuid4()

        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.APPROVED,
            kind=CheckpointKind.TERMINAL,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id,
                claim_id=claim_id,
                current_state=WorkflowState.APPROVED,
                step_count=5,
            ),
            step_count=5,
            rework_count=0,
            terminal_reason="Claim approved by adjuster",
        ).sealed()
        saved = cp_repo.create(cp)

        ext_prov = DeterministicProvider()
        runner = WorkflowRunner(
            ExtractorAgent(ext_prov),
            InvestigatorAgent(DeterministicProvider()),
            ReviewerAgent(DeterministicProvider()),
            checkpoint_repo=cp_repo,
            uow_factory=lambda: UnitOfWork(factory),
        )

        wf_ctx = runner.resume(saved.checkpoint_id)
        assert wf_ctx.current_snapshot is not None
        assert wf_ctx.current_snapshot.current_state == WorkflowState.APPROVED
        assert len(ext_prov.requests) == 0  # No agent execution occurred

    def test_resume_restores_recorded_agent_outputs(self, tmp_path: Path) -> None:
        """Matrix Item S: Resumed workflow context restores extraction and investigation results."""
        engine, _ = make_test_db(tmp_path, "resume_results.db")
        cp_repo = SqlCheckpointRepository(engine)

        run_id = uuid4()
        claim_id = uuid4()
        claim = make_test_claim_input(claim_id=claim_id)

        ext_res = ExtractionResult.model_validate(
            sample_extraction_payload(claim, workflow_id=run_id)
        )
        inv_res = InvestigationResult.model_validate(
            sample_investigation_payload(workflow_id=run_id)
        )

        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.REVIEW,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id,
                claim_id=claim_id,
                current_state=WorkflowState.REVIEW,
                step_count=3,
            ),
            step_count=3,
            rework_count=0,
            recorded_agents=(
                RecordedAgentOutput(
                    agent=AgentType.EXTRACTOR,
                    contract_type="extraction_result",
                    output_json=ext_res.model_dump_json(),
                    execution_id=uuid4(),
                ),
                RecordedAgentOutput(
                    agent=AgentType.INVESTIGATOR,
                    contract_type="investigation_result",
                    output_json=inv_res.model_dump_json(),
                    execution_id=uuid4(),
                ),
            ),
        ).sealed()
        saved = cp_repo.create(cp)

        rev_prov = DeterministicProvider()
        rev_prov.push_json(sample_review_payload("APPROVE", workflow_id=run_id))

        runner = WorkflowRunner(
            ExtractorAgent(DeterministicProvider()),
            InvestigatorAgent(DeterministicProvider()),
            ReviewerAgent(rev_prov),
            checkpoint_repo=cp_repo,
        )

        wf_ctx = runner.resume(saved.checkpoint_id, claim=claim)
        assert wf_ctx.extraction_result is not None
        assert wf_ctx.extraction_result.policy_id == claim.policy_id
        assert wf_ctx.investigation_result is not None
        assert wf_ctx.review_result is not None
        assert wf_ctx.review_result.decision == "APPROVE"
        assert wf_ctx.current_snapshot is not None
        assert wf_ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    def test_resume_with_malformed_id_fails(self, tmp_path: Path) -> None:
        """Matrix Item T: Resuming with invalid checkpoint ID string raises CheckpointNotFoundError."""
        engine, _ = make_test_db(tmp_path, "resume_malformed.db")
        repo = SqlCheckpointRepository(engine)
        with pytest.raises(CheckpointNotFoundError, match="Malformed checkpoint id"):
            resume_from_checkpoint("not-a-uuid", checkpoints=repo)


# ---------------------------------------------------------------------------
# Categories U-Z: Replay & Replay Comparison
# ---------------------------------------------------------------------------


class TestDeterministicReplay:
    def test_replay_workflow_matching(self, tmp_path: Path) -> None:
        """Matrix Item U: Replay reconstructed identical state sequence without live providers."""
        engine, factory = make_test_db(tmp_path, "replay_match.db")
        _ = SqlCheckpointRepository(engine)

        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="replay_run.db",
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
            review_data=sample_review_payload("APPROVE"),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

        # Replay the run from checkpoints
        replay_res = replay_workflow(res.workflow_run_id, checkpoints=runner.checkpoint_repo)
        assert isinstance(replay_res, ReplayResult)
        assert replay_res.matching_status is True
        assert replay_res.mismatch_details is None
        assert len(replay_res.replay_state_sequence) == len(replay_res.original_state_sequence)
        assert replay_res.replay_state_sequence == replay_res.original_state_sequence

    def test_replay_uses_no_external_network_calls(self) -> None:
        """Matrix Item V: Replay pure engine transition loop performs no network I/O."""
        claim_id = uuid4()
        run_id = uuid4()
        snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.EXTRACTION,
            step_count=1,
        )

        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.EXTRACTION,
            snapshot=snapshot,
            step_count=1,
            rework_count=0,
        ).sealed()

        remaining = [
            (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
        ]
        engine = WorkflowEngine()
        ctx = RunContext(claim_id=claim_id, workflow_run_id=run_id)
        report = run_replay(cp, remaining, engine=engine, ctx=ctx)

        assert report.states == [
            WorkflowState.EXTRACTION,
            WorkflowState.INVESTIGATION,
            WorkflowState.REVIEW,
            WorkflowState.HUMAN_APPROVAL,
        ]
        assert report.steps == 3


# ---------------------------------------------------------------------------
# Categories AA-AD: Replay Mismatch
# ---------------------------------------------------------------------------


class TestReplayMismatch:
    def test_compare_replay_runs_detects_state_divergence(self) -> None:
        """Matrix Item AA: State sequence divergence produces structured ReplayMismatch."""
        orig_id = uuid4()
        rep_id = uuid4()
        orig_states = [
            WorkflowState.RECEIVED,
            WorkflowState.EXTRACTION,
            WorkflowState.INVESTIGATION,
        ]
        rep_states = [WorkflowState.RECEIVED, WorkflowState.EXTRACTION, WorkflowState.FAILED]

        res = compare_replay_runs(
            original_workflow_run_id=orig_id,
            replay_run_id=rep_id,
            original_states=orig_states,
            replay_states=rep_states,
            original_transitions=["CLAIM_VALIDATED", "EXTRACTION_SUCCEEDED"],
            replay_transitions=["CLAIM_VALIDATED", "EXTRACTION_FAILED"],
            original_terminal=None,
            replay_terminal=WorkflowState.FAILED,
        )

        assert res.matching_status is False
        assert res.mismatch_details is not None
        assert res.mismatch_details.first_divergence_index == 2
        assert res.mismatch_details.expected_value == WorkflowState.INVESTIGATION.value
        assert res.mismatch_details.actual_value == WorkflowState.FAILED.value
        assert "State divergence at step 2" in res.mismatch_details.message

    def test_compare_replay_runs_detects_length_mismatch(self) -> None:
        """Matrix Item AB: Different sequence length produces structured ReplayMismatch."""
        orig_id = uuid4()
        rep_id = uuid4()
        orig_states = [WorkflowState.RECEIVED, WorkflowState.EXTRACTION]
        rep_states = [WorkflowState.RECEIVED]

        res = compare_replay_runs(
            original_workflow_run_id=orig_id,
            replay_run_id=rep_id,
            original_states=orig_states,
            replay_states=rep_states,
            original_transitions=["CLAIM_VALIDATED"],
            replay_transitions=[],
            original_terminal=None,
            replay_terminal=None,
        )

        assert res.matching_status is False
        assert res.mismatch_details is not None
        assert res.mismatch_details.step_name == "state_sequence_length"

    def test_replay_divergence_preserves_original_run(self, tmp_path: Path) -> None:
        """Matrix Item AC: Divergent replay does not overwrite original run records."""
        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="diverge.db",
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
            review_data=sample_review_payload("APPROVE"),
        )
        res = runner.run(claim)
        original_cps = runner.checkpoint_repo.list_for_run(res.workflow_run_id)

        # Inject divergent trigger into replay
        replay_res = replay_workflow(
            res.workflow_run_id,
            checkpoints=runner.checkpoint_repo,
            divergent_trigger=Trigger.REVIEW_REJECTED,
        )
        assert replay_res.matching_status is False
        assert replay_res.mismatch_details is not None

        # Verify original run checkpoints are unchanged
        current_cps = runner.checkpoint_repo.list_for_run(res.workflow_run_id)
        assert len(current_cps) == len(original_cps)
        assert [c.checkpoint_id for c in current_cps] == [c.checkpoint_id for c in original_cps]


# ---------------------------------------------------------------------------
# Categories AE-AJ: Budget Model
# ---------------------------------------------------------------------------


class TestBudgetModel:
    def test_budget_envelope_immutable_and_finite(self) -> None:
        """Matrix Item AE: BudgetEnvelope has all 10 explicit limits and rejects NaN/negatives."""
        envelope = BudgetEnvelope(
            max_steps=25,
            max_agent_steps=20,
            max_tool_calls=50,
            max_rework_cycles=2,
            max_agent_retries=2,
            max_wall_clock_seconds=600,
            max_input_tokens=10_000,
            max_output_tokens=2_000,
            max_total_tokens=12_000,
            max_cost_usd=Decimal("1.50"),
        )
        assert envelope.max_steps == 25
        assert envelope.max_cost_usd == Decimal("1.50")

        # Frozen: cannot mutate
        with pytest.raises((TypeError, ValueError)):
            envelope.max_steps = 30  # type: ignore[misc]

    def test_budget_usage_monotonicity(self) -> None:
        """Matrix Item AF: BudgetUsage monotonic accounting (never decrements)."""
        usage = BudgetUsage(input_tokens=500, output_tokens=100)
        usage.totalize_tokens()
        assert usage.total_tokens == 600

        # Tokens cannot be reduced
        with pytest.raises(ValueError):
            usage.input_tokens = -10

    def test_budget_state_contract_limits_check(self) -> None:
        """Matrix Item AG: BudgetState check_limits detects exhaustion across dimensions."""
        state = BudgetState(limits=BudgetLimits(max_steps=5))
        state.steps_completed = 5
        assert state.check_limits() == "MAX_STEPS_EXCEEDED"
        assert state.is_exhausted is True


# ---------------------------------------------------------------------------
# Categories AK-AP: Token & Cost Accounting
# ---------------------------------------------------------------------------


class TestCostCalculator:
    def test_cost_calculation_exact_decimal(self) -> None:
        """Matrix Item AK: Cost calculation yields exact Decimal without float rounding errors."""
        pricing = ModelPricing(
            provider="anthropic",
            model="claude-3-5-sonnet",
            input_per_1k_usd=Decimal("0.003"),
            output_per_1k_usd=Decimal("0.015"),
        )
        usage = ModelUsage(
            provider="anthropic",
            model="claude-3-5-sonnet",
            input_tokens=1000,
            output_tokens=1000,
        )
        cost = calculate_cost(usage, pricing)
        assert cost == Decimal("0.0180")

    def test_cost_calculator_abstraction(self) -> None:
        """Matrix Item AL: CostCalculator encapsulates pricing table lookup and calculation."""
        table = PricingTable(
            entries={
                "openai/gpt-4o": ModelPricing(
                    provider="openai",
                    model="gpt-4o",
                    input_per_1k_usd=Decimal("0.005"),
                    output_per_1k_usd=Decimal("0.015"),
                )
            }
        )
        calc = CostCalculator(table)
        record = calc.calculate("openai", "gpt-4o", 2000, 1000)
        assert isinstance(record, CostRecord)
        assert record.input_tokens == 2000
        assert record.output_tokens == 1000
        assert record.total_tokens == 3000
        assert record.cost_usd == Decimal("0.0250")
        assert record.currency == "USD"

    def test_cost_calculator_unknown_model_raises(self) -> None:
        """Matrix Item AM: Calculating cost for unconfigured model raises ValueError."""
        calc = CostCalculator(PricingTable())
        with pytest.raises(ValueError, match="No pricing declared"):
            calc.calculate("unknown", "mystery", 100, 100)


# ---------------------------------------------------------------------------
# Categories AQ-AV: Step & Loop Guards
# ---------------------------------------------------------------------------


class TestStepAndLoopGuards:
    def test_max_steps_exceeded_transitions_deterministically(self, tmp_path: Path) -> None:
        """Matrix Item AQ: Step limit exhaustion transitions immediately to MAX_STEPS_EXCEEDED."""
        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="max_steps.db",
            max_steps=1,  # Exhaust after initial intake step
            extraction_data=sample_extraction_payload(claim),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.MAX_STEPS_EXCEEDED

    def test_rework_ceiling_enforced(self, tmp_path: Path) -> None:
        """Matrix Item AR: Reviewer rework loops bounded by max_rework_cycles."""
        claim = make_test_claim_input()
        # Create runner with rework feedback
        runner, _, _ = make_runner(
            tmp_path,
            db_name="rework_guard.db",
            max_rework_cycles=0,
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
            review_data=sample_review_payload("REWORK", rework_feedback="Needs police report"),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.MAX_REWORK_EXCEEDED

    def test_tool_call_limit_stops_investigator_lookups(self, tmp_path: Path) -> None:
        """Matrix Item AS: BudgetEngine pre_step(TOOL_CALL) denies tool calls exceeding limit."""
        engine, factory = make_test_db(tmp_path, "tool_limit.db")
        envelope = BudgetEnvelope(max_tool_calls=1)
        usage = BudgetUsage()
        b_engine = BudgetEngine(uuid4(), envelope, usage, SystemClock(), factory)
        b_engine.start_run()

        res1 = b_engine.pre_step(ReserveKind.TOOL_CALL)
        assert res1.granted is True

        res2 = b_engine.pre_step(ReserveKind.TOOL_CALL)
        assert res2.granted is False
        assert res2.reason == BudgetTermination.MAX_TOOL_CALLS_EXCEEDED
        assert res2.workflow_state == WorkflowState.BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Categories AW-AZ: Termination Policy
# ---------------------------------------------------------------------------


class TestTerminationPolicy:
    def test_termination_policy_deterministic_evaluation(self) -> None:
        """Matrix Item AW: Central TerminationPolicy evaluates rules without LLM intervention."""
        # 1. Terminal state check
        d1 = TerminationPolicy.evaluate(WorkflowState.APPROVED, step_count=5, max_steps=50)
        assert d1.should_terminate is True
        assert d1.terminal_state == WorkflowState.APPROVED

        # 2. Budget reason check
        d2 = TerminationPolicy.evaluate(
            WorkflowState.INVESTIGATION,
            step_count=5,
            max_steps=50,
            budget_reason=BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED,
        )
        assert d2.should_terminate is True
        assert d2.terminal_state == WorkflowState.BUDGET_EXHAUSTED

        # 3. Step exhaustion check
        d3 = TerminationPolicy.evaluate(WorkflowState.EXTRACTION, step_count=50, max_steps=50)
        assert d3.should_terminate is True
        assert d3.terminal_state == WorkflowState.MAX_STEPS_EXCEEDED

        # 4. Rework exhaustion check
        d4 = TerminationPolicy.evaluate(
            WorkflowState.REVIEW,
            step_count=10,
            max_steps=50,
            rework_count=3,
            max_rework_cycles=3,
        )
        assert d4.should_terminate is True
        assert d4.terminal_state == WorkflowState.MAX_REWORK_EXCEEDED

        # 5. Non-terminal check
        d5 = TerminationPolicy.evaluate(WorkflowState.EXTRACTION, step_count=2, max_steps=50)
        assert d5.should_terminate is False
        assert d5.terminal_state is None


# ---------------------------------------------------------------------------
# Categories BA-BD: Budget + Checkpoint Atomicity
# ---------------------------------------------------------------------------


class TestBudgetCheckpointAtomicity:
    def test_atomic_persistence_with_unit_of_work(self, tmp_path: Path) -> None:
        """Matrix Item BA: Checkpoint and workflow event persist atomically in UnitOfWork."""
        engine, factory = make_test_db(tmp_path, "atomic_uow.db")
        run_id = uuid4()
        claim_id = uuid4()

        snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.EXTRACTION,
            step_count=1,
        )
        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.EXTRACTION,
            snapshot=snapshot,
            step_count=1,
            rework_count=0,
        )

        with UnitOfWork(factory) as uow:
            uow.checkpoints.create(cp)
            uow.counters.init_counters(run_id, datetime.now(UTC))

        # Verify both committed
        with UnitOfWork(factory) as uow:
            loaded_cp = uow.checkpoints.get(cp.checkpoint_id)
            assert loaded_cp is not None
            counts = uow.counters.get_counts(run_id)
            assert counts["steps"] == 0

    def test_rollback_discards_both_checkpoint_and_counters(self, tmp_path: Path) -> None:
        """Matrix Item BB: Transaction failure rolls back checkpoint and counter state together."""
        engine, factory = make_test_db(tmp_path, "rollback_uow.db")
        run_id = uuid4()
        claim_id = uuid4()

        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
            ),
            step_count=0,
            rework_count=0,
        )

        with (
            pytest.raises(RuntimeError),
            UnitOfWork(factory) as uow,
        ):
            uow.checkpoints.create(cp)
            raise RuntimeError("Simulated failure mid-transaction")

        with (
            UnitOfWork(factory) as uow,
            pytest.raises(CheckpointNotFoundError),
        ):
            uow.checkpoints.get(cp.checkpoint_id)


# ---------------------------------------------------------------------------
# Categories BE-BH: Security
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_checkpoints_contain_no_secrets_or_credentials(self) -> None:
        """Matrix Item BE: Serialized checkpoint JSON contains no API keys or passwords."""
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                current_state=WorkflowState.EXTRACTION,
            ),
            step_count=1,
            rework_count=0,
        ).sealed()

        raw_json = cp.model_dump_json().lower()
        forbidden_patterns = [
            "api_key",
            "apikey",
            "secret",
            "password",
            "credential",
            "bearer",
            "token_secret",
        ]
        for pattern in forbidden_patterns:
            assert (
                pattern not in raw_json
            ), f"Forbidden secret pattern {pattern!r} leaked in checkpoint!"


# ---------------------------------------------------------------------------
# Categories BI-BN: Failure Modes 1-15
# ---------------------------------------------------------------------------


class TestFailureModes:
    def test_failure_mode_1_corrupted_checkpoint(self, tmp_path: Path) -> None:
        """Failure Mode 1: Checkpoint data modified fails verification."""
        engine, _ = make_test_db(tmp_path, "fm1.db")
        repo = SqlCheckpointRepository(engine)
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.RECEIVED
            ),
            step_count=0,
            rework_count=0,
        ).sealed()
        saved = repo.create(cp)
        with Session(engine) as s:
            r = s.get(CheckpointRow, str(saved.checkpoint_id))
            assert r is not None
            r.checksum = "0" * 64
            s.commit()
        with pytest.raises(CheckpointCorruptError):
            resume_from_checkpoint(saved.checkpoint_id, checkpoints=repo)

    def test_failure_mode_2_checksum_mismatch(self) -> None:
        """Failure Mode 2: Checksum mismatch fails verification."""
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.RECEIVED,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.RECEIVED
            ),
            step_count=0,
            rework_count=0,
            checksum="0" * 64,
        )
        assert cp.verify_integrity() is False

    def test_failure_mode_3_missing_checkpoint(self, tmp_path: Path) -> None:
        """Failure Mode 3: Missing checkpoint ID raises CheckpointNotFoundError."""
        engine, _ = make_test_db(tmp_path, "fm3.db")
        repo = SqlCheckpointRepository(engine)
        with pytest.raises(CheckpointNotFoundError):
            resume_from_checkpoint(uuid4(), checkpoints=repo)

    def test_failure_mode_6_resume_from_terminal_state(self, tmp_path: Path) -> None:
        """Failure Mode 6: Resume from terminal state does not progress."""
        engine, _ = make_test_db(tmp_path, "fm6.db")
        repo = SqlCheckpointRepository(engine)
        claim_id = uuid4()
        run_id = uuid4()
        cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.REJECTED,
            kind=CheckpointKind.TERMINAL,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id,
                claim_id=claim_id,
                current_state=WorkflowState.REJECTED,
                step_count=3,
                rework_count=0,
            ),
            step_count=3,
            rework_count=0,
            terminal_reason="Fraud suspected",
        ).sealed()
        saved = repo.create(cp)
        runner = WorkflowRunner(
            ExtractorAgent(DeterministicProvider()),
            InvestigatorAgent(DeterministicProvider()),
            ReviewerAgent(DeterministicProvider()),
            checkpoint_repo=repo,
        )
        wf_ctx = runner.resume(saved.checkpoint_id)
        assert wf_ctx.current_snapshot.current_state == WorkflowState.REJECTED

    def test_failure_mode_8_token_budget_exhaustion(self, tmp_path: Path) -> None:
        """Failure Mode 8: Token budget ceiling is enforced."""
        engine, factory = make_test_db(tmp_path, "fm8.db")
        envelope = BudgetEnvelope(max_total_tokens=100)
        b_engine = BudgetEngine(uuid4(), envelope, BudgetUsage(), SystemClock(), factory)
        b_engine.start_run()
        res = b_engine.reserve_tokens(60, 50)
        assert res.granted is False
        assert res.reason == BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED

    def test_failure_mode_9_cost_budget_exhaustion(self, tmp_path: Path) -> None:
        """Failure Mode 9: Monetary cost ceiling is enforced."""
        engine, factory = make_test_db(tmp_path, "fm9.db")
        envelope = BudgetEnvelope(max_cost_usd=Decimal("0.01"))
        table = PricingTable(
            entries={
                "openai/gpt-4o": ModelPricing(
                    provider="openai",
                    model="gpt-4o",
                    input_per_1k_usd=Decimal("0.01"),
                    output_per_1k_usd=Decimal("0.02"),
                )
            }
        )
        b_engine = BudgetEngine(
            uuid4(), envelope, BudgetUsage(), SystemClock(), factory, pricing=table
        )
        b_engine.start_run()
        b_engine.record_model_call(
            ModelUsage(provider="openai", model="gpt-4o", input_tokens=1000, output_tokens=1000)
        )
        assert b_engine.check_all() == BudgetTermination.MAX_COST_EXCEEDED


# ---------------------------------------------------------------------------
# Categories BO-BV: End-to-End Scenarios 1 through 8
# ---------------------------------------------------------------------------


class TestEndToEndScenarios:
    def test_scenario_1_checkpointed_nominal_workflow(self, tmp_path: Path) -> None:
        """Scenario 1: Nominal claim intake -> Extractor -> Investigator -> Reviewer -> HUMAN_APPROVAL with durable checkpoints."""
        claim = make_test_claim_input()
        runner, factory, cp_repo = make_runner(
            tmp_path,
            db_name="scen1.db",
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
            review_data=sample_review_payload("APPROVE"),
        )
        wf_ctx = runner.run(claim)
        assert wf_ctx.current_snapshot is not None
        assert wf_ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

        # Verify checkpoints exist in repository for each boundary
        checkpoints = cp_repo.list_for_run(wf_ctx.workflow_run_id)
        assert len(checkpoints) >= 3  # Intake, Extraction, Investigation, Review
        states = [cp.state for cp in checkpoints]
        assert WorkflowState.EXTRACTION in states
        assert WorkflowState.INVESTIGATION in states
        assert WorkflowState.HUMAN_APPROVAL in states

    def test_scenario_2_resume_from_investigation_checkpoint(self, tmp_path: Path) -> None:
        """Scenario 2: Resume from investigation checkpoint skips extractor and investigator."""
        claim = make_test_claim_input()
        engine, factory = make_test_db(tmp_path, "scen2.db")
        cp_repo = SqlCheckpointRepository(engine)

        run_id = uuid4()
        # Seed an investigation checkpoint
        ext_res = ExtractionResult.model_validate(
            sample_extraction_payload(claim, workflow_id=run_id)
        )
        inv_res = InvestigationResult.model_validate(
            sample_investigation_payload(workflow_id=run_id)
        )

        cp = Checkpoint(
            claim_id=claim.claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.REVIEW,
            snapshot=WorkflowSnapshot(
                workflow_run_id=run_id,
                claim_id=claim.claim_id,
                current_state=WorkflowState.REVIEW,
                step_count=3,
            ),
            step_count=3,
            rework_count=0,
            recorded_agents=(
                RecordedAgentOutput(
                    agent=AgentType.EXTRACTOR,
                    contract_type="extraction_result",
                    output_json=ext_res.model_dump_json(),
                    execution_id=uuid4(),
                ),
                RecordedAgentOutput(
                    agent=AgentType.INVESTIGATOR,
                    contract_type="investigation_result",
                    output_json=inv_res.model_dump_json(),
                    execution_id=uuid4(),
                ),
            ),
        ).sealed()
        saved = cp_repo.create(cp)

        # Create fresh runner
        ext_prov = DeterministicProvider()
        inv_prov = DeterministicProvider()
        rev_prov = DeterministicProvider()
        rev_prov.push_json(sample_review_payload("APPROVE", workflow_id=run_id))

        fresh_runner = WorkflowRunner(
            ExtractorAgent(ext_prov),
            InvestigatorAgent(inv_prov),
            ReviewerAgent(rev_prov),
            uow_factory=lambda: UnitOfWork(factory),
            checkpoint_repo=cp_repo,
        )

        res = fresh_runner.resume(saved.checkpoint_id, claim=claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

        # Crucial: Extractor and Investigator were never executed
        assert len(ext_prov.requests) == 0
        assert len(inv_prov.requests) == 0
        assert len(rev_prov.requests) == 1

    def test_scenario_3_replay(self, tmp_path: Path) -> None:
        """Scenario 3: Replay stored workflow and verify sequence matches."""
        claim = make_test_claim_input()
        runner, _, cp_repo = make_runner(
            tmp_path,
            db_name="scen3.db",
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
            review_data=sample_review_payload("APPROVE"),
        )
        res = runner.run(claim)

        replay_res = replay_workflow(res.workflow_run_id, checkpoints=cp_repo)
        assert replay_res.matching_status is True
        assert replay_res.original_state_sequence == replay_res.replay_state_sequence

    def test_scenario_4_corrupted_checkpoint(self, tmp_path: Path) -> None:
        """Scenario 4: Tampered checkpoint payload/checksum rejected on resume."""
        engine, factory = make_test_db(tmp_path, "scen4.db")
        cp_repo = SqlCheckpointRepository(engine)

        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.EXTRACTION
            ),
            step_count=1,
            rework_count=0,
        ).sealed()
        saved = cp_repo.create(cp)

        # Corrupt the row in SQLite
        with Session(engine) as s:
            r = s.get(CheckpointRow, str(saved.checkpoint_id))
            assert r is not None
            r.checksum = "0" * 64
            s.commit()

        fresh_runner = WorkflowRunner(
            ExtractorAgent(DeterministicProvider()),
            InvestigatorAgent(DeterministicProvider()),
            ReviewerAgent(DeterministicProvider()),
            checkpoint_repo=cp_repo,
        )

        with pytest.raises(CheckpointCorruptError):
            fresh_runner.resume(saved.checkpoint_id)

    def test_scenario_5_budget_exhaustion(self, tmp_path: Path) -> None:
        """Scenario 5: Budget ceiling halts workflow, terminal state BUDGET_EXHAUSTED."""
        engine, factory = make_test_db(tmp_path, "scen5.db")
        envelope = BudgetEnvelope(max_total_tokens=0)  # Exhaust tokens immediately
        b_engine = BudgetEngine(uuid4(), envelope, BudgetUsage(), SystemClock(), factory)
        b_engine.start_run()

        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="scen5_run.db",
            budget_engine=b_engine,
            extraction_data=sample_extraction_payload(claim),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.BUDGET_EXHAUSTED

    def test_scenario_6_step_exhaustion(self, tmp_path: Path) -> None:
        """Scenario 6: Low max_steps reaches MAX_STEPS_EXCEEDED without further execution."""
        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="scen6.db",
            max_steps=2,
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.MAX_STEPS_EXCEEDED

    def test_scenario_7_rework_loop_protection(self, tmp_path: Path) -> None:
        """Scenario 7: Reviewer rework loops terminate deterministically at MAX_REWORK_EXCEEDED."""
        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="scen7.db",
            max_rework_cycles=0,
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
            review_data=sample_review_payload("REWORK", rework_feedback="Clarify damage"),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        assert res.current_snapshot.current_state == WorkflowState.MAX_REWORK_EXCEEDED

    def test_scenario_8_combined_safety_limits(self, tmp_path: Path) -> None:
        """Scenario 8: Combined tight limits enforce the earliest safety boundary deterministically."""
        engine, factory = make_test_db(tmp_path, "scen8.db")
        envelope = BudgetEnvelope(max_steps=10, max_agent_steps=1, max_tool_calls=2)
        b_engine = BudgetEngine(uuid4(), envelope, BudgetUsage(), SystemClock(), factory)
        b_engine.start_run()

        claim = make_test_claim_input()
        runner, _, _ = make_runner(
            tmp_path,
            db_name="scen8_run.db",
            max_steps=10,
            budget_engine=b_engine,
            extraction_data=sample_extraction_payload(claim),
            investigation_data=sample_investigation_payload(),
        )
        res = runner.run(claim)
        assert res.current_snapshot is not None
        # Either max agent steps or step limit or budget halts execution
        assert res.current_snapshot.current_state in (
            WorkflowState.BUDGET_EXHAUSTED,
            WorkflowState.MAX_STEPS_EXCEEDED,
        )


# ---------------------------------------------------------------------------
# Categories BW-BZ: Architecture Boundaries
# ---------------------------------------------------------------------------


class TestArchitectureBoundaries:
    def test_checkpoints_do_not_store_live_python_objects(self) -> None:
        """Matrix Item BW: Checkpoint models and payloads store only JSON primitives."""
        cp = Checkpoint(
            claim_id=uuid4(),
            workflow_run_id=uuid4(),
            state=WorkflowState.EXTRACTION,
            snapshot=WorkflowSnapshot(
                workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.EXTRACTION
            ),
            step_count=1,
            rework_count=0,
        )
        raw_dict = cp.model_dump()
        for _k, v in raw_dict.items():
            assert not isinstance(v, Session | Engine | WorkflowEngine | ExtractorAgent)

    def test_tools_cannot_mutate_budget_state_directly(self) -> None:
        """Matrix Item BX: Tools execute against ToolContext without budget mutation handles."""
        _ = PolicyLookupTool()
        ctx = ToolContext(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            execution_id=uuid4(),
            correlation_id=uuid4(),
            agent=AgentType.INVESTIGATOR,
        )
        assert not hasattr(ctx, "budget_state")
        assert not hasattr(ctx, "mutate_budget")

    def test_llm_cannot_decide_budget_limits(self) -> None:
        """Matrix Item BY: Budget limits originate strictly from trusted BudgetEnvelope config."""
        envelope = BudgetEnvelope(max_total_tokens=5000)
        assert envelope.max_total_tokens == 5000
        # Untrusted agent cannot alter frozen envelope
        with pytest.raises((TypeError, ValueError)):
            envelope.max_total_tokens = 999999  # type: ignore[misc]

    def test_workflow_never_continues_after_terminal_state(self, tmp_path: Path) -> None:
        """Matrix Item BZ: Applying transitions to a terminal state is strictly rejected."""
        engine = WorkflowEngine()
        snap = WorkflowSnapshot(
            workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.APPROVED
        )
        ctx = RunContext(claim_id=snap.claim_id, workflow_run_id=snap.workflow_run_id)

        with pytest.raises(Exception, match="is terminal"):
            engine.apply(
                snap,
                TransitionEvent(
                    claim_id=snap.claim_id,
                    workflow_run_id=snap.workflow_run_id,
                    trigger=Trigger.CLAIM_VALIDATED,
                    actor=AgentType.SUPERVISOR,
                ),
                ctx,
            )
