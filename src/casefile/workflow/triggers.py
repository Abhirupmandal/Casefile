"""
CASEFILE transition triggers: the typed events that drive state changes
(docs/state-machine.md transition table).

Triggers are explicit enum values, never arbitrary strings. Each trigger
names the outcome it carries (e.g. EXTRACTION_SUCCEEDED), so routing stays
deterministic: current state + trigger + context ⇒ next state.
"""

from __future__ import annotations

from enum import Enum


class Trigger(str, Enum):
    """Events that may cause a workflow state transition."""

    CLAIM_VALIDATED = "CLAIM_VALIDATED"
    CLAIM_INVALID = "CLAIM_INVALID"
    EXTRACTION_SUCCEEDED = "EXTRACTION_SUCCEEDED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    EXTRACTION_TIMED_OUT = "EXTRACTION_TIMED_OUT"
    INVESTIGATION_SUCCEEDED = "INVESTIGATION_SUCCEEDED"
    INVESTIGATION_FAILED = "INVESTIGATION_FAILED"
    INVESTIGATION_TIMED_OUT = "INVESTIGATION_TIMED_OUT"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    REWORK_REQUESTED = "REWORK_REQUESTED"
    REWORK_DISPATCHED = "REWORK_DISPATCHED"
    REWORK_EXHAUSTED = "REWORK_EXHAUSTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_TIMED_OUT = "APPROVAL_TIMED_OUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    STEPS_EXHAUSTED = "STEPS_EXHAUSTED"
    WORKFLOW_TIMED_OUT = "WORKFLOW_TIMED_OUT"
    NODE_FAILED = "NODE_FAILED"
