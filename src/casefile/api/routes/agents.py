"""
Agent execution visibility endpoints (Phase 7 §7).

- GET /api/v1/workflows/{workflow_run_id}/agents: Inspect sanitized agent executions.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import AgentExecutionListResponse

router = APIRouter(prefix="/api/v1/workflows", tags=["Agents"])

OPERATOR_ROLES = (
    Role.OPERATOR,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("/{workflow_run_id}/agents", response_model=AgentExecutionListResponse)
def get_workflow_agents(
    workflow_run_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*OPERATOR_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> AgentExecutionListResponse:
    """Retrieve sanitized agent executions for a workflow run."""
    return service.get_agent_executions(workflow_run_id)
