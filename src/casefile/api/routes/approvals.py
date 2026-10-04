"""
Human-in-the-Loop Approval Queue and Decision endpoints (Phase 7 §11, §12).

- GET /api/v1/approvals: List approval queue items (filtered by status).
- GET /api/v1/approvals/{approval_id}: Inspect approval details and reviewer recommendations.
- POST /api/v1/approvals/{approval_id}/decision: Submit human approval or rejection decision.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import (
    ApiService,
    IdempotencyStore,
    get_api_service,
    get_idempotency_store,
)
from casefile.api.rate_limit import rate_limit
from casefile.api.schemas.models import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalItemResponse,
    ApprovalListResponse,
)

router = APIRouter(prefix="/api/v1/approvals", tags=["Approvals"])

APPROVER_ROLES = (
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("", response_model=ApprovalListResponse)
def list_approvals(
    principal: Annotated[Principal, Depends(require_roles(*APPROVER_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> ApprovalListResponse:
    """Retrieve human approval queue requests."""
    return service.list_approvals(status_filter=status_filter)


@router.get("/{approval_id}", response_model=ApprovalItemResponse)
def get_approval(
    approval_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*APPROVER_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> ApprovalItemResponse:
    """Retrieve specific approval request and recommendation summary."""
    return service.get_approval(approval_id)


@router.post(
    "/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60.0))],
)
def decide_approval(
    approval_id: UUID,
    decision_req: ApprovalDecisionRequest,
    principal: Annotated[Principal, Depends(require_roles(*APPROVER_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApprovalDecisionResponse:
    """
    Submit a human adjudication decision.
    Enforces:
    - Separation of duties (requester cannot approve)
    - Expiration verification (deadline passed is rejected)
    - Role-based authorization: actor identity is derived strictly from the authenticated principal
    - Idempotency
    """
    # 1. Verify approval capability
    item = service.get_approval(approval_id)
    if not principal.can_decide_approval(item.required_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal role '{principal.role.value}' is insufficient for required role '{item.required_role}'",
        )

    # 2. Check idempotency
    scope_key = f"approval:{approval_id}"
    if idempotency_key:
        cached = idempotency_store.check(scope_key, idempotency_key, decision_req)
        if cached is not None:
            return ApprovalDecisionResponse(**cached.response_data)

    # 3. Apply decision
    response = service.decide_approval(
        approval_id=approval_id,
        principal=principal,
        decision_verdict=decision_req.decision,
        reason=decision_req.reason,
        decision_key=idempotency_key,
    )

    # 4. Store idempotency record
    if idempotency_key:
        idempotency_store.record(
            scope_key,
            idempotency_key,
            decision_req,
            status_code=status.HTTP_200_OK,
            response_data=response.model_dump(),
        )

    return response
