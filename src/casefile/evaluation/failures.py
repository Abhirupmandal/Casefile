"""
Typed failure-injection framework for Phase 11 evaluation.

Injection is opt-in per scenario: an empty injection list disables every
check. The injector is owned and consulted exclusively by the evaluation
runner — production code has no evaluation_mode branch, and untrusted
claim/model/tool text cannot construct or activate injections (only
typed EvaluationScenario.initial.injections can).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from casefile.models.versioning import SchemaVersion


class FailurePoint(str, Enum):
    """Named observation points where the runner may inject a failure."""

    BEFORE_AGENT = "BEFORE_AGENT"
    AFTER_AGENT = "AFTER_AGENT"
    BEFORE_TOOL = "BEFORE_TOOL"
    AFTER_TOOL = "AFTER_TOOL"
    BEFORE_WORKFLOW_TRANSITION = "BEFORE_WORKFLOW_TRANSITION"
    AFTER_WORKFLOW_TRANSITION = "AFTER_WORKFLOW_TRANSITION"
    BEFORE_CHECKPOINT_CREATE = "BEFORE_CHECKPOINT_CREATE"
    AFTER_CHECKPOINT_CREATE = "AFTER_CHECKPOINT_CREATE"
    BEFORE_BUDGET_RESERVE = "BEFORE_BUDGET_RESERVE"
    AFTER_BUDGET_RESERVE = "AFTER_BUDGET_RESERVE"
    BEFORE_APPROVAL_DECIDE = "BEFORE_APPROVAL_DECIDE"
    AFTER_APPROVAL_DECIDE = "AFTER_APPROVAL_DECIDE"
    BEFORE_PERSISTENCE_COMMIT = "BEFORE_PERSISTENCE_COMMIT"
    AFTER_PERSISTENCE_COMMIT = "AFTER_PERSISTENCE_COMMIT"
    DURING_REPLAY_ARTIFACT_RETRIEVAL = "DURING_REPLAY_ARTIFACT_RETRIEVAL"


class FailureType(str, Enum):
    """Structured failure categories the injector can simulate."""

    EXCEPTION = "EXCEPTION"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE_DEPENDENCY = "UNAVAILABLE_DEPENDENCY"
    MALFORMED_RESULT = "MALFORMED_RESULT"
    MISSING_ARTIFACT = "MISSING_ARTIFACT"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"


class RecoveryBehavior(str, Enum):
    """How the runner is expected to recover from one injected failure."""

    RETRY_STEP = "RETRY_STEP"
    SKIP_OPERATION = "SKIP_OPERATION"
    TERMINATE_NODE = "TERMINATE_NODE"
    TERMINATE_WORKFLOW = "TERMINATE_WORKFLOW"
    PROPAGATE = "PROPAGATE"


class FailureInjection(BaseModel):
    """One typed, bounded injection rule. Frozen."""

    model_config = {"frozen": True}

    failure_id: str
    point: FailurePoint
    type: FailureType
    target_component: str = "*"
    max_triggers: int | None = Field(default=None, ge=1)
    expected_recovery_behavior: RecoveryBehavior = RecoveryBehavior.PROPAGATE
    schema_version: SchemaVersion = "1.0.0"


class InjectedFailureError(Exception):
    """Raised by the runner when an injection rule fires."""

    def __init__(self, injection: FailureInjection) -> None:
        super().__init__(
            f"Injected {injection.type.value} at {injection.point.value} "
            f"for {injection.target_component!r} ({injection.failure_id})"
        )
        self.injection = injection


class TriggeredInjection(BaseModel):
    """Record of one fire for the evaluation result. Frozen."""

    model_config = {"frozen": True}

    failure_id: str
    point: FailurePoint
    type: FailureType
    target_component: str
    fire_index: int = Field(ge=1)
    schema_version: SchemaVersion = "1.0.0"


def _target_matches(pattern: str, target: str) -> bool:
    """'*' matches everything; otherwise exact or 'kind:name' suffix match."""
    if pattern == "*":
        return True
    if pattern == target:
        return True
    return target.endswith(pattern) or pattern.endswith(target)


class FailureInjector:
    """Opt-in injector: disabled whenever the injection list is empty.

    Counts and fire history live here (mutable runtime state); the
    FailureInjection rules themselves stay frozen. Only the evaluation
    runner calls should_fail — no production module imports this type.
    """

    def __init__(self, injections: list[FailureInjection] | None = None) -> None:
        self._injections: list[FailureInjection] = list(injections or [])
        self._counts: dict[str, int] = {}
        self.triggered: list[TriggeredInjection] = []

    @property
    def enabled(self) -> bool:
        """True only when at least one rule is configured."""
        return bool(self._injections)

    @property
    def rules(self) -> list[FailureInjection]:
        """Configured rules (copy)."""
        return list(self._injections)

    def fire_count(self, failure_id: str) -> int:
        """How many times a rule has fired."""
        return self._counts.get(failure_id, 0)

    def should_fail(self, point: FailurePoint, target: str) -> InjectedFailureError | None:
        """Return an error when a rule matches and may still fire, else None.

        Disabled injector (no rules) always returns None — injection is
        impossible unless a scenario explicitly opted in.
        """
        if not self.enabled:
            return None
        for injection in self._injections:
            if injection.point != point:
                continue
            if not _target_matches(injection.target_component, target):
                continue
            count = self._counts.get(injection.failure_id, 0)
            if injection.max_triggers is not None and count >= injection.max_triggers:
                continue
            self._counts[injection.failure_id] = count + 1
            self.triggered.append(
                TriggeredInjection(
                    failure_id=injection.failure_id,
                    point=injection.point,
                    type=injection.type,
                    target_component=target,
                    fire_index=count + 1,
                )
            )
            return InjectedFailureError(injection)
        return None

    def maybe_raise(self, point: FailurePoint, target: str) -> None:
        """Raise immediately when a rule matches (caller handles recovery)."""
        error = self.should_fail(point, target)
        if error is not None:
            raise error
