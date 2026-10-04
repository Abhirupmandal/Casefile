# Failure Engineering

**CASEFILE Phase 6 — Deterministic Failure Injection Architecture**

---

## Overview

The failure engineering subsystem (`src/casefile/evaluation/`) provides:

1. Deterministic, repeatable fault injection scoped exclusively to evaluation runs
2. Typed failure classification taxonomy for every outcome
3. Containment verification: failures must not leak state or corrupt downstream components
4. Auditable injection history per scenario

**Production code never imports from `casefile.evaluation.injector` or `casefile.evaluation.failures`.**
Injection is fully opt-in — disabled by default whenever the injection list is empty.

---

## Architecture

```
FailurePlan (immutable)
    |
    v
FailureRule (typed FaultType -> FailureInjection mapping)
    |
    v
FailureInjector (runtime, stateful)
    |
    +-- should_fail(point, target) -> InjectedFailureError | None
    +-- injected_history: list[InjectedFailure]
    +-- enabled: bool
```

---

## FailurePoint Taxonomy

| FailurePoint | When it fires |
|---|---|
| BEFORE_AGENT | Before an agent invocation |
| AFTER_AGENT | After an agent invocation |
| BEFORE_TOOL | Before a tool invocation |
| AFTER_TOOL | After a tool invocation |
| BEFORE_PERSISTENCE_COMMIT | Before a database write |
| AFTER_PERSISTENCE_COMMIT | After a database write |

---

## FaultType Taxonomy (15+ types)

| FaultType | Mapped FailurePoint | Recovery |
|---|---|---|
| PROVIDER_ERROR | BEFORE_AGENT | RETRY_STEP |
| PROVIDER_TIMEOUT | BEFORE_AGENT | RETRY_STEP |
| MALFORMED_AGENT_OUTPUT | AFTER_AGENT | RETRY_STEP |
| TOOL_ERROR | BEFORE_TOOL | PROPAGATE |
| TOOL_TIMEOUT | BEFORE_TOOL | PROPAGATE |
| TOOL_UNAVAILABLE | BEFORE_TOOL | PROPAGATE |
| PERSISTENCE_ERROR | BEFORE_PERSISTENCE_COMMIT | PROPAGATE |
| CHECKPOINT_WRITE_ERROR | BEFORE_PERSISTENCE_COMMIT | PROPAGATE |
| CHECKPOINT_CORRUPT | AFTER_PERSISTENCE_COMMIT | PROPAGATE |
| BUDGET_EXCEEDED | BEFORE_AGENT | PROPAGATE |
| MAX_STEPS_EXCEEDED | BEFORE_AGENT | PROPAGATE |
| MAX_REWORK_EXCEEDED | BEFORE_AGENT | PROPAGATE |
| APPROVAL_UNAUTHORIZED | BEFORE_AGENT | PROPAGATE |
| TELEMETRY_ERROR | BEFORE_TOOL | RETRY_STEP |
| NONE | N/A | N/A |

---

## FailureClassification Taxonomy

Every scenario outcome is classified with:

| Field | Type | Example |
|---|---|---|
| category | FailureCategory | TOOL_FAILURE |
| stage | FailureStage | INVESTIGATION |
| type | FaultType | TOOL_ERROR |
| originating_component | str | "tool:policy_lookup" |
| recoverable | bool | True |
| retry_attempted | bool | False |
| retry_succeeded | bool | False |
| terminal_state | str | "FAILED" |
| contained | bool | True |

Classifier logic (`classify_failure`) derives all fields from:
- `status` (PASS/FAIL/ERROR)
- `terminal_state`
- `terminal_reason`
- `errors` tuple

---

## Injection Scoping

- `FailureInjector.__enter__` activates the injector
- `FailureInjector.__exit__` clears all rules and counts
- `is_active` property indicates whether injection is in scope

This ensures injection is impossible outside an explicit evaluation context.

---

## Usage Example

```python
from casefile.evaluation.injector import FailureInjector, FailurePlan, FailureRule
from casefile.evaluation.model import FaultType

rule = FailureRule(
    rule_id="r1",
    fault_type=FaultType.PROVIDER_TIMEOUT,
    target_component="agent:investigator",
    max_triggers=1,
)
plan = FailurePlan(rules=(rule,))
injector = FailureInjector(plan=plan)

# Fires once, then returns None
err = injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:investigator")
assert err is not None
assert injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:investigator") is None
```

---

## References

- `src/casefile/evaluation/injector.py` — Enhanced FailureInjector
- `src/casefile/evaluation/failures.py` — Base FailureInjector
- `src/casefile/evaluation/classifier.py` — FailureClassification derivation
- `src/casefile/evaluation/dataset.py` — 30 deterministic evaluation scenarios
- `docs/phase-6-completion.md` — Phase 6 completion and gate results
