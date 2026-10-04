# CASEFILE Human-in-the-Loop Approval

## Implementation Status (Phase 5)

Phase 5 implements the complete Human-in-the-Loop (HITL) approval architecture:
- **Domain Models**: `ApprovalRequest`, `ApprovalDecision`, `ApprovalActor`, `ApprovalAuditRecord`, `ApprovalStatus`, `ApprovalOutcome`, `DecisionResult`.
- **Hard Control Boundary**: `HUMAN_APPROVAL` is an absolute control-plane boundary. LLMs, agents, supervisors, and tools are strictly forbidden from approving, rejecting, impersonating humans, or modifying approval state.
- **Authorization & Roles**: Explicit hierarchical roles (`CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`) enforced prior to any mutation.
- **Separation of Duties**: Requesters cannot approve their own requests.
- **State Machine**: `PENDING → APPROVED | REJECTED | EXPIRED | CANCELLED` with immutable terminal states.
- **Persistence & Audit**: Atomic SQLAlchemy transactions, Alembic migration `0008_phase5_approval_metadata.py`, and comprehensive audit event tracking without credentials or raw documents.
- **Workflow & Checkpoint Integration**: Workflows entering `HUMAN_APPROVAL` park safely with a `HUMAN_WAIT` checkpoint. Resuming preserves pending approval without automatic decisions. Deterministic replay treats recorded decisions as immutable events without live side-effects.

`request_approval`, `request_approval_with_checkpoint`, and `decide`
emit `casefile.approval.decide` spans with `casefile.operation` =
`request` / `decide`, outcome, actor type, and wait duration — no
credentials or free-form reason text. Decisions increment the
`casefile.approval.decisions` counter (low-cardinality labels only).

**Human approval is an explicit control-plane transition, not an LLM
output.** No model text, document snippet, or tool result can approve
anything: decisions require a typed command from a typed `HumanActor`
applied atomically to a versioned request.

## Lifecycle

`PENDING → APPROVED | REJECTED | EXPIRED | CANCELLED`, terminal states
immutable. Requests are created idempotently per run+checkpoint with an
auto-assigned version; decisions target an explicit expected version and
carry an idempotency key.

## Actor model

`HumanActor` admits only `role="HUMAN"`. Supervisor, extractor,
investigator, reviewer, model, tool, and unknown actors fail at the
type boundary (proven per-actor in tests) and again at the engine's
transition table, which only accepts `HUMAN` for grant/reject triggers.

## Concurrency

Decisions use conditional single-statement UPDATEs
(`status=PENDING AND version=expected`): exactly one concurrent decider
wins; losers get deterministic `CONFLICT`. Sessions expire identity-map
state after failed conditional writes so losers never mistake their own
uncommitted values for committed data.

## Idempotency

Same key + same verdict replays the stored outcome with no new audit or
transition side effects. Same key + different verdict, or any attempt
after terminal, yields `CONFLICT`. Stale versions are rejected without
touching state.

## Expiration / cancellation

Explicit operations only, PENDING-only, atomic, audited. Expiry never
approves; `expire_if_past_due` is a no-op before the deadline (no
background sweeps in Phase 9). Cancellation preserves the distinct
`CANCELLED` status durably and never rewrites it as rejection —
although it drives the `APPROVAL_REJECTED` engine trigger (with the
cancellation preserved in record, audit, and reason), since the
transition table has no separate cancelled edge.

## Checkpoint relationship

`request_approval_with_checkpoint` persists the approval row and the
`HUMAN_WAIT` checkpoint in one transaction: approval-required state
always has its request and checkpoint, and vice versa. Resume
reconstructs the wait without re-running reviewers, duplicating
requests, auto-approving, or consuming agent steps.

## Replay behavior

Replay feeds recorded decisions into engine triggers
(`decision_trigger`), never contacts humans, creates no production
approvals, and fails loudly on missing artifacts.

## Budget interaction

Waiting accrues wall-clock time against the durable start timestamp but
consumes no steps, tokens, or retries. After approval, budget gates run
before any further work; an exhausted budget keeps the workflow
terminal even though the human decision itself remains valid and stored.

## Rejection behavior

Rejection drives an explicit terminal `REJECTED` workflow state with
the recorded reason. No automatic payout/writeback exists anywhere in
the system. Human-requested rework is out of scope: rejection
terminates (documented, no unbounded approval→rework loop).

## Audit events

Requested, granted, rejected, expired, cancelled, and rejected
attempts (stale/conflict/forbidden) are all persisted as typed
`AuditEvent`s with actor, run, approval ID, correlation, timestamp,
and outcome. Duplicate deliveries intentionally emit no second
decision event. No secrets or credentials are stored.
