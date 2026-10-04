"""
Unit Tests: Repository CRUD, domain round-trips, ordering, money precision,
and typed persistence errors (Phase 6 §26 A–N, U–X).
"""

from datetime import UTC, date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from casefile.models.contracts import BudgetState, WorkflowState
from casefile.models.domain import (
    ActorType,
    AgentType,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    Claim,
    ClaimDocumentReference,
    ClaimType,
    CoverageLimits,
    DamageEstimate,
    DamageLineItem,
    DocumentContentType,
    DocumentType,
    FraudIndicator,
    FraudSeverity,
    HumanApproval,
    HumanApprovalRequest,
    Money,
    Policy,
    PriorClaim,
    Recommendation,
    RecommendationType,
    WorkflowRun,
)
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.errors import PersistenceError, PersistenceErrorCode
from casefile.storage.unit_of_work import UnitOfWork


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = get_engine(f"sqlite:///{tmp_path / 'repos.db'}")
    init_db(engine)
    return get_session_factory(engine)


def _claim() -> Claim:
    return Claim(
        external_claim_id="EXT-REPO-1",
        policy_number="POL-REPO-1",
        customer_id="CUST-REPO-1",
        claim_type=ClaimType.COLLISION,
        date_of_loss=date(2026, 9, 10),
        date_reported=date(2026, 9, 12),
        description="Repository probe",
        claim_amount=Money(amount=Decimal("1234.56")),
    )


def _uow(tmp_path: Path) -> UnitOfWork:
    return UnitOfWork(_factory(tmp_path))


