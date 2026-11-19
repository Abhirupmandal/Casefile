"""
Batch claim processing and disaster scenario orchestration (Phase 12 §4, Phase 12.6).

Provides:
- Batch intake with configurable size limits (default 50) and validation.
- Shared disaster/catastrophe context propagation without compromising individual claim budgets.
- Bounded concurrency with resource protection.
- Per-claim fault isolation: one invalid or failed claim does not crash the batch.
- Transient error retry policies with non-retryable failure discrimination.
- Durable persistence of batch identities, progress, and per-claim results.
- Resuming and retrying failed claims independently.
- Claimant tenant isolation and role-based access control.
- Strict financial invariants: authoritative claim amounts, no auto-approval, human approval mandatory.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
from contextlib import suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from casefile.models.contracts import (
    ClaimInput,
    DisasterContext,
)
from casefile.models.persistence import (
    AuditEventRecord,
    BatchRecord,
    ClaimRecord,
    HumanApprovalRecord,
    WorkflowRunRecord,
)
from casefile.observability.spans import batch_attributes
from casefile.storage.errors import PersistenceError, PersistenceErrorCode
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import Clock, SystemClock

if TYPE_CHECKING:
    from casefile.api.auth import Principal
    from casefile.api.service import ApiService

logger = logging.getLogger(__name__)

DEFAULT_MAX_BATCH_SIZE: int = 50
DEFAULT_MAX_CONCURRENCY: int = 5
DEFAULT_MAX_RETRIES_PER_CLAIM: int = 2


class BatchStatus(str, Enum):
    """Lifecycle status of a claim batch execution."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


