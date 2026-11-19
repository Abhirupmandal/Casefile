"""
CASEFILE Phase 12.1 Acceptance & Foundation Suite.

Verifies:
1. Multi-language and batch metadata contracts and validation (en, es, fr, de).
2. Backward compatibility of ClaimInput, Claim, ClaimRecord without new fields.
3. Partial approval contracts (APPROVE_PARTIAL verdict, positive amount, bounds checks).
4. Shared Phase 12 contracts (CounterOffer, DisasterContext, BatchSubmissionRequest,
   DecisionExplanation, CounterfactualScenario, ApprovalFeedback).
5. SQLAlchemy models and persistence round-trip.
6. Alembic migration upgrade (0008 -> 0009) and downgrade (0009 -> 0008).
7. Data preservation across migration.
8. Fresh database schema creation with all Phase 12 tables.
9. Safety invariants: human-only approval transition, actor fencing, and no automated payout.
"""

from __future__ import annotations

import contextlib
import gc
import json
import os
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from casefile.approval.model import (
    ApprovalActor,
    ApprovalDecision,
    ApprovalVerdict,
    HumanActor,
)
from casefile.models.contracts import (
    ApprovalFeedback,
    BatchSubmissionRequest,
    ClaimInput,
    CounterfactualScenario,
    CounterOffer,
    DecisionExplanation,
    DisasterContext,
    ExtractionResult,
    InvestigationResult,
    ReviewResult,
    WorkflowState,
    validate_language_code,
)
from casefile.models.domain import (
    AgentType,
    ApprovalStatus,
    Claim,
    ClaimType,
    HumanApproval,
    Money,
)
from casefile.models.persistence import (
    ApprovalFeedbackRecord,
    BatchRecord,
    init_db,
)
from casefile.storage.mappings import (
    approval_to_record,
    claim_to_record,
    record_to_approval,
    record_to_claim,
)
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.transitions import (
    TRANSITION_TABLE,
    ActorNotAllowedError,
    TransitionEvent,
)
from casefile.workflow.triggers import Trigger


# ==============================================================================
# Category A: Multi-Language & Batch Metadata Contracts
# ==============================================================================
class TestMultiLanguageAndBatchContracts:
    def test_supported_languages_en_es_fr_de(self) -> None:
        """Supported languages en, es, fr, de are accepted."""
        for code in ("en", "es", "fr", "de", "EN", "ES", "FR", "DE"):
            inp = ClaimInput(
                policy_id="POL-1234",
                claimant_name="Maria Garcia",
                incident_date="2026-05-10",
                claim_amount=Decimal("3500.00"),
                description="Daño por granizo en el techo",
                language=code,
            )
            assert inp.language == code.lower()

    def test_language_normalization(self) -> None:
        """BCP-47 subtag locales (es-MX, fr-CA, de-DE) normalize to primary code."""
        assert validate_language_code("es-MX") == "es"
        assert validate_language_code("fr_CA") == "fr"
        assert validate_language_code("de-AT") == "de"
        assert validate_language_code("en-US") == "en"

    def test_unsupported_language_raises_validation_error(self) -> None:
        """Unsupported languages (e.g. ja, it, ru) raise ValidationError."""
        for invalid in ("ja", "it", "ru", "chinese", "unknown", ""):
            with pytest.raises(ValidationError):
                ClaimInput(
                    policy_id="POL-1234",
                    claimant_name="Jane Doe",
                    incident_date="2026-05-10",
                    claim_amount=Decimal("1500.00"),
                    description="Collision damage",
                    language=invalid,
                )

    def test_claim_input_default_language_is_en(self) -> None:
        """Omitted language defaults to 'en' (backward compatibility)."""
        inp = ClaimInput(
            policy_id="POL-9999",
            claimant_name="John Smith",
            incident_date="2026-06-01",
            claim_amount=Decimal("2000.00"),
            description="Rear-end bumper damage",
        )
        assert inp.language == "en"
        assert inp.batch_id is None

    def test_claim_input_with_optional_batch_id(self) -> None:
        """ClaimInput accepts an optional UUID batch_id."""
        b_id = uuid4()
        inp = ClaimInput(
            policy_id="POL-9999",
            claimant_name="John Smith",
            incident_date="2026-06-01",
            claim_amount=Decimal("2000.00"),
            description="Windstorm damage",
            batch_id=b_id,
        )
        assert inp.batch_id == b_id

    def test_claim_domain_model_supports_language_and_batch(self) -> None:
        """Domain Claim entity supports language and batch_id."""
        b_id = uuid4()
        claim = Claim(
            external_claim_id="CLM-2026-ES-01",
            policy_number="POL-ES-1",
            customer_id="CUST-001",
            claim_type=ClaimType.COLLISION,
            date_of_loss=date(2026, 4, 1),
            date_reported=date(2026, 4, 2),
            description="Colisión leve",
            claim_amount=Money(amount=Decimal("2500.00")),
            language="es",
            batch_id=b_id,
        )
        assert claim.language == "es"
        assert claim.batch_id == b_id


