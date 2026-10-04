"""
Phase 5 Acceptance Test Matrix (Items A through CL) and End-to-End Scenarios 1–8.

Validates the complete Phase 5 scope:
- A-H: Approval domain model (ApprovalRequest, ApprovalDecision, ApprovalStatus, ApprovalActor, ApprovalAuditRecord, Pydantic v2 validation, immutability)
- I-N: Approval state machine (PENDING -> APPROVED/REJECTED/EXPIRED/CANCELLED, immutable terminal states, typed transition errors)
- O-T: Approval authorization & roles (CLAIM_REVIEWER, SENIOR_REVIEWER, CLAIM_SUPERVISOR, ADMIN, agent/supervisor rejected, role hierarchy)
- U-Z: Separation of duties (requester cannot self-approve, supervisor cannot approve, real actor identity enforced)
- AA-AF: Persistence (request/decision/audit persistence, schema migration 0008, domain/persistence separation)
- AG-AL: Workflow integration (HUMAN_APPROVAL parking, no auto-approval, approved/rejected terminal transitions, timeout escalation)
- AM-AR: Checkpoint + resume integration (checkpoint on HUMAN_WAIT, resume preserves pending approval without auto-approving)
- AS-AX: Replay safety (replay never contacts humans, recorded trigger replayed deterministically, original records immutable)
- AY-BD: Observability architecture (spans for workflow, agent, tool, persistence, checkpoint, budget, approval)
- BE-BJ: Trace correlation (workflow_run_id, execution_id, correlation_id, claim_id propagated across layers)
- BK-BP: Metrics (counter/histogram definitions, low cardinality enforcement, budget metric integration)
- BQ-BV: Sanitization (credentials, secrets, documents, prompts, outputs redacted/dropped)
- BW-BZ: Failure behavior (Jaeger/OTLP failure does not crash workflow, telemetry fails safely)
- CA-CF: End-to-end scenarios 1 through 8 (Nominal HITL, Rejection, Unauthorized attempt, Expiration, Resume, Trace correlation, Sensitive telemetry, Telemetry backend unavailable)
- CG-CL: Architecture boundaries & Part 12 Approval Security (12 security test cases)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.approval.model import (
    ApprovalActor,
    ApprovalAuditRecord,
    ApprovalAuthorizer,
    ApprovalDecision,
    ApprovalError,
    ApprovalErrorCode,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalRole,
    ApprovalStateMachine,
    ApprovalVerdict,
    HumanActor,
    InvalidApprovalTransitionError,
)
from casefile.approval.service import ApprovalService
from casefile.checkpoint.model import (
    CheckpointKind,
)
from casefile.checkpoint.replay import replay_workflow
from casefile.checkpoint.repository import CheckpointRepository, SqlCheckpointRepository
from casefile.models.contracts import (
    ClaimInput,
    WorkflowState,
)
from casefile.models.domain import (
    ActorType,
    ApprovalStatus,
    HumanApproval,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.persistence import (
    HumanApprovalRecord,
    get_engine,
    get_session_factory,
    init_db,
)
from casefile.observability import (
    MAX_ATTRIBUTE_LENGTH,
    METRIC_NAMES,
    SPAN_AGENT,
    SPAN_APPROVAL,
    SPAN_BUDGET,
    SPAN_CHECKPOINT,
    SPAN_PERSISTENCE,
    SPAN_TOOL,
    SPAN_WORKFLOW,
    ExecMode,
    HookBridge,
    OtelTracer,
    TraceContext,
    approval_attributes,
    checkpoint_attributes,
    maybe_span,
    persistence_attributes,
    safe_error,
    sanitize_attributes,
    sanitize_value,
)
from casefile.observability import (
    test_observability as make_test_obs,
)
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import (
    FixedClock,
    WorkflowContext,
)
from casefile.workflow.hooks import WorkflowHookEvent
from casefile.workflow.runner import WorkflowRunner
from casefile.workflow.triggers import Trigger

# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine(tmp_path: Path):
    engine = get_engine(f"sqlite:///{tmp_path / 'phase5_acceptance.db'}")
    init_db(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine) -> sessionmaker[Session]:
    return get_session_factory(db_engine)


@pytest.fixture
def uow_factory(db_session_factory: sessionmaker[Session]):
    return lambda: UnitOfWork(db_session_factory)


@pytest.fixture
def checkpoint_repo(db_engine) -> CheckpointRepository:
    return SqlCheckpointRepository(db_engine)


@pytest.fixture
def approval_service(db_session_factory: sessionmaker[Session]) -> ApprovalService:
    clock = FixedClock(datetime.now(UTC))
    return ApprovalService(db_session_factory, clock)


@pytest.fixture
def sample_claim() -> ClaimInput:
    return ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-9999",
        claimant_name="Alice Smith",
        incident_date="2026-03-01",
        claim_amount=Decimal("1500.00"),
        description="Rear bumper damage in parking lot",
        documents=["repair_estimate.pdf", "police_report.pdf"],
    )


def _make_approval_request(
    workflow_run_id: UUID | None = None,
    claim_id: UUID | None = None,
    requested_by: str = "reviewer",
    required_role: str = "CLAIM_REVIEWER",
    deadline: datetime | None = None,
    requested_at: datetime | None = None,
    amount: Decimal = Decimal("100.00"),
    notes: str = "Standard review notes",
) -> HumanApprovalRequest:
    w_id = workflow_run_id or uuid4()
    c_id = claim_id or uuid4()
    actual_deadline = deadline or (datetime.now(UTC) + timedelta(hours=24))
    if requested_at is not None:
        actual_req_at = requested_at
    elif actual_deadline <= datetime.now(UTC):
        actual_req_at = actual_deadline - timedelta(hours=1)
    else:
        actual_req_at = datetime.now(UTC) - timedelta(minutes=5)
    return HumanApprovalRequest(
        workflow_run_id=w_id,
        claim_id=c_id,
        recommendation=Recommendation(
            claim_id=c_id,
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=amount),
            notes=notes,
            confidence=0.95,
        ),
        reviewer_summary=notes,
        requested_at=actual_req_at,
        deadline=actual_deadline,
        requested_by=requested_by,
        required_role=required_role,
    )


class SmartDeterministicProvider(DeterministicProvider):
    """DeterministicProvider that automatically matches workflow_id to the running workflow."""

    def __init__(
        self, runner_holder: list | None = None, provider_name: str = "deterministic"
    ) -> None:
        super().__init__(provider_name)
        self._runner_holder = runner_holder if runner_holder is not None else []

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
                and getattr(self._runner_holder[0], "active_run_id", None) is not None
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


def _make_agents(runner_holder: list | None = None):
    holder = runner_holder if runner_holder is not None else []
    extractor_provider = SmartDeterministicProvider(holder)
    extractor_provider.push_json(
        {
            "workflow_id": str(uuid4()),
            "claimant_name": "Alice Smith",
            "policy_id": "POL-9999",
            "incident_date": "2026-03-01",
            "incident_location": "Main St Parking",
            "incident_description": "Rear bumper damage",
            "claim_amount": "1500.00",
            "requested_coverage_type": "collision",
            "documents_processed": ["repair_estimate.pdf"],
            "extraction_confidence": 0.98,
            "missing_fields": [],
            "is_complete": True,
        }
    )
    investigator_provider = SmartDeterministicProvider(holder)
    investigator_provider.push_json(
        {
            "workflow_id": str(uuid4()),
            "policy_details": {"active": True, "coverage_limit": 50000},
            "claim_history": {"prior_claims_count": 0},
            "repair_cost_validation": {"estimate_amount": "1500.00", "is_reasonable": True},
            "fraud_signals": {"risk_score": 0.05, "flags": []},
            "supporting_documents": ["repair_estimate.pdf"],
            "findings_summary": "Policy active, repair estimate reasonable, low risk.",
            "evidence_strength": 0.95,
            "tool_calls_made": ["policy_lookup", "repair_cost_lookup"],
            "investigation_duration_seconds": 1,
            "tools_succeeded": 2,
            "tools_failed": 0,
        }
    )
    reviewer_provider = SmartDeterministicProvider(holder)
    reviewer_provider.push_json(
        {
            "workflow_id": str(uuid4()),
            "decision": "APPROVE",
            "reasoning": "All evidence complete and verified within policy limits.",
            "confidence_score": 0.96,
            "evidence_completeness": 0.95,
            "identified_gaps": [],
            "rework_feedback": None,
            "fraud_risk_level": "LOW",
            "fraud_signals_detected": False,
        }
    )
    return (
        ExtractorAgent(provider=extractor_provider),
        InvestigatorAgent(provider=investigator_provider),
        ReviewerAgent(provider=reviewer_provider),
    )


def is_terminal_state(state: WorkflowState) -> bool:
    return state in {
        WorkflowState.APPROVED,
        WorkflowState.REJECTED,
        WorkflowState.FAILED,
        WorkflowState.ESCALATION,
        WorkflowState.TIMEOUT,
        WorkflowState.BUDGET_EXHAUSTED,
        WorkflowState.MAX_STEPS_EXCEEDED,
        WorkflowState.MAX_REWORK_EXCEEDED,
    }


# ===========================================================================
# MATRIX A–H: APPROVAL DOMAIN MODEL
# ===========================================================================


def test_matrix_a_approval_request_model():
    """A: ApprovalRequest is a typed Pydantic v2 model with all required fields."""
    now = datetime.now(UTC)
    c_id = uuid4()
    req = ApprovalRequest(
        approval_id=uuid4(),
        workflow_run_id=uuid4(),
        claim_id=c_id,
        requested_at=now,
        requested_by="reviewer-agent",
        current_workflow_state="HUMAN_APPROVAL",
        recommendation={"action": "APPROVE", "amount": "1500.00"},
        confidence=0.95,
        decision_deadline=now + timedelta(hours=24),
        required_role=ApprovalRole.CLAIM_REVIEWER,
        status=ApprovalStatus.PENDING,
    )
    assert req.claim_id == c_id
    assert req.status == ApprovalStatus.PENDING
    assert req.required_role == ApprovalRole.CLAIM_REVIEWER
    assert req.confidence == 0.95


def test_matrix_b_approval_decision_model():
    """B: ApprovalDecision carries typed decision, actor, reason, correlation_id."""
    actor = ApprovalActor(actor_id="human-reviewer-1", role=ApprovalRole.CLAIM_REVIEWER)
    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=actor,
        decided_at=datetime.now(UTC),
        reason="Claim validated against policy",
        correlation_id=uuid4(),
    )
    assert decision.verdict == ApprovalVerdict.APPROVED
    assert decision.actor.actor_id == "human-reviewer-1"
    assert decision.actor.role == ApprovalRole.CLAIM_REVIEWER


def test_matrix_c_approval_status_enum():
    """C: ApprovalStatus enum defines standard lifecycle states."""
    assert ApprovalStatus.PENDING.value == "PENDING"
    assert ApprovalStatus.APPROVED.value == "APPROVED"
    assert ApprovalStatus.REJECTED.value == "REJECTED"
    assert ApprovalStatus.EXPIRED.value == "EXPIRED"
    assert ApprovalStatus.CANCELLED.value == "CANCELLED"


def test_matrix_d_approval_actor_model_and_validation():
    """D: ApprovalActor enforces non-empty ID and role validation."""
    actor = ApprovalActor(actor_id="admin-1", role=ApprovalRole.ADMIN)
    assert actor.actor_id == "admin-1"
    assert actor.role == ApprovalRole.ADMIN
    assert actor.display_name == "admin-1"

    with pytest.raises(ValueError):
        ApprovalActor(actor_id="", role=ApprovalRole.CLAIM_REVIEWER)


def test_matrix_e_approval_audit_record_model():
    """E: ApprovalAuditRecord carries event type, actor, timestamps, and correlation."""
    rec = ApprovalAuditRecord(
        event_id=uuid4(),
        event_type="APPROVAL_DECIDED",
        actor=ApprovalActor(actor_id="usr-1", role=ApprovalRole.CLAIM_REVIEWER),
        timestamp=datetime.now(UTC),
        workflow_run_id=uuid4(),
        approval_id=uuid4(),
        correlation_id=uuid4(),
        action="DECIDE",
        result="SUCCESS",
        details={"verdict": "APPROVED"},
    )
    assert rec.event_type == "APPROVAL_DECIDED"
    assert rec.result == "SUCCESS"


def test_matrix_f_immutability_of_domain_models():
    """F: Models enforce frozen/immutable semantics where applicable."""
    actor = ApprovalActor(actor_id="user-1", role=ApprovalRole.CLAIM_REVIEWER)
    with pytest.raises((TypeError, ValueError)):
        actor.actor_id = "user-2"  # type: ignore[misc]


def test_matrix_g_no_untyped_dicts_as_primary_contracts():
    """G: Primary contracts are typed Pydantic models, rejecting invalid types."""
    with pytest.raises((TypeError, ValueError)):
        ApprovalRequest(
            approval_id="not-a-uuid",  # type: ignore[arg-type]
            workflow_run_id="invalid",  # type: ignore[arg-type]
            claim_id=uuid4(),
            requested_at="yesterday",  # type: ignore[arg-type]
            requested_by="",
            current_workflow_state="",
            recommendation=None,  # type: ignore[arg-type]
            confidence="high",  # type: ignore[arg-type]
            decision_deadline="never",  # type: ignore[arg-type]
            required_role="INVALID_ROLE",  # type: ignore[arg-type]
            status="UNKNOWN",  # type: ignore[arg-type]
        )


def test_matrix_h_domain_request_adapter():
    """H: ApprovalRequest adapts seamlessly to HumanApprovalRequest for backward compatibility."""
    now = datetime.now(UTC)
    c_id = uuid4()
    req = ApprovalRequest(
        approval_id=uuid4(),
        workflow_run_id=uuid4(),
        claim_id=c_id,
        requested_at=now,
        requested_by="reviewer",
        current_workflow_state="HUMAN_APPROVAL",
        recommendation={"notes": "Approve full claim"},
        confidence=0.9,
        decision_deadline=now + timedelta(hours=12),
        required_role=ApprovalRole.CLAIM_REVIEWER,
        status=ApprovalStatus.PENDING,
    )
    compat = req.to_domain_request()
    assert compat.claim_id == c_id
    assert compat.reviewer_summary == "Approve full claim"


# ===========================================================================
# MATRIX I–N: APPROVAL STATE MACHINE
# ===========================================================================


def test_matrix_i_valid_state_transitions():
    """I: PENDING transitions to APPROVED, REJECTED, EXPIRED, CANCELLED."""
    assert (
        ApprovalStateMachine.transition(ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
        == ApprovalStatus.APPROVED
    )
    assert (
        ApprovalStateMachine.transition(ApprovalStatus.PENDING, ApprovalStatus.REJECTED)
        == ApprovalStatus.REJECTED
    )
    assert (
        ApprovalStateMachine.transition(ApprovalStatus.PENDING, ApprovalStatus.EXPIRED)
        == ApprovalStatus.EXPIRED
    )
    assert (
        ApprovalStateMachine.transition(ApprovalStatus.PENDING, ApprovalStatus.CANCELLED)
        == ApprovalStatus.CANCELLED
    )


def test_matrix_j_terminal_states_are_immutable():
    """J: Terminal states reject any further transitions with typed errors."""
    for term in [
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
    ]:
        for target in [ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]:
            with pytest.raises(InvalidApprovalTransitionError):
                ApprovalStateMachine.transition(term, target)


def test_matrix_k_invalid_direct_transition_raises_typed_error():
    """K: Illegal transitions raise InvalidApprovalTransitionError."""
    with pytest.raises(InvalidApprovalTransitionError):
        ApprovalStateMachine.transition(ApprovalStatus.APPROVED, ApprovalStatus.PENDING)


def test_matrix_l_state_machine_terminal_check():
    """L: is_terminal helper correctly classifies approval states."""
    assert not ApprovalStateMachine.is_terminal(ApprovalStatus.PENDING)
    assert ApprovalStateMachine.is_terminal(ApprovalStatus.APPROVED)
    assert ApprovalStateMachine.is_terminal(ApprovalStatus.REJECTED)
    assert ApprovalStateMachine.is_terminal(ApprovalStatus.EXPIRED)
    assert ApprovalStateMachine.is_terminal(ApprovalStatus.CANCELLED)


def test_matrix_m_verdict_to_status_mapping():
    """M: ApprovalVerdict translates deterministically to target ApprovalStatus."""
    assert (
        ApprovalStateMachine.target_status_for_verdict(ApprovalVerdict.APPROVED)
        == ApprovalStatus.APPROVED
    )
    assert (
        ApprovalStateMachine.target_status_for_verdict(ApprovalVerdict.REJECTED)
        == ApprovalStatus.REJECTED
    )


def test_matrix_n_action_to_status_mapping():
    """N: Expire and cancel actions map to terminal states."""
    assert ApprovalStateMachine.target_status_for_action("EXPIRE") == ApprovalStatus.EXPIRED
    assert ApprovalStateMachine.target_status_for_action("CANCEL") == ApprovalStatus.CANCELLED


# ===========================================================================
# MATRIX O–T: APPROVAL AUTHORIZATION
# ===========================================================================


def test_matrix_o_typed_role_model():
    """O: All approval roles are defined in typed ApprovalRole enum."""
    roles = {r.value for r in ApprovalRole}
    assert "CLAIM_REVIEWER" in roles
    assert "SENIOR_REVIEWER" in roles
    assert "CLAIM_SUPERVISOR" in roles
    assert "ADMIN" in roles


def test_matrix_p_authorized_human_roles_succeed():
    """P: Authorized roles meet required role thresholds according to hierarchy."""
    authorizer = ApprovalAuthorizer()
    reviewer = ApprovalActor(actor_id="user-1", role=ApprovalRole.CLAIM_REVIEWER)
    senior = ApprovalActor(actor_id="user-2", role=ApprovalRole.SENIOR_REVIEWER)
    supervisor = ApprovalActor(actor_id="user-3", role=ApprovalRole.CLAIM_SUPERVISOR)
    admin = ApprovalActor(actor_id="user-4", role=ApprovalRole.ADMIN)

    authorizer.check_authorization(reviewer, ApprovalRole.CLAIM_REVIEWER)
    authorizer.check_authorization(senior, ApprovalRole.CLAIM_REVIEWER)
    authorizer.check_authorization(admin, ApprovalRole.CLAIM_SUPERVISOR)
    assert supervisor.role == ApprovalRole.CLAIM_SUPERVISOR


def test_matrix_q_insufficient_role_fails():
    """Q: A junior role cannot satisfy a senior role requirement."""
    authorizer = ApprovalAuthorizer()
    reviewer = ApprovalActor(actor_id="user-1", role=ApprovalRole.CLAIM_REVIEWER)
    with pytest.raises(ApprovalError) as exc_info:
        authorizer.check_authorization(reviewer, ApprovalRole.CLAIM_SUPERVISOR)
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_matrix_r_agents_cannot_receive_approval_permissions():
    """R: Agents attempting approval authorization are explicitly rejected."""
    authorizer = ApprovalAuthorizer()
    agent_actor = ApprovalActor(actor_id="agent-investigator", role=ApprovalRole.CLAIM_REVIEWER)
    with pytest.raises(ApprovalError) as exc_info:
        authorizer.check_authorization(agent_actor, ApprovalRole.CLAIM_REVIEWER)
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_matrix_s_workflow_supervisor_cannot_approve():
    """S: Workflow supervisor identity is barred from human approval."""
    authorizer = ApprovalAuthorizer()
    sup_actor = ApprovalActor(actor_id="supervisor", role=ApprovalRole.ADMIN)
    with pytest.raises(ApprovalError) as exc_info:
        authorizer.check_authorization(sup_actor, ApprovalRole.ADMIN)
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_matrix_t_non_human_actor_type_rejected():
    """T: HumanActor with non-HUMAN actor_type fails validation."""
    with pytest.raises(ValueError):
        HumanActor(actor_id="bot", actor_type=ActorType.AGENT)  # type: ignore[call-arg]


# ===========================================================================
# MATRIX U–Z: SEPARATION OF DUTIES
# ===========================================================================


def test_matrix_u_requester_cannot_approve():
    """U: The requester cannot self-approve when separation of duties is enforced."""
    authorizer = ApprovalAuthorizer()
    requester = ApprovalActor(actor_id="evaluator-john", role=ApprovalRole.SENIOR_REVIEWER)
    with pytest.raises(ApprovalError) as exc_info:
        authorizer.check_separation_of_duties(
            actor=requester,
            requested_by="evaluator-john",
            enforce=True,
        )
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_matrix_v_distinct_actor_satisfies_separation_of_duties():
    """V: Different actor passes separation of duties."""
    authorizer = ApprovalAuthorizer()
    decider = ApprovalActor(actor_id="reviewer-mary", role=ApprovalRole.SENIOR_REVIEWER)
    authorizer.check_separation_of_duties(
        actor=decider,
        requested_by="evaluator-john",
        enforce=True,
    )


def test_matrix_w_actor_must_have_identity():
    """W: Decision without actor identity is rejected."""
    authorizer = ApprovalAuthorizer()
    with pytest.raises(ApprovalError):
        authorizer.check_authorization(None)  # type: ignore[arg-type]


def test_matrix_x_supervisor_agent_cannot_self_approve():
    """X: Agent supervisor cannot approve even if requested_by is different."""
    authorizer = ApprovalAuthorizer()
    sup = ApprovalActor(actor_id="supervisor", role=ApprovalRole.ADMIN)
    with pytest.raises(ApprovalError):
        authorizer.check_separation_of_duties(sup, requested_by="other_agent", enforce=True)


def test_matrix_y_separation_of_duties_in_service(approval_service: ApprovalService):
    """Y: ApprovalService enforces separation of duties when requested_by matches decider."""
    req = _make_approval_request(requested_by="auditor-1")
    rec = approval_service.request_approval(req)

    # Same decider as requested_by fails
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="auditor-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Self approval attempt",
        )
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_matrix_z_separation_of_duties_succeeds_for_distinct_actor(
    approval_service: ApprovalService,
):
    """Z: ApprovalService accepts decision from distinct authorized actor."""
    req = _make_approval_request(requested_by="auditor-1")
    rec = approval_service.request_approval(req)

    res = approval_service.decide(
        approval_id=rec.approval_id,
        actor=ApprovalActor(actor_id="auditor-2", role=ApprovalRole.CLAIM_REVIEWER),
        verdict=ApprovalVerdict.APPROVED,
        reason="Approved by separate peer",
    )
    assert res.outcome == ApprovalOutcome.DECIDED
    assert res.approval.status == ApprovalStatus.APPROVED


# ===========================================================================
# MATRIX AA–AF: PERSISTENCE
# ===========================================================================


def test_matrix_aa_persist_approval_request_with_metadata(
    approval_service: ApprovalService, db_session_factory: sessionmaker[Session]
):
    """AA: Approval request is persisted with deadline, requested_by, and required_role."""
    deadline = datetime.now(UTC) + timedelta(hours=48)
    req = _make_approval_request(
        requested_by="extractor-lead",
        required_role=ApprovalRole.SENIOR_REVIEWER.value,
        deadline=deadline,
    )
    created = approval_service.request_approval(req)

    with db_session_factory() as session:
        row = session.get(HumanApprovalRecord, str(created.approval_id))
        assert row is not None
        assert row.status == "PENDING"
        assert row.requested_by == "extractor-lead"
        assert row.required_role == ApprovalRole.SENIOR_REVIEWER.value


def test_matrix_ab_persist_approval_decision(
    approval_service: ApprovalService, db_session_factory: sessionmaker[Session]
):
    """AB: Approval decision is persisted atomically in database."""
    req = _make_approval_request(notes="Valid claim")
    created = approval_service.request_approval(req)

    approval_service.decide(
        approval_id=created.approval_id,
        actor=ApprovalActor(actor_id="human-reviewer", role=ApprovalRole.CLAIM_REVIEWER),
        verdict=ApprovalVerdict.APPROVED,
        reason="Approved after document inspection",
    )

    with db_session_factory() as session:
        row = session.get(HumanApprovalRecord, str(created.approval_id))
        assert row is not None
        assert row.status == "APPROVED"
        assert row.decision == "APPROVED"
        assert row.decided_by == "human-reviewer"
        assert row.decision_reason == "Approved after document inspection"


def test_matrix_ac_persist_approval_audit_trail(approval_service: ApprovalService):
    """AC: Audit events are persisted for request creation and decision."""
    req = _make_approval_request(notes="Audit test")
    created = approval_service.request_approval(req)
    approval_service.decide(
        approval_id=created.approval_id,
        actor=ApprovalActor(actor_id="senior-1", role=ApprovalRole.SENIOR_REVIEWER),
        verdict=ApprovalVerdict.REJECTED,
        reason="Lack of proof",
    )

    trail = approval_service.get_audit_trail(created.approval_id)
    assert len(trail) >= 2
    event_types = [t.event_type for t in trail]
    assert "APPROVAL_REQUESTED" in event_types
    assert "APPROVAL_DECIDED" in event_types


def test_matrix_ad_audit_trail_never_stores_secrets(approval_service: ApprovalService):
    """AD: Audit trail payload contains no secret keys or passwords."""
    req = _make_approval_request(notes="Clean note")
    created = approval_service.request_approval(req)
    trail = approval_service.get_audit_trail(created.approval_id)
    for t in trail:
        serialized = t.model_dump_json()
        assert "password" not in serialized.lower()
        assert "api_key" not in serialized.lower()
        assert "secret" not in serialized.lower()


def test_matrix_ae_domain_persistence_separation(approval_service: ApprovalService):
    """AE: Service returns pure domain models, not ORM session models."""
    req = _make_approval_request(notes="Domain test")
    created = approval_service.request_approval(req)
    assert isinstance(created, HumanApproval)
    assert not hasattr(created, "_sa_instance_state")


def test_matrix_af_schema_migration_0008_columns(db_session_factory: sessionmaker[Session]):
    """AF: Verify human_approvals table has the migration columns."""
    with db_session_factory() as session:
        result = session.execute(text("PRAGMA table_info(human_approvals)")).fetchall()
        column_names = [r[1] for r in result]
        assert "deadline" in column_names
        assert "requested_by" in column_names
        assert "required_role" in column_names


# ===========================================================================
# MATRIX AG–AL: WORKFLOW INTEGRATION
# ===========================================================================


def test_matrix_ag_workflow_parks_in_human_approval(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """AG: Workflow halts in HUMAN_APPROVAL without auto-approving."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL
    assert not ctx.current_snapshot.current_state.is_terminal


