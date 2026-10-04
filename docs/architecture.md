# CASEFILE Architecture Overview

## Implementation Status (Phase 5)

Phase 5 establishes the Human-in-the-Loop (HITL) control boundary and production-ready distributed observability:
- **Human-in-the-Loop**: A strict control plane boundary around `HUMAN_APPROVAL`. Specialist agents and supervisor orchestrators can propose recommendations but cannot execute decisions. Multi-role authorization (`CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`), separation of duties (requester cannot approve), explicit lifecycle state machine, atomic single-statement updates, and complete audit trail.
- **Observability Architecture**: Distributed OpenTelemetry-compatible tracing and metrics. Context correlation across workflow → supervisor → agent → tool → persistence → checkpoint → approval. Bounded, sanitized telemetry with strict deny-lists and 256-character truncation. Out-of-band telemetry design where collector or network failure cannot fail core workflow execution.

## Implementation Status (Phase 10)

Observability & traceability in `src/casefile/observability/` per
`docs/observability.md` and ADR-008: guarded OpenTelemetry tracer with
null-span degradation, exact 16-metric set with enforced low-cardinality
labels, sanitized span attribute builders, optional `obs` parameters on
workflow/agent/tool/checkpoint/budget/approval surfaces, hook-event
bridge, OTLP→Jaeger export path, and in-memory exporters for tests.
Telemetry is strictly out-of-band: exporter failure never breaks
business correctness; no sensitive payloads appear in spans or labels.

## Implementation Status (Phase 9)

Human-in-the-loop approval boundary in `src/casefile/approval/` per
`docs/approval.md`: typed human actors, versioned requests, atomic
conditional decisions (single-winner concurrency), idempotent retries,
explicit expiry/cancellation, atomic approval+checkpoint creation,
resume/replay integration, budget-aware continuation, and complete
audit. Approval is control-plane, never model output.

## Implementation Status (Phase 8)

Budget & termination enforcement in `src/casefile/budget/` per
`docs/budget.md`: immutable per-run envelopes, durable usage, atomic
single-statement reservations (exactly-one-winner concurrency),
versioned pricing with exact Decimal costs, typed termination mapped
onto existing terminal states, monotonic terminal latches, wall-clock
budgets from durable timestamps, retry/rework accounting integrated
with Phase 3/4 behavior, checkpoint-pinned budget state for
resume/replay, and fail-closed gates throughout.

Durable checkpointing, resumability, and deterministic replay in
`src/casefile/checkpoint/` per `docs/checkpointing.md` and
`docs/replay.md`: verified checkpoints (integrity + versions), resume
with fresh execution identity, replay from recordings with explicit
LIVE/REPLAY modes, cross-restart idempotency ledger, and retention
pruning. No checkpoint state in Redis; no live calls during replay.

## Implementation Status (Phase 3)

Workflow/state engine implemented per this document and `state-machine.md`:
`src/casefile/workflow/` holds the explicit transition table, deterministic
engine (terminal immutability, actor authorization, rework bound,
idempotency, injected clock), pure `SupervisorRouter`, LangGraph graph with
typed `GraphState` and placeholder nodes (no intelligence — Phase 4),
typed run context, SQLite store boundary, and OTEL-ready hook events.
Proven by `tests/unit/test_workflow_*.py`, including bounded-termination
invariants over every reachable path.

## Implementation Status (Phase 4)

Specialized agents implemented in `src/casefile/agents/` per AGENTS.md:
provider-agnostic `LLMProvider` protocol (deterministic scripted provider
for tests; optional key-gated LangChain adapters for OpenAI/Anthropic),
structured-output pipeline with typed failure categories, versioned
prompts with untrusted-content quarantine, bounded retries preserving
execution identity, per-agent tool allowlists, typed execution records,
and Phase 3 lifecycle hooks. Provider fallback boundary deferred to
Phase 8/10 alongside the budget engine.

## Implementation Status (Phase 6)

Durable persistence integration in `src/casefile/storage/` per
`docs/persistence.md`: repository protocols + implementations, unit of
work, explicit mappings, migrations to `0003`, synthetic seed data, and a
repository-backed tool data source (`tools/sqlite_source.py`) so the
registry path ToolRegistry → Tool → Repository → SQLite is proven end to
end. Workflow runs persist through `SqliteWorkflowStore` on the unit of
work (restart-safe); checkpoints stay deferred to Phase 7.

