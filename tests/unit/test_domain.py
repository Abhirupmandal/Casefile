"""
Unit Tests: Phase 2 domain entities and validation rules.

Covers model construction, required fields, invalid values, enum
validation, domain invariants, document metadata, and audit events.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    ActorType,
    AgentExecution,
    AgentType,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    AuditResult,
    Claim,
    ClaimDocumentReference,
    ClaimStatus,
    ClaimType,
    CostAssessment,
    CoverageLimits,
    DamageEstimate,
    DamageLineItem,
    DocumentExtractionStatus,
    DocumentType,
    ExecutionStatus,
    FocusArea,
    FraudIndicator,
    FraudSeverity,
    HumanApproval,
    HumanApprovalRequest,
    InvestigationFindings,
    Money,
    Policy,
    PriorClaim,
    Recommendation,
    RecommendationType,
    RejectionDetails,
    ReviewFindings,
    ReworkPriority,
    ReworkRequest,
    RiskLevel,
    WorkflowRun,
)


def _money(amount: str = "100.00") -> Money:
    return Money(amount=Decimal(amount))


def _claim_kwargs() -> dict[str, Any]:
    return {
        "external_claim_id": "EXT-001",
        "policy_number": "pol-42",
        "customer_id": "CUST-7",
        "claim_type": ClaimType.COLLISION,
        "date_of_loss": date(2026, 9, 10),
        "date_reported": date(2026, 9, 12),
        "description": "Rear collision at intersection",
        "claim_amount": _money("4500.50"),
    }


def _cost_assessment() -> CostAssessment:
    return CostAssessment(
        submitted=_money("5000.00"),
        submitted_source="body_shop",
        expected_low=_money("4500.00"),
        expected_high=_money("5500.00"),
        is_within_expected_range=True,
        variance_pct=2.5,
    )


def _recommendation(
    rec_type: RecommendationType = RecommendationType.APPROVE_FULL,
) -> Recommendation:
    payout = _money("4200.00") if rec_type != RecommendationType.DENY else None
    return Recommendation(
        claim_id=uuid4(),
        recommendation_type=rec_type,
        estimated_payout=payout,
        notes="Evidence supports approval",
        confidence=0.9,
    )


@pytest.mark.unit
class TestMoney:
    def test_positive_and_zero_amounts(self) -> None:
        assert Money(amount=Decimal("10.25")).amount == Decimal("10.25")
        assert Money(amount=Decimal("0.00")).amount == Decimal("0.00")

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Money(amount=Decimal("-0.01"))

    def test_currency_explicit_and_validated(self) -> None:
        assert Money(amount=Decimal("1.00")).currency == "USD"
        assert Money(amount=Decimal("1.00"), currency="EUR").currency == "EUR"
        with pytest.raises(ValidationError):
            Money(amount=Decimal("1.00"), currency="usd")
        with pytest.raises(ValidationError):
            Money(amount=Decimal("1.00"), currency="US")

    def test_serialization_round_trip(self) -> None:
        m = Money(amount=Decimal("1234.56"), currency="USD")
        assert Money.model_validate_json(m.model_dump_json()) == m


@pytest.mark.unit
class TestClaim:
    def test_construction_and_normalization(self) -> None:
        claim = Claim(**_claim_kwargs())
        assert claim.policy_number == "POL-42"
        assert claim.status == ClaimStatus.SUBMITTED
        assert claim.schema_version == "1.0.0"

    def test_empty_identifiers_rejected(self) -> None:
        kwargs = _claim_kwargs()
        kwargs["policy_number"] = "   "
        with pytest.raises(ValidationError):
            Claim(**kwargs)

    def test_report_before_loss_rejected(self) -> None:
        kwargs = _claim_kwargs()
        kwargs["date_reported"] = date(2026, 9, 1)
        with pytest.raises(ValidationError):
            Claim(**kwargs)

    def test_invalid_status_rejected(self) -> None:
        kwargs = _claim_kwargs()
        kwargs["status"] = "NONSENSE"
        with pytest.raises(ValidationError):
            Claim(**kwargs)

    def test_json_round_trip(self) -> None:
        claim = Claim(**_claim_kwargs())
        assert Claim.model_validate_json(claim.model_dump_json()) == claim


@pytest.mark.unit
class TestPolicyAndHistory:
    def test_coverage_consistency(self) -> None:
        limits = CoverageLimits(
            bodily_injury_per_person=Decimal("25000"),
            bodily_injury_per_accident=Decimal("50000"),
            property_damage=Decimal("10000"),
        )
        policy = Policy(
            policy_number="POL-1",
            policyholder_name="Jane Doe",
            policy_type="AUTO",
            effective_date=date(2026, 1, 1),
            expiration_date=date(2027, 1, 1),
            coverage=limits,
        )
        assert policy.is_active is True

    def test_inverted_limits_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoverageLimits(
                bodily_injury_per_person=Decimal("50000"),
                bodily_injury_per_accident=Decimal("25000"),
                property_damage=Decimal("10000"),
            )

    def test_inverted_policy_dates_rejected(self) -> None:
        limits = CoverageLimits(
            bodily_injury_per_person=Decimal("10000"),
            bodily_injury_per_accident=Decimal("20000"),
            property_damage=Decimal("5000"),
        )
        with pytest.raises(ValidationError):
            Policy(
                policy_number="POL-1",
                policyholder_name="Jane",
                policy_type="AUTO",
                effective_date=date(2027, 1, 1),
                expiration_date=date(2027, 1, 1),
                coverage=limits,
            )

    def test_prior_claim(self) -> None:
        prior = PriorClaim(
            claim_reference="OLD-9",
            claim_date=date(2025, 3, 1),
            claim_type=ClaimType.COMPREHENSIVE,
            amount=_money("800.00"),
            status="CLOSED",
        )
        assert prior.at_fault is None


@pytest.mark.unit
class TestDamageEstimate:
    def _items(self) -> list[DamageLineItem]:
        return [
            DamageLineItem(
                description="Bumper",
                quantity=1,
                unit_cost=_money("400.00"),
                total_cost=_money("400.00"),
            ),
            DamageLineItem(
                description="Clips",
                quantity=4,
                unit_cost=_money("25.00"),
                total_cost=_money("100.00"),
            ),
        ]

    def test_valid_estimate(self) -> None:
        est = DamageEstimate(
            source="shop",
            estimate_date=date(2026, 9, 15),
            line_items=self._items(),
            total_cost=_money("500.00"),
        )
        assert est.total_cost.amount == Decimal("500.00")

    def test_line_total_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DamageLineItem(
                description="Bumper",
                quantity=2,
                unit_cost=_money("400.00"),
                total_cost=_money("400.00"),
            )

    def test_estimate_total_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DamageEstimate(
                source="shop",
                estimate_date=date(2026, 9, 15),
                line_items=self._items(),
                total_cost=_money("999.00"),
            )

    def test_mixed_currencies_rejected(self) -> None:
        items = self._items()
        with pytest.raises(ValidationError):
            DamageEstimate(
                source="shop",
                estimate_date=date(2026, 9, 15),
                line_items=items,
                total_cost=Money(amount=Decimal("500.00"), currency="EUR"),
            )

    def test_empty_items_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DamageEstimate(
                source="shop",
                estimate_date=date(2026, 9, 15),
                line_items=[],
                total_cost=_money("0.00"),
            )


@pytest.mark.unit
class TestInvestigationFindings:
    def test_valid_findings(self) -> None:
        findings = InvestigationFindings(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            coverage_verified=True,
            cost_assessment=_cost_assessment(),
            fraud_risk_score=0.2,
            fraud_risk_level=RiskLevel.LOW,
            summary="Policy active; costs within range",
            confidence=0.88,
        )
        assert findings.schema_version == "1.0.0"

    def test_range_contradiction_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CostAssessment(
                submitted=_money("9000.00"),
                submitted_source="shop",
                expected_low=_money("4500.00"),
                expected_high=_money("5500.00"),
                is_within_expected_range=True,
                variance_pct=60.0,
            )

    def test_confidence_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationFindings(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                coverage_verified=True,
                cost_assessment=_cost_assessment(),
                fraud_risk_score=1.5,
                fraud_risk_level=RiskLevel.HIGH,
                summary="x",
                confidence=0.5,
            )

    def test_fraud_indicator(self) -> None:
        indicator = FraudIndicator(
            indicator_type="DUPLICATE_CLAIM",
            description="Same damage claimed twice",
            severity=FraudSeverity.HIGH,
        )
        assert indicator.severity == FraudSeverity.HIGH


@pytest.mark.unit
class TestReviewFindings:
    def test_approve_findings(self) -> None:
        findings = ReviewFindings(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            evidence_summary="Complete evidence",
            evidence_completeness=1.0,
            confidence=0.95,
            recommendation=_recommendation(RecommendationType.APPROVE_PARTIAL),
        )
        assert findings.rejection is None

    def test_deny_requires_rejection(self) -> None:
        with pytest.raises(ValidationError):
            ReviewFindings(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                evidence_summary="Excluded peril",
                evidence_completeness=0.9,
                confidence=0.9,
                recommendation=_recommendation(RecommendationType.DENY),
            )

    def test_deny_with_rejection(self) -> None:
        findings = ReviewFindings(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            evidence_summary="Excluded peril",
            evidence_completeness=0.9,
            confidence=0.9,
            recommendation=_recommendation(RecommendationType.DENY),
            rejection=RejectionDetails(primary_reason="Flood exclusion applies"),
        )
        assert findings.rejection is not None

    def test_investigate_further_requires_rework(self) -> None:
        with pytest.raises(ValidationError):
            ReviewFindings(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                evidence_summary="Gaps remain",
                evidence_completeness=0.4,
                confidence=0.5,
                recommendation=_recommendation(RecommendationType.INVESTIGATE_FURTHER),
            )

    def test_rework_request(self) -> None:
        rework = ReworkRequest(
            focus_areas=[FocusArea.REPAIR_COSTS],
            rationale="Need itemized estimate",
            priority=ReworkPriority.HIGH,
        )
        assert rework.priority == ReworkPriority.HIGH
        with pytest.raises(ValidationError):
            ReworkRequest(focus_areas=[], rationale="x")


@pytest.mark.unit
class TestHumanApproval:
    def _request(self) -> HumanApprovalRequest:
        now = datetime.now(UTC)
        return HumanApprovalRequest(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            recommendation=_recommendation(),
            reviewer_summary="Ready for approval",
            deadline=now + timedelta(hours=24),
        )

    def test_pending_without_approver(self) -> None:
        approval = HumanApproval(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            status=ApprovalStatus.PENDING,
        )
        assert approval.decided_at is None

    def test_approved_requires_metadata(self) -> None:
        with pytest.raises(ValidationError):
            HumanApproval(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                status=ApprovalStatus.APPROVED,
            )

    def test_approved_with_metadata(self) -> None:
        requested = datetime.now(UTC)
        approval = HumanApproval(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            status=ApprovalStatus.APPROVED,
            approver_id="u-1",
            approver_name="A. Approver",
            approver_role="ADJUSTER",
            requested_at=requested,
            decided_at=requested + timedelta(minutes=5),
        )
        assert approval.status == ApprovalStatus.APPROVED

    def test_override_requires_justification(self) -> None:
        with pytest.raises(ValidationError):
            HumanApproval(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                status=ApprovalStatus.PENDING,
                is_override=True,
            )

    def test_request_deadline_rule(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            HumanApprovalRequest(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                recommendation=_recommendation(),
                reviewer_summary="x",
                requested_at=now,
                deadline=now,
            )
        assert self._request().schema_version == "1.0.0"


@pytest.mark.unit
class TestWorkflowRun:
    def test_active_run(self) -> None:
        run = WorkflowRun(claim_id=uuid4(), current_state=WorkflowState.EXTRACTION)
        assert run.terminal_state is None
        assert run.step_count == 0

    def test_terminated_run(self) -> None:
        now = datetime.now(UTC)
        run = WorkflowRun(
            claim_id=uuid4(),
            current_state=WorkflowState.APPROVED,
            terminal_state=WorkflowState.APPROVED,
            termination_reason="Human approved",
            completed_at=now,
        )
        assert run.current_state.is_terminal

    def test_terminal_without_metadata_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowRun(claim_id=uuid4(), current_state=WorkflowState.APPROVED)

    def test_mismatched_terminal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowRun(
                claim_id=uuid4(),
                current_state=WorkflowState.REVIEW,
                terminal_state=WorkflowState.APPROVED,
                termination_reason="x",
                completed_at=datetime.now(UTC),
            )


@pytest.mark.unit
class TestAgentExecutionAndAudit:
    def test_execution_metadata(self) -> None:
        started = datetime.now(UTC)
        execution = AgentExecution(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            agent=AgentType.EXTRACTOR,
            operation="extract",
            status=ExecutionStatus.SUCCESS,
            started_at=started,
            finished_at=started + timedelta(seconds=3),
            idempotency_key="run:exec",
        )
        assert execution.idempotency_key == "run:exec"

    def test_failure_requires_error_code(self) -> None:
        with pytest.raises(ValidationError):
            AgentExecution(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                agent=AgentType.INVESTIGATOR,
                operation="investigate",
                status=ExecutionStatus.FAILURE,
            )

    def test_audit_event(self) -> None:
        event = AuditEvent(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            trace_id="trace-1",
            event_type=AuditEventType.STATE_TRANSITION,
            actor_type=ActorType.AGENT,
            actor_id="supervisor",
            action="transition",
            from_state=WorkflowState.EXTRACTION,
            to_state=WorkflowState.INVESTIGATION,
        )
        assert event.result == AuditResult.SUCCESS
        assert AuditEvent.model_validate_json(event.model_dump_json()) == event

    def test_failure_event_requires_error(self) -> None:
        with pytest.raises(ValidationError):
            AuditEvent(
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                trace_id="t",
                event_type=AuditEventType.ERROR_OCCURRED,
                actor_type=ActorType.SYSTEM,
                actor_id="sys",
                action="fail",
                result=AuditResult.FAILURE,
            )

    def test_document_reference(self) -> None:
        doc = ClaimDocumentReference(
            document_type=DocumentType.POLICE_REPORT,
            content_hash="ab12cd34ef56",
            source="intake-upload",
        )
        assert doc.extraction_status == DocumentExtractionStatus.PENDING
        assert doc.trusted_source is False