# ==============================================================================
# Category B: Partial Approval & Payout Invariants
# ==============================================================================
class TestPartialApprovalContracts:
    def test_approval_verdict_approve_partial_exists(self) -> None:
        """ApprovalVerdict includes APPROVE_PARTIAL."""
        assert ApprovalVerdict.APPROVE_PARTIAL.value == "APPROVE_PARTIAL"

    def test_approve_partial_requires_positive_approved_amount(self) -> None:
        """APPROVE_PARTIAL verdict requires an approved_amount > 0."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        # Missing approved_amount
        with pytest.raises(
            ValidationError, match="approved_amount is required for APPROVE_PARTIAL"
        ):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                requested_amount=Decimal("10000.00"),
                reason="Partial settlement agreed",
            )

        # Zero amount
        with pytest.raises(ValidationError, match="approved_amount must be positive"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("0.00"),
                requested_amount=Decimal("10000.00"),
                reason="Zero settlement",
            )

        # Negative amount
        with pytest.raises(ValidationError, match="approved_amount must be positive"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("-500.00"),
                requested_amount=Decimal("10000.00"),
                reason="Negative settlement",
            )

    def test_approve_partial_requires_requested_amount(self) -> None:
        """APPROVE_PARTIAL requires an authoritative requested_amount."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        # Missing requested_amount
        with pytest.raises(
            ValidationError, match="requested_amount is required for APPROVE_PARTIAL"
        ):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("5000.00"),
                reason="Missing claim amount",
            )

    def test_approve_partial_requires_positive_requested_amount(self) -> None:
        """requested_amount must be strictly positive."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        with pytest.raises(ValidationError, match="requested_amount must be positive"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("100.00"),
                requested_amount=Decimal("0.00"),
                reason="Zero requested amount",
            )

        with pytest.raises(ValidationError, match="requested_amount must be positive"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("100.00"),
                requested_amount=Decimal("-1000.00"),
                reason="Negative requested amount",
            )

    def test_requested_amount_decimal_precision(self) -> None:
        """requested_amount cannot carry more than 2 decimal places."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        with pytest.raises(ValidationError, match="cannot.*2 decimal places"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("500.00"),
                requested_amount=Decimal("1000.555"),
                reason="Too many fractional digits",
            )

    def test_approved_amount_cannot_exceed_requested_amount(self) -> None:
        """approved_amount cannot exceed requested_amount when requested_amount is known."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        with pytest.raises(ValidationError, match="cannot exceed requested_amount"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("12000.00"),
                requested_amount=Decimal("10000.00"),
                reason="Excessive amount",
            )

    def test_approved_amount_decimal_precision(self) -> None:
        """approved_amount cannot carry more than 2 decimal places."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        with pytest.raises(ValidationError, match="cannot.*2 decimal places"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("5000.555"),
                requested_amount=Decimal("10000.00"),
                reason="Too many decimals",
            )

    def test_reject_verdict_cannot_carry_approved_amount(self) -> None:
        """REJECT verdict cannot carry an approved_amount."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        with pytest.raises(ValidationError, match="cannot be specified for REJECT"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.REJECT,
                approved_amount=Decimal("1000.00"),
                reason="Rejecting claim but giving payout",
            )

    def test_existing_full_approve_compatibility(self) -> None:
        """Full APPROVE verdict without approved_amount continues to work as standard."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        decision = ApprovalDecision(
            approval_id=uuid4(),
            actor=actor,
            verdict=ApprovalVerdict.APPROVE,
            reason="Full claim approved",
        )
        assert decision.verdict == ApprovalVerdict.APPROVE
        assert decision.approved_amount is None

    def test_existing_reject_compatibility(self) -> None:
        """REJECT verdict continues to work as standard."""
        actor = ApprovalActor(actor_id="adjuster-1", display_name="Alice Adjuster")
        decision = ApprovalDecision(
            approval_id=uuid4(),
            actor=actor,
            verdict=ApprovalVerdict.REJECT,
            reason="Fraud signals detected",
        )
        assert decision.verdict == ApprovalVerdict.REJECT
        assert decision.approved_amount is None

    def test_actor_must_be_human_for_decision(self) -> None:
        """Only an authorized HumanActor/ApprovalActor can decide."""
        with pytest.raises(ValidationError):
            ApprovalDecision(
                approval_id=uuid4(),
                actor={"actor_id": "", "role": "INVALID"},  # type: ignore[arg-type]
                verdict=ApprovalVerdict.APPROVE,
            )


