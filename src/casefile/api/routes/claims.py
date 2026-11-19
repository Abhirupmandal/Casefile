"""
Claims intake endpoints (Phase 7 §4).

- POST /api/v1/claims: Submit a new insurance claim into the orchestration pipeline.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import (
    ApiService,
    IdempotencyStore,
    get_api_service,
    get_idempotency_store,
)
from casefile.api.rate_limit import rate_limit
from casefile.api.schemas.models import (
    BatchClaimsListResponse,
    BatchClaimSubmissionRequest,
    BatchDisasterResponse,
    BatchRetryRequest,
    BatchStatusResponse,
    ClaimSubmissionRequest,
    ClaimSubmissionResponse,
    DecisionExplanationResponse,
)

router = APIRouter(prefix="/api/v1/claims", tags=["Claims"])


@router.post(
    "",
    response_model=ClaimSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60.0))],
)
def submit_claim(
    request: Request,
    claim: ClaimSubmissionRequest,
    principal: Annotated[
        Principal, Depends(require_roles(Role.OPERATOR, Role.ADMIN, Role.CLAIM_SUPERVISOR))
    ],
    service: Annotated[ApiService, Depends(get_api_service)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ClaimSubmissionResponse:
    """Submit a claim for automated orchestration intake. Enforces idempotency and rate limiting."""
    # Check idempotency
    if idempotency_key:
        cached = idempotency_store.check("claims", idempotency_key, claim)
        if cached is not None:
            return ClaimSubmissionResponse(**cached.response_data)

    corr_id = getattr(request.state, "correlation_id", None)
    response = service.submit_claim(claim, correlation_id=corr_id)

    # Store for idempotency
    if idempotency_key:
        idempotency_store.record(
            "claims",
            idempotency_key,
            claim,
            status_code=status.HTTP_201_CREATED,
            response_data=response.model_dump(),
        )

    return response


EXPLAIN_ROLES = (
    Role.VIEWER,
    Role.CLAIMANT,
    Role.OPERATOR,
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("/{claim_id}/explain", response_model=DecisionExplanationResponse)
def get_claim_explanation(
    claim_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*EXPLAIN_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> DecisionExplanationResponse:
    """Retrieve adjudication decision explanation brief and sensitivity counterfactuals for a claim."""
    return service.get_claim_explanation(claim_id, principal=principal)


# ======================================================================
# Batch Claim Intake & Disaster Orchestration Endpoints (Phase 12 §4, Phase 12.6)
# ======================================================================

BATCH_SUBMIT_ROLES = (Role.OPERATOR, Role.ADMIN, Role.CLAIM_SUPERVISOR)
BATCH_READ_ROLES = (
    Role.VIEWER,
    Role.CLAIMANT,
    Role.OPERATOR,
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.post(
    "/batch",
    response_model=BatchStatusResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60.0))],
)
def submit_claims_batch(
    request: Request,
    batch_req: BatchClaimSubmissionRequest,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_SUBMIT_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BatchStatusResponse:
    """Enroll and execute multiple claims in batch with shared disaster context and failure isolation."""
    if idempotency_key:
        cached = idempotency_store.check("claims_batch", idempotency_key, batch_req)
        if cached is not None:
            return BatchStatusResponse(**cached.response_data)

    corr_id = getattr(request.state, "correlation_id", None)
    response = service.submit_batch(
        batch_req,
        principal=principal,
        correlation_id=corr_id,
        idempotency_key=idempotency_key,
    )

    if idempotency_key:
        idempotency_store.record(
            "claims_batch",
            idempotency_key,
            batch_req,
            status_code=status.HTTP_201_CREATED,
            response_data=response.model_dump(),
        )

    return response


@router.get("/batch/{batch_id}", response_model=BatchStatusResponse)
def get_batch_status(
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchStatusResponse:
    """Retrieve aggregate progress and itemized status of a claim batch with tenant isolation."""
    return service.get_batch(batch_id, principal=principal)


@router.get("/batch/{batch_id}/claims", response_model=BatchClaimsListResponse)
def get_batch_claims(
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchClaimsListResponse:
    """Retrieve itemized claims list for a batch with claimant tenant isolation."""
    return service.get_batch_claims(batch_id, principal=principal)


@router.get("/batch/{batch_id}/disaster", response_model=BatchDisasterResponse)
def get_batch_disaster(
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchDisasterResponse:
    """Retrieve catastrophe and disaster event context for a batch."""
    return service.get_batch_disaster(batch_id, principal=principal)


@router.post("/batch/{batch_id}/retry", response_model=BatchStatusResponse)
def retry_batch_failed_claims(
    request: Request,
    batch_id: UUID,
    retry_req: BatchRetryRequest | None,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_SUBMIT_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchStatusResponse:
    """Retry failed claims in a batch without re-executing successful claims."""
    corr_id = getattr(request.state, "correlation_id", None)
    return service.retry_batch_failed_claims(
        batch_id=batch_id,
        request=retry_req,
        principal=principal,
        correlation_id=corr_id,
    )
