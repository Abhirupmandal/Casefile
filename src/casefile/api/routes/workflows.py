"""
Workflow status and history endpoints (Phase 7 §5, §6).

- GET /api/v1/workflows: List recent workflows.
- GET /api/v1/workflows/{workflow_run_id}: Inspect operational status and budget.
- GET /api/v1/workflows/{workflow_run_id}/history: Inspect chronological state transition audit trail.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import (
    WorkflowHistoryResponse,
    WorkflowListResponse,
    WorkflowStatusResponse,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["Workflows"])

# All workflow read endpoints admit VIEWER and higher roles
VIEWER_AND_ABOVE = (
    Role.VIEWER,
    Role.OPERATOR,
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("", response_model=WorkflowListResponse)
def list_workflows(
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkflowListResponse:
    """List recent workflows with pagination."""
    return service.list_workflows(limit=limit, offset=offset)


@router.get("/{workflow_run_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(
    workflow_run_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> WorkflowStatusResponse:
    """Retrieve operational workflow status, budget metrics, and current step."""
    return service.get_workflow_status(workflow_run_id)


@router.get("/{workflow_run_id}/history", response_model=WorkflowHistoryResponse)
def get_workflow_history(
    workflow_run_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> WorkflowHistoryResponse:
    """Retrieve chronological state transition history for a workflow run."""
    return service.get_workflow_history(workflow_run_id)
