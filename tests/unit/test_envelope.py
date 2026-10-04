"""
Unit Tests: Contract envelope for the eight Supervisor/specialist/human handoffs.

Proves typed wrap/unwrap, JSON round-trip equality, route enforcement,
schema-version policy, and rejection of free-form or wrong-type payloads.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from casefile.models.contracts import (
    ClaimInput,
    ExtractionRequest,
    ExtractionResult,
    InvestigationRequest,
    InvestigationResult,
    ReviewRequest,
    ReviewResult,
)
from casefile.models.domain import (
    AgentType,
    ApprovalStatus,
    HumanApproval,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.envelope import (
    HANDOFF_ROUTES,
    ContractEnvelope,
    ContractPayloadMismatchError,
    ContractType,
    UnknownContractTypeError,
)


def _claim_input() -> ClaimInput:
    return ClaimInput(
        policy_id="POL-ENV",
        claimant_name="Env Tester",
        incident_date="2026-09-19",
        claim_amount=Decimal("1200.00"),
        description="Envelope test",
    )


def _extraction_request() -> ExtractionRequest:
    return ExtractionRequest(workflow_id=uuid4(), claim_input=_claim_input())


def _extraction_result(workflow_id: UUID) -> ExtractionResult:
    return ExtractionResult(
        workflow_id=workflow_id,
        claimant_name="Env Tester",
        policy_id="POL-ENV",
        incident_date="2026-09-19",
        incident_location="Springfield",
        incident_description="Envelope test",
        claim_amount=Decimal("1200.00"),
        requested_coverage_type="COLLISION",
        extraction_confidence=0.9,
    )


def _wrap_request(req: ExtractionRequest) -> ContractEnvelope:
    return ContractEnvelope.wrap(
        "extraction_request",
        req,
        claim_id=req.claim_input.claim_id,
        workflow_run_id=req.workflow_id,
    )


@pytest.mark.unit
class TestHandoffRegistry:
    def test_all_eight_handoffs_registered(self) -> None:
        assert set(HANDOFF_ROUTES) == {
            "extraction_request",
            "extraction_result",
            "investigation_request",
            "investigation_result",
            "review_request",
            "review_result",
            "human_approval_request",
            "human_approval",
        }

    def test_routes_have_no_agent_to_agent_shortcuts(self) -> None:
        specialists = {AgentType.EXTRACTOR, AgentType.INVESTIGATOR, AgentType.REVIEWER}
        for route in HANDOFF_ROUTES.values():
            assert not (route.sender in specialists and route.recipient in specialists)


@pytest.mark.unit
class TestEnvelopeWrapUnwrap:
    def test_wrap_assigns_route_and_ids(self) -> None:
        req = _extraction_request()
        env = _wrap_request(req)
        assert env.sender == AgentType.SUPERVISOR
        assert env.recipient == AgentType.EXTRACTOR
        assert env.schema_version == "1.0.0"
        assert env.idempotency_key == f"{env.workflow_run_id}:{env.execution_id}"
        assert env.correlation_id is not None

    def test_unwrap_recovers_payload(self) -> None:
        req = _extraction_request()
        env = _wrap_request(req)
        assert env.unwrap(ExtractionRequest) == req

    def test_json_round_trip_preserves_equality(self) -> None:
        req = _extraction_request()
        env = _wrap_request(req)
        restored = ContractEnvelope.model_validate_json(env.model_dump_json())
        assert restored == env
        assert isinstance(restored.payload, ExtractionRequest)

    def test_request_envelopes_cover_all_handoffs(self) -> None:
        wf_id = uuid4()
        claim_id = uuid4()
        extraction = _extraction_result(wf_id)
        investigation = InvestigationRequest(
            workflow_id=wf_id,
            extraction_result=extraction,
        )
        review = ReviewRequest(
            workflow_id=wf_id,
            extraction_result=extraction,
            investigation_result=InvestigationResult(
                workflow_id=wf_id,
                findings_summary="ok",
                evidence_strength=0.7,
            ),
        )
        cases: list[tuple[ContractType, BaseModel, AgentType, AgentType]] = [
            ("investigation_request", investigation, AgentType.SUPERVISOR, AgentType.INVESTIGATOR),
            ("review_request", review, AgentType.SUPERVISOR, AgentType.REVIEWER),
        ]
        for contract_type, payload, sender, recipient in cases:
            env = ContractEnvelope.wrap(
                contract_type,
                payload,
                claim_id=claim_id,
                workflow_run_id=wf_id,
            )
            assert env.sender == sender
            assert env.recipient == recipient
            assert ContractEnvelope.model_validate_json(env.model_dump_json()) == env

    def test_reviewer_to_human_handoff(self) -> None:
        request = HumanApprovalRequest(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            recommendation=Recommendation(
                claim_id=uuid4(),
                recommendation_type=RecommendationType.APPROVE_FULL,
                estimated_payout=Money(amount=Decimal("1200.00")),
                notes="Full approval",
                confidence=0.99,
            ),
            reviewer_summary="Ready",
            deadline=datetime.now(UTC) + timedelta(hours=24),
        )
        env = ContractEnvelope.wrap(
            "human_approval_request",
            request,
            claim_id=request.claim_id,
            workflow_run_id=request.workflow_run_id,
        )
        assert env.sender == AgentType.REVIEWER
        assert env.recipient == AgentType.HUMAN

    def test_human_to_supervisor_handoff(self) -> None:
        approval = HumanApproval(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            status=ApprovalStatus.PENDING,
        )
        env = ContractEnvelope.wrap(
            "human_approval",
            approval,
            claim_id=approval.claim_id,
            workflow_run_id=approval.workflow_run_id,
        )
        assert env.sender == AgentType.HUMAN
        assert env.recipient == AgentType.SUPERVISOR
        assert env.unwrap(HumanApproval) == approval


@pytest.mark.unit
class TestEnvelopeRejection:
    def test_wrong_route_rejected(self) -> None:
        req = _extraction_request()
        with pytest.raises(ValidationError, match="must travel SUPERVISOR->EXTRACTOR"):
            ContractEnvelope(
                contract_type="extraction_request",
                claim_id=req.claim_input.claim_id,
                workflow_run_id=req.workflow_id,
                sender=AgentType.EXTRACTOR,
                recipient=AgentType.SUPERVISOR,
                payload=req,
            )

    def test_wrong_payload_type_rejected(self) -> None:
        req = _extraction_request()
        result = _extraction_result(req.workflow_id)
        with pytest.raises(ValidationError, match="requires payload ExtractionRequest"):
            ContractEnvelope.wrap(
                "extraction_request",
                result,
                claim_id=req.claim_input.claim_id,
                workflow_run_id=req.workflow_id,
            )

    def test_free_form_dict_payload_rejected(self) -> None:
        req = _extraction_request()
        with pytest.raises((ValidationError, ContractPayloadMismatchError)):
            ContractEnvelope.wrap(
                "extraction_request",
                {"workflow_id": str(req.workflow_id)},  # type: ignore[arg-type]
                claim_id=req.claim_input.claim_id,
                workflow_run_id=req.workflow_id,
            )

    def test_unwrap_wrong_model_rejected(self) -> None:
        req = _extraction_request()
        env = _wrap_request(req)
        with pytest.raises(ContractPayloadMismatchError):
            env.unwrap(ExtractionResult)

    def test_unknown_contract_type_rejected(self) -> None:
        req = _extraction_request()
        with pytest.raises((UnknownContractTypeError, ValidationError)):
            ContractEnvelope.wrap(
                "telepathy",  # type: ignore[arg-type]
                req,
                claim_id=req.claim_input.claim_id,
                workflow_run_id=req.workflow_id,
            )

    def test_incompatible_payload_version_rejected(self) -> None:
        wf_id = uuid4()
        result = _extraction_result(wf_id)
        data = result.model_dump(mode="json")
        data["schema_version"] = "2.0.0"
        evolved = ExtractionResult.model_validate(data)
        with pytest.raises(ValidationError, match="major version must match"):
            ContractEnvelope.wrap(
                "extraction_result",
                evolved,
                claim_id=uuid4(),
                workflow_run_id=wf_id,
            )

    def test_all_result_contracts_validate(self) -> None:
        wf_id = uuid4()
        claim = _claim_input()
        extraction = _extraction_result(wf_id)
        investigation = InvestigationResult(
            workflow_id=wf_id,
            findings_summary="ok",
            evidence_strength=0.7,
        )
        review = ReviewResult(
            workflow_id=wf_id,
            decision="APPROVE",
            reasoning="ok",
            confidence_score=0.9,
        )
        cases: list[tuple[ContractType, BaseModel, type[BaseModel]]] = [
            ("extraction_result", extraction, ExtractionResult),
            ("investigation_result", investigation, InvestigationResult),
            ("review_result", review, ReviewResult),
        ]
        for contract_type, payload, model in cases:
            env = ContractEnvelope.wrap(
                contract_type,
                payload,
                claim_id=claim.claim_id,
                workflow_run_id=wf_id,
            )
            assert isinstance(env.unwrap(model), BaseModel)