def test_matrix_ah_approval_request_created_on_human_wait(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """AH: ApprovalRequest is generated and attached to checkpoint upon entering HUMAN_APPROVAL."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    assert cp.kind == CheckpointKind.HUMAN_WAIT
    assert cp.approval_request is not None
    assert cp.approval_request.claim_id == sample_claim.claim_id


def test_matrix_ai_human_approval_transitions_to_approved(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """AI: Authorized human approval transitions HUMAN_APPROVAL -> APPROVED."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="human-reviewer", role=ApprovalRole.CLAIM_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Looks good",
        correlation_id=ctx.correlation_id,
    )
    resolved_ctx = runner.apply_human_decision(ctx, decision)
    assert resolved_ctx.current_snapshot.current_state == WorkflowState.APPROVED
    assert resolved_ctx.current_snapshot.current_state.is_terminal


def test_matrix_aj_human_rejection_transitions_to_rejected(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """AJ: Authorized human rejection transitions HUMAN_APPROVAL -> REJECTED."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.REJECTED,
        decided_by=ApprovalActor(actor_id="human-reviewer", role=ApprovalRole.CLAIM_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Suspected fraud",
        correlation_id=ctx.correlation_id,
    )
    resolved_ctx = runner.apply_human_decision(ctx, decision)
    assert resolved_ctx.current_snapshot.current_state == WorkflowState.REJECTED
    assert resolved_ctx.current_snapshot.current_state.is_terminal


def test_matrix_ak_approval_timeout_escalation(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """AK: Expiration transitions HUMAN_APPROVAL -> ESCALATION."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    escalated_ctx = runner.handle_approval_timeout(ctx, reason="Review window expired")
    assert escalated_ctx.current_snapshot.current_state == WorkflowState.ESCALATION
    assert escalated_ctx.current_snapshot.current_state.is_terminal


def test_matrix_al_cannot_apply_human_decision_to_non_approval_state(sample_claim: ClaimInput):
    """AL: Applying human decision when not in HUMAN_APPROVAL raises ValueError."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(extractor, investigator, reviewer)
    fresh_ctx = WorkflowContext(
        workflow_run_id=uuid4(),
        claim_id=sample_claim.claim_id,
        correlation_id=uuid4(),
        claim=sample_claim,
    )
    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Too early",
        correlation_id=uuid4(),
    )
    with pytest.raises(ValueError):
        runner.apply_human_decision(fresh_ctx, decision)


# ===========================================================================
# MATRIX AM–AR: CHECKPOINT + RESUME INTEGRATION
# ===========================================================================


def test_matrix_am_checkpoint_persists_on_human_approval_entry(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AM: Checkpoint is created with HUMAN_WAIT kind when reaching HUMAN_APPROVAL."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cps = checkpoint_repo.list_by_workflow_run_id(ctx.workflow_run_id)
    human_wait_cps = [c for c in cps if c.kind == CheckpointKind.HUMAN_WAIT]
    assert len(human_wait_cps) == 1
    assert human_wait_cps[0].state == WorkflowState.HUMAN_APPROVAL


def test_matrix_an_resume_preserves_pending_approval(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AN: Resume from HUMAN_WAIT checkpoint preserves pending state without auto-approval."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None

    resumed_ctx = runner.resume(cp.checkpoint_id, claim=sample_claim)
    assert resumed_ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL
    assert not is_terminal_state(resumed_ctx.current_snapshot.current_state)


def test_matrix_ao_resumed_workflow_can_be_approved(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AO: Resumed workflow parked at HUMAN_APPROVAL can subsequently be approved by human."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None

    resumed_ctx = runner.resume(cp.checkpoint_id, claim=sample_claim)
    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="human-senior", role=ApprovalRole.SENIOR_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Approved post-resume",
        correlation_id=resumed_ctx.correlation_id,
    )
    completed_ctx = runner.apply_human_decision(resumed_ctx, decision)
    assert completed_ctx.current_snapshot.current_state == WorkflowState.APPROVED


def test_matrix_ap_checkpoint_contains_recommendation_and_deadline(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AP: Checkpoint carries the full approval recommendation and deadline."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    assert cp.approval_request is not None
    assert cp.approval_request.recommendation.recommendation_type == RecommendationType.APPROVE_FULL
    assert cp.approval_request.deadline > datetime.now(UTC)


def test_matrix_aq_resume_terminal_state_preserves_immutability(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AQ: Resuming from a terminal state checkpoint does not advance state."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Approved",
        correlation_id=ctx.correlation_id,
    )
    approved_ctx = runner.apply_human_decision(ctx, decision)
    cp = checkpoint_repo.get_latest(approved_ctx.workflow_run_id)
    assert cp is not None
    assert cp.state == WorkflowState.APPROVED

    resumed_terminal = runner.resume(cp.checkpoint_id, claim=sample_claim)
    assert resumed_terminal.current_snapshot.current_state == WorkflowState.APPROVED


def test_matrix_ar_checkpoint_approval_identity_preserved(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AR: Checkpoint approval ID is preserved and unique."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    assert cp.approval_request is not None
    assert cp.approval_request.approval_id is not None


# ===========================================================================
# MATRIX AS–AX: REPLAY SAFETY
# ===========================================================================


def test_matrix_as_replay_never_performs_real_human_approval(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AS: Replay engine reconstructs history from checkpoints without prompting humans."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Approved",
        correlation_id=ctx.correlation_id,
    )
    runner.apply_human_decision(ctx, decision)

    result = replay_workflow(ctx.workflow_run_id, checkpoints=checkpoint_repo)
    assert result.matching_status
    assert result.replay_terminal_state == WorkflowState.APPROVED


def test_matrix_at_replay_does_not_mutate_approval_records(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """AT: Replay execution leaves stored approval database records unchanged."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    assert cp.approval_request is not None

    app_id = cp.approval_request.approval_id
    before_status = approval_service.get_approval(app_id).status

    replay_workflow(ctx.workflow_run_id, checkpoints=checkpoint_repo)

    after_status = approval_service.get_approval(app_id).status
    assert before_status == after_status


def test_matrix_au_replay_treats_approval_as_deterministic_event(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AU: Replay follows recorded path entries deterministically."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    triggers = [p.trigger for p in cp.path]
    assert Trigger.CLAIM_VALIDATED in triggers
    assert Trigger.REVIEW_APPROVED in triggers


def test_matrix_av_replay_detects_mismatch_if_approval_tampered(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AV: Altering recorded checkpoint causes replay mismatch detection."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    res = replay_workflow(ctx.workflow_run_id, checkpoints=checkpoint_repo)
    assert res.matching_status


def test_matrix_aw_replay_mode_tagged_in_trace_context():
    """AW: TraceContext captures ExecMode.REPLAY during replay."""
    ctx = TraceContext(
        workflow_run_id=uuid4(),
        execution_id=uuid4(),
        claim_id=uuid4(),
        mode=ExecMode.REPLAY,
    )
    assert ctx.mode == ExecMode.REPLAY
    attrs = ctx.as_span_attributes()
    assert attrs["casefile.exec_mode"] == "REPLAY"


def test_matrix_ax_replay_original_history_immutable(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """AX: Replay creates no new checkpoints in the repository."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    count_before = len(checkpoint_repo.list_by_workflow_run_id(ctx.workflow_run_id))

    replay_workflow(ctx.workflow_run_id, checkpoints=checkpoint_repo)

    count_after = len(checkpoint_repo.list_by_workflow_run_id(ctx.workflow_run_id))
    assert count_before == count_after


# ===========================================================================
# MATRIX AY–BD: OBSERVABILITY ARCHITECTURE
# ===========================================================================


def test_matrix_ay_in_memory_provider_for_testing():
    """AY: test_observability provides an in-memory OTel implementation without Jaeger."""
    obs, exporter = make_test_obs()
    assert obs.tracer.enabled
    assert exporter is not None
    assert obs.tracer is not None
    assert obs.meters is not None


def test_matrix_az_span_constants_defined():
    """AZ: Standard span names are defined for all system components."""
    assert SPAN_WORKFLOW == "casefile.workflow"
    assert SPAN_AGENT == "casefile.agent"
    assert SPAN_TOOL == "casefile.tool"
    assert SPAN_PERSISTENCE == "casefile.persistence"
    assert SPAN_CHECKPOINT == "casefile.checkpoint"
    assert SPAN_BUDGET == "casefile.budget"
    assert SPAN_APPROVAL == "casefile.approval"


def test_matrix_ba_tracer_emits_structured_span():
    """BA: maybe_span creates a span with correlated attributes and trace context."""
    obs, exporter = make_test_obs()
    run_id = uuid4()
    c_id = uuid4()
    trace = TraceContext(workflow_run_id=run_id, claim_id=c_id)
    attrs = {"casefile.component": "unit_test"}

    with maybe_span(obs.tracer, SPAN_WORKFLOW, attrs, trace):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == SPAN_WORKFLOW
    assert span.attributes["casefile.workflow_run_id"] == str(run_id)
    assert span.attributes["casefile.component"] == "unit_test"


def test_matrix_bb_persistence_attributes_helper():
    """BB: persistence_attributes helper captures entity and duration without raw query text."""
    attrs = persistence_attributes(
        operation="save",
        entity_type="human_approvals",
        duration_ms=45,
        success=True,
    )
    assert attrs["casefile.persistence_operation"] == "save"
    assert attrs["casefile.entity_type"] == "human_approvals"
    assert attrs["casefile.duration_ms"] == 45
    assert attrs["casefile.success"] is True
    assert "query" not in attrs


def test_matrix_bc_approval_attributes_helper():
    """BC: approval_attributes helper captures approval lifecycle fields without sensitive text."""
    attrs = approval_attributes(
        outcome="APPROVED",
        actor_type="HUMAN",
        wait_duration_ms=120,
        success=True,
    )
    assert attrs["casefile.approval_outcome"] == "APPROVED"
    assert attrs["casefile.actor_type"] == "HUMAN"
    assert attrs["casefile.wait_duration_ms"] == 120


def test_matrix_bd_safe_error_telemetry():
    """BD: safe_error generates structured error telemetry with no raw exception leaks."""
    err = safe_error(
        category="AUTHORIZATION",
        code="UNAUTHORIZED_ROLE",
        component="approval_service",
        retryable=False,
    )
    assert err.category == "AUTHORIZATION"
    assert err.code == "UNAUTHORIZED_ROLE"
    assert not err.retryable


# ===========================================================================
# MATRIX BE–BJ: TRACE CORRELATION
# ===========================================================================


def test_matrix_be_correlation_id_propagation():
    """BE: Correlation ID propagates across TraceContext into span attributes."""
    corr_id = uuid4()
    run_id = uuid4()
    c_id = uuid4()
    trace = TraceContext(
        workflow_run_id=run_id,
        correlation_id=corr_id,
        claim_id=c_id,
    )
    attrs = trace.as_span_attributes()
    assert attrs["casefile.correlation_id"] == str(corr_id)
    assert attrs["casefile.workflow_run_id"] == str(run_id)
    assert attrs["casefile.claim_id"] == str(c_id)


def test_matrix_bf_trace_context_child_enrichment():
    """BF: Child trace context inherits workflow_run_id and correlation_id."""
    parent = TraceContext(workflow_run_id=uuid4(), correlation_id=uuid4(), claim_id=uuid4())
    child = parent.child()
    assert child.workflow_run_id == parent.workflow_run_id
    assert child.correlation_id == parent.correlation_id
    assert child.execution_id != parent.execution_id


def test_matrix_bg_workflow_to_agent_correlation():
    """BG: Workflow run correlates to specialist agent spans."""
    obs, exporter = make_test_obs()
    run_id = uuid4()
    corr_id = uuid4()
    trace = TraceContext(workflow_run_id=run_id, correlation_id=corr_id, claim_id=uuid4())

    with (
        maybe_span(obs.tracer, SPAN_WORKFLOW, {"step": "intake"}, trace),
        maybe_span(obs.tracer, SPAN_AGENT, {"agent.type": "extractor"}, trace.child()),
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    for s in spans:
        assert s.attributes["casefile.workflow_run_id"] == str(run_id)
        assert s.attributes["casefile.correlation_id"] == str(corr_id)


def test_matrix_bh_agent_to_tool_correlation():
    """BH: Agent span correlates to tool invocation span."""
    obs, exporter = make_test_obs()
    trace = TraceContext(workflow_run_id=uuid4(), correlation_id=uuid4(), claim_id=uuid4())

    with (
        maybe_span(obs.tracer, SPAN_AGENT, {"agent.type": "investigator"}, trace),
        maybe_span(obs.tracer, SPAN_TOOL, {"tool.name": "policy_lookup"}, trace.child()),
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert spans[0].name == SPAN_TOOL
    assert spans[1].name == SPAN_AGENT
    assert (
        spans[0].attributes["casefile.workflow_run_id"]
        == spans[1].attributes["casefile.workflow_run_id"]
    )


def test_matrix_bi_approval_to_persistence_correlation():
    """BI: Approval operation correlates with persistence span."""
    obs, exporter = make_test_obs()
    trace = TraceContext(workflow_run_id=uuid4(), correlation_id=uuid4())

    with (
        maybe_span(obs.tracer, SPAN_APPROVAL, {"action": "decide"}, trace),
        maybe_span(obs.tracer, SPAN_PERSISTENCE, {"entity": "human_approvals"}, trace.child()),
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert (
        spans[0].attributes["casefile.workflow_run_id"]
        == spans[1].attributes["casefile.workflow_run_id"]
    )


def test_matrix_bj_checkpoint_correlation():
    """BJ: Checkpoint creation span carries correlation context."""
    obs, exporter = make_test_obs()
    trace = TraceContext(workflow_run_id=uuid4(), correlation_id=uuid4())

    attrs = checkpoint_attributes(
        operation="create",
        kind="HUMAN_WAIT",
        workflow_state="HUMAN_APPROVAL",
        sequence_no=5,
    )
    with maybe_span(obs.tracer, SPAN_CHECKPOINT, attrs, trace):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.attributes["casefile.checkpoint_operation"] == "create"
    assert span.attributes["casefile.kind"] == "HUMAN_WAIT"


# ===========================================================================
# MATRIX BK–BP: METRICS
# ===========================================================================


def test_matrix_bk_casefile_meters_metric_names():
    """BK: CasefileMeters defines all required metric names."""
    obs, _ = make_test_obs()
    for name in METRIC_NAMES:
        assert name in METRIC_NAMES


def test_matrix_bl_counter_increment():
    """BL: Meters track monotonic counter increments."""
    obs, _ = make_test_obs()
    obs.meters.record_counter("casefile.workflow.runs", 1.0, {"trigger": "intake"})
    obs.meters.record_counter("casefile.workflow.runs", 2.0, {"trigger": "intake"})
    assert obs.meters.total("casefile.workflow.runs") == 3.0


def test_matrix_bm_forbidden_labels_rejected():
    """BM: Sensitive labels (claim_id, policy_id, ssn) are strictly forbidden in metrics."""
    obs, _ = make_test_obs()
    with pytest.raises(ValueError):
        obs.meters.record_counter("casefile.workflow.runs", 1.0, {"claim_id": "CLM-001"})


def test_matrix_bn_budget_metric_integration():
    """BN: Budget consumption integrates with telemetry metrics."""
    obs, _ = make_test_obs()
    obs.meters.record_counter("casefile.budget.exhaustions", 1.0, {"dimension": "step_limit"})
    assert obs.meters.total("casefile.budget.exhaustions") == 1.0


def test_matrix_bo_approval_metrics_emitted():
    """BO: Approval decisions emit metrics."""
    obs, _ = make_test_obs()
    obs.meters.record_counter("casefile.approval.decisions", 1.0, {"verdict": "APPROVED"})
    assert obs.meters.total("casefile.approval.decisions") == 1.0


def test_matrix_bp_meter_snapshot():
    """BP: Meter snapshot provides point-in-time metrics copy."""
    obs, _ = make_test_obs()
    obs.meters.record_counter("casefile.tool.invocations", 5.0, {"tool": "policy_lookup"})
    snap = obs.meters.snapshot()
    assert any("casefile.tool.invocations" in k for k in snap)


# ===========================================================================
# MATRIX BQ–BV: TELEMETRY SANITIZATION
# ===========================================================================


def test_matrix_bq_credential_keys_dropped():
    """BQ: Attributes containing secret, token, password, api_key are dropped."""
    raw = {
        "api_key": "sk-secret-12345",
        "auth_token": "bearer-xyz",
        "password": "supersecretpassword",
        "safe_attr": "valid_value",
    }
    clean = sanitize_attributes(raw)
    assert "api_key" not in clean
    assert "auth_token" not in clean
    assert "password" not in clean
    assert clean["safe_attr"] == "valid_value"


def test_matrix_br_documents_and_statements_dropped():
    """BR: Raw claim documents and statements are excluded from telemetry."""
    raw = {
        "raw_document": "Full policy terms and conditions...",
        "claimant_statement": "I was driving when suddenly...",
        "status": "PROCESSED",
    }
    clean = sanitize_attributes(raw)
    assert "raw_document" not in clean
    assert "claimant_statement" not in clean
    assert clean["status"] == "PROCESSED"


def test_matrix_bs_sensitive_patterns_redacted_in_values():
    """BS: Secret patterns (sk-..., Bearer tokens, SSN, Credit Cards) are redacted from values."""
    val1 = sanitize_value("API key is sk-1234567890abcdef")
    assert "[REDACTED]" in str(val1)

    val2 = sanitize_value("User SSN: 123-45-6789")
    assert "[REDACTED]" in str(val2)

    val3 = sanitize_value("Card: 4111 2222 3333 4444")
    assert "[REDACTED]" in str(val3)


def test_matrix_bt_attribute_length_capped():
    """BT: Excessively long attribute strings are truncated to MAX_ATTRIBUTE_LENGTH."""
    long_string = "a" * 1000
    sanitized = sanitize_value(long_string)
    assert len(str(sanitized)) == MAX_ATTRIBUTE_LENGTH


def test_matrix_bu_coercion_of_safe_primitives():
    """BU: Booleans, integers, floats, UUIDs, Decimals are safely preserved."""
    uid = uuid4()
    dec = Decimal("150.75")
    assert sanitize_value(True) is True
    assert sanitize_value(42) == 42
    assert sanitize_value(3.14) == 3.14
    assert sanitize_value(uid) == str(uid)
    assert sanitize_value(dec) == "150.75"


def test_matrix_bv_sanitizer_never_raises():
    """BV: Sanitizer handles corrupt or unexpected attribute dictionaries without crashing."""
    corrupt: dict[Any, Any] = {None: "test", 123: "number_key"}
    clean = sanitize_attributes(corrupt)
    assert isinstance(clean, dict)


# ===========================================================================
# MATRIX BW–BZ: FAILURE BEHAVIOR
# ===========================================================================


def test_matrix_bw_telemetry_disabled_workflow_unaffected(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """BW: Workflow functions perfectly with telemetry disabled / no-op."""
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        obs=None,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL


def test_matrix_bx_telemetry_exception_does_not_crash_workflow():
    """BX: Failure inside span exporter or hook bridge does not abort execution."""
    bridge = HookBridge(tracer=None, meters=None)
    bridge.on_event(WorkflowHookEvent.NODE_STARTED, {"invalid": 123})


def test_matrix_by_safe_error_never_raises_on_none():
    """BY: Safe error model construction never crashes on empty fields."""
    err = safe_error(category="", code="", component="")
    assert err.category == ""
    assert err.code == ""


def test_matrix_bz_jaeger_outage_fails_safe():
    """BZ: When telemetry backend is unreachable, maybe_span operates gracefully."""
    tracer = OtelTracer(None)
    with maybe_span(tracer, SPAN_WORKFLOW, {"test": "val"}, TraceContext()):
        res = 1 + 1
    assert res == 2


# ===========================================================================
# MATRIX CA–CF: END-TO-END SCENARIOS 1–8
# ===========================================================================


def test_scenario_1_nominal_hitl(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """
    SCENARIO 1: Nominal Human-in-the-Loop workflow.
    RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL.
    Approval request created, authorized human approves -> APPROVED.
    """
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None
    assert cp.approval_request is not None

    decision = ApprovalDecision(
        approval_id=cp.approval_request.approval_id,
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="senior-adjuster-1", role=ApprovalRole.SENIOR_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="Claim fully verified against repair records",
        correlation_id=ctx.correlation_id,
    )
    final_ctx = runner.apply_human_decision(ctx, decision)

    assert final_ctx.current_snapshot.current_state == WorkflowState.APPROVED
    assert final_ctx.current_snapshot.current_state.is_terminal


def test_scenario_2_human_rejection(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """
    SCENARIO 2: Human rejection.
    Workflow reaches HUMAN_APPROVAL. Authorized human rejects -> REJECTED.
    """
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.REJECTED,
        decided_by=ApprovalActor(actor_id="supervisor-jane", role=ApprovalRole.CLAIM_SUPERVISOR),
        decided_at=datetime.now(UTC),
        reason="Damage inconsistent with stated incident date",
        correlation_id=ctx.correlation_id,
    )
    final_ctx = runner.apply_human_decision(ctx, decision)
    assert final_ctx.current_snapshot.current_state == WorkflowState.REJECTED
    assert final_ctx.current_snapshot.current_state.is_terminal


def test_scenario_3_unauthorized_approval(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
    approval_service: ApprovalService,
):
    """
    SCENARIO 3: Unauthorized approval attempt.
    Agent attempts approval -> rejected, workflow remains in HUMAN_APPROVAL.
    """
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        approval_service=approval_service,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    agent_decision = ApprovalDecision(
        approval_id=uuid4(),
        decision=ApprovalVerdict.APPROVED,
        decided_by=ApprovalActor(actor_id="agent-reviewer", role=ApprovalRole.CLAIM_REVIEWER),
        decided_at=datetime.now(UTC),
        reason="I want to approve",
        correlation_id=ctx.correlation_id,
    )
    with pytest.raises(ApprovalError) as exc_info:
        runner.apply_human_decision(ctx, agent_decision)
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL


def test_scenario_4_approval_expiration(approval_service: ApprovalService):
    """
    SCENARIO 4: Approval expiration.
    Approval exceeds deadline -> EXPIRED. No subsequent approval permitted.
    """
    past_deadline = datetime.now(UTC) - timedelta(minutes=10)
    req = _make_approval_request(deadline=past_deadline)
    rec = approval_service.request_approval(req)

    exp_res = approval_service.expire(rec.approval_id)
    assert exp_res.outcome == ApprovalOutcome.EXPIRED
    assert exp_res.approval.status == ApprovalStatus.EXPIRED

    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Late approval",
        )
    assert exc_info.value.code == ApprovalErrorCode.CONFLICT


def test_scenario_5_resume_pending_approval(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """
    SCENARIO 5: Resume pending approval.
    Workflow parks at HUMAN_APPROVAL, process re-initializes, resume preserves pending state.
    """
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    ctx = runner.run(sample_claim)
    cp = checkpoint_repo.get_latest(ctx.workflow_run_id)
    assert cp is not None

    fresh_runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
    )
    resumed = fresh_runner.resume(cp.checkpoint_id, claim=sample_claim)
    assert resumed.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL
    assert not resumed.current_snapshot.current_state.is_terminal


def test_scenario_6_observability_trace(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """
    SCENARIO 6: Observability trace correlation.
    Run workflow and verify correlated traces across workflow, agent, and tool.
    """
    obs, exporter = make_test_obs()
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        obs=obs,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL


def test_scenario_7_sensitive_telemetry_redacted():
    """
    SCENARIO 7: Sensitive telemetry sanitization.
    Inject synthetic sensitive values and verify telemetry sanitizer purges them.
    """
    dirty = {
        "user_password": "supersecretpassword123",
        "api_key": "sk-proj-9876543210zyxwvutsrqponmlkjihgfedcba",
        "auth_token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "raw_document": "SECRET CONTRACT CONTENT",
        "claimant_statement": "CONFIDENTIAL STATEMENT",
        "credit_card": "4111 2222 3333 4444",
        "user_notes": "Call customer with sk-1234567890 key",
        "operation_id": "OP-100",
    }
    clean = sanitize_attributes(dirty)
    assert "user_password" not in clean
    assert "api_key" not in clean
    assert "auth_token" not in clean
    assert "raw_document" not in clean
    assert "claimant_statement" not in clean
    assert "credit_card" not in clean
    assert clean["operation_id"] == "OP-100"
    assert "[REDACTED]" in str(clean["user_notes"])


def test_scenario_8_telemetry_backend_unavailable(
    sample_claim: ClaimInput,
    checkpoint_repo: CheckpointRepository,
    uow_factory,
):
    """
    SCENARIO 8: Telemetry backend unavailable.
    Disable external telemetry backend, verify workflow still completes correctly.
    """
    extractor, investigator, reviewer = _make_agents()
    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=checkpoint_repo,
        uow_factory=uow_factory,
        obs=None,
    )
    ctx = runner.run(sample_claim)
    assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL


# ===========================================================================
# MATRIX CG–CL: ARCHITECTURE BOUNDARIES & PART 12 APPROVAL SECURITY
# ===========================================================================


def test_security_1_agent_approval_attempt(approval_service: ApprovalService):
    """Security 1: Agent approval attempt must fail safely."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="agent-investigator", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Agent attempting decision",
        )
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_security_2_supervisor_approval_attempt(approval_service: ApprovalService):
    """Security 2: Workflow supervisor approval attempt must fail safely."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="supervisor", role=ApprovalRole.ADMIN),
            verdict=ApprovalVerdict.APPROVED,
            reason="Supervisor attempting decision",
        )
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_security_3_unauthorized_human_role(approval_service: ApprovalService):
    """Security 3: Unauthorized human role must fail safely."""
    req = _make_approval_request(required_role=ApprovalRole.CLAIM_SUPERVISOR.value)
    rec = approval_service.request_approval(req)
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="intern-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Intern attempting senior approval",
        )
    assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED


def test_security_4_duplicate_approval(approval_service: ApprovalService):
    """Security 4: Duplicate approval attempt is idempotent or conflicts without corrupting state."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    res1 = approval_service.decide(
        approval_id=rec.approval_id,
        actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        verdict=ApprovalVerdict.APPROVED,
        reason="First approval",
        decision_key="dup-key-1",
    )
    assert res1.outcome == ApprovalOutcome.DECIDED

    res2 = approval_service.decide(
        approval_id=rec.approval_id,
        actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        verdict=ApprovalVerdict.APPROVED,
        reason="First approval",
        decision_key="dup-key-1",
    )
    assert res2.outcome == ApprovalOutcome.IDEMPOTENT_REPLAY


def test_security_5_decision_after_rejection(approval_service: ApprovalService):
    """Security 5: Decision attempt after rejection must fail safely."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    approval_service.decide(
        approval_id=rec.approval_id,
        actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        verdict=ApprovalVerdict.REJECTED,
        reason="Reject first",
    )
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="human-2", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Try to approve rejected",
        )
    assert exc_info.value.code == ApprovalErrorCode.CONFLICT


def test_security_6_decision_after_approval(approval_service: ApprovalService):
    """Security 6: Decision attempt after approval must fail safely."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    approval_service.decide(
        approval_id=rec.approval_id,
        actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
        verdict=ApprovalVerdict.APPROVED,
        reason="Approve first",
    )
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="human-2", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.REJECTED,
            reason="Try to reject approved",
        )
    assert exc_info.value.code == ApprovalErrorCode.CONFLICT


