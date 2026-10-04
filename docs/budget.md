# CASEFILE Budget & Termination Engine (Phase 8 Implementation)

## Implementation Status (Phase 11)

Evaluation verifies budget enforcement offline: G01 asserts non-zero cost
with explicit `deterministic/eval-stub` pricing, G08 proves
`max_steps=2` → `MAX_STEPS_EXCEEDED` with trigger mapping
(`STEPS_EXHAUSTED`), G12 exercises the final-unit reservation race
(exactly one winner). Invariant D never allows an envelope breach.

## Implementation Status (Phase 10)

Budget pre-flight, terminate, and model-call accounting emit
`casefile.budget.check` spans and `casefile.budget.*` metrics when an
`Observability` handle is supplied; denial increments
`casefile.budget.exhaustions` with a low-cardinality `reason` label.
Telemetry is optional and out-of-band — without `obs`, enforcement is
byte-for-byte unchanged; with a broken exporter, reservations and
termination still succeed.

## Implementation Status (Phase 9)

Waiting for approval accrues wall-clock time against the durable start
timestamp but consumes no steps, tokens, or retries. After a decision,
budget gates run before any further work: an exhausted budget keeps the
workflow terminal even though the human decision itself remains valid
and stored. Approval decisions do not reset any counter.

Every run carries an immutable `BudgetEnvelope` (10 finite limits) and
durable `BudgetUsage`. Enforcement lives in `BudgetEngine`
(`src/casefile/budget/`); persistence in `budget_counters`,
`run_budget_envelopes`, `checkpoint_budgets` (+ extended
`budget_snapshots`); migration `0005`.

## Vocabulary

Fine-grained `BudgetTermination` codes (MAX_STEPS, MAX_AGENT_STEPS,
MAX_TOOL_CALLS, MAX_REWORK, MAX_AGENT_RETRIES, MAX_WALL_CLOCK,
MAX_INPUT/OUTPUT/TOTAL_TOKENS, MAX_COST) map onto the existing Phase 3
terminal states (`MAX_STEPS_EXCEEDED`, `MAX_REWORK_EXCEEDED`, `TIMEOUT`,
`BUDGET_EXHAUSTED`). No existing reason was replaced; structured
outcomes always keep the fine-grained code.

## Accounting semantics

- Workflow step: one gated state transition.
- Agent step: one agent invocation granted before execution.
- Tool call: one registry execution granted before running.
- Retry: gated via `should_retry`/`make_retry_guard`; each granted retry
  consumes one retry unit AND counts in usage; denial stops the agent
  with `MAX_AGENT_RETRIES_EXCEEDED`.
- Rework cycle: one granted rework unit per reviewer→rework request;
  the engine bound and the durable counter agree (4th request at
  max=3 terminates).
- Tokens: reserved pre-call atomically against `max_total_tokens`;
  actuals reconciled post-call. Cost = tokens × versioned pricing,
  exact `Decimal`, persisted per call.

## Reservation semantics

Discrete units use single-statement conditional UPDATEs
(`counter < limit`): exactly one concurrent worker wins; losers get a
deterministic denial. Reservations are non-refundable consumption
claims keyed by idempotency key — a same-key retry replays the prior
verdict instead of consuming again (with the Phase 7 ledger).

## Crash semantics (Case F)

A crash between reserve and completion costs at most one unit and leaks
nothing unboundedly: there is no hold/release protocol to leak, and the
idempotency ledger prevents double-charge on keyed retries. An
unkeyed crash-retry consumes one fresh unit — bounded and auditable.

## Termination rules

Terminal reasons latch monotonically in `run_budget_envelopes`; a
second latch attempt fails. After latching, every gate (steps, tokens,
checks, resume, replay seed) refuses work, so stale workers, resumed
runs, and concurrent racers all observe termination. Fail-closed: any
undecidable gate denies execution.

## Replay semantics

Replay restores envelope + usage from checkpoint payloads, runs against
recorded provider/tool outputs, and reproduces the same terminal state,
path, and budget outcome. Replay uses isolated counters and never
writes production usage rows.

## Configuration ownership

Only trusted control-plane code (`BudgetEnvelope.from_app_config`,
orchestration setup) defines limits. Claim documents, model outputs,
tool outputs, prompts, and untrusted text have no path to budget
values — the engine accepts only typed envelope/usage inputs.

## Boundaries

Budget pre-flight/terminate/model-call accounting emit
`casefile.budget.check` spans and the `casefile.budget.*` metrics
(Phase 10); structured `summary()` remains available for consumers that
do not need OTel. No payouts, HITL, evaluation, or API. Budgets bound
execution only.
