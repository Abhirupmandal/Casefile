"""
Unit Tests: ApprovalService — requests, decisions, races, idempotency,
stale versions, expiry, cancellation, terminal guards (Phase 9 §7–§12,
§20 partial).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from casefile.approval.model import (
    ApprovalDecision,
    ApprovalError,
    ApprovalErrorCode,
    ApprovalOutcome,
    ApprovalVerdict,
    CancelApproval,
    DecisionResult,
    ExpireApproval,
    HumanActor,
)
from casefile.approval.service import ApprovalService
from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    ApprovalStatus,
    AuditEvent,
    HumanApprovalRequest,
    Recommendation,
    RecommendationType,
)
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.workflow.context import FixedClock


def _service(tmp_path: Path, name: str = "approval.db") -> ApprovalService:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    clock = FixedClock(datetime.now(UTC))
    return ApprovalService(get_session_factory(engine), clock)


def _actor(actor_id: str = "human-1") -> HumanActor:
    return HumanActor(actor_id=actor_id, display_name=f"Human {actor_id}")


# Fixed past epoch: service clocks (real now) always postdate it, so
# decided_at can never precede requested_at regardless of host granularity.
_REQUEST_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _request(run_id: UUID | None = None, now: datetime | None = None) -> HumanApprovalRequest:
    from casefile.models.domain import Money

    moment = now or _REQUEST_EPOCH
    return HumanApprovalRequest(
        workflow_run_id=run_id or uuid4(),
        claim_id=uuid4(),
        recommendation=Recommendation(
            claim_id=uuid4(),
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("50.00")),
            notes="ok",
            confidence=0.9,
        ),
        reviewer_summary="ready",
        requested_at=moment,
        deadline=moment + timedelta(hours=24),
    )


def _decide(
    service: ApprovalService,
    approval_id: UUID,
    verdict: ApprovalVerdict,
    key: str,
    version: int = 1,
    actor: HumanActor | None = None,
) -> DecisionResult:
    return service.decide(
        ApprovalDecision(
            approval_id=approval_id,
            actor=actor or _actor(),
            verdict=verdict,
            reason="test decision",
            decision_key=key,
            expected_version=version,
        ),
        WorkflowState.HUMAN_APPROVAL,
    )


@pytest.mark.unit
class TestApprovalRequests:
    def test_create_pending(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        request = _request()
        approval = service.request_approval(request, WorkflowState.HUMAN_APPROVAL)
        assert approval.status == ApprovalStatus.PENDING
        assert approval.request_version == 1
        assert approval.approval_id == request.approval_id

    def test_duplicate_request_returns_existing(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        request = _request()
        first = service.request_approval(request, WorkflowState.HUMAN_APPROVAL)
        second = service.request_approval(request, WorkflowState.HUMAN_APPROVAL)
        assert first.approval_id == second.approval_id
        assert len(service.list_for_run(request.workflow_run_id)) == 1

    def test_terminal_run_rejects_request(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        with pytest.raises(ApprovalError) as exc_info:
            service.request_approval(_request(), WorkflowState.APPROVED)
        assert exc_info.value.code == ApprovalErrorCode.TERMINAL_RUN


@pytest.mark.unit
class TestApprovalDecisions:
    def test_approve(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        result = _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "k-1")
        assert result.outcome == ApprovalOutcome.DECIDED
        assert result.approval.status == ApprovalStatus.APPROVED
        assert result.approval.approver_id == "human-1"
        assert result.approval.decided_at is not None

    def test_reject(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        result = _decide(service, approval.approval_id, ApprovalVerdict.REJECT, "k-1")
        assert result.approval.status == ApprovalStatus.REJECTED

    def test_terminal_decision_immutable(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "k-1")
        with pytest.raises(ApprovalError) as exc_info:
            _decide(service, approval.approval_id, ApprovalVerdict.REJECT, "k-2")
        assert exc_info.value.code == ApprovalErrorCode.CONFLICT

    def test_unknown_approval(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        with pytest.raises(ApprovalError) as exc_info:
            _decide(service, uuid4(), ApprovalVerdict.APPROVE, "k-1")
        assert exc_info.value.code == ApprovalErrorCode.NOT_FOUND

    def test_stale_version_rejected(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        with pytest.raises(ApprovalError) as exc_info:
            _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "k-1", version=99)
        assert exc_info.value.code == ApprovalErrorCode.STALE_VERSION
        assert service.get(approval.approval_id).status == ApprovalStatus.PENDING

    def test_terminal_run_cannot_be_revived(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        with pytest.raises(ApprovalError) as exc_info:
            service.decide(
                ApprovalDecision(
                    approval_id=approval.approval_id,
                    actor=_actor(),
                    verdict=ApprovalVerdict.APPROVE,
                    decision_key="k-1",
                    expected_version=1,
                ),
                WorkflowState.APPROVED,
            )
        assert exc_info.value.code == ApprovalErrorCode.TERMINAL_RUN


@pytest.mark.unit
class TestApprovalIdempotency:
    def test_same_key_same_verdict_replays(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        first = _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "same-key")
        second = _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "same-key")
        assert first.outcome == ApprovalOutcome.DECIDED
        assert second.outcome == ApprovalOutcome.DUPLICATE
        assert second.approval.status == ApprovalStatus.APPROVED
        events = _audit_events(service, approval.workflow_run_id)
        decisions = [event for event in events if event.action == "approval_approved"]
        assert len(decisions) == 1

    def test_same_key_different_verdict_conflicts(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "same-key")
        with pytest.raises(ApprovalError) as exc_info:
            _decide(service, approval.approval_id, ApprovalVerdict.REJECT, "same-key")
        assert exc_info.value.code == ApprovalErrorCode.CONFLICT

    def test_concurrent_race_single_winner(self, tmp_path: Path) -> None:
        import threading

        service = _service(tmp_path, "race.db")
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        outcomes: list[str] = []

        def worker(verdict: ApprovalVerdict, key: str) -> None:
            try:
                result = _decide(service, approval.approval_id, verdict, key)
                outcomes.append(result.outcome.value)
            except ApprovalError as exc:
                outcomes.append(exc.code.value)

        threads = [
            threading.Thread(target=worker, args=(ApprovalVerdict.APPROVE, f"key-{i}"))
            for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes.count("DECIDED") == 1
        assert outcomes.count("CONFLICT") == 7
        final = service.get(approval.approval_id)
        assert final.status == ApprovalStatus.APPROVED


def _audit_events(service: ApprovalService, run_id: UUID) -> list[AuditEvent]:
    from casefile.storage.unit_of_work import UnitOfWork

    with UnitOfWork(service.sessions) as uow:
        return uow.audit.list_for_run(run_id)


@pytest.mark.unit
class TestExpiryCancellation:
    def test_expire_pending(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        result = service.expire(
            ExpireApproval(approval_id=approval.approval_id, actor=_actor(), reason="timed out")
        )
        assert result.outcome == ApprovalOutcome.DECIDED
        assert result.approval.status == ApprovalStatus.EXPIRED

    def test_expire_if_past_due(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        future = datetime.now(UTC) + timedelta(hours=48)
        assert service.expire_if_past_due(approval.approval_id, _actor(), future) is None
        assert service.get(approval.approval_id).status == ApprovalStatus.PENDING
        past = datetime.now(UTC) - timedelta(seconds=1)
        result = service.expire_if_past_due(approval.approval_id, _actor(), past)
        assert result is not None
        assert result.approval.status == ApprovalStatus.EXPIRED

    def test_expired_cannot_approve(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        service.expire(ExpireApproval(approval_id=approval.approval_id, actor=_actor()))
        with pytest.raises(ApprovalError) as exc_info:
            _decide(service, approval.approval_id, ApprovalVerdict.APPROVE, "late-key")
        assert exc_info.value.code == ApprovalErrorCode.CONFLICT

    def test_cancel_pending_stays_cancelled(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        approval = service.request_approval(_request(), WorkflowState.HUMAN_APPROVAL)
        result = service.cancel(
            CancelApproval(approval_id=approval.approval_id, actor=_actor(), reason="duplicate")
        )
        assert result.approval.status == ApprovalStatus.CANCELLED
        with pytest.raises(ApprovalError) as exc_info:
            _decide(service, approval.approval_id, ApprovalVerdict.REJECT, "k-after-cancel")
        assert exc_info.value.code == ApprovalErrorCode.CONFLICT
        assert service.get(approval.approval_id).status == ApprovalStatus.CANCELLED

    def test_cancel_missing(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        with pytest.raises(ApprovalError) as exc_info:
            service.cancel(CancelApproval(approval_id=uuid4(), actor=_actor()))
        assert exc_info.value.code == ApprovalErrorCode.NOT_FOUND