# ==============================================================================
# Category C: Shared Phase 12 Contracts
# ==============================================================================
class TestSharedPhase12Contracts:
    def test_counter_offer_contract(self) -> None:
        """CounterOffer validates bounds and precision."""
        c_id = uuid4()
        offer = CounterOffer(
            claim_id=c_id,
            original_amount=Decimal("15000.00"),
            proposed_amount=Decimal("11500.00"),
            reduction_reason="Deducted unauthorized OEM paint rate surcharge and pre-existing rear scuff",
            disputed_items=["OEM paint surcharge: $2,500.00", "Pre-existing rear scuff: $1,000.00"],
        )
        assert offer.proposed_amount == Decimal("11500.00")
        assert offer.original_amount == Decimal("15000.00")
        assert len(offer.disputed_items) == 2

    def test_counter_offer_proposed_exceeds_original_raises(self) -> None:
        """CounterOffer proposed_amount > original_amount raises ValidationError."""
        with pytest.raises(ValidationError, match="cannot exceed original_amount"):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("10000.00"),
                proposed_amount=Decimal("12000.00"),
                reduction_reason="Invalid increase",
            )

    def test_disaster_context_contract(self) -> None:
        """DisasterContext encapsulates shared catastrophic incident data."""
        ctx = DisasterContext(
            disaster_id="CAT-2026-HURRICANE-ZETA",
            disaster_name="Hurricane Zeta",
            disaster_type="HURRICANE",
            geographic_zone="FL-COASTAL-GULF",
            declared_date="2026-09-15",
            emergency_deductible_policy="Waive hurricane deductible for active primary residences",
            shared_notes="Prioritize roof and water ingress claims",
        )
        assert ctx.disaster_id == "CAT-2026-HURRICANE-ZETA"
        assert ctx.disaster_type == "HURRICANE"

    def test_batch_submission_request_contract(self) -> None:
        """BatchSubmissionRequest validates claim enrollment and auto-assigns batch_id."""
        b_id = uuid4()
        c1 = ClaimInput(
            policy_id="POL-01",
            claimant_name="Claimant 1",
            incident_date="2026-09-15",
            claim_amount=Decimal("5000.00"),
            description="Roof shingle loss",
        )
        c2 = ClaimInput(
            policy_id="POL-02",
            claimant_name="Claimant 2",
            incident_date="2026-09-15",
            claim_amount=Decimal("8000.00"),
            description="Window breach",
        )
        batch = BatchSubmissionRequest(
            batch_id=b_id,
            batch_name="Hurricane Zeta Gulf Batch 01",
            claims=[c1, c2],
            disaster_context=DisasterContext(
                disaster_id="CAT-2026-ZETA",
                disaster_name="Zeta",
                disaster_type="HURRICANE",
            ),
        )
        assert len(batch.claims) == 2
        assert c1.batch_id == b_id
        assert c2.batch_id == b_id

    def test_counterfactual_scenario_contract(self) -> None:
        """CounterfactualScenario captures sensitivity analysis attributes."""
        cf = CounterfactualScenario(
            factor_name="repair_cost",
            current_value="$8,500.00",
            counterfactual_value="$4,200.00",
            hypothetical_outcome="Eligible for fast-track low-risk review",
            explanation="Repair cost below $5,000 threshold allows expedited human queueing",
        )
        assert cf.factor_name == "repair_cost"
        assert cf.hypothetical_outcome == "Eligible for fast-track low-risk review"

    def test_decision_explanation_contract(self) -> None:
        """DecisionExplanation aggregates summary, citations, and counterfactuals."""
        w_id = uuid4()
        c_id = uuid4()
        cf = CounterfactualScenario(
            factor_name="fraud_signals",
            current_value="0 detected",
            counterfactual_value="1 prior fraud match",
            hypothetical_outcome="Mandatory full forensic investigation",
        )
        exp = DecisionExplanation(
            workflow_run_id=w_id,
            claim_id=c_id,
            summary="Claim fully substantiated by police report and facility estimate.",
            key_factors=["Valid active policy", "Clean loss history", "Consistent damage scope"],
            evidence_citations=["POL-10023 Section 4", "EST-8840 Line Items 1-5", "DOC-POLICE-09"],
            counterfactuals=[cf],
        )
        assert exp.workflow_run_id == w_id
        assert len(exp.key_factors) == 3
        assert len(exp.counterfactuals) == 1

    def test_approval_feedback_contract(self) -> None:
        """ApprovalFeedback captures structured adjuster corrections."""
        fb = ApprovalFeedback(
            approval_id=uuid4(),
            claim_id=uuid4(),
            actor_id="adjuster-42",
            category="REPAIR_COST_OVERESTIMATE",
            adjuster_notes="Adjusted body labor hours from 14.5 to 9.0 per industry standard book",
            overridden_recommendation="APPROVE_FULL",
            sanitized_sample={"loss_type": "collision", "variance_pct": -38.0},
        )
        assert fb.category == "REPAIR_COST_OVERESTIMATE"
        assert fb.sanitized_sample is not None

    def test_review_result_with_partial_decision(self) -> None:
        """ReviewResult supports APPROVE_PARTIAL with CounterOffer."""
        w_id = uuid4()
        c_id = uuid4()
        offer = CounterOffer(
            claim_id=c_id,
            original_amount=Decimal("12000.00"),
            proposed_amount=Decimal("9500.00"),
            reduction_reason="Paint blending deduction",
        )
        res = ReviewResult(
            workflow_id=w_id,
            decision="APPROVE_PARTIAL",
            reasoning="Investigation confirmed loss, but labor rate exceeds authorized limit",
            confidence_score=0.94,
            counter_offer=offer,
        )
        assert res.decision == "APPROVE_PARTIAL"
        assert res.counter_offer is not None
        assert res.counter_offer.proposed_amount == Decimal("9500.00")