## Implementation Status (Phase 5)

Secure typed tool layer in `src/casefile/tools/` per `docs/tools.md`:
authorization-first registry, six read-only tools over deterministic
fixtures, typed errors, bounded execution, idempotency keys, usage
metadata, lifecycle hooks, and registry-backed agent integration
(Extractor fetches documents; Investigator/Reviewer look up evidence
through the matrix). No external APIs, no network, no write tools.

## Executive Summary

CASEFILE is a production-oriented multi-agent AI orchestration platform designed for insurance claims intelligence and triage. It processes approximately 900 claims per day for a regional auto insurer, performing document extraction, policy verification, investigation, review, and recommendation generation with guaranteed termination and comprehensive audit trails.

## Design Philosophy

### Core Principles

1. **Determinism Over Flexibility**: The workflow is a finite state machine with explicitly defined transitions. Agents do not autonomously decide what to do next—the Supervisor orchestrates every step.

2. **Explicit Over Implicit**: All inter-agent communication uses strongly typed Pydantic contracts. No free-form message passing. No hidden state transitions. All limits enforced in code, not prompts.

3. **Observable By Design**: Every action produces OpenTelemetry-compatible traces. The complete execution path for any claim can be reconstructed from persisted data.

4. **Failure-Aware**: Every component has defined failure modes, retry policies, timeouts, and escalation paths. The system gracefully handles LLM failures, tool failures, and infrastructure issues.

5. **Reproducible**: Checkpointing and replay allow any workflow run to be resumed from a stored snapshot and reach the same terminal state (accounting for LLM nondeterminism through recorded tool outputs).

6. **Cost-Controlled**: Token budgets, monetary budgets, step limits, and time limits are enforced in code. The workflow cannot exceed defined boundaries.

## System Context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EXTERNAL SYSTEMS                               │
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Claims    │  │   Policy    │  │   Repair    │  │   Fraud     │        │
│  │   Intake    │  │   System    │  │   Estimator │  │   Database  │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
│         │                │                │                │               │
└─────────┼────────────────┼────────────────┼────────────────┼───────────────┘
          │                │                │                │
          ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CASEFILE SYSTEM                                │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         API LAYER (FastAPI)                           │  │
│  │  • REST Endpoints                                                      │  │
│  │  • WebSocket for real-time updates                                    │  │
│  │  • Authentication/Authorization                                        │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    ORCHESTRATION LAYER (LangGraph)                    │  │
│  │                                                                        │  │
│  │  ┌─────────────┐                                                      │  │
│  │  │  SUPERVISOR │ ◄─── Deterministic workflow orchestration            │  │
│  │  │    NODE     │     • State transitions                              │  │
│  │  └──────┬──────┘     • Budget enforcement                             │  │
│  │         │            • Termination guarantees                         │  │
│  │         ▼            • Checkpoint coordination                        │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │                    SPECIALIST AGENTS                             │  │  │
│  │  │                                                                  │  │  │
│  │  │  ┌───────────┐  ┌───────────────┐  ┌────────────┐              │  │  │
│  │  │  │ EXTRACTOR │  │ INVESTIGATOR  │  │  REVIEWER  │              │  │  │
│  │  │  │           │  │               │  │            │              │  │  │
│  │  │  │ Document  │  │ Policy lookup │  │ Evidence   │              │  │  │
│  │  │  │ parsing   │  │ Claim history │  │ synthesis  │              │  │  │
│  │  │  │ Data      │  │ Cost sanity   │  │ Decision   │              │  │  │
│  │  │  │ extraction│  │ check         │  │ Rework     │              │  │  │
│  │  │  └───────────┘  └───────────────┘  └────────────┘              │  │  │
│  │  │                                                                  │  │  │
│  │  │  ALL COMMUNICATION VIA TYPED CONTRACTS ◄── No free-form text    │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      INFRASTRUCTURE LAYER                             │  │
│  │                                                                        │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────────┐   │  │
│  │  │  SQLite   │  │   Redis    │  │   LLM      │  │ OpenTelemetry  │   │  │
│  │  │            │  │            │  │  Provider  │  │                │   │  │
│  │  │ Persistent │  │ Checkpoint │  │ Abstraction│  │   Collector    │   │  │
│  │  │ State      │  │ Cache      │  │            │  │                │   │  │
│  │  └────────────┘  └────────────┘  └────────────┘  └────────────────┘   │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Supervisor Node

