"""
Unit Tests: Pydantic Inter-Agent Contracts

Validates typed contract behavior, validation rules, state machine representations,
multi-dimensional budget enforcement, and checkpoint checksum calculations.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from casefile.models.contracts import (
    BudgetLimits,
    BudgetState,
    Checkpoint,
    ClaimInput,
    ExtractionRequest,
    ExtractionResult,
    ReviewResult,
    TokenUsage,
    WorkflowState,
)


@pytest.mark.unit
class TestWorkflowState:
    """Validate 14-state machine contracts and terminal state classifications."""

    def test_total_state_count(self) -> None:
        assert len(WorkflowState) == 14

    def test_terminal_states(self) -> None:
        terminal_states = {s for s in WorkflowState if s.is_terminal}
        expected_terminal = {
            WorkflowState.APPROVED,
            WorkflowState.REJECTED,
            WorkflowState.FAILED,
            WorkflowState.ESCALATION,
            WorkflowState.BUDGET_EXHAUSTED,
            WorkflowState.TIMEOUT,
            WorkflowState.MAX_STEPS_EXCEEDED,
            WorkflowState.MAX_REWORK_EXCEEDED,
        }
        assert terminal_states == expected_terminal

    def test_non_terminal_states(self) -> None:
        non_terminal = {s for s in WorkflowState if not s.is_terminal}
        expected_non_terminal = {
            WorkflowState.RECEIVED,
            WorkflowState.EXTRACTION,
            WorkflowState.INVESTIGATION,
            WorkflowState.REVIEW,
            WorkflowState.REWORK_LOOP,
            WorkflowState.HUMAN_APPROVAL,
        }
        assert non_terminal == expected_non_terminal


@pytest.mark.unit
class TestClaimInput:
    """Validate initial claim submission contracts and field validators."""

    def test_valid_claim_input(self) -> None:
        claim = ClaimInput(
            policy_id="pol-12345",
            claimant_name="Jane Doe",
            incident_date="2026-09-19",
            claim_amount=Decimal("4500.50"),
            description="Vehicle collision at intersection",
            documents=["doc-001", "doc-002"],
        )
        assert claim.policy_id == "POL-12345"
        assert claim.claimant_name == "Jane Doe"
        assert claim.claim_amount == Decimal("4500.50")
        assert len(claim.documents) == 2

    def test_empty_policy_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClaimInput(
                policy_id="   ",
                claimant_name="Jane Doe",
                incident_date="2026-09-19",
                claim_amount=Decimal("1000.00"),
                description="Test description",
            )

    def test_non_positive_claim_amount_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClaimInput(
                policy_id="POL-123",
                claimant_name="Jane Doe",
                incident_date="2026-09-19",
                claim_amount=Decimal("0.00"),
                description="Test description",
            )


@pytest.mark.unit
class TestBudgetEnforcement:
    """Validate multi-dimensional budget state tracking and limit checking."""

    def test_default_limits(self) -> None:
        limits = BudgetLimits()
        assert limits.max_input_tokens == 100_000
        assert limits.max_output_tokens == 20_000
        assert limits.max_total_tokens == 150_000
        assert limits.max_cost_usd == Decimal("5.00")
        assert limits.max_steps == 50
        assert limits.max_execution_time_seconds == 1800
        assert limits.max_rework_cycles == 3

    def test_token_accumulation_and_total(self) -> None:
        state = BudgetState()
        usage = TokenUsage(input_tokens=1500, output_tokens=300)
        state.add_usage(usage, cost=Decimal("0.05"))

        assert state.input_tokens_used == 1500
        assert state.output_tokens_used == 300
        assert state.total_tokens_used == 1800
        assert state.estimated_cost_usd == Decimal("0.05")
        assert not state.is_exhausted

    def test_token_budget_exhaustion(self) -> None:
        state = BudgetState()
        usage = TokenUsage(input_tokens=120_000, output_tokens=35_000)
        state.add_usage(usage, cost=Decimal("1.50"))

        assert state.is_exhausted
        assert state.exhaustion_reason == "MAX_TOKENS_EXCEEDED"

    def test_cost_budget_exhaustion(self) -> None:
        state = BudgetState()
        usage = TokenUsage(input_tokens=1000, output_tokens=200)
        state.add_usage(usage, cost=Decimal("5.50"))

        assert state.is_exhausted
        assert state.exhaustion_reason == "MAX_COST_EXCEEDED"

    def test_step_count_exhaustion(self) -> None:
        state = BudgetState(steps_completed=50)
        reason = state.check_limits()

        assert state.is_exhausted
        assert reason == "MAX_STEPS_EXCEEDED"

    def test_rework_count_exhaustion(self) -> None:
        state = BudgetState(rework_count=3)
        reason = state.check_limits()

        assert state.is_exhausted
        assert reason == "MAX_REWORK_EXCEEDED"


@pytest.mark.unit
class TestAgentContracts:
    """Validate inter-agent handoff contracts."""

    def test_extraction_handoff(self) -> None:
        wf_id = uuid4()
        claim = ClaimInput(
            policy_id="POL-999",
            claimant_name="Alice Smith",
            incident_date="2026-09-18",
            claim_amount=Decimal("3200.00"),
            description="Fender bender",
        )
        req = ExtractionRequest(workflow_id=wf_id, claim_input=claim)
        assert req.workflow_id == wf_id

        res = ExtractionResult(
            workflow_id=wf_id,
            claimant_name="Alice Smith",
            policy_id="POL-999",
            incident_date="2026-09-18",
            incident_location="Springfield, IL",
            incident_description="Fender bender",
            claim_amount=Decimal("3200.00"),
            requested_coverage_type="COLLISION",
            extraction_confidence=0.98,
        )
        assert res.is_complete is True
        assert res.extraction_confidence == 0.98

    def test_review_decision_literals(self) -> None:
        wf_id = uuid4()
        valid_decisions = ["APPROVE", "REJECT", "REWORK"]
        for dec in valid_decisions:
            res = ReviewResult(
                workflow_id=wf_id,
                decision=dec,  # type: ignore[arg-type]
                reasoning="Review rationale",
                confidence_score=0.95,
            )
            assert res.decision == dec

        with pytest.raises(ValidationError):
            ReviewResult(
                workflow_id=wf_id,
                decision="INVALID_DECISION",  # type: ignore[arg-type]
                reasoning="Invalid",
                confidence_score=0.5,
            )


@pytest.mark.unit
class TestCheckpointIntegrity:
    """Validate Checkpoint checksum and tamper-detection logic."""

    def test_checkpoint_checksum_verification(self) -> None:
        chk = Checkpoint(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            state=WorkflowState.EXTRACTION,
            budget_state=BudgetState(),
            step_count=1,
        )
        chk.checksum = chk.compute_checksum()
        assert chk.verify_integrity() is True

        # Tampering with state_data should invalidate integrity
        chk.state_data["tampered"] = True
        assert chk.verify_integrity() is False