# ==============================================================================
# Category D: SQLAlchemy Models, Mappings & Persistence
# ==============================================================================
class TestDatabaseFoundation:
    def test_sqlalchemy_model_declarations(self) -> None:
        """All Phase 12 tables and columns are declared on Base metadata."""
        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        inspector = inspect(engine)

        # Check existing tables have new columns
        claim_cols = {col["name"] for col in inspector.get_columns("claims")}
        assert "language" in claim_cols
        assert "batch_id" in claim_cols

        approval_cols = {col["name"] for col in inspector.get_columns("human_approvals")}
        assert "approved_amount" in approval_cols

        # Check new tables exist
        table_names = inspector.get_table_names()
        assert "batches" in table_names
        assert "approval_feedback" in table_names
        assert "counterfactual_scenarios" in table_names

    def test_claim_mapping_roundtrip(self) -> None:
        """claim_to_record and record_to_claim preserve language and batch_id."""
        b_id = uuid4()
        c_id = uuid4()
        claim = Claim(
            claim_id=c_id,
            external_claim_id="CLM-ROUNDTRIP-01",
            policy_number="POL-DE-1",
            customer_id="CUST-DE-1",
            claim_type=ClaimType.COLLISION,
            date_of_loss=date(2026, 3, 10),
            date_reported=date(2026, 3, 11),
            description="Blechschaden an der Kreuzung",
            claim_amount=Money(amount=Decimal("4200.00")),
            language="de",
            batch_id=b_id,
        )

        record = claim_to_record(claim)
        assert record.language == "de"
        assert record.batch_id == str(b_id)

        reconstituted = record_to_claim(record)
        assert reconstituted.language == "de"
        assert reconstituted.batch_id == b_id
        assert reconstituted.claim_amount.amount == Decimal("4200.00")

    def test_approval_mapping_roundtrip_with_partial_amount(self) -> None:
        """approval_to_record and record_to_approval preserve approved_amount."""
        app_id = uuid4()
        w_id = uuid4()
        c_id = uuid4()
        approval = HumanApproval(
            approval_id=app_id,
            workflow_run_id=w_id,
            claim_id=c_id,
            status=ApprovalStatus.APPROVED,
            approver_id="adjuster-99",
            approver_name="Senior Adjuster",
            approver_role="SENIOR_REVIEWER",
            requested_at=datetime.now(UTC),
            decided_at=datetime.now(UTC),
            approved_amount=Money(amount=Decimal("8500.00")),
            notes="Authorized negotiated partial payout",
        )

        record = approval_to_record(approval)
        assert record.approved_amount == Decimal("8500.00")

        reconstituted = record_to_approval(record)
        assert reconstituted.approved_amount is not None
        assert reconstituted.approved_amount.amount == Decimal("8500.00")

    def test_batch_record_persistence(self) -> None:
        """BatchRecord persists and reloads correctly."""
        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        session_factory = sessionmaker(bind=engine)

        b_id = str(uuid4())
        with session_factory() as session:
            batch = BatchRecord(
                batch_id=b_id,
                batch_name="Catastrophe Batch FL",
                status="PROCESSING",
                disaster_id="CAT-2026-FL",
                disaster_type="HURRICANE",
                disaster_name="Hurricane Ian II",
                total_claims=12,
            )
            session.add(batch)
            session.commit()

        with session_factory() as session:
            loaded = session.get(BatchRecord, b_id)
            assert loaded is not None
            assert loaded.batch_name == "Catastrophe Batch FL"
            assert loaded.total_claims == 12

    def test_approval_feedback_persistence(self) -> None:
        """ApprovalFeedbackRecord persists and reloads correctly."""
        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        session_factory = sessionmaker(bind=engine)

        fb_id = str(uuid4())
        with session_factory() as session:
            fb = ApprovalFeedbackRecord(
                feedback_id=fb_id,
                approval_id=str(uuid4()),
                claim_id=str(uuid4()),
                actor_id="adjuster-7",
                category="POLICY_INTERPRETATION",
                adjuster_notes="Comprehensive coverage applies under Section 3 endorsement",
                overridden_recommendation="REJECT",
                sanitized_sample_json='{"loss": "flood", "endorsement": true}',
            )
            session.add(fb)
            session.commit()

        with session_factory() as session:
            loaded = session.get(ApprovalFeedbackRecord, fb_id)
            assert loaded is not None
            assert loaded.category == "POLICY_INTERPRETATION"
            assert "Section 3" in loaded.adjuster_notes


