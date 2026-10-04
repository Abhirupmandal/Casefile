"""API routes package."""

from fastapi import APIRouter

from casefile.api.routes.agents import router as agents_router
from casefile.api.routes.approvals import router as approvals_router
from casefile.api.routes.audit import router as audit_router
from casefile.api.routes.checkpoints import router as checkpoints_router
from casefile.api.routes.claims import router as claims_router
from casefile.api.routes.evaluations import router as evaluations_router
from casefile.api.routes.health import router as health_router
from casefile.api.routes.metrics import router as metrics_router
from casefile.api.routes.replay import router as replay_router
from casefile.api.routes.tools import router as tools_router
from casefile.api.routes.workflows import router as workflows_router

__all__ = [
    "agents_router",
    "approvals_router",
    "audit_router",
    "checkpoints_router",
    "claims_router",
    "evaluations_router",
    "health_router",
    "metrics_router",
    "replay_router",
    "tools_router",
    "workflows_router",
]
