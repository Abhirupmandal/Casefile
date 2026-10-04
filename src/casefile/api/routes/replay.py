"""
Safe Replay Simulation endpoints (Phase 7 §10).

- POST /api/v1/workflows/{workflow_run_id}/replay: Execute deterministic simulation replay.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import (
    ApiService,
    IdempotencyStore,
    get_api_service,
    get_idempotency_store,
)
from casefile.api.rate_limit import rate_limit
from casefile.api.schemas.models import ReplayRequest, ReplayResponse

router = APIRouter(prefix="/api/v1/workflows", tags=["Replay"])

OPERATOR_ROLES = (
    Role.OPERATOR,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.post(
    "/{workflow_run_id}/replay",
    response_model=ReplayResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60.0))],
)
def replay_workflow_endpoint(
    workflow_run_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*OPERATOR_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    replay_req: ReplayRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReplayResponse:
    """
    Execute offline deterministic replay from recorded checkpoints.
    Safe simulation only:
    - Never mutates production workflow state
    - Never calls external providers or tools
    - Generates a distinct replay run identifier
    """
    req_body = replay_req or ReplayRequest()
    scope_key = f"replay:{workflow_run_id}"

    # Check idempotency
    if idempotency_key:
        cached = idempotency_store.check(scope_key, idempotency_key, req_body)
        if cached is not None:
            return ReplayResponse(**cached.response_data)

    # Perform replay
    response = service.replay_workflow_safe(workflow_run_id)

    # Store for idempotency
    if idempotency_key:
        idempotency_store.record(
            scope_key,
            idempotency_key,
            req_body,
            status_code=status.HTTP_200_OK,
            response_data=response.model_dump(),
        )

    return response
