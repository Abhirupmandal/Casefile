"""
Claims intake endpoints (Phase 7 §4).

- POST /api/v1/claims: Submit a new insurance claim into the orchestration pipeline.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import (
    ApiService,
    IdempotencyStore,
    get_api_service,
    get_idempotency_store,
)
from casefile.api.rate_limit import rate_limit
from casefile.api.schemas.models import ClaimSubmissionRequest, ClaimSubmissionResponse

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