The Supervisor is the single orchestration authority. It:
- Maintains the authoritative workflow state
- Makes all transition decisions based on typed results
- Enforces budgets and limits
- Coordinates checkpointing
- Handles failures and escalation
- Never delegates orchestration decisions to LLMs

### 2. Specialist Agents

Each specialist is an isolated execution unit with:
- Defined input contract
- Defined output contract
- Specific tools (if applicable)
- Bounded execution (timeout, token limit)
- No direct communication with other agents

#### EXTRACTOR
- **Purpose**: Parse claim documents and extract structured data
- **Input**: `ExtractionRequest`
- **Output**: `ExtractionResult`
- **Tools**: Document retrieval (mocked)
- **Failure Modes**: Malformed document, extraction timeout, schema validation failure

#### INVESTIGATOR
- **Purpose**: Verify policy, check history, validate costs
- **Input**: `InvestigationRequest`
- **Output**: `InvestigationResult`
- **Tools**: Policy lookup, claim history lookup, repair cost lookup, fraud signal lookup (all mocked)
- **Failure Modes**: Tool timeout, policy not found, data inconsistency

#### REVIEWER
- **Purpose**: Synthesize evidence, make recommendation decision, request rework
- **Input**: `ReviewRequest`
- **Output**: `ReviewResult` (includes rework request or approval)
- **Tools**: None (pure synthesis)
- **Failure Modes**: Schema validation failure, budget exhaustion during synthesis

### 3. Workflow State Machine

```
                                    ┌──────────────────┐
                                    │    RECEIVED      │
                                    └────────┬─────────┘
                                             │
                                             │ ClaimInput validated
                                             │ Initial checkpoint created
                                             ▼
                                    ┌──────────────────┐
                                    │   EXTRACTION     │
                                    └────────┬─────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              │              │              │
                      Extraction     Extraction      Extraction
                       Success        Failed         Timeout
                              │              │              │
                              ▼              ▼              ▼
                         ┌────────┐    ┌────────────┐  ┌─────────────┐
                         │ (cont) │    │ FAILED     │  │ ESCALATION  │
                         └────────┘    └────────────┘  └─────────────┘
                              │
                              ▼
                      ┌──────────────────┐
                      │  INVESTIGATION   │
                      └────────┬─────────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
         Investigation   Investigation   Investigation
           Success         Failed         Timeout
                │              │              │
                ▼              ▼              ▼
           ┌────────┐    ┌────────────┐  ┌─────────────┐
           │ (cont) │    │ FAILED     │  │ ESCALATION  │
           └────────┘    └────────────┘  └─────────────┘
                │
                ▼
         ┌──────────────────┐
         │     REVIEW       │
         └────────┬─────────┘
                  │
       ┌──────────┼──────────┐
       │          │          │
   Approved   Rework      Rejected
       │          │          │
       │          │          └──────► TERMINAL (REJECTED)
       │          │
       │          └──────► REWORK_LOOP
       │                      │
       │                      ▼
       │                ┌──────────────────┐
       │                │ Rework cycle     │
       │                │ count < MAX?     │
       │                └────────┬─────────┘
       │                         │
       │              ┌──────────┼──────────┐
       │              │                     │
       │              YES                   NO
       │              │                     │
       │              ▼                     ▼
       │         ┌─────────────┐     ┌──────────────┐
       │         │ Return to   │     │ MAX_REWORK   │
       │         │ Investigation│    │ EXCEEDED     │
       │         └─────────────┘     └──────────────┘
       │
       ▼
┌──────────────────┐
│ HUMAN_APPROVAL   │
└────────┬─────────┘
         │
    ┌────┴────┐
    │         │
Approved   Rejected
    │         │
    ▼         ▼
┌────────┐ ┌────────────┐
│APPROVED│ │ REJECTED   │
└────────┘ └────────────┘
```