def _to_json_dict(model: Any) -> dict[str, Any]:
    res = json.loads(model.model_dump_json())
    assert isinstance(res, dict)
    return res


def _make_extraction(run_id: UUID, claim: ClaimInput) -> ExtractionResult:
    return ExtractionResult(
        workflow_id=run_id,
        claimant_name=claim.claimant_name,
        policy_id=claim.policy_id,
        incident_date=claim.incident_date,
        incident_location="Main St Parking",
        incident_description=claim.description,
        claim_amount=claim.claim_amount,
        requested_coverage_type="COLLISION",
        documents_processed=list(claim.documents),
        extraction_confidence=0.98,
        missing_fields=[],
        is_complete=True,
    )


def _make_investigation(run_id: UUID) -> InvestigationResult:
    return InvestigationResult(
        workflow_id=run_id,
        policy_details={"status": "ACTIVE", "coverage": "COLLISION"},
        claim_history={"prior_claims_count": 0},
        repair_cost_validation={"verified": True, "discrepancy": False},
        fraud_signals={"score": 0.05, "risk": "LOW"},
        supporting_documents=["DOC-001"],
        findings_summary="Clean driving history, verified damage consistent with claim.",
        evidence_strength=0.95,
        tool_calls_made=["policy_lookup", "repair_cost_lookup"],
        investigation_duration_seconds=2,
        tools_succeeded=2,
        tools_failed=0,
    )


