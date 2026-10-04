"""
Audit trail endpoints (Phase 7 §12, §20).

- GET /api/v1/audit: Inspect chronological audit events across the platform.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import AuditListResponse

router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])

AUDIT_ROLES = (
    Role.OPERATOR,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("", response_model=AuditListResponse)
def list_audit_events(
    principal: Annotated[Principal, Depends(require_roles(*AUDIT_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    workflow_run_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditListResponse:
    """Retrieve immutable audit events."""
    return service.list_audit_events(workflow_run_id=workflow_run_id, limit=limit)
