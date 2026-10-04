"""Tests for deterministic failure injection framework (Phase 6).

Verifies FaultTypes, FailureRule, FailurePlan, FailureInjector lifecycle,
isolation, and determinism.
"""

from __future__ import annotations

from casefile.evaluation.failures import (
    FailurePoint,
    FailureType,
)
from casefile.evaluation.injector import (
    FailureInjector,
    FailurePlan,
    FailureRule,
)
from casefile.evaluation.model import FaultType


def test_all_fifteen_fault_types_defined() -> None:
    """All 15 required fault types must be present in the FaultType enum."""
    required = {
        "PROVIDER_ERROR",
        "PROVIDER_TIMEOUT",
        "MALFORMED_AGENT_OUTPUT",
        "TOOL_ERROR",
        "TOOL_TIMEOUT",
        "TOOL_UNAVAILABLE",
        "PERSISTENCE_ERROR",
        "CHECKPOINT_WRITE_ERROR",
        "TELEMETRY_ERROR",
        "INVALID_CONTRACT",
        "UNAUTHORIZED_APPROVAL",
        "REPLAY_MISMATCH",
        "BUDGET_EXHAUSTION",
        "MAX_STEPS_EXCEEDED",
        "MAX_REWORK_EXCEEDED",
    }
    actual = {f.value for f in FaultType}
    assert required.issubset(actual)


def test_failure_rule_conversion_to_injection() -> None:
    """FailureRule correctly maps FaultType to underlying runner injection."""
    rule = FailureRule(
        rule_id="test-rule-1",
        fault_type=FaultType.PROVIDER_ERROR,
        target_component="agent:extractor",
        max_triggers=2,
    )
    inj = rule.to_failure_injection()
    assert inj.failure_id == "test-rule-1"
    assert inj.point == FailurePoint.BEFORE_AGENT
    assert inj.type == FailureType.EXCEPTION
    assert inj.target_component == "agent:extractor"
    assert inj.max_triggers == 2


def test_failure_plan_conversion() -> None:
    """FailurePlan groups multiple rules into a frozen plan."""
    rule1 = FailureRule(
        rule_id="r1",
        fault_type=FaultType.TOOL_ERROR,
        target_component="tool:policy_lookup",
    )
    rule2 = FailureRule(
        rule_id="r2",
        fault_type=FaultType.MALFORMED_AGENT_OUTPUT,
        target_component="agent:extractor",
    )
    plan = FailurePlan(plan_id="plan-1", name="Test Plan", rules=(rule1, rule2))
    injections = plan.to_injections()
    assert len(injections) == 2
    assert injections[0].failure_id == "r1"
    assert injections[1].failure_id == "r2"


def test_injector_disabled_by_default() -> None:
    """An injector with no rules is disabled and never triggers."""
    injector = FailureInjector()
    assert not injector.enabled
    assert injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:extractor") is None


def test_injector_deterministic_trigger_count() -> None:
    """Injector triggers exactly up to max_triggers and stops."""
    rule = FailureRule(
        rule_id="bounded-rule",
        fault_type=FaultType.PROVIDER_ERROR,
        target_component="agent:extractor",
        max_triggers=2,
    )
    plan = FailurePlan(rules=(rule,))
    injector = FailureInjector(plan=plan)
    assert injector.enabled

    # Fire 1
    err1 = injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:extractor")
    assert err1 is not None
    assert injector.fire_count("bounded-rule") == 1

    # Fire 2
    err2 = injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:extractor")
    assert err2 is not None
    assert injector.fire_count("bounded-rule") == 2

    # Fire 3 - exhausted
    err3 = injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:extractor")
    assert err3 is None
    assert injector.fire_count("bounded-rule") == 2


def test_injector_context_manager_isolation() -> None:
    """Injector resets and deactivates after context manager exit."""
    rule = FailureRule(
        rule_id="scoped-rule",
        fault_type=FaultType.TELEMETRY_ERROR,
        target_component="sink",
    )
    plan = FailurePlan(rules=(rule,))
    with FailureInjector(plan=plan) as inj:
        assert inj.is_active
        err = inj.should_fail(FailurePoint.BEFORE_WORKFLOW_TRANSITION, "sink")
        assert err is not None

    # After exit, rules and counts are cleared
    assert not inj.enabled
    assert inj.fire_count("scoped-rule") == 0
