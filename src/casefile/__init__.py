"""
CASEFILE: AI-Powered Insurance Claim Adjudication System

A production-grade multi-agent system for automated claim processing with:
- LangGraph workflow orchestration
- Pydantic typed contracts
- SQLite persistence with checkpointing
- Redis caching and rate limiting
- OpenTelemetry observability
- Multi-dimensional budget enforcement
"""

__version__ = "0.1.0"
__author__ = "CASEFILE Team"

from casefile.models.contracts import (
    ClaimInput,
    WorkflowResult,
    WorkflowState,
)

__all__ = [
    "__version__",
    "ClaimInput",
    "WorkflowResult",
    "WorkflowState",
]
