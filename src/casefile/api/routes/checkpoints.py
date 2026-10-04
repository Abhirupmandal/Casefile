"""
Checkpoint visibility endpoints (Phase 7 §9).

- GET /api/v1/workflows/{workflow_run_id}/checkpoints: List durable checkpoints and integrity status.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import CheckpointListResponse

router = APIRouter(prefix="/api/v1/workflows", tags=["Checkpoints"])

OPERATOR_ROLES = (
    Role.OPERATOR,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("/{workflow_run_id}/checkpoints", response_model=CheckpointListResponse)
def get_workflow_checkpoints(
    workflow_run_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*OPERATOR_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> CheckpointListResponse:
    """Retrieve metadata and integrity verification status of durable checkpoints."""
    return service.get_checkpoints(workflow_run_id)
