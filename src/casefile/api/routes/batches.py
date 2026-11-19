"""
Batch Claim Processing & Disaster Orchestration Endpoints (Phase 12 §4, Phase 12.6).

Exposes:
- POST /api/v1/batches: Submit and enroll multiple claims in a batch.
- GET /api/v1/batches/{batch_id}: Retrieve batch status and progress.
- GET /api/v1/batches/{batch_id}/claims: Retrieve itemized claim records.
- GET /api/v1/batches/{batch_id}/disaster: Retrieve disaster event context.
- POST /api/v1/batches/{batch_id}/retry: Retry failed claims in a batch.
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
)

router = APIRouter(prefix="/api/v1/batches", tags=["Batches"])

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
    "",
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
    """Submit multiple claims as a single batch with failure isolation."""
    if idempotency_key:
        cached = idempotency_store.check("batches", idempotency_key, batch_req)
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
            "batches",
            idempotency_key,
            batch_req,
            status_code=status.HTTP_201_CREATED,
            response_data=response.model_dump(),
        )

    return response


@router.get("/{batch_id}", response_model=BatchStatusResponse)
def get_batch_status(
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchStatusResponse:
    """Retrieve status, progress, and financial totals for a batch."""
    return service.get_batch(batch_id, principal=principal)


@router.get("/{batch_id}/claims", response_model=BatchClaimsListResponse)
def get_batch_claims(
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchClaimsListResponse:
    """Retrieve itemized claim results for a batch."""
    return service.get_batch_claims(batch_id, principal=principal)


@router.get("/{batch_id}/disaster", response_model=BatchDisasterResponse)
def get_batch_disaster(
    batch_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchDisasterResponse:
    """Retrieve disaster and catastrophe context for a batch."""
    return service.get_batch_disaster(batch_id, principal=principal)


@router.post("/{batch_id}/retry", response_model=BatchStatusResponse)
def retry_batch_failed_claims(
    request: Request,
    batch_id: UUID,
    retry_req: BatchRetryRequest | None,
    principal: Annotated[Principal, Depends(require_roles(*BATCH_SUBMIT_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> BatchStatusResponse:
    """Retry execution of failed claims in a batch."""
    corr_id = getattr(request.state, "correlation_id", None)
    return service.retry_batch_failed_claims(
        batch_id=batch_id,
        request=retry_req,
        principal=principal,
        correlation_id=corr_id,
    )
