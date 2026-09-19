"""
CASEFILE data models package.

Contains all Pydantic models for:
- Agent contracts (requests/responses)
- Workflow state
- Budget tracking
- Checkpoints
"""

from casefile.models.contracts import (
    BudgetState,
    Checkpoint,
    ClaimInput,
    ExtractionRequest,
    ExtractionResult,
    InvestigationRequest,
    InvestigationResult,
    ReviewRequest,
    ReviewResult,
    WorkflowResult,
    WorkflowState,
)

__all__ = [
    "ClaimInput",
    "ExtractionRequest",
    "ExtractionResult",
    "InvestigationRequest",
    "InvestigationResult",
    "ReviewRequest",
    "ReviewResult",
    "WorkflowResult",
    "WorkflowState",
    "BudgetState",
    "Checkpoint",
]
