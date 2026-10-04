# CASEFILE Tool Layer (Phase 5 Implementation)

Read-only, typed, authorized tools behind `ToolRegistry`
(`src/casefile/tools/`). Design follows `docs/tool-architecture.md` with
one adaptation: tools are **synchronous** (the codebase is sync; async
sketches in the architecture doc map 1:1 to the sync `Tool.run`).

## Tool registry

`ToolRegistry` (`registry.py`): explicit `register()`, stable-name
resolution, version pinning, `tools_for(agent)`, and `execute()` with the
order authorize → validate → run bounded → outcome. Agents never import
tool implementations;unknown tools and version mismatches return typed
errors. Default set via `create_default_registry()`.

## Authorization matrix (Phase 5)

| Agent | Tools |
|---|---|
| Supervisor | none |
| Extractor | `document_retrieval` |
| Investigator | `document_retrieval`, `evidence_lookup`, `claim_history_lookup`, `policy_lookup`, `repair_cost_lookup`, `fraud_signal_lookup` |
| Reviewer | `document_retrieval`, `evidence_lookup`, `policy_lookup`, `repair_cost_lookup` |
| Human / unknown | none |

Note: the Reviewer gains `evidence_lookup`, `policy_lookup`, and
`repair_cost_lookup` over the AGENTS.md draft (which listed only document
retrieval) per the Phase 5 tool-layer decision. Enforcement is two-deep:
agent `allowed_tools` then registry matrix; denials happen before any tool
code runs and emit `TOOL_AUTHORIZATION_DENIED`.

## Tool contracts

Every invocation carries `ToolCall` identities (tool/version/invocation,
claim/workflow/execution/correlation, requesting agent), typed Pydantic
input/output, status, timing, and optional `ToolError`. Tools raise
`ToolFailureError`; the registry returns `ToolOutcome` (success flag,
output or error, duration, cost units, idempotency key, full call record).
No `dict[str, Any]` interfaces.

| Tool | Input | Output |
|---|---|---|
| `document_retrieval` | document/claim refs | metadata + UNTRUSTED content |
| `policy_lookup` | policy number | typed `Policy` or not-found |
| `claim_history_lookup` | customer scope + limit | newest-first priors + totals |
| `repair_cost_lookup` | estimate/claim refs | reconciled estimate + sanity findings |
| `evidence_lookup` | claim ref + filters | normalized items with provenance |
| `fraud_signal_lookup` | claim ref | score, level, indicators |

## Trust boundaries

Tools return data; agents and the workflow decide. Results cannot touch
workflow state, budgets, approvals, permissions, prompts, or other tools.
All tools are read-only: no payouts, writeback, mutations, or messaging
(those need later human-approval boundaries). Inputs are validated
(reference patterns, scope checks, size caps); content is UNTRUSTED and
flows into the Phase 4 prompt quarantine unchanged.

## Deterministic fixtures

`fixtures.py` (`FixtureStore`, marker `SYNTHETIC-FIXTURE-DO-NOT-USE-IN-PRODUCTION`):
normal, high-value, prior-history, inconsistent-evidence, suspicious/fraud,
rework-needed, and estimate-inconsistency scenarios. Fixed constants,
sha256 digests (never `hash()`), sorted outputs. SQLite repositories can
replace the store later behind the same typed accessors.

## Budget integration boundary

`ToolUsage` counters (calls, latency, cost units) ride `ToolContext` and
are bumped per execution; `ToolOutcome` reports duration + cost units.
Phase 8 enforces max calls/latency/cost from this metadata. Thread-pool
timeouts bound every call; read-only tools carry stable idempotency keys
for Phase 7 replay.

## Implementation Status (Phase 8)

Durable `tool_calls` budget is gated via `BudgetEngine.pre_step` before
registry execution, and per-call latency/cost metadata accumulates into
`BudgetUsage`. The registry itself is unchanged; budgeting wraps it at
the orchestration layer.
