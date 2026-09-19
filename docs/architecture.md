# CASEFILE Architecture Overview

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
│  │  │ PostgreSQL │  │   Redis    │  │   LLM      │  │ OpenTelemetry  │   │  │
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

PostgreSQL stores:
- Claims and documents
- Workflow runs and states
- Agent execution records
- Tool execution records
- Audit events
- Checkpoint metadata

Redis provides:
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
