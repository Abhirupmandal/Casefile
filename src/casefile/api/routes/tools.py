"""
Tool invocation visibility endpoints (Phase 7 §8).

- GET /api/v1/workflows/{workflow_run_id}/tools: Inspect sanitized tool invocations.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import ToolInvocationListResponse

router = APIRouter(prefix="/api/v1/workflows", tags=["Tools"])

OPERATOR_ROLES = (
    Role.OPERATOR,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("/{workflow_run_id}/tools", response_model=ToolInvocationListResponse)
def get_workflow_tools(
    workflow_run_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*OPERATOR_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> ToolInvocationListResponse:
    """Retrieve sanitized tool invocations for a workflow run."""
    return service.get_tool_invocations(workflow_run_id)