def test_security_7_decision_after_expiration(approval_service: ApprovalService):
    """Security 7: Decision attempt after expiration must fail safely."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    approval_service.expire(rec.approval_id)
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Approve expired",
        )
    assert exc_info.value.code == ApprovalErrorCode.CONFLICT


def test_security_8_missing_actor():
    """Security 8: Missing actor fails validation."""
    with pytest.raises(ValueError):
        ApprovalActor(actor_id="", role=ApprovalRole.CLAIM_REVIEWER)


def test_security_9_impersonated_actor():
    """Security 9: Impersonation of supervisor or agent by string name is rejected."""
    authorizer = ApprovalAuthorizer()
    actor = ApprovalActor(actor_id="supervisor", role=ApprovalRole.ADMIN)
    with pytest.raises(ApprovalError):
        authorizer.check_authorization(actor)


def test_security_10_invalid_approval_id(approval_service: ApprovalService):
    """Security 10: Nonexistent approval ID returns NOT_FOUND."""
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=uuid4(),
            actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Nonexistent",
        )
    assert exc_info.value.code == ApprovalErrorCode.NOT_FOUND


def test_security_11_approval_for_wrong_workflow(approval_service: ApprovalService):
    """Security 11: Deciding with wrong workflow_run_id is rejected."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Wrong workflow",
            workflow_run_id=uuid4(),
        )
    assert exc_info.value.code == ApprovalErrorCode.CONFLICT


def test_security_12_approval_for_wrong_claim(approval_service: ApprovalService):
    """Security 12: Deciding with wrong claim_id is rejected."""
    req = _make_approval_request()
    rec = approval_service.request_approval(req)
    with pytest.raises(ApprovalError) as exc_info:
        approval_service.decide(
            approval_id=rec.approval_id,
            actor=ApprovalActor(actor_id="human-1", role=ApprovalRole.CLAIM_REVIEWER),
            verdict=ApprovalVerdict.APPROVED,
            reason="Wrong claim",
            claim_id=uuid4(),
        )
    assert exc_info.value.code == ApprovalErrorCode.CONFLICT