class ClaimBatchStatus(str, Enum):
    """Execution status of an individual claim within a batch."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchClaimResult(BaseModel):
    """Itemized execution result for an individual claim within a batch."""

    claim_id: UUID
    workflow_run_id: UUID | None = None
    policy_id: str
    claimant_name: str
    claim_amount: Decimal
    status: str = "PENDING"
    current_state: str | None = None
    approved_amount: Decimal | None = None
    error_message: str | None = None
    retry_count: int = 0
    duration_seconds: float = 0.0
    schema_version: str = "1.0.0"


class BatchExecutionResult(BaseModel):
    """Aggregate execution summary and itemized results for a batch."""

    batch_id: UUID
    batch_name: str = ""
    status: str = "PENDING"
    disaster_context: DisasterContext | None = None
    total_claims: int = 0
    processed_claims: int = 0
    successful_claims: int = 0
    failed_claims: int = 0
    total_requested_amount: Decimal = Decimal("0.00")
    total_approved_amount: Decimal = Decimal("0.00")
    claim_results: list[BatchClaimResult] = Field(default_factory=list)
    submitted_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    schema_version: str = "1.0.0"


def _is_retryable_error(exc: Exception) -> bool:
    """Determine whether an execution failure is transient and safe to retry."""
    if isinstance(exc, ValueError | TypeError | KeyError | AttributeError | PermissionError):
        return False
    msg = str(exc).lower()
    return any(
        term in msg
        for term in ("locked", "timeout", "connection", "busy", "temporary", "deadlock", "retry")
    )


class BatchCoordinator:
    """Orchestrates intake, bounded concurrent execution, failure isolation, and tracking of claim batches."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        api_service: ApiService | None = None,
        clock: Clock | None = None,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_retries: int = DEFAULT_MAX_RETRIES_PER_CLAIM,
    ) -> None:
        self._session_factory = session_factory
        self._api_service = api_service
        self._clock = clock or SystemClock()
        self._max_batch_size = max_batch_size
        self._max_concurrency = max(1, max_concurrency)
        self._max_retries = max(0, max_retries)

    def set_api_service(self, api_service: ApiService) -> None:
        """Bind or update ApiService instance."""
        self._api_service = api_service

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    def validate_batch_submission(
        self,
        claims: list[ClaimInput],
        disaster_context: DisasterContext | None = None,
    ) -> None:
        """Validate batch submission parameters before processing."""
        if not claims:
            raise ValueError("Batch must contain at least 1 claim")

        if len(claims) > self._max_batch_size:
            raise ValueError(
                f"Batch size {len(claims)} exceeds maximum allowed limit of {self._max_batch_size}"
            )

        seen_claim_ids: set[UUID] = set()
        for idx, c in enumerate(claims):
            if c.claim_id in seen_claim_ids:
                raise ValueError(f"Duplicate claim_id detected in batch: {c.claim_id}")
            seen_claim_ids.add(c.claim_id)

            if c.claim_amount <= Decimal("0.00"):
                raise ValueError(
                    f"Claim {idx} ({c.claim_id}) has invalid claim_amount: {c.claim_amount}. Amount must be positive."
                )

            # Decimal precision check
            exp = c.claim_amount.as_tuple().exponent
            if isinstance(exp, int) and exp < -2:
                raise ValueError(
                    f"Claim {idx} ({c.claim_id}) precision exceeds 2 decimal places: {c.claim_amount}"
                )

            if not c.policy_id.strip():
                raise ValueError(f"Claim {idx} ({c.claim_id}) has empty policy_id")

            if not c.claimant_name.strip():
                raise ValueError(f"Claim {idx} ({c.claim_id}) has empty claimant_name")

            if not c.description.strip():
                raise ValueError(f"Claim {idx} ({c.claim_id}) has empty description")

            try:
                date.fromisoformat(c.incident_date)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Claim {idx} ({c.claim_id}) has invalid incident_date: {c.incident_date}"
                ) from exc

        if disaster_context is not None:
            if not disaster_context.disaster_id.strip():
                raise ValueError("DisasterContext.disaster_id cannot be empty")
            if not disaster_context.disaster_name.strip():
                raise ValueError("DisasterContext.disaster_name cannot be empty")
            if not disaster_context.disaster_type.strip():
                raise ValueError("DisasterContext.disaster_type cannot be empty")

    def submit_batch(
        self,
        batch_id: UUID,
        claims: list[ClaimInput],
        batch_name: str = "",
        disaster_context: DisasterContext | None = None,
        submitted_by: str = "system",
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> BatchExecutionResult:
        """
        Validate, enroll, and execute a batch of claims with bounded concurrency and fault isolation.
        """
        corr_id = correlation_id or uuid4().hex
        self.validate_batch_submission(claims, disaster_context=disaster_context)

        # Enforce batch_id propagation
        for c in claims:
            c.batch_id = batch_id

        # Check existing batch record for idempotency
        with self._uow() as uow:
            existing = uow.batches.exists(batch_id)
            if existing:
                row = uow.batches.get(batch_id)
                # If already completed or processing, return existing status
                return self._build_result_from_record(row)

            now = datetime.now(UTC)
            shared_payload: dict[str, Any] = {
                "disaster_context": (
                    disaster_context.model_dump(mode="json")
                    if disaster_context is not None
                    else None
                ),
                "idempotency_key": idempotency_key,
                "execution_summary": {},
                "claims_input": {str(c.claim_id): c.model_dump(mode="json") for c in claims},
            }
            batch_record = BatchRecord(
                batch_id=str(batch_id),
                batch_name=batch_name
                or (
                    f"Batch-{batch_id.hex[:8]}"
                    if not disaster_context
                    else f"{disaster_context.disaster_name} Batch"
                ),
                status=BatchStatus.PROCESSING.value,
                disaster_id=disaster_context.disaster_id if disaster_context else None,
                disaster_type=disaster_context.disaster_type if disaster_context else None,
                disaster_name=disaster_context.disaster_name if disaster_context else None,
                shared_context_json=json.dumps(shared_payload),
                total_claims=len(claims),
                submitted_by=submitted_by,
                created_at=now,
                updated_at=now,
            )
            uow.batches.save(batch_record)

            # Record audit event for batch intake
            audit_evt = AuditEventRecord(
                event_id=str(uuid4()),
                workflow_run_id=str(batch_id),
                claim_id=str(batch_id),
                trace_id=corr_id,
                event_type="WORKFLOW_BATCH_SUBMITTED",
                timestamp=now,
                actor_type="SYSTEM",
                actor_id=submitted_by,
                action="submit_batch",
                summary=f"Batch {batch_id} submitted with {len(claims)} claims"
                + (
                    f" under catastrophe {disaster_context.disaster_id}" if disaster_context else ""
                ),
                attributes=batch_attributes(
                    batch_id=batch_id,
                    total_claims=len(claims),
                    disaster_id=disaster_context.disaster_id if disaster_context else None,
                    disaster_type=disaster_context.disaster_type if disaster_context else None,
                    status=BatchStatus.PROCESSING.value,
                ),
                result="SUCCESS",
                correlation_id=corr_id,
            )
            uow.session.add(audit_evt)

        # Execute claims with bounded concurrency and fault isolation
        claim_results = self._execute_claims_pool(
            claims=claims,
            disaster_context=disaster_context,
            batch_id=batch_id,
            correlation_id=corr_id,
        )

        # Aggregate metrics
        successful = sum(1 for r in claim_results if r.status == ClaimBatchStatus.COMPLETED.value)
        failed = sum(1 for r in claim_results if r.status == ClaimBatchStatus.FAILED.value)
        total_requested = sum((c.claim_amount for c in claims), Decimal("0.00"))
        total_approved = sum(
            (r.approved_amount or Decimal("0.00") for r in claim_results), Decimal("0.00")
        )

        final_status = (
            BatchStatus.COMPLETED.value
            if failed == 0
            else (
                BatchStatus.FAILED.value
                if successful == 0
                else BatchStatus.PARTIALLY_COMPLETED.value
            )
        )

        completed_at = datetime.now(UTC)
        with self._uow() as uow:
            row = uow.batches.get(batch_id)
            row.status = final_status
            row.updated_at = completed_at

            summary_map = {
                str(r.claim_id): {
                    "workflow_run_id": str(r.workflow_run_id) if r.workflow_run_id else None,
                    "status": r.status,
                    "current_state": r.current_state,
                    "approved_amount": (
                        str(r.approved_amount) if r.approved_amount is not None else None
                    ),
                    "error_message": r.error_message,
                    "retry_count": r.retry_count,
                    "duration_seconds": r.duration_seconds,
                }
                for r in claim_results
            }
            shared_payload["execution_summary"] = summary_map
            shared_payload["completed_at"] = completed_at.isoformat()
            row.shared_context_json = json.dumps(shared_payload)
            uow.batches.save(row)

            # Record audit event for completion
            audit_evt = AuditEventRecord(
                event_id=str(uuid4()),
                workflow_run_id=str(batch_id),
                claim_id=str(batch_id),
                trace_id=corr_id,
                event_type="WORKFLOW_BATCH_COMPLETED",
                timestamp=completed_at,
                actor_type="SYSTEM",
                actor_id=submitted_by,
                action="complete_batch",
                summary=f"Batch {batch_id} execution finished with status {final_status}: {successful} succeeded, {failed} failed.",
                attributes=batch_attributes(
                    batch_id=batch_id,
                    total_claims=len(claims),
                    disaster_id=disaster_context.disaster_id if disaster_context else None,
                    disaster_type=disaster_context.disaster_type if disaster_context else None,
                    status=final_status,
                    processed_claims=len(claim_results),
                    successful_claims=successful,
                    failed_claims=failed,
                ),
                result="SUCCESS" if failed == 0 else ("PARTIAL" if successful > 0 else "FAILURE"),
                correlation_id=corr_id,
            )
            uow.session.add(audit_evt)

        return BatchExecutionResult(
            batch_id=batch_id,
            batch_name=batch_name,
            status=final_status,
            disaster_context=disaster_context,
            total_claims=len(claims),
            processed_claims=len(claim_results),
            successful_claims=successful,
            failed_claims=failed,
            total_requested_amount=total_requested,
            total_approved_amount=total_approved,
            claim_results=claim_results,
            submitted_by=submitted_by,
            created_at=now,
            updated_at=completed_at,
            completed_at=completed_at,
        )

    def _execute_claims_pool(
        self,
        claims: list[ClaimInput],
        disaster_context: DisasterContext | None,
        batch_id: UUID,
        correlation_id: str,
    ) -> list[BatchClaimResult]:
        """Execute claims in a bounded worker pool with individual failure isolation."""
        claim_results: list[BatchClaimResult] = []

        def _worker(c: ClaimInput) -> BatchClaimResult:
            return self._execute_single_claim(
                c, disaster_context=disaster_context, correlation_id=correlation_id
            )

        workers = min(self._max_concurrency, len(claims))
        if workers <= 1:
            for c in claims:
                claim_results.append(_worker(c))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_claim = {executor.submit(_worker, c): c for c in claims}
                for future in concurrent.futures.as_completed(future_to_claim):
                    c = future_to_claim[future]
                    try:
                        res = future.result()
                        claim_results.append(res)
                    except Exception as exc:
                        # Defensive fallback: if future.result() threw, isolate failure
                        claim_results.append(
                            BatchClaimResult(
                                claim_id=c.claim_id,
                                workflow_run_id=None,
                                policy_id=c.policy_id,
                                claimant_name=c.claimant_name,
                                claim_amount=c.claim_amount,
                                status=ClaimBatchStatus.FAILED.value,
                                current_state="FAILED",
                                error_message=f"Thread execution crashed: {exc}",
                                retry_count=0,
                                duration_seconds=0.0,
                            )
                        )

        # Preserve deterministic order matching original claims list
        res_by_id = {r.claim_id: r for r in claim_results}
        ordered: list[BatchClaimResult] = [
            res_by_id.get(
                c.claim_id,
                BatchClaimResult(
                    claim_id=c.claim_id,
                    policy_id=c.policy_id,
                    claimant_name=c.claimant_name,
                    claim_amount=c.claim_amount,
                    status=ClaimBatchStatus.FAILED.value,
                    error_message="Missing claim result from worker pool",
                ),
            )
            for c in claims
        ]
        return ordered

    def _execute_single_claim(
        self,
        claim: ClaimInput,
        disaster_context: DisasterContext | None,
        correlation_id: str,
    ) -> BatchClaimResult:
        """Execute one claim with retry logic and strict fault isolation."""
        t_start = datetime.now(UTC)
        retries = 0
        last_error: str | None = None

        if self._api_service is None:
            return BatchClaimResult(
                claim_id=claim.claim_id,
                policy_id=claim.policy_id,
                claimant_name=claim.claimant_name,
                claim_amount=claim.claim_amount,
                status=ClaimBatchStatus.FAILED.value,
                error_message="ApiService not bound to BatchCoordinator",
            )

        from casefile.api.schemas.models import ClaimSubmissionRequest

        sub_req = ClaimSubmissionRequest(
            policy_id=claim.policy_id,
            claimant_name=claim.claimant_name,
            incident_date=claim.incident_date,
            claim_amount=claim.claim_amount,
            description=claim.description,
            documents=claim.documents,
            claim_id=str(claim.claim_id),
            language=claim.language,
            batch_id=str(claim.batch_id) if claim.batch_id else None,
        )

        for attempt in range(1 + self._max_retries):
            retries = attempt
            try:
                sub_resp = self._api_service.submit_claim(
                    sub_req,
                    correlation_id=correlation_id,
                    disaster_context=disaster_context,
                )
                w_run_id = UUID(sub_resp.workflow_run_id)

                # Query human approval to see if partial/full approved_amount was set
                approved_amt: Decimal | None = None
                with suppress(Exception), self._uow() as uow:
                    appr_row = (
                        uow.session.query(HumanApprovalRecord)
                        .filter_by(workflow_run_id=str(w_run_id))
                        .first()
                    )
                    if (
                        appr_row is not None
                        and getattr(appr_row, "approved_amount", None) is not None
                    ):
                        approved_amt = Decimal(str(appr_row.approved_amount))

                t_dur = (datetime.now(UTC) - t_start).total_seconds()
                return BatchClaimResult(
                    claim_id=claim.claim_id,
                    workflow_run_id=w_run_id,
                    policy_id=claim.policy_id,
                    claimant_name=claim.claimant_name,
                    claim_amount=claim.claim_amount,
                    status=ClaimBatchStatus.COMPLETED.value,
                    current_state=sub_resp.current_state,
                    approved_amount=approved_amt,
                    error_message=None,
                    retry_count=retries,
                    duration_seconds=round(t_dur, 3),
                )
            except Exception as exc:
                last_error = str(exc)
                if _is_retryable_error(exc) and attempt < self._max_retries:
                    logger.warning(
                        "Transient error executing claim %s (attempt %d/%d): %s. Retrying...",
                        claim.claim_id,
                        attempt + 1,
                        self._max_retries + 1,
                        exc,
                    )
                    continue
                # Non-retryable or retries exhausted: break out
                break

        t_dur = (datetime.now(UTC) - t_start).total_seconds()
        return BatchClaimResult(
            claim_id=claim.claim_id,
            workflow_run_id=None,
            policy_id=claim.policy_id,
            claimant_name=claim.claimant_name,
            claim_amount=claim.claim_amount,
            status=ClaimBatchStatus.FAILED.value,
            current_state="FAILED",
            approved_amount=None,
            error_message=last_error or "Unknown failure",
            retry_count=retries,
            duration_seconds=round(t_dur, 3),
        )

    def get_batch_status(
        self,
        batch_id: UUID,
        principal: Principal | None = None,
    ) -> BatchExecutionResult:
        """
        Retrieve batch status and per-claim execution results with tenant isolation.
        """
        with self._uow() as uow:
            if not uow.batches.exists(batch_id):
                raise PersistenceError(
                    PersistenceErrorCode.NOT_FOUND, f"Batch {batch_id} not found"
                )
            record = uow.batches.get(batch_id)
            res = self._build_result_from_record(record)

            # Tenant isolation enforcement for Role.CLAIMANT
            if principal is not None:
                from casefile.api.auth import Role

                if principal.role == Role.CLAIMANT:
                    # Find claims belonging to this claimant
                    allowed_claims: list[BatchClaimResult] = []
                    for cr in res.claim_results:
                        c_row = uow.session.get(ClaimRecord, str(cr.claim_id))
                        is_owner = False
                        if c_row is not None:
                            is_owner = (
                                c_row.customer_id == principal.principal_id
                                or c_row.submitted_by == principal.principal_id
                            )
                        if (
                            is_owner
                            or cr.claimant_name == principal.principal_id
                            or cr.claimant_name == principal.display_name
                        ):
                            allowed_claims.append(cr)

                    if not allowed_claims:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Tenant isolation violation: Claimant '{principal.principal_id}' is not authorized to access batch '{batch_id}'",
                        )

                    # Return claimant-sanitized view containing ONLY their claims and isolated totals
                    res.claim_results = allowed_claims
                    res.total_claims = len(allowed_claims)
                    res.processed_claims = len(allowed_claims)
                    res.successful_claims = sum(
                        1 for r in allowed_claims if r.status == ClaimBatchStatus.COMPLETED.value
                    )
                    res.failed_claims = sum(
                        1 for r in allowed_claims if r.status == ClaimBatchStatus.FAILED.value
                    )
                    res.total_requested_amount = sum(
                        (r.claim_amount for r in allowed_claims), Decimal("0.00")
                    )
                    res.total_approved_amount = sum(
                        (r.approved_amount or Decimal("0.00") for r in allowed_claims),
                        Decimal("0.00"),
                    )

            return res

    def get_batch_claims(
        self,
        batch_id: UUID,
        principal: Principal | None = None,
    ) -> list[BatchClaimResult]:
        """Retrieve itemized claim results for a batch."""
        res = self.get_batch_status(batch_id, principal=principal)
        return res.claim_results

    def get_batch_disaster(
        self,
        batch_id: UUID,
        principal: Principal | None = None,
    ) -> DisasterContext | None:
        """Retrieve catastrophe / disaster context associated with a batch."""
        res = self.get_batch_status(batch_id, principal=principal)
        return res.disaster_context

    def retry_failed_claims(
        self,
        batch_id: UUID,
        claim_ids: list[UUID] | None = None,
        correlation_id: str | None = None,
    ) -> BatchExecutionResult:
        """
        Retry execution of failed claims in a batch without re-running successful claims.
        """
        corr_id = correlation_id or uuid4().hex
        with self._uow() as uow:
            if not uow.batches.exists(batch_id):
                raise PersistenceError(
                    PersistenceErrorCode.NOT_FOUND, f"Batch {batch_id} not found"
                )
            record = uow.batches.get(batch_id)
            current_res = self._build_result_from_record(record)

            # Determine which claims to retry
            wanted_ids = set(claim_ids) if claim_ids is not None else None
            claims_to_retry: list[ClaimInput] = []

            cached_inputs: dict[str, Any] = {}
            if record.shared_context_json:
                with suppress(Exception):
                    cached_data = json.loads(record.shared_context_json)
                    if isinstance(cached_data, dict):
                        cached_inputs = cached_data.get("claims_input", {})

            for cr in current_res.claim_results:
                if cr.status == ClaimBatchStatus.FAILED.value and (
                    wanted_ids is None or cr.claim_id in wanted_ids
                ):
                    # Reconstruct ClaimInput from persisted ClaimRecord or cached inputs
                    c_row = uow.session.get(ClaimRecord, str(cr.claim_id))
                    if c_row is not None:
                        claims_to_retry.append(
                            ClaimInput(
                                claim_id=cr.claim_id,
                                policy_id=c_row.policy_number,
                                claimant_name=cr.claimant_name,
                                incident_date=c_row.incident_date,
                                claim_amount=cr.claim_amount,
                                description=c_row.description,
                                language=c_row.language,
                                batch_id=batch_id,
                            )
                        )
                    elif str(cr.claim_id) in cached_inputs:
                        claims_to_retry.append(ClaimInput(**cached_inputs[str(cr.claim_id)]))

            if not claims_to_retry:
                return current_res

        # Re-execute targeted claims
        retried_results = self._execute_claims_pool(
            claims=claims_to_retry,
            disaster_context=current_res.disaster_context,
            batch_id=batch_id,
            correlation_id=corr_id,
        )

        retried_map = {r.claim_id: r for r in retried_results}
        updated_claim_results: list[BatchClaimResult] = []
        for cr in current_res.claim_results:
            if cr.claim_id in retried_map:
                updated_claim_results.append(retried_map[cr.claim_id])
            else:
                updated_claim_results.append(cr)

        # Recompute totals and status
        successful = sum(
            1 for r in updated_claim_results if r.status == ClaimBatchStatus.COMPLETED.value
        )
        failed = sum(1 for r in updated_claim_results if r.status == ClaimBatchStatus.FAILED.value)
        final_status = (
            BatchStatus.COMPLETED.value
            if failed == 0
            else (
                BatchStatus.FAILED.value
                if successful == 0
                else BatchStatus.PARTIALLY_COMPLETED.value
            )
        )
        completed_at = datetime.now(UTC)

        with self._uow() as uow:
            row = uow.batches.get(batch_id)
            row.status = final_status
            row.updated_at = completed_at

            shared_data: dict[str, Any] = {}
            if row.shared_context_json:
                try:
                    shared_data = json.loads(row.shared_context_json)
                except Exception:
                    shared_data = {}

            summary_map = {
                str(r.claim_id): {
                    "workflow_run_id": str(r.workflow_run_id) if r.workflow_run_id else None,
                    "status": r.status,
                    "current_state": r.current_state,
                    "approved_amount": (
                        str(r.approved_amount) if r.approved_amount is not None else None
                    ),
                    "error_message": r.error_message,
                    "retry_count": r.retry_count,
                    "duration_seconds": r.duration_seconds,
                }
                for r in updated_claim_results
            }
            shared_data["execution_summary"] = summary_map
            shared_data["last_retried_at"] = completed_at.isoformat()
            row.shared_context_json = json.dumps(shared_data)
            uow.batches.save(row)

        current_res.status = final_status
        current_res.successful_claims = successful
        current_res.failed_claims = failed
        current_res.claim_results = updated_claim_results
        current_res.updated_at = completed_at
        current_res.total_approved_amount = sum(
            (r.approved_amount or Decimal("0.00") for r in updated_claim_results), Decimal("0.00")
        )
        return current_res

    def _build_result_from_record(self, row: BatchRecord) -> BatchExecutionResult:
        """Assemble BatchExecutionResult from persisted BatchRecord and related entities."""
        b_id = UUID(row.batch_id)
        d_ctx: DisasterContext | None = None
        exec_summary: dict[str, Any] = {}
        cached_inputs: dict[str, Any] = {}

        if row.shared_context_json:
            try:
                data = json.loads(row.shared_context_json)
                if isinstance(data, dict):
                    if data.get("disaster_context"):
                        d_ctx = DisasterContext(**data["disaster_context"])
                    exec_summary = data.get("execution_summary", {})
                    cached_inputs = data.get("claims_input", {})
            except Exception:
                d_ctx = None
                exec_summary = {}
                cached_inputs = {}

        if d_ctx is None and row.disaster_id:
            d_ctx = DisasterContext(
                disaster_id=row.disaster_id,
                disaster_name=row.disaster_name or row.disaster_id,
                disaster_type=row.disaster_type or "CATASTROPHE",
            )

        # Reconstruct claims and workflow runs from database
        claim_results: list[BatchClaimResult] = []
        with self._uow() as uow:
            claim_rows = (
                uow.session.query(ClaimRecord)
                .filter_by(batch_id=row.batch_id)
                .order_by(ClaimRecord.created_at.asc())
                .all()
            )

            for cr in claim_rows:
                c_uuid = UUID(cr.claim_id)
                item_summary = exec_summary.get(cr.claim_id, {})
                c_status = item_summary.get("status")
                err_msg = item_summary.get("error_message")
                retry_ct = int(item_summary.get("retry_count", 0))
                dur = float(item_summary.get("duration_seconds", 0.0))

                w_run_id_val = item_summary.get("workflow_run_id")
                w_run_id: UUID | None = UUID(w_run_id_val) if w_run_id_val else None

                # Find latest workflow run if not in summary
                latest_run = (
                    uow.session.query(WorkflowRunRecord)
                    .filter_by(claim_id=cr.claim_id)
                    .order_by(WorkflowRunRecord.started_at.desc())
                    .first()
                )
                curr_state: str | None = None
                if latest_run is not None:
                    if w_run_id is None:
                        w_run_id = UUID(latest_run.workflow_run_id)
                    curr_state = latest_run.current_state

                # Check approved amount
                appr_amt: Decimal | None = None
                if w_run_id is not None:
                    appr_row = (
                        uow.session.query(HumanApprovalRecord)
                        .filter_by(workflow_run_id=str(w_run_id))
                        .first()
                    )
                    if (
                        appr_row is not None
                        and getattr(appr_row, "approved_amount", None) is not None
                    ):
                        appr_amt = Decimal(str(appr_row.approved_amount))

                if c_status is None:
                    c_status = (
                        ClaimBatchStatus.COMPLETED.value
                        if (curr_state and curr_state not in ("FAILED", "UNKNOWN"))
                        else (
                            ClaimBatchStatus.FAILED.value
                            if curr_state == "FAILED"
                            else ClaimBatchStatus.PENDING.value
                        )
                    )

                inp_info = cached_inputs.get(cr.claim_id, {})
                c_name = inp_info.get("claimant_name") or cr.customer_id

                claim_results.append(
                    BatchClaimResult(
                        claim_id=c_uuid,
                        workflow_run_id=w_run_id,
                        policy_id=cr.policy_number,
                        claimant_name=c_name,
                        claim_amount=Decimal(str(cr.claim_amount)),
                        status=c_status,
                        current_state=curr_state or item_summary.get("current_state"),
                        approved_amount=appr_amt,
                        error_message=err_msg,
                        retry_count=retry_ct,
                        duration_seconds=dur,
                    )
                )

            # Reconstruct any claims that failed before database ClaimRecord was created
            existing_claim_ids = {str(r.claim_id) for r in claim_results}
            for cid_str, inp_data in cached_inputs.items():
                if cid_str not in existing_claim_ids:
                    item_summary = exec_summary.get(cid_str, {})
                    c_status = item_summary.get("status", ClaimBatchStatus.FAILED.value)
                    err_msg = item_summary.get("error_message")
                    retry_ct = int(item_summary.get("retry_count", 0))
                    dur = float(item_summary.get("duration_seconds", 0.0))
                    w_run_id_val = item_summary.get("workflow_run_id")
                    w_run_id = UUID(w_run_id_val) if w_run_id_val else None
                    curr_state = item_summary.get("current_state")

                    c_amt = Decimal(str(inp_data.get("claim_amount", "0.00")))

                    claim_results.append(
                        BatchClaimResult(
                            claim_id=UUID(cid_str),
                            workflow_run_id=w_run_id,
                            policy_id=str(inp_data.get("policy_id", "")),
                            claimant_name=str(inp_data.get("claimant_name", "")),
                            claim_amount=c_amt,
                            status=c_status,
                            current_state=curr_state,
                            approved_amount=None,
                            error_message=err_msg,
                            retry_count=retry_ct,
                            duration_seconds=dur,
                        )
                    )

        successful = sum(1 for r in claim_results if r.status == ClaimBatchStatus.COMPLETED.value)
        failed = sum(1 for r in claim_results if r.status == ClaimBatchStatus.FAILED.value)
        total_requested = sum((r.claim_amount for r in claim_results), Decimal("0.00"))
        total_approved = sum(
            (r.approved_amount or Decimal("0.00") for r in claim_results), Decimal("0.00")
        )

        return BatchExecutionResult(
            batch_id=b_id,
            batch_name=row.batch_name,
            status=row.status,
            disaster_context=d_ctx,
            total_claims=row.total_claims or len(claim_results),
            processed_claims=len(claim_results),
            successful_claims=successful,
            failed_claims=failed,
            total_requested_amount=total_requested,
            total_approved_amount=total_approved,
            claim_results=claim_results,
            submitted_by=row.submitted_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
