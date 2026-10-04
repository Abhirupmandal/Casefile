"""Strongly-typed evaluation domain models (Phase 6).

Provides the domain models for evaluation cases, runs, outcomes, expectations,
and metrics. All models are frozen Pydantic v2 models.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from casefile.evaluation.fixtures import ClaimSpec
from casefile.evaluation.scenarios import EvaluationScenario
from casefile.models.contracts import WorkflowState
from casefile.models.versioning import SchemaVersion


class FaultType(str, Enum):
    """Formal fault types for deterministic failure injection and classification."""

    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    MALFORMED_AGENT_OUTPUT = "MALFORMED_AGENT_OUTPUT"
    TOOL_ERROR = "TOOL_ERROR"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    CHECKPOINT_WRITE_ERROR = "CHECKPOINT_WRITE_ERROR"
    TELEMETRY_ERROR = "TELEMETRY_ERROR"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    UNAUTHORIZED_APPROVAL = "UNAUTHORIZED_APPROVAL"
    REPLAY_MISMATCH = "REPLAY_MISMATCH"
    BUDGET_EXHAUSTION = "BUDGET_EXHAUSTION"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MAX_REWORK_EXCEEDED = "MAX_REWORK_EXCEEDED"


class ExpectedTerminalState(BaseModel):
    """Expected workflow terminal state outcome."""

    model_config = {"frozen": True}

    state: WorkflowState | None = None
    reason: str | None = None
    schema_version: SchemaVersion = "1.0.0"


class ExpectedNodePath(BaseModel):
    """Expected exact sequence of states traversed."""

    model_config = {"frozen": True}

    path: tuple[WorkflowState, ...] = ()
    schema_version: SchemaVersion = "1.0.0"


class ExpectedBudgetOutcome(BaseModel):
    """Expected budget metrics and ceiling compliance."""

    model_config = {"frozen": True}

    steps_consumed: int | None = Field(default=None, ge=0)
    cost_ceiling: Decimal | None = None
    rework_cycles: int | None = Field(default=None, ge=0)
    cost_nonzero: bool = False
    terminal_reason: str | None = None
    schema_version: SchemaVersion = "1.0.0"


class ExpectedApprovalOutcome(BaseModel):
    """Expected human approval lifecycle behavior."""

    model_config = {"frozen": True}

    outcome: Literal[
        "granted",
        "rejected",
        "pending",
        "none",
        "race_one_winner",
        "expired",
        "unauthorized",
    ] = "none"
    actor_role: str | None = None
    schema_version: SchemaVersion = "1.0.0"


class ExpectedCheckpointBehavior(BaseModel):
    """Expected checkpoint creation and restore verification."""

    model_config = {"frozen": True}

    checkpoint_created: bool = True
    checkpoint_restored: bool = False
    integrity_verified: bool = True
    schema_version: SchemaVersion = "1.0.0"


class ReplayExpectation(BaseModel):
    """Expected determinism and safety under replay."""

    model_config = {"frozen": True}

    runs: bool = False
    terminal_matches: bool = True
    expect_mismatch: bool = False
    expect_no_mutation: bool = True
    schema_version: SchemaVersion = "1.0.0"


class ExpectedFailureClassification(BaseModel):
    """Expected failure category and containment metadata."""

    model_config = {"frozen": True}

    category: str | None = None
    stage: str | None = None
    failure_type: str | None = None
    recoverable: bool | None = None
    contained: bool = True
    schema_version: SchemaVersion = "1.0.0"


class EvaluationCase(BaseModel):
    """Specification of one deterministic evaluation case."""

    model_config = {"frozen": True}

    case_id: str = Field(pattern=r"^CASE-\d{3}$")
    scenario_name: str
    category: str
    description: str = ""
    claim_fixture: ClaimSpec = Field(default_factory=ClaimSpec)
    scenario: EvaluationScenario
    expected_state_path: tuple[WorkflowState, ...] = ()
    expected_terminal_state: WorkflowState | None = None
    expected_approval_behavior: ExpectedApprovalOutcome = Field(
        default_factory=ExpectedApprovalOutcome
    )
    expected_rework_count: int | None = None
    expected_retry_count: int | None = None
    expected_tool_failures: int | None = None
    expected_budget_outcome: ExpectedBudgetOutcome = Field(default_factory=ExpectedBudgetOutcome)
    expected_checkpoint_behavior: ExpectedCheckpointBehavior = Field(
        default_factory=ExpectedCheckpointBehavior
    )
    expected_replay_behavior: ReplayExpectation = Field(default_factory=ReplayExpectation)
    expected_failure_classification: ExpectedFailureClassification | None = None
    schema_version: SchemaVersion = "1.0.0"


class EvaluationOutcome(BaseModel):
    """Record of execution results and comparison against expectations for one case."""

    model_config = {"frozen": True}

    case_id: str
    scenario_name: str
    status: Literal["PASS", "FAIL", "ERROR"]
    actual_path: tuple[str, ...] = ()
    expected_path: tuple[str, ...] = ()
    path_matched: bool = True
    terminal_state: str | None = None
    expected_terminal_state: str | None = None
    terminal_matched: bool = True
    rework_count: int = 0
    retry_count: int = 0
    tool_failures: int = 0
    budget_used_steps: int = 0
    cost_usd: Decimal = Field(default=Decimal("0"))
    input_tokens: int = 0
    output_tokens: int = 0
    replay_matched: bool | None = None
    failure_classification: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()
    safe_error: str | None = None
    schema_version: SchemaVersion = "1.0.0"


class EvaluationMetric(BaseModel):
    """Individual typed metric item."""

    model_config = {"frozen": True}

    name: str
    value: float | int | Decimal | str
    target: float | int | Decimal | str | None = None
    description: str = ""
    schema_version: SchemaVersion = "1.0.0"


class EvaluationSummary(BaseModel):
    """High-level summary of evaluation outcomes."""

    model_config = {"frozen": True}

    total_cases: int
    passed: int
    failed: int
    errored: int
    pass_rate: float
    metrics: dict[str, Any] = Field(default_factory=dict)
    schema_version: SchemaVersion = "1.0.0"


class EvaluationRun(BaseModel):
    """Complete machine-readable record of an evaluation execution batch."""

    model_config = {"frozen": True}

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    evaluation_version: str = "1.0.0"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_cases: int
    passed: int
    failed: int
    pass_rate: float
    metrics: dict[str, Any] = Field(default_factory=dict)
    cases: tuple[EvaluationOutcome, ...] = ()
    schema_version: SchemaVersion = "1.0.0"