# ==============================================================================
# Category E: Safety Architecture & Non-Negotiables
# ==============================================================================
class TestSafetyArchitectureInvariants:
    def test_workflow_state_approved_requires_human_actor(self) -> None:
        """APPROVED state is reachable EXCLUSIVELY via human actor."""
        rule = TRANSITION_TABLE[(WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_GRANTED)]
        assert rule.destination == WorkflowState.APPROVED
        assert rule.allowed_actors == frozenset({AgentType.HUMAN})

    def test_agent_cannot_fire_approval_granted(self) -> None:
        """Supervisor or Reviewer attempting APPROVAL_GRANTED raises ActorNotAllowedError."""
        engine = WorkflowEngine()
        from casefile.workflow.context import RunContext, WorkflowSnapshot

        snap = WorkflowSnapshot(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            current_state=WorkflowState.HUMAN_APPROVAL,
            step_count=5,
        )
        ctx = RunContext(workflow_run_id=snap.workflow_run_id, claim_id=snap.claim_id)
        for forbidden_actor in (
            AgentType.SUPERVISOR,
            AgentType.REVIEWER,
            AgentType.INVESTIGATOR,
            AgentType.SYSTEM,
        ):
            event = TransitionEvent(
                claim_id=snap.claim_id,
                workflow_run_id=snap.workflow_run_id,
                trigger=Trigger.APPROVAL_GRANTED,
                actor=forbidden_actor,
            )
            with pytest.raises(ActorNotAllowedError):
                engine.apply(snap, event, ctx)

    def test_partial_approval_routes_through_human_approval_gate(self) -> None:
        """ReviewResult with APPROVE_PARTIAL routes to HUMAN_APPROVAL, not APPROVED."""
        from casefile.agents.deterministic import DeterministicProvider
        from casefile.agents.extractor import ExtractorAgent
        from casefile.agents.investigator import InvestigatorAgent
        from casefile.agents.reviewer import ReviewerAgent
        from casefile.workflow.runner import WorkflowRunner

        run_id = uuid4()
        claim_input = ClaimInput(
            claim_id=uuid4(),
            policy_id="POL-PARTIAL-01",
            claimant_name="Sam Adjust",
            incident_date="2026-05-01",
            claim_amount=Decimal("10000.00"),
            description="Partial roof hail damage",
            documents=["DOC-PARTIAL-01"],
        )

        ext_prov = DeterministicProvider("ext")
        ext_prov.push_json(_to_json_dict(_make_extraction(run_id, claim_input)))

        inv_prov = DeterministicProvider("inv")
        inv_prov.push_json(_to_json_dict(_make_investigation(run_id)))

        rev_prov = DeterministicProvider("rev")
        offer = CounterOffer(
            claim_id=claim_input.claim_id,
            original_amount=Decimal("10000.00"),
            proposed_amount=Decimal("8000.00"),
            reduction_reason="Deductible applied",
        )
        rev_res = ReviewResult(
            workflow_id=run_id,
            decision="APPROVE_PARTIAL",
            reasoning="Valid partial loss",
            confidence_score=0.95,
            counter_offer=offer,
        )
        rev_prov.push_json(_to_json_dict(rev_res))

        runner = WorkflowRunner(
            ExtractorAgent(ext_prov),
            InvestigatorAgent(inv_prov),
            ReviewerAgent(rev_prov),
        )

        wf_ctx = runner.run(claim_input, workflow_run_id=run_id)
        # MUST park at HUMAN_APPROVAL, never terminate autonomously
        assert wf_ctx.current_snapshot is not None
        assert wf_ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    def test_human_partial_approval_decision_transitions_to_approved_with_metadata(self) -> None:
        """Authorized human partial decision transitions to APPROVED with payout recorded."""
        from casefile.agents.deterministic import DeterministicProvider
        from casefile.agents.extractor import ExtractorAgent
        from casefile.agents.investigator import InvestigatorAgent
        from casefile.agents.reviewer import ReviewerAgent
        from casefile.workflow.runner import WorkflowRunner

        run_id = uuid4()
        claim_input = ClaimInput(
            claim_id=uuid4(),
            policy_id="POL-PARTIAL-02",
            claimant_name="Carol Danvers",
            incident_date="2026-05-01",
            claim_amount=Decimal("10000.00"),
            description="Dent repair",
            documents=["DOC-PARTIAL-02"],
        )

        ext_prov = DeterministicProvider("ext")
        ext_prov.push_json(_to_json_dict(_make_extraction(run_id, claim_input)))

        inv_prov = DeterministicProvider("inv")
        inv_prov.push_json(_to_json_dict(_make_investigation(run_id)))

        rev_prov = DeterministicProvider("rev")
        rev_res = ReviewResult(
            workflow_id=run_id,
            decision="APPROVE_PARTIAL",
            reasoning="Valid claim with $2,000 paint deduction",
            confidence_score=0.95,
        )
        rev_prov.push_json(_to_json_dict(rev_res))

        runner = WorkflowRunner(
            ExtractorAgent(ext_prov),
            InvestigatorAgent(inv_prov),
            ReviewerAgent(rev_prov),
        )

        wf_ctx = runner.run(claim_input, workflow_run_id=run_id)
        assert wf_ctx.current_snapshot is not None
        assert wf_ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

        # Human applies authorized partial approval
        actor = HumanActor(actor_id="human-adjuster-01", display_name="Carol Senior")
        decision = ApprovalDecision(
            approval_id=uuid4(),
            actor=actor,
            verdict=ApprovalVerdict.APPROVE_PARTIAL,
            approved_amount=Decimal("8000.00"),
            requested_amount=Decimal("10000.00"),
            reason="Authorized partial settlement of $8,000.00",
        )

        wf_ctx = runner.apply_human_decision(wf_ctx, decision)
        assert wf_ctx.current_snapshot is not None
        assert wf_ctx.current_snapshot.current_state == WorkflowState.APPROVED
        assert wf_ctx.workflow_metadata["approved_amount"] == "8000.00"
        assert wf_ctx.workflow_metadata["approval_verdict"] == "APPROVE_PARTIAL"


