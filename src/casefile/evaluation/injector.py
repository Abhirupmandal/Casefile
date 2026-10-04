"""Deterministic failure-injection framework for Phase 6 evaluation.

Scoped exclusively to evaluation runs; completely disabled by default.
Provides FailureInjector, FailureRule, FailurePlan, and InjectedFailure models
with typed FaultTypes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from casefile.evaluation.failures import (
    FailureInjection,
    FailurePoint,
    FailureType,
    RecoveryBehavior,
)
from casefile.evaluation.failures import (
    FailureInjector as BaseFailureInjector,
)
from casefile.evaluation.model import FaultType
from casefile.models.versioning import SchemaVersion

# Mapping from Phase 6 FaultType to legacy FailurePoint / FailureType defaults
_FAULT_TYPE_MAPPINGS: dict[FaultType, tuple[FailurePoint, FailureType, RecoveryBehavior]] = {
    FaultType.PROVIDER_ERROR: (
        FailurePoint.BEFORE_AGENT,
        FailureType.EXCEPTION,
        RecoveryBehavior.RETRY_STEP,
    ),
    FaultType.PROVIDER_TIMEOUT: (
        FailurePoint.BEFORE_AGENT,
        FailureType.TIMEOUT,
        RecoveryBehavior.RETRY_STEP,
    ),
    FaultType.MALFORMED_AGENT_OUTPUT: (
        FailurePoint.AFTER_AGENT,
        FailureType.MALFORMED_RESULT,
        RecoveryBehavior.RETRY_STEP,
    ),
    FaultType.TOOL_ERROR: (
        FailurePoint.BEFORE_TOOL,
        FailureType.EXCEPTION,
        RecoveryBehavior.PROPAGATE,
    ),
    FaultType.TOOL_TIMEOUT: (
        FailurePoint.BEFORE_TOOL,
        FailureType.TIMEOUT,
        RecoveryBehavior.PROPAGATE,
    ),
    FaultType.TOOL_UNAVAILABLE: (
        FailurePoint.BEFORE_TOOL,
        FailureType.UNAVAILABLE_DEPENDENCY,
        RecoveryBehavior.PROPAGATE,
    ),
    FaultType.PERSISTENCE_ERROR: (
        FailurePoint.BEFORE_PERSISTENCE_COMMIT,
        FailureType.EXCEPTION,
        RecoveryBehavior.TERMINATE_WORKFLOW,
    ),
    FaultType.CHECKPOINT_WRITE_ERROR: (
        FailurePoint.BEFORE_CHECKPOINT_CREATE,
        FailureType.EXCEPTION,
        RecoveryBehavior.TERMINATE_WORKFLOW,
    ),
    FaultType.TELEMETRY_ERROR: (
        FailurePoint.BEFORE_WORKFLOW_TRANSITION,
        FailureType.EXCEPTION,
        RecoveryBehavior.SKIP_OPERATION,
    ),
    FaultType.INVALID_CONTRACT: (
        FailurePoint.AFTER_AGENT,
        FailureType.MALFORMED_RESULT,
        RecoveryBehavior.PROPAGATE,
    ),
    FaultType.UNAUTHORIZED_APPROVAL: (
        FailurePoint.BEFORE_APPROVAL_DECIDE,
        FailureType.EXCEPTION,
        RecoveryBehavior.PROPAGATE,
    ),
    FaultType.REPLAY_MISMATCH: (
        FailurePoint.DURING_REPLAY_ARTIFACT_RETRIEVAL,
        FailureType.INTEGRITY_FAILURE,
        RecoveryBehavior.PROPAGATE,
    ),
    FaultType.BUDGET_EXHAUSTION: (
        FailurePoint.BEFORE_BUDGET_RESERVE,
        FailureType.EXCEPTION,
        RecoveryBehavior.TERMINATE_WORKFLOW,
    ),
    FaultType.MAX_STEPS_EXCEEDED: (
        FailurePoint.BEFORE_BUDGET_RESERVE,
        FailureType.EXCEPTION,
        RecoveryBehavior.TERMINATE_WORKFLOW,
    ),
    FaultType.MAX_REWORK_EXCEEDED: (
        FailurePoint.BEFORE_WORKFLOW_TRANSITION,
        FailureType.EXCEPTION,
        RecoveryBehavior.TERMINATE_WORKFLOW,
    ),
}


class FailureRule(BaseModel):
    """Specification of a single deterministic fault injection rule."""

    model_config = {"frozen": True}

    rule_id: str = Field(default_factory=lambda: f"rule-{uuid4().hex[:8]}")
    fault_type: FaultType
    target_component: str = "*"
    point: FailurePoint | None = None
    max_triggers: int | None = Field(default=1, ge=1)
    expected_recovery: RecoveryBehavior = RecoveryBehavior.PROPAGATE
    schema_version: SchemaVersion = "1.0.0"

    def to_failure_injection(self) -> FailureInjection:
        """Convert to base runner FailureInjection."""
        default_point, default_type, default_recovery = _FAULT_TYPE_MAPPINGS.get(
            self.fault_type,
            (FailurePoint.BEFORE_AGENT, FailureType.EXCEPTION, RecoveryBehavior.PROPAGATE),
        )
        return FailureInjection(
            failure_id=self.rule_id,
            point=self.point or default_point,
            type=default_type,
            target_component=self.target_component,
            max_triggers=self.max_triggers,
            expected_recovery_behavior=self.expected_recovery or default_recovery,
        )


class FailurePlan(BaseModel):
    """A collection of FailureRules configuring a scenario's injection profile."""

    model_config = {"frozen": True}

    plan_id: str = Field(default_factory=lambda: f"plan-{uuid4().hex[:8]}")
    name: str = ""
    rules: tuple[FailureRule, ...] = ()
    schema_version: SchemaVersion = "1.0.0"

    def to_injections(self) -> tuple[FailureInjection, ...]:
        """Convert all rules to base FailureInjections."""
        return tuple(rule.to_failure_injection() for rule in self.rules)


class InjectedFailure(BaseModel):
    """Record of an executed failure injection."""

    model_config = {"frozen": True}

    rule_id: str
    fault_type: FaultType
    target_component: str
    fire_index: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: SchemaVersion = "1.0.0"


class FailureInjector(BaseFailureInjector):
    """Enhanced failure injector with plan lifecycle, context scoping, and audit."""

    def __init__(
        self,
        injections: list[FailureInjection] | None = None,
        plan: FailurePlan | None = None,
    ) -> None:
        merged_injections = list(injections or [])
        if plan is not None:
            merged_injections.extend(plan.to_injections())
        super().__init__(merged_injections)
        self.plan = plan
        self.injected_history: list[InjectedFailure] = []
        self._active: bool = False

    def __enter__(self) -> FailureInjector:
        self._active = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._active = False
        self._injections.clear()
        self._counts.clear()

    @property
    def is_active(self) -> bool:
        """True if the injector is in an active evaluation context."""
        return self._active or bool(self._injections)
