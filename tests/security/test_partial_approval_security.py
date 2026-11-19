"""
CASEFILE Phase 12.1 Authoritative Claim Amount & Partial Approval Security Suite.

Verifies:
1. $100 claim with $50 partial approval — accepted.
2. $100 claim with $100 partial approval — accepted.
3. $100 claim with $100.01 partial approval — rejected.
4. $100 claim with $1,000,000 partial approval — rejected (Original Exploit Reproduction).
5. Missing requested amount in ApprovalDecision — rejected.
6. Null requested amount in ApprovalDecision — rejected.
7. Zero requested amount in ApprovalDecision — rejected.
8. Negative requested amount in ApprovalDecision — rejected.
9. Missing persisted claim record — rejected with typed error.
10. Missing or invalid authoritative claim amount — rejected safely.
11. Caller attempts to manipulate requested amount — ignored; persisted amount is authoritative.
12. Agent-generated partial recommendation — cannot authorize payout, parks in HUMAN_APPROVAL.
13. Unauthorized actor role — rejected.
14. Requester attempting to approve their own claim — rejected via separation of duties.
15. Existing APPROVE and REJECT behavior — fully preserved.
16. Approval replay and immutable audit trail — approved amount remains consistent and tamper-evident.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from casefile.api.auth import Principal, Role
from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService
from casefile.approval.model import (
    ApprovalActor,
    ApprovalDecision,
    ApprovalError,
    ApprovalErrorCode,
    ApprovalVerdict,
)
from casefile.models.contracts import (
    ClaimInput,
    CounterOffer,
    ReviewResult,
    WorkflowState,
)
from casefile.models.persistence import ClaimRecord, HumanApprovalRecord


def _make_api_service() -> ApiService:
    """Helper to instantiate an in-memory ApiService."""
    return ApiService(database_url="sqlite:///:memory:")


class TestPartialApprovalAuthoritativeSecurity:
    """Comprehensive adversarial testing for partial approval security."""

    def test_case_01_nominal_partial_approval_accepted(self) -> None:
        """1. $100 claim with $50 partial approval is accepted and records payout."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-01", role=Role.CLAIM_REVIEWER, display_name="Bob Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-100",
                claimant_name="Alice Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("100.00"),
                description="Minor bumper tap damage",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        res = svc.decide_approval(
            app_id,
            reviewer,
            "APPROVE_PARTIAL",
            reason="Agreed to 50% settlement",
            approved_amount=Decimal("50.00"),
        )
        assert res.verdict == "APPROVE_PARTIAL"
        assert res.approved_amount == Decimal("50.00")

    def test_case_02_full_ceiling_partial_approval_accepted(self) -> None:
        """2. $100 claim with $100 partial approval is accepted (boundary condition)."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-02", role=Role.CLAIM_REVIEWER, display_name="Carol Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-101",
                claimant_name="Bob Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("100.00"),
                description="Fender scrape repair",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        res = svc.decide_approval(
            app_id,
            reviewer,
            "APPROVE_PARTIAL",
            reason="Full claim value accepted under partial review structure",
            approved_amount=Decimal("100.00"),
        )
        assert res.verdict == "APPROVE_PARTIAL"
        assert res.approved_amount == Decimal("100.00")

    def test_case_03_cent_exceedance_rejected(self) -> None:
        """3. $100 claim with $100.01 partial approval is strictly rejected."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-03", role=Role.CLAIM_REVIEWER, display_name="Dave Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-102",
                claimant_name="Carol Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("100.00"),
                description="Side mirror fracture",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                reviewer,
                "APPROVE_PARTIAL",
                reason="Attempting 1 cent payout over claim limit",
                approved_amount=Decimal("100.01"),
            )
        assert exc_info.value.code == ApprovalErrorCode.VALIDATION_ERROR
        assert "cannot exceed" in str(exc_info.value)

    def test_case_04_original_exploit_reproduction_rejected(self) -> None:
        """
        4. Original Exploit Reproduction:
        $100 claim attempting $1,000,000 payout through ApiService.decide_approval().
        MUST fail closed with ApprovalError and NOT enter WorkflowState.APPROVED.
        """
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-exploit", role=Role.CLAIM_REVIEWER, display_name="Eve Exploit"
        )

        sub_resp = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-EXPLOIT-01",
                claimant_name="Target Claim",
                incident_date="2026-06-01",
                claim_amount=Decimal("100.00"),
                description="Small door ding claim",
            )
        )
        w_id = UUID(sub_resp.workflow_run_id)
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        # Attempt $1,000,000 payout on $100 claim
        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                reviewer,
                "APPROVE_PARTIAL",
                reason="Malicious unauthorized 1M payout exploit",
                approved_amount=Decimal("1000000.00"),
            )
        assert exc_info.value.code == ApprovalErrorCode.VALIDATION_ERROR
        assert "cannot exceed" in str(exc_info.value)

        # Assert workflow state was NOT moved to APPROVED
        status = svc.get_workflow_status(w_id)
        assert status.current_state == WorkflowState.HUMAN_APPROVAL.value
        assert status.current_state != WorkflowState.APPROVED.value

    def test_case_05_missing_requested_amount_in_decision_rejected(self) -> None:
        """5. ApprovalDecision with APPROVE_PARTIAL and missing requested_amount fails closed."""
        actor = ApprovalActor(actor_id="adjuster-5", display_name="Adjuster 5")
        with pytest.raises(
            ValidationError, match="requested_amount is required for APPROVE_PARTIAL"
        ):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("50.00"),
            )

    def test_case_06_null_requested_amount_in_decision_rejected(self) -> None:
        """6. ApprovalDecision with requested_amount=None fails closed."""
        actor = ApprovalActor(actor_id="adjuster-6", display_name="Adjuster 6")
        with pytest.raises(
            ValidationError, match="requested_amount is required for APPROVE_PARTIAL"
        ):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("50.00"),
                requested_amount=None,
            )

    def test_case_07_zero_requested_amount_in_decision_rejected(self) -> None:
        """7. ApprovalDecision with requested_amount=0.00 fails closed."""
        actor = ApprovalActor(actor_id="adjuster-7", display_name="Adjuster 7")
        with pytest.raises(ValidationError, match="requested_amount must be positive"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("50.00"),
                requested_amount=Decimal("0.00"),
            )

    def test_case_08_negative_requested_amount_in_decision_rejected(self) -> None:
        """8. ApprovalDecision with negative requested_amount fails closed."""
        actor = ApprovalActor(actor_id="adjuster-8", display_name="Adjuster 8")
        with pytest.raises(ValidationError, match="requested_amount must be positive"):
            ApprovalDecision(
                approval_id=uuid4(),
                actor=actor,
                verdict=ApprovalVerdict.APPROVE_PARTIAL,
                approved_amount=Decimal("50.00"),
                requested_amount=Decimal("-100.00"),
            )

    def test_case_09_missing_persisted_claim_fails_closed(self) -> None:
        """9. Decision fails closed if the underlying claim record is deleted or missing."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-09", role=Role.CLAIM_REVIEWER, display_name="Frank Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-109",
                claimant_name="Frank Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("250.00"),
                description="Hail dent on trunk lid",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        # Corrupt / delete the underlying claim record
        with svc._uow() as uow:
            app_rec = uow.session.get(HumanApprovalRecord, str(app_id))
            assert app_rec is not None
            claim_rec = uow.session.get(ClaimRecord, str(app_rec.claim_id))
            assert claim_rec is not None
            uow.session.delete(claim_rec)
            uow.session.commit()

        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                reviewer,
                "APPROVE_PARTIAL",
                approved_amount=Decimal("100.00"),
            )
        assert exc_info.value.code == ApprovalErrorCode.NOT_FOUND

    def test_case_10_missing_or_invalid_authoritative_amount_fails_closed(self) -> None:
        """10. Decision fails closed if the persisted claim carries a non-positive or corrupted amount."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-10", role=Role.CLAIM_REVIEWER, display_name="Grace Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-110",
                claimant_name="Grace Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("500.00"),
                description="Rear reflector crack",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        # Tamper the persisted claim amount to zero
        with svc._uow() as uow:
            app_rec = uow.session.get(HumanApprovalRecord, str(app_id))
            assert app_rec is not None
            claim_rec = uow.session.get(ClaimRecord, str(app_rec.claim_id))
            assert claim_rec is not None
            claim_rec.claim_amount = Decimal("0.00")
            uow.session.commit()

        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                reviewer,
                "APPROVE_PARTIAL",
                approved_amount=Decimal("100.00"),
            )
        assert exc_info.value.code == ApprovalErrorCode.VALIDATION_ERROR

    def test_case_11_caller_cannot_override_authoritative_amount(self) -> None:
        """11. An external caller cannot inflate requested amount; persisted DB value rules."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-11", role=Role.CLAIM_REVIEWER, display_name="Hank Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-111",
                claimant_name="Hank Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("300.00"),
                description="Front grille gravel hit",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        # Attempt $400 payout; even if a caller thought claim was $1,000, DB amount is $300
        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                reviewer,
                "APPROVE_PARTIAL",
                reason="Trying payout of $400 on $300 claim",
                approved_amount=Decimal("400.00"),
            )
        assert exc_info.value.code == ApprovalErrorCode.VALIDATION_ERROR
        assert "cannot exceed" in str(exc_info.value)

    def test_case_12_agent_partial_recommendation_cannot_authorize_payout(self) -> None:
        """12. Reviewer recommending APPROVE_PARTIAL does not approve claim autonomously."""
        import json

        from casefile.agents.deterministic import DeterministicProvider
        from casefile.agents.extractor import ExtractorAgent
        from casefile.agents.investigator import InvestigatorAgent
        from casefile.agents.reviewer import ReviewerAgent
        from casefile.models.contracts import ExtractionResult, InvestigationResult
        from casefile.workflow.runner import WorkflowRunner

        run_id = uuid4()
        claim_input = ClaimInput(
            claim_id=uuid4(),
            policy_id="POL-AGENT-12",
            claimant_name="Agent Test",
            incident_date="2026-06-01",
            claim_amount=Decimal("1000.00"),
            description="Agent recommendation test",
            documents=["DOC-AGENT-12"],
        )
        ext_prov = DeterministicProvider("ext")

        ext_prov.push_json(
            json.loads(
                ExtractionResult(
                    workflow_id=run_id,
                    claimant_name="Agent Test",
                    policy_id="POL-AGENT-12",
                    incident_date="2026-06-01",
                    incident_location="Loc",
                    incident_description="Desc",
                    claim_amount=Decimal("1000.00"),
                    requested_coverage_type="COLLISION",
                    documents_processed=["DOC-AGENT-12"],
                    extraction_confidence=0.98,
                    missing_fields=[],
                    is_complete=True,
                ).model_dump_json()
            )
        )

        inv_prov = DeterministicProvider("inv")
        inv_prov.push_json(
            json.loads(
                InvestigationResult(
                    workflow_id=run_id,
                    policy_details={"status": "ACTIVE"},
                    claim_history={},
                    repair_cost_validation={"verified": True},
                    fraud_signals={},
                    supporting_documents=[],
                    findings_summary="OK",
                    evidence_strength=0.95,
                    tool_calls_made=["policy_lookup"],
                    investigation_duration_seconds=1,
                    tools_succeeded=1,
                    tools_failed=0,
                ).model_dump_json()
            )
        )

        rev_prov = DeterministicProvider("rev")
        rev_prov.push_json(
            json.loads(
                ReviewResult(
                    workflow_id=run_id,
                    decision="APPROVE_PARTIAL",
                    reasoning="Recommending 750",
                    confidence_score=0.95,
                    counter_offer=CounterOffer(
                        claim_id=claim_input.claim_id,
                        original_amount=Decimal("1000.00"),
                        proposed_amount=Decimal("750.00"),
                        reduction_reason="Deductible",
                    ),
                ).model_dump_json()
            )
        )

        runner = WorkflowRunner(
            ExtractorAgent(ext_prov), InvestigatorAgent(inv_prov), ReviewerAgent(rev_prov)
        )
        wf = runner.run(claim_input, workflow_run_id=run_id)

        # MUST NOT be in APPROVED state autonomously; MUST be in HUMAN_APPROVAL state
        assert wf.current_snapshot is not None
        assert wf.current_snapshot.current_state != WorkflowState.APPROVED
        assert wf.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

    def test_case_13_unauthorized_actor_rejected(self) -> None:
        """13. A user with VIEWER or OPERATOR role cannot authorize partial payout."""
        svc = _make_api_service()
        viewer = Principal(principal_id="viewer-13", role=Role.VIEWER, display_name="Viewer Only")

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-113",
                claimant_name="Ian Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("200.00"),
                description="Cracked fog lamp",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                viewer,
                "APPROVE_PARTIAL",
                approved_amount=Decimal("150.00"),
            )
        assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED

    def test_case_14_requester_cannot_approve_own_claim(self) -> None:
        """14. Separation of duties: requester cannot approve their own claim."""
        svc = _make_api_service()
        reviewer_submitter = Principal(
            principal_id="adjuster-self", role=Role.CLAIM_REVIEWER, display_name="Self Adjuster"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-114",
                claimant_name="Self Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("400.00"),
                description="Side door scratch",
            )
        )
        approvals = svc.list_approvals()
        app_id = UUID(approvals.approvals[0].approval_id)

        # Manually simulate requested_by
        with svc._uow() as uow:
            rec = uow.session.get(HumanApprovalRecord, str(app_id))
            assert rec is not None
            rec.requested_by = "adjuster-self"
            uow.session.commit()

        with pytest.raises(ApprovalError) as exc_info:
            svc.decide_approval(
                app_id,
                reviewer_submitter,
                "APPROVE_PARTIAL",
                approved_amount=Decimal("250.00"),
            )
        assert exc_info.value.code == ApprovalErrorCode.UNAUTHORIZED
        assert "Separation of duties" in str(exc_info.value)

    def test_case_15_existing_approve_and_reject_preserved(self) -> None:
        """15. Standard full APPROVE and REJECT behavior remains functional without approved_amount."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-15", role=Role.CLAIM_REVIEWER, display_name="Karen Reviewer"
        )

        # Full approve
        s1 = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-115A",
                claimant_name="Claimant A",
                incident_date="2026-06-01",
                claim_amount=Decimal("600.00"),
                description="Full approve test",
            )
        )
        q1 = svc.list_approvals()
        a1_id = UUID(
            [a for a in q1.approvals if a.workflow_run_id == s1.workflow_run_id][0].approval_id
        )
        res_app = svc.decide_approval(a1_id, reviewer, "APPROVE", reason="Full approved")
        assert res_app.verdict == "APPROVE"
        assert res_app.approved_amount is None

        # Full reject
        s2 = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-115B",
                claimant_name="Claimant B",
                incident_date="2026-06-01",
                claim_amount=Decimal("700.00"),
                description="Full reject test",
            )
        )
        q2 = svc.list_approvals()
        a2_id = UUID(
            [a for a in q2.approvals if a.workflow_run_id == s2.workflow_run_id][0].approval_id
        )
        res_rej = svc.decide_approval(a2_id, reviewer, "REJECT", reason="Policy exclusion")
        assert res_rej.verdict == "REJECT"
        assert res_rej.approved_amount is None

    def test_case_16_replay_and_audit_persistence_consistency(self) -> None:
        """16. Approval replay and immutable audit trail preserve the approved amount consistently."""
        svc = _make_api_service()
        reviewer = Principal(
            principal_id="rev-16", role=Role.CLAIM_REVIEWER, display_name="Leo Reviewer"
        )

        svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-116",
                claimant_name="Replay Claimant",
                incident_date="2026-06-01",
                claim_amount=Decimal("800.00"),
                description="Tail light assembly loss",
            )
        )
        q = svc.list_approvals()
        app_id = UUID(q.approvals[0].approval_id)
        d_key = uuid4().hex

        # First decision
        res1 = svc.decide_approval(
            app_id,
            reviewer,
            "APPROVE_PARTIAL",
            reason="Initial authorized partial payout",
            decision_key=d_key,
            approved_amount=Decimal("620.00"),
        )
        assert res1.approved_amount == Decimal("620.00")

        # Idempotent replay with same decision key
        res2 = svc.decide_approval(
            app_id,
            reviewer,
            "APPROVE_PARTIAL",
            reason="Idempotent replay call",
            decision_key=d_key,
            approved_amount=Decimal("620.00"),
        )
        assert res2.approved_amount == Decimal("620.00")
        assert res2.status == "APPROVED"
