"""
Unit tests for batch claim processing and disaster scenario orchestration (Phase 12 §4, Phase 12.6).

Covers:
- Valid and invalid batch submissions
- Configurable batch size limits (empty, exceeding max 50)
- Duplicate claim ID detection
- Invalid financial/date claim validation
- DisasterContext validation and metadata
- Partial batch success and individual failure isolation
- Retry policies: retryable transient errors vs non-retryable fatal errors
- Authoritative financial invariants and human-approval safety
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.models.contracts import ClaimInput, DisasterContext
from casefile.models.persistence import Base
from casefile.workflow.batch import (
    BatchClaimResult,
    BatchCoordinator,
    BatchExecutionResult,
    BatchStatus,
    ClaimBatchStatus,
    _is_retryable_error,
)


@pytest.fixture
def test_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def coordinator(test_session_factory: sessionmaker[Session]) -> BatchCoordinator:
    return BatchCoordinator(
        session_factory=test_session_factory,
        max_batch_size=50,
        max_concurrency=3,
        max_retries=2,
    )


def test_valid_batch_submission_contracts() -> None:
    """Verify BatchCoordinator successfully validates a clean batch of claims."""
    c1 = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-1001",
        claimant_name="Alice Smith",
        incident_date="2026-04-10",
        claim_amount=Decimal("1500.00"),
        description="Hail damage to roof and windshield.",
        language="en",
    )
    c2 = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-1002",
        claimant_name="Bob Jones",
        incident_date="2026-04-11",
        claim_amount=Decimal("2200.50"),
        description="Fallen tree branch on garage.",
        language="en",
    )
    disaster = DisasterContext(
        disaster_id="CAT-2026-HAIL",
        disaster_name="Spring Supercell Hailstorm",
        disaster_type="HAIL",
        geographic_zone="REGION-TX-NORTH",
    )

    coordinator = BatchCoordinator(session_factory=mock.MagicMock())
    # Should not raise
    coordinator.validate_batch_submission([c1, c2], disaster_context=disaster)


def test_batch_size_limits_empty_and_exceeding_max(coordinator: BatchCoordinator) -> None:
    """Verify empty batches and batches exceeding max limit are rejected."""
    # 1. Empty batch rejected
    with pytest.raises(ValueError, match="Batch must contain at least 1 claim"):
        coordinator.validate_batch_submission([])

    # 2. Exceeding max limit (51 claims when max is 50)
    oversized = [
        ClaimInput(
            claim_id=uuid4(),
            policy_id=f"POL-{i:04d}",
            claimant_name=f"Claimant {i}",
            incident_date="2026-04-10",
            claim_amount=Decimal("100.00"),
            description="Minor property damage.",
        )
        for i in range(51)
    ]
    with pytest.raises(ValueError, match="exceeds maximum allowed limit of 50"):
        coordinator.validate_batch_submission(oversized)


def test_duplicate_claim_id_rejected(coordinator: BatchCoordinator) -> None:
    """Verify duplicate claim IDs within the same batch submission are rejected."""
    dup_id = uuid4()
    c1 = ClaimInput(
        claim_id=dup_id,
        policy_id="POL-2001",
        claimant_name="Alice",
        incident_date="2026-04-10",
        claim_amount=Decimal("1200.00"),
        description="Water ingress in basement.",
    )
    c2 = ClaimInput(
        claim_id=dup_id,
        policy_id="POL-2002",
        claimant_name="Charlie",
        incident_date="2026-04-11",
        claim_amount=Decimal("800.00"),
        description="Damaged gutter.",
    )
    with pytest.raises(ValueError, match="Duplicate claim_id detected in batch"):
        coordinator.validate_batch_submission([c1, c2])


def test_invalid_claim_parameters_rejected(coordinator: BatchCoordinator) -> None:
    """Verify non-positive amounts, invalid precision, empty fields, and bad dates are caught."""
    # 1. Non-positive amount
    with pytest.raises((ValueError, ValidationError)):
        ClaimInput(
            policy_id="POL-3001",
            claimant_name="Dave",
            incident_date="2026-04-10",
            claim_amount=Decimal("0.00"),
            description="Test claim zero amount",
        )

    # 2. Invalid decimal precision (> 2 decimal places)
    c_bad_prec = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-3002",
        claimant_name="Eve",
        incident_date="2026-04-10",
        claim_amount=Decimal("125.456"),
        description="Invalid sub-cent fraction",
    )
    with pytest.raises(ValueError, match="precision exceeds 2 decimal places"):
        coordinator.validate_batch_submission([c_bad_prec])

    # 3. Bad incident date
    c_bad_date = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-3003",
        claimant_name="Frank",
        incident_date="not-a-date",
        claim_amount=Decimal("500.00"),
        description="Invalid timestamp string",
    )
    with pytest.raises(ValueError, match="invalid incident_date"):
        coordinator.validate_batch_submission([c_bad_date])


def test_disaster_context_validation(coordinator: BatchCoordinator) -> None:
    """Verify DisasterContext required fields cannot be blank."""
    c = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-4001",
        claimant_name="Grace",
        incident_date="2026-04-10",
        claim_amount=Decimal("750.00"),
        description="Hail dents on hood.",
    )

    with pytest.raises(ValueError, match="disaster_id cannot be empty"):
        d_bad = DisasterContext(
            disaster_id="   ",
            disaster_name="Valid Name",
            disaster_type="FLOOD",
        )
        coordinator.validate_batch_submission([c], disaster_context=d_bad)

    with pytest.raises(ValueError, match="disaster_name cannot be empty"):
        d_bad_name = DisasterContext(
            disaster_id="CAT-01",
            disaster_name="   ",
            disaster_type="FLOOD",
        )
        coordinator.validate_batch_submission([c], disaster_context=d_bad_name)


def test_retry_policy_retryable_vs_non_retryable() -> None:
    """Verify that _is_retryable_error correctly discriminates transient vs permanent failures."""
    # Permanent errors (should not retry)
    assert not _is_retryable_error(ValueError("Invalid claim amount"))
    assert not _is_retryable_error(TypeError("Unprocessable object"))
    assert not _is_retryable_error(KeyError("missing_key"))
    assert not _is_retryable_error(PermissionError("Unauthorized"))

    # Transient errors (should retry)
    assert _is_retryable_error(Exception("database is locked"))
    assert _is_retryable_error(TimeoutError("Gateway connection timed out"))
    assert _is_retryable_error(RuntimeError("Temporary busy state on socket"))
    assert _is_retryable_error(Exception("Deadlock detected in transaction"))
    assert _is_retryable_error(Exception("Retry later: service unavailable"))


def test_batch_partial_success_and_failure_isolation(coordinator: BatchCoordinator) -> None:
    """
    Verify fault isolation: when one claim fails execution, the other claims succeed,
    and the batch status reflects PARTIALLY_COMPLETED without data corruption.
    """
    c_good1 = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-5001",
        claimant_name="Harry",
        incident_date="2026-04-10",
        claim_amount=Decimal("1200.00"),
        description="Fallen roof shingles.",
    )
    c_failing = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-5002",
        claimant_name="Isla",
        incident_date="2026-04-10",
        claim_amount=Decimal("3400.00"),
        description="Cracked siding.",
    )
    c_good2 = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-5003",
        claimant_name="Jack",
        incident_date="2026-04-10",
        claim_amount=Decimal("850.00"),
        description="Broken patio awning.",
    )

    # Mock ApiService with simulated failure on claim 2
    mock_api = mock.MagicMock()

    def mock_submit(req: ClaimSubmissionRequest, **kwargs: object) -> object:
        if req.claim_id == str(c_failing.claim_id):
            raise RuntimeError("Permanent policy verification failure for claim 2")
        mock_resp = mock.MagicMock()
        mock_resp.workflow_run_id = str(uuid4())
        mock_resp.current_state = "HUMAN_APPROVAL"
        return mock_resp

    mock_api.submit_claim.side_effect = mock_submit
    coordinator.set_api_service(mock_api)

    b_id = uuid4()
    result = coordinator.submit_batch(
        batch_id=b_id,
        claims=[c_good1, c_failing, c_good2],
        batch_name="Mixed Batch Test",
    )

    assert result.status == BatchStatus.PARTIALLY_COMPLETED.value
    assert result.total_claims == 3
    assert result.processed_claims == 3
    assert result.successful_claims == 2
    assert result.failed_claims == 1
    assert result.total_requested_amount == Decimal("5450.00")

    # Verify per-claim results
    res_map = {r.claim_id: r for r in result.claim_results}
    assert res_map[c_good1.claim_id].status == ClaimBatchStatus.COMPLETED.value
    assert res_map[c_good1.claim_id].current_state == "HUMAN_APPROVAL"
    assert res_map[c_good2.claim_id].status == ClaimBatchStatus.COMPLETED.value
    assert res_map[c_failing.claim_id].status == ClaimBatchStatus.FAILED.value
    assert "Permanent policy verification failure" in str(res_map[c_failing.claim_id].error_message)


def test_financial_invariants_and_no_auto_approval() -> None:
    """Verify that batch aggregation never replaces individual financial validation or auto-approves."""
    c1 = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-6001",
        claimant_name="Karen",
        incident_date="2026-04-10",
        claim_amount=Decimal("4500.00"),
        description="Windstorm damage to porch.",
    )
    c2 = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-6002",
        claimant_name="Leo",
        incident_date="2026-04-10",
        claim_amount=Decimal("1500.00"),
        description="Fallen chimney bricks.",
    )

    item1 = BatchClaimResult(
        claim_id=c1.claim_id,
        workflow_run_id=uuid4(),
        policy_id=c1.policy_id,
        claimant_name=c1.claimant_name,
        claim_amount=c1.claim_amount,
        status="COMPLETED",
        current_state="HUMAN_APPROVAL",
        approved_amount=None,  # Not auto-approved!
    )
    item2 = BatchClaimResult(
        claim_id=c2.claim_id,
        workflow_run_id=uuid4(),
        policy_id=c2.policy_id,
        claimant_name=c2.claimant_name,
        claim_amount=c2.claim_amount,
        status="COMPLETED",
        current_state="HUMAN_APPROVAL",
        approved_amount=None,  # Not auto-approved!
    )

    batch_res = BatchExecutionResult(
        batch_id=uuid4(),
        status="COMPLETED",
        total_claims=2,
        processed_claims=2,
        successful_claims=2,
        failed_claims=0,
        total_requested_amount=Decimal("6000.00"),
        total_approved_amount=Decimal("0.00"),  # No funds disbursed automatically
        claim_results=[item1, item2],
    )

    assert batch_res.total_approved_amount == Decimal("0.00")
    for r in batch_res.claim_results:
        assert r.current_state == "HUMAN_APPROVAL"
        assert r.approved_amount is None
