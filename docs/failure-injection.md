# CASEFILE Failure Injection

## Implementation Status (Phase 11)

Typed failure injection lives in `src/casefile/evaluation/failures.py` and is
consulted exclusively by `ScenarioRunner` (`src/casefile/evaluation/runner.py`).
Production code has no `evaluation_mode` branch. Injection is disabled by
default: an empty `initial.injections` list makes every `should_fail` call
return `None`. Only a typed `EvaluationScenario` can configure rules —
untrusted claim/model/tool text cannot construct or activate injections.

Golden coverage: G03 (`BEFORE_TOOL` → `UNAVAILABLE_DEPENDENCY`), G07
(provider retry exhaustion → `MAX_AGENT_RETRIES_EXCEEDED`), G08 (budget
steps exhaustion), G11 (corrupt checkpoint → `CheckpointCorruptError`),
G12/G13 (budget and approval races), G14 (missing replay artifact), G15
(broken telemetry).

Unit matrix: `tests/unit/test_evaluation_core.py` (`TestFailureInjector` —
all 15 `FailurePoint`s, all 7 `FailureType`s, max-triggers, wildcard and
kind-suffix targets). Integration: `tests/integration/test_evaluation_scenarios.py`.

---

## Overview

Failure injection lets evaluation scenarios exercise real recovery paths
without random chaos or code/callback injection. Rules are declarative,
bounded, and offline.

## Taxonomy

### Failure Points (15)

| Point | Fires when |
|-------|------------|
| `BEFORE_AGENT` / `AFTER_AGENT` | Around each agent invocation |
| `BEFORE_TOOL` / `AFTER_TOOL` | Around each tool execution |
| `BEFORE_WORKFLOW_TRANSITION` / `AFTER_WORKFLOW_TRANSITION` | Around state transitions |
| `BEFORE_CHECKPOINT_CREATE` / `AFTER_CHECKPOINT_CREATE` | Around durable checkpoints |
| `BEFORE_BUDGET_RESERVE` / `AFTER_BUDGET_RESERVE` | Around budget pre-flight |
| `BEFORE_APPROVAL_DECIDE` / `AFTER_APPROVAL_DECIDE` | Around human decision apply |
| `BEFORE_PERSISTENCE_COMMIT` / `AFTER_PERSISTENCE_COMMIT` | Around unit-of-work commit |
| `DURING_REPLAY_ARTIFACT_RETRIEVAL` | During recorded-output load |

### Failure Types (7)

`EXCEPTION`, `TIMEOUT`, `UNAVAILABLE_DEPENDENCY`, `MALFORMED_RESULT`,
`MISSING_ARTIFACT`, `INTEGRITY_FAILURE`, `CONCURRENCY_CONFLICT`.

### Recovery Behaviors (4 + propagate)

| Behavior | Expected recovery |
|----------|-------------------|
| `RETRY_STEP` | Bounded retry within the node |
| `SKIP_OPERATION` | Continue without the optional op |
| `TERMINATE_NODE` | Node fails; workflow may continue or fail closed |
| `TERMINATE_WORKFLOW` | Latch a terminal state |
| `PROPAGATE` | Surface as typed error (default) |

## Rule Shape

```python
FailureInjection(
    failure_id="G03-TOOL-001",
    point=FailurePoint.BEFORE_TOOL,
    type=FailureType.UNAVAILABLE_DEPENDENCY,
    target_component="tool:policy_lookup",
    max_triggers=1,
    expected_recovery_behavior=RecoveryBehavior.TERMINATE_NODE,
)
```

- `target_component="*"` matches everything.
- Otherwise exact match, `kind:name` suffix match, or reverse suffix.
- `max_triggers=None` means unbounded within the scenario (still typed).

## Safety Properties

1. **Opt-in only** — empty list disables every check.
2. **Scenario-owned** — only `EvaluationScenario.initial.injections`.
3. **No production hooks** — production modules never import `FailureInjector`.
4. **Deterministic** — same scenario + seedless scripts → same fire history.
5. **Bounded** — `max_triggers` caps repeated fires; report records each fire.

## Report Integration

Fires appear on `EvaluationResult.failure_injections` as
`TriggeredInjection(failure_id, point, type, target_component, fire_index)`.
Metrics count scenarios with injections or errors for
`failure_scenarios` / `failure_recovered` (rate clamped to ≤ 1.0).
