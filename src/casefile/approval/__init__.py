"""
CASEFILE human-in-the-loop approval boundary (Phase 5).

Approval is an explicit control-plane transition, never an LLM output.
Typed actors, versioned requests, atomic conditional decisions,
idempotent retries, expiry, cancellation, and full audit.
"""

from casefile.approval.model import (
    ApprovalActor,
    ApprovalAuditRecord,
    ApprovalAuthorizer,
    ApprovalDecision,
    ApprovalError,
    ApprovalErrorCode,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalRole,
    ApprovalStateMachine,
    ApprovalVerdict,
    CancelApproval,
    DecisionResult,
    ExpireApproval,
    HumanActor,
    InvalidApprovalTransitionError,
)
from casefile.approval.service import ApprovalService

__all__ = [
    "ApprovalActor",
    "ApprovalAuditRecord",
    "ApprovalAuthorizer",
    "ApprovalDecision",
    "ApprovalError",
    "ApprovalErrorCode",
    "ApprovalOutcome",
    "ApprovalRequest",
    "ApprovalRole",
    "ApprovalStateMachine",
    "ApprovalVerdict",
    "CancelApproval",
    "DecisionResult",
    "ExpireApproval",
    "HumanActor",
    "InvalidApprovalTransitionError",
    "ApprovalService",
]