# ==============================================================================
# Category F: Alembic Migration Reversibility & Existing Data Survival
# ==============================================================================
class TestAlembicMigrationReversibility:
    def test_migration_upgrade_and_downgrade(self) -> None:
        """Alembic revision 0009 upgrades and downgrades cleanly."""
        from alembic import command
        from alembic.config import Config

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            alembic_cfg = Config("alembic.ini")
            alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

            # Upgrade to head (0009)
            command.upgrade(alembic_cfg, "head")

            # Verify tables exist
            engine = create_engine(f"sqlite:///{db_path}")
            inspector = inspect(engine)
            assert "batches" in inspector.get_table_names()
            assert "approval_feedback" in inspector.get_table_names()
            claim_cols = {c["name"] for c in inspector.get_columns("claims")}
            assert "language" in claim_cols
            assert "batch_id" in claim_cols

            # Downgrade to 0008
            command.downgrade(alembic_cfg, "0008_phase5_approval_metadata")

            # Verify tables dropped
            inspector = inspect(engine)
            assert "batches" not in inspector.get_table_names()
            assert "approval_feedback" not in inspector.get_table_names()
            claim_cols = {c["name"] for c in inspector.get_columns("claims")}
            assert "language" not in claim_cols

            # Upgrade back to head
            command.upgrade(alembic_cfg, "head")
            inspector = inspect(engine)
            assert "batches" in inspector.get_table_names()
            engine.dispose()

        finally:
            if "engine" in locals():
                engine.dispose()
            gc.collect()
            if os.path.exists(db_path):
                with contextlib.suppress(OSError):
                    os.unlink(db_path)

    def test_existing_records_survive_migration(self) -> None:
        """Existing records in claims and human_approvals survive migration 0009."""
        from alembic import command
        from alembic.config import Config

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            alembic_cfg = Config("alembic.ini")
            alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

            # 1. Upgrade to revision 0008
            command.upgrade(alembic_cfg, "0008_phase5_approval_metadata")

            # 2. Insert existing records into claims and human_approvals
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO claims (
                            claim_id, external_claim_id, policy_number, customer_id,
                            claim_type, status, claim_amount, currency, incident_date,
                            reported_date, description, priority, submitted_by,
                            submitted_at, created_at, updated_at
                        ) VALUES (
                            'c-1111', 'EXT-1111', 'POL-1111', 'CUST-1',
                            'COLLISION', 'SUBMITTED', 7500.00, 'USD', '2026-01-01',
                            '2026-01-02', 'Prior claim text', 'NORMAL', 'system',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO human_approvals (
                            approval_id, workflow_run_id, claim_id, status,
                            requested_at, request_version, is_override
                        ) VALUES (
                            'app-1111', 'wf-1111', 'c-1111', 'PENDING',
                            CURRENT_TIMESTAMP, 1, 0
                        )
                        """
                    )
                )
            engine.dispose()

            # 3. Upgrade to 0009
            command.upgrade(alembic_cfg, "0009_phase12_advanced_features")

            # 4. Verify existing records survived with default column values
            engine2 = create_engine(f"sqlite:///{db_path}")
            with engine2.connect() as conn:
                claim_row = conn.execute(
                    text(
                        "SELECT claim_id, language, batch_id, claim_amount FROM claims WHERE claim_id = 'c-1111'"
                    )
                ).fetchone()
                assert claim_row is not None
                assert claim_row[0] == "c-1111"
                assert claim_row[1] == "en"  # server_default="en"
                assert claim_row[2] is None
                assert float(claim_row[3]) == 7500.00

                approval_row = conn.execute(
                    text(
                        "SELECT approval_id, status, approved_amount FROM human_approvals WHERE approval_id = 'app-1111'"
                    )
                ).fetchone()
                assert approval_row is not None
                assert approval_row[0] == "app-1111"
                assert approval_row[1] == "PENDING"
                assert approval_row[2] is None
            engine2.dispose()

        finally:
            if "engine" in locals():
                engine.dispose()
            if "engine2" in locals():
                engine2.dispose()
            gc.collect()
            if os.path.exists(db_path):
                with contextlib.suppress(OSError):
                    os.unlink(db_path)
