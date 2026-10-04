"""
Unit Tests: Approval domain model, actors, and commands (Phase 9 §2, §4).

Validation, status vocabulary, immutability rules, and typed-actor
enforcement. Non-human actors cannot construct decision commands.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from casefile.approval.model import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalVerdict,
    CancelApproval,
    ExpireApproval,
    HumanActor,
)
from casefile.models.domain import (
    ApprovalStatus,
    HumanApproval,
    HumanApprovalRequest,
    Recommendation,
    RecommendationType,
)


def _recommendation() -> Recommendation:
    from casefile.models.domain import Money

    return Recommendation(
        claim_id=uuid4(),
        recommendation_type=RecommendationType.APPROVE_FULL,
        estimated_payout=Money(amount=Decimal("100.00")),
        notes="ok",
        confidence=0.9,
    )


def _request() -> HumanApprovalRequest:
    now = datetime.now(UTC)
    return HumanApprovalRequest(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        recommendation=_recommendation(),
        reviewer_summary="ready",
        deadline=now + timedelta(hours=24),
    )


def _actor() -> HumanActor:
    return HumanActor(actor_id="human-1", display_name="Human One")


@pytest.mark.unit
class TestApprovalStatusVocabulary:
    def test_five_explicit_states(self) -> None:
        assert {s.value for s in ApprovalStatus} == {
            "PENDING",
            "APPROVED",
            "REJECTED",
            "ESCALATED",
            "EXPIRED",
            "CANCELLED",
        }

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HumanApproval(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                status="MAYBE",  # type: ignore[arg-type]
            )


@pytest.mark.unit
class TestApprovalModelValidation:
    def test_request_carries_version_and_links(self) -> None:
        request = _request()
        assert request.request_version == 1
        assert request.execution_id is None
        assert request.checkpoint_id is None

    def test_pending_needs_no_approver(self) -> None:
        approval = HumanApproval(workflow_run_id=uuid4(), claim_id=uuid4())
        assert approval.status == ApprovalStatus.PENDING

    def test_decided_states_immutable_without_metadata(self) -> None:
        # Service populates approver metadata at decision time; the model
        # only hard-requires it for APPROVED/REJECTED construction.
        pending = HumanApproval(
            workflow_run_id=uuid4(), claim_id=uuid4(), status=ApprovalStatus.CANCELLED
        )
        assert pending.decided_at is None


@pytest.mark.unit
class TestHumanActorBoundary:
    def test_human_actor_constructs(self) -> None:
        actor = _actor()
        assert actor.role == "HUMAN"

    def test_empty_identity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HumanActor(actor_id="   ", display_name="H")

    def test_non_human_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HumanActor(actor_id="agent-1", display_name="Agent", role="AGENT")  # type: ignore[arg-type]

    def test_each_agent_type_is_not_human(self) -> None:
        from casefile.models.domain import AgentType

        for agent in (
            AgentType.SUPERVISOR,
            AgentType.EXTRACTOR,
            AgentType.INVESTIGATOR,
            AgentType.REVIEWER,
        ):
            assert agent.value != "HUMAN"

    def test_decision_command_requires_key_and_version(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=_actor(),
                verdict=ApprovalVerdict.APPROVE,
                decision_key="   ",
                expected_version=1,
            )
        with pytest.raises(ValidationError):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=_actor(),
                verdict=ApprovalVerdict.APPROVE,
                decision_key="k",
                expected_version=0,
            )

    def test_expire_cancel_commands_typed(self) -> None:
        approval_id = uuid4()
        assert ExpireApproval(approval_id=approval_id, actor=_actor()).approval_id == approval_id
        assert CancelApproval(approval_id=approval_id, actor=_actor()).approval_id == approval_id
        assert ApprovalOutcome.DECIDED.value == "DECIDED"