### 4. Budget Control System

Enforces hard limits at the workflow level:

| Limit Type | Default | Enforcement Point |
|------------|---------|-------------------|
| Max Input Tokens | 100,000 | Before LLM call |
| Max Output Tokens | 20,000 | LLM request parameter |
| Max Total Tokens | 150,000 | After each LLM call |
| Max Cost (USD) | $5.00 | After each LLM call |
| Max Steps | 50 | Before each node execution |
| Max Execution Time | 30 min | Before each node execution |
| Max Rework Cycles | 3 | Before rework transition |

Budget violations trigger controlled termination to `BUDGET_EXHAUSTED` terminal state.

### 5. Persistence Layer

SQLite stores (local file, zero-install — see ADR-011):
- Claims and documents
- Workflow runs and states
- Agent execution records
- Tool execution records
- Audit events
- Checkpoint metadata

Tradeoff: SQLite gives every developer a reproducible local database with no
server, at the cost of single-writer semantics — it is not intended as the
production-scale multi-writer database for a large deployed workload.

Redis provides (ephemeral coordination only — never the permanent database):
- Active workflow state cache
- Checkpoint serialization cache
- Distributed locks for idempotency

### 6. Checkpoint and Replay

**Checkpoint Triggers**:
- After state transition (before node execution)
- After successful node completion
- Before human approval
- After human approval
- On any error state

**Replay Process**:
1. Load checkpoint from storage
2. Reconstruct WorkflowState
3. Resume from saved position
4. Use recorded tool outputs for determinism
5. Compare terminal states for validation

### 7. Observability Stack

All traces exported via OpenTelemetry:

```
WorkflowRun (Trace)
├── Span: state_transition (RECEIVED → EXTRACTION)
├── Span: node_execution (extractor)
│   ├── Span: llm_call (input_extraction)
│   └── Span: tool_call (document_retrieval)
├── Span: state_transition (EXTRACTION → INVESTIGATION)
├── Span: node_execution (investigator)
│   ├── Span: tool_call (policy_lookup)
│   ├── Span: tool_call (claim_history)
│   ├── Span: tool_call (repair_cost)
│   └── Span: llm_call (investigation_synthesis)
├── Span: state_transition (INVESTIGATION → REVIEW)
└── ... (continues)
```

### 8. Security Model

**Trust Boundaries**:
1. External input (claim documents) → Untrusted
2. Tool outputs (mocked data) → Semi-trusted
3. Internal state → Trusted
4. Human approval → Trusted

**Controls**:
- Input validation at API boundary
- Structured output validation after LLM calls
- Tool authorization per agent
- No direct agent-to-agent communication
- Human approval required for APPROVED terminal state

## Key Guarantees

1. **Termination**: Every workflow reaches a terminal state within bounds
2. **Auditability**: Complete execution history is persisted and traceable
3. **Reproducibility**: Stored runs can be replayed from checkpoints
4. **Cost Control**: Budgets are enforced in code, not delegated to LLMs
5. **Observability**: Every action is traceable via OpenTelemetry
6. **Failure Recovery**: Defined handling for all failure modes
7. **Data Integrity**: State transitions are atomic and versioned

## Integration Points

### Input
- REST API: `POST /api/v1/claims`
- Async processing via internal queue
- Claim documents attached as base64 or references

### Output
- REST API: `GET /api/v1/claims/{id}/recommendation`
- WebSocket: Real-time status updates
- Webhook: Terminal state notifications

### Human Approval
- REST API: `POST /api/v1/approvals/{id}`
- Dashboard UI (future)
- Email notification (future)

## Scaling Considerations

Phase 0 establishes single-instance architecture. Future phases will address:
- Horizontal scaling via stateless workers
- Redis-backed workflow coordination
- Database read replicas for audit queries
- Async claim intake queue

## References

- [State Machine Design](./state-machine.md)
- [Agent Architecture](./agent-architecture.md)
- [Agent Contracts](./agent-contracts.md)
- [Persistence Model](./persistence.md)
- [Budget Control](./budget-control.md)
- [Observability](./observability.md)