@pytest.mark.unit
class TestClaimRepository:
    def test_save_get_round_trip(self, tmp_path: Path) -> None:
        claim = _claim()
        with _uow(tmp_path) as uow:
            uow.claims.save(claim)
        with _uow(tmp_path) as uow:
            assert uow.claims.exists(claim.claim_id) is True
            loaded = uow.claims.get(claim.claim_id)
            assert loaded == claim
            assert loaded.claim_amount.amount == Decimal("1234.56")

    def test_get_missing_raises_not_found(self, tmp_path: Path) -> None:
        with _uow(tmp_path) as uow:
            with pytest.raises(PersistenceError) as exc_info:
                uow.claims.get(uuid4())
            assert exc_info.value.code == PersistenceErrorCode.NOT_FOUND
            assert uow.claims.exists(uuid4()) is False

    def test_invalid_domain_rejected_before_persistence(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Claim(
                external_claim_id="",
                policy_number="P",
                customer_id="C",
                claim_type=ClaimType.COLLISION,
                date_of_loss=date(2026, 9, 10),
                date_reported=date(2026, 9, 12),
                description="x",
                claim_amount=Money(amount=Decimal("1.00")),
            )


@pytest.mark.unit
class TestDocumentRepository:
    def test_save_list_get(self, tmp_path: Path) -> None:
        ref = ClaimDocumentReference(
            document_type=DocumentType.POLICE_REPORT,
            filename="report.txt",
            content_type=DocumentContentType.TEXT,
            content_hash="ab12cd34ef56gh78",
            source="intake",
        )
        with _uow(tmp_path) as uow:
            uow.documents.save(ref, "SYN-X-1", "police report body")
        with _uow(tmp_path) as uow:
            loaded_ref, content = uow.documents.get(ref.document_id)
            assert loaded_ref == ref
            assert content == "police report body"
            listed = uow.documents.list_for_claim("SYN-X-1")
            assert len(listed) == 1

    def test_oversized_content_rejected(self, tmp_path: Path) -> None:
        ref = ClaimDocumentReference(
            document_type=DocumentType.OTHER,
            content_hash="ab12cd34ef56gh78",
            source="intake",
        )
        with _uow(tmp_path) as uow:
            with pytest.raises(PersistenceError) as exc_info:
                uow.documents.save(ref, "SYN-X-1", "x" * 32769)
            assert exc_info.value.code == PersistenceErrorCode.VALIDATION_FAILED


@pytest.mark.unit
class TestPolicyPriorRepositories:
    def _policy(self) -> Policy:
        return Policy(
            policy_number="POL-SAVE-1",
            policyholder_name="Holder",
            policy_type="AUTO",
            effective_date=date(2024, 1, 1),
            expiration_date=date(2026, 12, 31),
            coverage=CoverageLimits(
                bodily_injury_per_person=Decimal("10000"),
                bodily_injury_per_accident=Decimal("20000"),
                property_damage=Decimal("5000"),
            ),
        )

    def test_policy_round_trip(self, tmp_path: Path) -> None:
        policy = self._policy()
        with _uow(tmp_path) as uow:
            uow.policies.save(policy)
        with _uow(tmp_path) as uow:
            loaded = uow.policies.get("pol-save-1")
            assert loaded == policy

    def test_prior_ordering_and_limit(self, tmp_path: Path) -> None:
        priors = [
            PriorClaim(
                claim_reference=f"OLD-{i}",
                claim_date=date(2024 + i, 1, 1),
                claim_type=ClaimType.COLLISION,
                amount=Money(amount=Decimal("100.00")),
                status="CLOSED",
            )
            for i in range(3)
        ]
        with _uow(tmp_path) as uow:
            for prior in priors:
                uow.priors.save(prior, "CUST-ORD", "POL-SAVE-1")
        with _uow(tmp_path) as uow:
            listed = uow.priors.list_for_customer("CUST-ORD")
            assert [p.claim_reference for p in listed] == ["OLD-2", "OLD-1", "OLD-0"]
            assert len(uow.priors.list_for_customer("CUST-ORD", limit=2)) == 2
            assert uow.priors.list_for_customer("NOBODY") == []


@pytest.mark.unit
class TestDamageEvidenceFraudRepositories:
    def _estimate(self) -> DamageEstimate:
        return DamageEstimate(
            source="shop",
            estimate_date=date(2026, 9, 15),
            line_items=[
                DamageLineItem(
                    description="Bumper",
                    quantity=2,
                    unit_cost=Money(amount=Decimal("100.00")),
                    total_cost=Money(amount=Decimal("200.00")),
                )
            ],
            total_cost=Money(amount=Decimal("200.00")),
        )

    def test_estimate_round_trip(self, tmp_path: Path) -> None:
        estimate = self._estimate()
        with _uow(tmp_path) as uow:
            uow.estimates.save(estimate, "SYN-X-1", "EST-X-1")
        with _uow(tmp_path) as uow:
            assert uow.estimates.get("EST-X-1") == estimate
            assert uow.estimates.list_for_claim("SYN-X-1") == ["EST-X-1"]

    def test_evidence_round_trip(self, tmp_path: Path) -> None:
        from uuid import uuid4 as new_id

        from casefile.models.domain import EvidenceItem, EvidenceSourceType

        item = EvidenceItem(
            evidence_id=new_id(),
            source="SYN-DOC-1",
            source_type=EvidenceSourceType.DOCUMENT,
            claim_ref="SYN-X-1",
            confidence=0.9,
            value="bumper damage",
            provenance="fixture:test",
        )
        with _uow(tmp_path) as uow:
            uow.evidence.save(item)
        with _uow(tmp_path) as uow:
            assert uow.evidence.get(item.evidence_id) == item
            assert len(uow.evidence.list_for_claim("SYN-X-1")) == 1

    def test_fraud_replace_semantics(self, tmp_path: Path) -> None:
        first = FraudIndicator(indicator_type="A", description="first", severity=FraudSeverity.LOW)
        second = FraudIndicator(
            indicator_type="B", description="second", severity=FraudSeverity.HIGH
        )
        with _uow(tmp_path) as uow:
            uow.fraud.save_all("SYN-X-1", [first])
            uow.fraud.save_all("SYN-X-1", [second])
        with _uow(tmp_path) as uow:
            listed = uow.fraud.list_for_claim("SYN-X-1")
            assert [i.indicator_type for i in listed] == ["B"]


@pytest.mark.unit
class TestRunExecutionAuditApprovalBudget:
    def test_run_round_trip(self, tmp_path: Path) -> None:
        run = WorkflowRun(claim_id=uuid4(), current_state=WorkflowState.EXTRACTION)
        with _uow(tmp_path) as uow:
            uow.runs.save(run)
        with _uow(tmp_path) as uow:
            assert uow.runs.get(run.workflow_run_id) == run

    def test_execution_record_no_secrets(self, tmp_path: Path) -> None:
        from datetime import datetime

        from casefile.agents.results import AgentExecutionRecord

        record = AgentExecutionRecord(
            execution_id=uuid4(),
            agent=AgentType.EXTRACTOR,
            started_at=datetime.now(UTC),
            input_correlation_id=uuid4(),
            output_contract_type="extraction_result",
            provider_name="deterministic",
            model_name="test-model",
            attempts=1,
        )
        run_id, claim_id = uuid4(), uuid4()
        with _uow(tmp_path) as uow:
            uow.executions.save(record, "extract", claim_id=claim_id, workflow_run_id=run_id)
        with _uow(tmp_path) as uow:
            listed = uow.executions.list_for_run(run_id)
            assert len(listed) == 1
            assert listed[0].provider_name == "deterministic"
            dumped = str(listed[0].__dict__)
            assert "sk-" not in dumped

    def test_audit_append_and_order(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime, timedelta

        run_id, claim_id = uuid4(), uuid4()
        base = datetime.now(UTC)
        first = AuditEvent(
            workflow_run_id=run_id,
            claim_id=claim_id,
            trace_id="t",
            event_type=AuditEventType.WORKFLOW_STARTED,
            timestamp=base,
            actor_type=ActorType.SYSTEM,
            actor_id="sys",
            action="start",
        )
        second = AuditEvent(
            workflow_run_id=run_id,
            claim_id=claim_id,
            trace_id="t",
            event_type=AuditEventType.STATE_TRANSITION,
            timestamp=base + timedelta(seconds=1),
            actor_type=ActorType.AGENT,
            actor_id="supervisor",
            action="transition",
            from_state=WorkflowState.RECEIVED,
            to_state=WorkflowState.EXTRACTION,
        )
        with _uow(tmp_path) as uow:
            uow.audit.append(first)
            uow.audit.append(second)
        with _uow(tmp_path) as uow:
            history = uow.audit.list_for_run(run_id)
            assert [e.event_type for e in history] == [
                AuditEventType.WORKFLOW_STARTED,
                AuditEventType.STATE_TRANSITION,
            ]
            assert uow.audit.list_for_claim(claim_id)[0].event_id == first.event_id

    def test_approval_request_and_decision(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        request = HumanApprovalRequest(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            recommendation=Recommendation(
                claim_id=uuid4(),
                recommendation_type=RecommendationType.APPROVE_FULL,
                estimated_payout=Money(amount=Decimal("100.00")),
                notes="ok",
                confidence=0.9,
            ),
            reviewer_summary="ready",
            deadline=now + timedelta(hours=24),
        )
        with _uow(tmp_path) as uow:
            uow.approvals.save_request(request)
            assert uow.approvals.recommendation_json(request.approval_id) is not None
            decision = HumanApproval(
                approval_id=request.approval_id,
                workflow_run_id=request.workflow_run_id,
                claim_id=request.claim_id,
                status=ApprovalStatus.APPROVED,
                approver_id="u-1",
                approver_name="Approver",
                approver_role="ADJUSTER",
                requested_at=request.requested_at,
                decided_at=now + timedelta(minutes=5),
            )
            uow.approvals.save_decision(decision)
        with _uow(tmp_path) as uow:
            loaded = uow.approvals.get(request.approval_id)
            assert loaded.status == ApprovalStatus.APPROVED
            assert loaded.approver_id == "u-1"

    def test_budget_snapshot_round_trip(self, tmp_path: Path) -> None:
        run_id = uuid4()
        budget = BudgetState(input_tokens_used=100, output_tokens_used=50)
        budget.total_tokens_used = 150
        with _uow(tmp_path) as uow:
            snapshot_id = uow.budgets.save_snapshot(budget, run_id)
            assert snapshot_id != ""
        with _uow(tmp_path) as uow:
            latest = uow.budgets.latest_for_run(run_id)
            assert latest is not None
            assert latest.total_tokens_used == 150
            assert latest.estimated_cost_usd == Decimal("0.00")
            assert uow.budgets.latest_for_run(uuid4()) is None


@pytest.mark.unit
class TestMoneyPrecision:
    def test_decimal_exactness(self, tmp_path: Path) -> None:
        claim = _claim()
        with _uow(tmp_path) as uow:
            uow.claims.save(claim)
        with _uow(tmp_path) as uow:
            loaded = uow.claims.get(claim.claim_id)
            assert isinstance(loaded.claim_amount.amount, Decimal)
            assert loaded.claim_amount.amount == Decimal("1234.56")
            assert loaded.claim_amount.currency == "USD"
