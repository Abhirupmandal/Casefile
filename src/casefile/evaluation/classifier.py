"""Typed failure taxonomy and classification for Phase 6 evaluation.

Categorizes and diagnoses failures from runner execution data, identifying
component, stage, recoverability, containment, and retry outcomes.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from casefile.evaluation.failures import TriggeredInjection
from casefile.models.versioning import SchemaVersion


class FailureCategory(str, Enum):
    """Broad categories for evaluation failures."""

    NOMINAL = "NOMINAL"
    TOOL_FAILURE = "TOOL_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    AGENT_OUTPUT_FAILURE = "AGENT_OUTPUT_FAILURE"
    BUDGET_EXHAUSTION = "BUDGET_EXHAUSTION"
    STEP_EXHAUSTION = "STEP_EXHAUSTION"
    REWORK_EXHAUSTION = "REWORK_EXHAUSTION"
    APPROVAL_FAILURE = "APPROVAL_FAILURE"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"
    CHECKPOINT_FAILURE = "CHECKPOINT_FAILURE"
    TELEMETRY_FAILURE = "TELEMETRY_FAILURE"
    REPLAY_FAILURE = "REPLAY_FAILURE"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    INVARIANT_FAILURE = "INVARIANT_FAILURE"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


class FailureStage(str, Enum):
    """Lifecycle stage where the failure originated or surfaced."""

    RECEIVED = "RECEIVED"
    EXTRACTION = "EXTRACTION"
    INVESTIGATION = "INVESTIGATION"
    REVIEW = "REVIEW"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    PERSISTENCE = "PERSISTENCE"
    CHECKPOINT = "CHECKPOINT"
    REPLAY = "REPLAY"
    ROUTING = "ROUTING"
    SUPERVISOR = "SUPERVISOR"
    BUDGET = "BUDGET"
    UNKNOWN = "UNKNOWN"


class FailureClassification(BaseModel):
    """Structured failure diagnosis for evaluation results. Frozen."""

    model_config = {"frozen": True}

    category: FailureCategory
    stage: FailureStage
    failure_type: str
    originating_component: str
    expected_behavior: str
    actual_behavior: str
    recoverable: bool = False
    retry_attempted: bool = False
    retry_succeeded: bool = False
    terminal_state: str | None = None
    contained: bool = True
    schema_version: SchemaVersion = "1.0.0"


def classify_failure(
    *,
    status: str,
    terminal_state: str | None,
    terminal_reason: str | None,
    errors: tuple[str, ...],
    injections: tuple[TriggeredInjection, ...] = (),
    retry_count: int = 0,
    rework_count: int = 0,
    safe_error: str | None = None,
) -> FailureClassification:
    """Derive a typed FailureClassification from run execution observables."""
    combined_err = " ".join(errors) + " " + (safe_error or "") + " " + (terminal_reason or "")
    combined_err_lower = combined_err.lower()

    # If the run passed without errors or injections, it is nominal
    has_fault = bool(errors or injections or (safe_error and "ok" not in safe_error.lower()))
    is_budget_latch = terminal_reason in (
        "BUDGET_EXHAUSTED",
        "MAX_COST_EXCEEDED",
        "MAX_AGENT_STEPS_EXCEEDED",
    )
    is_step_latch = terminal_reason == "MAX_STEPS_EXCEEDED"
    is_rework_latch = terminal_reason == "REWORK_EXHAUSTED"

    if (
        status == "PASS"
        and not has_fault
        and not (is_budget_latch or is_step_latch or is_rework_latch)
    ):
        return FailureClassification(
            category=FailureCategory.NOMINAL,
            stage=FailureStage.SUPERVISOR,
            failure_type="NONE",
            originating_component="System",
            expected_behavior="Nominal execution without unhandled errors",
            actual_behavior="Workflow completed nominally",
            recoverable=True,
            retry_attempted=retry_count > 0,
            retry_succeeded=True,
            terminal_state=terminal_state,
            contained=True,
        )

    # Check for budget / step / rework exhaustion
    if "budget" in combined_err_lower or "cost_limit" in combined_err_lower or is_budget_latch:
        return FailureClassification(
            category=FailureCategory.BUDGET_EXHAUSTION,
            stage=FailureStage.BUDGET,
            failure_type="BUDGET_EXHAUSTION",
            originating_component="BudgetEngine",
            expected_behavior="Halt workflow before exceeding cost or token ceiling",
            actual_behavior="Budget exhaustion latched; workflow terminated safely",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    if "max_steps" in combined_err_lower or "step_limit" in combined_err_lower:
        return FailureClassification(
            category=FailureCategory.STEP_EXHAUSTION,
            stage=FailureStage.SUPERVISOR,
            failure_type="MAX_STEPS_EXCEEDED",
            originating_component="SupervisorRouter",
            expected_behavior="Enforce hard bound on total state transitions",
            actual_behavior="Step limit exceeded; workflow terminated",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    if "rework" in combined_err_lower and ("exhaust" in combined_err_lower or rework_count >= 2):
        return FailureClassification(
            category=FailureCategory.REWORK_EXHAUSTION,
            stage=FailureStage.REVIEW,
            failure_type="MAX_REWORK_EXCEEDED",
            originating_component="ReviewerAgent",
            expected_behavior="Terminate workflow when rework limit is reached",
            actual_behavior=f"Max rework limit reached ({rework_count} cycles); terminated",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    # Check for tool errors
    if "tool" in combined_err_lower:
        tool_name = "tool_registry"
        for part in errors:
            if "tool" in part.lower():
                tool_name = part
                break
        recovered = status == "PASS"
        return FailureClassification(
            category=FailureCategory.TOOL_FAILURE,
            stage=FailureStage.INVESTIGATION,
            failure_type="TOOL_ERROR",
            originating_component=tool_name,
            expected_behavior="Execute tool call or safely handle/retry failure",
            actual_behavior=safe_error or "Tool failure encountered",
            recoverable=True,
            retry_attempted=retry_count > 0,
            retry_succeeded=recovered,
            terminal_state=terminal_state,
            contained=True,
        )

    # Check for provider / LLM / malformed output
    if (
        "provider" in combined_err_lower
        or "json" in combined_err_lower
        or "malformed" in combined_err_lower
    ):
        recovered = status == "PASS"
        return FailureClassification(
            category=FailureCategory.PROVIDER_FAILURE,
            stage=(
                FailureStage.EXTRACTION
                if "extractor" in combined_err_lower
                else FailureStage.INVESTIGATION
            ),
            failure_type=(
                "PROVIDER_ERROR" if "provider" in combined_err_lower else "MALFORMED_AGENT_OUTPUT"
            ),
            originating_component="DeterministicProvider",
            expected_behavior="Parse valid JSON conforming to contract, retry on transient failure",
            actual_behavior=safe_error or "Provider error or contract violation encountered",
            recoverable=True,
            retry_attempted=retry_count > 0,
            retry_succeeded=recovered,
            terminal_state=terminal_state,
            contained=True,
        )

    # Check for checkpoint / replay
    if "checkpoint" in combined_err_lower or "corrupt" in combined_err_lower:
        return FailureClassification(
            category=FailureCategory.CHECKPOINT_FAILURE,
            stage=FailureStage.CHECKPOINT,
            failure_type="CHECKPOINT_WRITE_ERROR",
            originating_component="SqlCheckpointRepository",
            expected_behavior="Fail-closed on corrupted checkpoint integrity",
            actual_behavior="Corrupted checkpoint rejected safely",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    if "replay" in combined_err_lower:
        return FailureClassification(
            category=FailureCategory.REPLAY_FAILURE,
            stage=FailureStage.REPLAY,
            failure_type="REPLAY_MISMATCH",
            originating_component="ReplayEngine",
            expected_behavior="Deterministic replay matches live trajectory without side-effects",
            actual_behavior=safe_error or "Replay comparison failed",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    # Check for approval issues
    if "approval" in combined_err_lower:
        return FailureClassification(
            category=FailureCategory.APPROVAL_FAILURE,
            stage=FailureStage.HUMAN_APPROVAL,
            failure_type=(
                "UNAUTHORIZED_APPROVAL"
                if "unauthorized" in combined_err_lower
                else "APPROVAL_FAILURE"
            ),
            originating_component="ApprovalService",
            expected_behavior="Human-only approval with role authorization",
            actual_behavior=safe_error or "Approval operation denied or failed",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    # Check for telemetry
    if "telemetry" in combined_err_lower or "sink" in combined_err_lower:
        return FailureClassification(
            category=FailureCategory.TELEMETRY_FAILURE,
            stage=FailureStage.SUPERVISOR,
            failure_type="TELEMETRY_ERROR",
            originating_component="ObservabilityProvider",
            expected_behavior="Telemetry outage does not alter workflow semantics",
            actual_behavior="Telemetry errors isolated; workflow proceeded",
            recoverable=True,
            retry_attempted=False,
            retry_succeeded=True,
            terminal_state=terminal_state,
            contained=True,
        )

    # Invariant failure
    if "invariant" in combined_err_lower:
        return FailureClassification(
            category=FailureCategory.INVARIANT_FAILURE,
            stage=FailureStage.SUPERVISOR,
            failure_type="INVARIANT_FAILURE",
            originating_component="InvariantEngine",
            expected_behavior="All required invariants must evaluate to PASS",
            actual_behavior=safe_error or "Invariant evaluation failed",
            recoverable=False,
            retry_attempted=False,
            retry_succeeded=False,
            terminal_state=terminal_state,
            contained=True,
        )

    # Default / Nominal fallback
    if status == "PASS":
        return FailureClassification(
            category=FailureCategory.NOMINAL,
            stage=FailureStage.SUPERVISOR,
            failure_type="NONE",
            originating_component="System",
            expected_behavior="Nominal execution without unhandled errors",
            actual_behavior="Workflow completed nominally",
            recoverable=True,
            retry_attempted=retry_count > 0,
            retry_succeeded=True,
            terminal_state=terminal_state,
            contained=True,
        )

    return FailureClassification(
        category=FailureCategory.UNEXPECTED_ERROR,
        stage=FailureStage.UNKNOWN,
        failure_type="UNKNOWN",
        originating_component="System",
        expected_behavior="Handled safely",
        actual_behavior=safe_error or "Unknown failure",
        recoverable=False,
        retry_attempted=retry_count > 0,
        retry_succeeded=False,
        terminal_state=terminal_state,
        contained=True,
    )
