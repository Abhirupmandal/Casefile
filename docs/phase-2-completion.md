# CASEFILE Phase 2 Completion Report: Workflow Engine + Specialized Agents

**Document Version**: 2.0  
**Phase**: Phase 2 (Consolidated: Original Phase 3 Workflow/State Engine + Original Phase 4 Specialized Agents)  
**Status**: COMPLETE  
**Gate Verdict**: PASS  

---

## 1. Executive Summary

Phase 2 transforms the Phase 1 typed foundation into a deterministic, production-ready multi-agent workflow engine and specialized agent orchestration system for CASEFILE.

In accordance with architectural specifications:
- The workflow state machine enforces explicit transitions across all 14 `WorkflowState`s with immutable terminal states.
- The `SupervisorRouter` performs deterministic next-hop routing without requiring LLM invocations.
- LangGraph is integrated via strongly typed `GraphState` and compiled state graphs with conditional supervisor dispatch.
- Three specialized agents (`ExtractorAgent`, `InvestigatorAgent`, `ReviewerAgent`) execute bounded structured tasks.
- Agents interact strictly across typed boundaries through `ContractEnvelope` handoffs. No raw dictionaries or unstructured messages cross agent boundaries.
- All untrusted document texts are isolated within explicit delimiters (`UNTRUSTED_DOCUMENT_CONTENT_BEGIN/END`). Models cannot alter control plane routing, state, or budgets.
- Provider interaction is fully abstracted behind `LLMProvider`, backed by a scripted `DeterministicProvider` for 100% offline, reproducible testing.
- Bounded retry policies and bounded reviewer rework (max 3 cycles) prevent runaway execution.
- 100% of unit tests pass (540 passed), coverage is enforced, Black, Ruff, and strict Mypy checks pass with zero errors.

---

## 2. Files Created & Modified

### New Files Created
- `src/casefile/workflow/errors.py`: Unified error taxonomy for workflow, transition, contract validation, provider, retry, and rework violations.
- `src/casefile/workflow/runner.py`: Deterministic multi-agent workflow runner orchestrating Extractor, Investigator, and Reviewer lifecycles with bounded retry, rework loops, and lifecycle hook emissions.
- `tests/unit/test_phase2_acceptance.py`: Exhaustive acceptance test matrix covering Items A through AB and End-to-End Scenarios 1 through 6 (34 tests).
- `docs/phase-2-completion.md`: This completion specification and report.

### Files Enhanced & Modified
- `src/casefile/workflow/transitions.py`: Added `StateDeclaration`, `incoming_triggers`, `incoming_sources`, and `state_declaration(state)` inspection API.
- `src/casefile/workflow/context.py`: Added `WorkflowContext` carrying typed control plane state, `untrusted_documents` separation, and execution record tracking.
- `src/casefile/workflow/hooks.py`: Extended `WorkflowHookEvent` with `AGENT_STARTED`, `AGENT_COMPLETED`, `AGENT_FAILED`, `TRANSITION_APPLIED`, `REWORK_REQUESTED`, `WORKFLOW_TERMINAL`.
- `src/casefile/workflow/nodes.py`: Enhanced `GraphState` with explicit typed contract fields (`claim_input`, `extraction_result`, `investigation_result`, `review_result`).
- `src/casefile/workflow/graph.py`: Implemented `build_agent_graph(extractor, investigator, reviewer)` compiling active LangGraph topologies with supervisor dispatch while preserving backward compatibility.
- `src/casefile/workflow/__init__.py`: Registered lazy exports for newly introduced models, errors, and runner facilities.
- `src/casefile/models/envelope.py`: Made `ContractEnvelope.unwrap[T](expected_model: type[T]) -> T` generic and type-safe.
- `src/casefile/agents/results.py`: Propagated `workflow_run_id` and `input_contract_version` on `AgentExecutionRecord`.
- `src/casefile/agents/base.py`: Bound `workflow_run_id` and contract versions on all successful and failed execution attempts.

---

## 3. Workflow State Machine & Transition Table

The state machine implements all 14 `WorkflowState`s defined in Phase 1:

```
                          ┌──────────────┐
                          │   RECEIVED   │
                          └──────┬───────┘
                                 │ CLAIM_VALIDATED
                                 ▼
                          ┌──────────────┐
                          │  EXTRACTION  │
                          └──────┬───────┘
                                 │ EXTRACTION_SUCCEEDED
                                 ▼
                       ┌───────────────────┐
                ┌─────►│   INVESTIGATION   │◄─────┐
                │      └─────────┬─────────┘      │
                │                │ INVESTIGATION_SUCCEEDED
                │                ▼                │
                │      ┌───────────────────┐      │
                │      │      REVIEW       │      │
                │      └─────────┬─────────┘      │
                │                │                │
REWORK_REQUESTED│    ┌───────────┴───────────┐    │ REWORK_DISPATCHED
(rework_count<3)│    │           │           │    │
                │    ▼           ▼           ▼    │
                │ APPROVE      REJECT      REWORK ┘
                │    │           │           │
                │    │           │           ▼
                │    │           │      REWORK_LOOP
                │    │           │           │ REWORK_EXHAUSTED (>= 3)
                │    ▼           ▼           ▼
                │ HUMAN_APPROVAL REJECTED  FAILED / ESCALATION
                ▼
            TERMINAL
```

### State Characteristics

Every state declares its operational characteristics:
- **Terminal States (Immutable)**: `APPROVED`, `REJECTED`, `FAILED`, `TIMEOUT`, `ESCALATION`, `CANCELLED`, `BUDGET_EXHAUSTED`, `MAX_STEPS_EXCEEDED`. Attempting any transition from a terminal state raises `TerminalStateError`.
- **Active Operational States**: `RECEIVED`, `EXTRACTION`, `INVESTIGATION`, `REVIEW`, `REWORK_LOOP`, `HUMAN_APPROVAL`.
- **Authorization**: Transitions enforce `allowed_actors` (Supervisor, System, Human).

---

## 4. Supervisor Routing & LangGraph Architecture

### SupervisorRouter
The `SupervisorRouter` inspects `WorkflowSnapshot` and deterministically decides the next architectural node:
- `RECEIVED` / `EXTRACTION` → `NodeName.EXTRACTOR`
- `INVESTIGATION` / `REWORK_LOOP` → `NodeName.INVESTIGATOR`
- `REVIEW` → `NodeName.REVIEWER`
- `HUMAN_APPROVAL` → `NodeName.HUMAN_APPROVAL` (parks the run)
- Terminal states → `NodeName.END`

The supervisor does not parse documents, interpret policy, or perform fraud checks; it acts purely as a deterministic orchestrator.

### LangGraph Topology
```
           START
             │
             ▼
     ┌──────────────┐
     │  SUPERVISOR  │◄───────────────────────┐
     └──────┬───────┘                        │
            │ conditional                    │
   ┌────────┼────────┬───────────────┐       │
   ▼        ▼        ▼               ▼       │
EXTRACTOR INVESTIGATOR REVIEWER HUMAN_APPROVAL│
   │        │        │               │       │
   └────────┴────────┴───────────────┴───────┘
                                     │
                                     ▼
                                    END
```
All specialist nodes return control to the `SUPERVISOR`, ensuring single-responsibility routing and central state governance.

---

## 5. Specialized Agents

| Agent | Input Contract | Output Contract | Role | Security / Boundary |
|---|---|---|---|---|
| **Extractor** | `ExtractionRequest` | `ExtractionResult` | Parses claim details, damage descriptions, amounts | Treats document content as untrusted input; no adjudication or tools |
| **Investigator** | `InvestigationRequest` | `InvestigationResult` | Gathers structured policy/prior-claims/damage facts | Operates against abstract data boundaries; does not approve |
| **Reviewer** | `ReviewRequest` | `ReviewResult` | Evaluates completeness and consistency | Recommends APPROVE, REJECT, or REWORK; cannot execute final approval |

---

## 6. Typed Agent Handoffs & Boundary Guarantees

Agents never communicate via raw dicts or unstructured messages. All handoffs pass through `ContractEnvelope`:

```
ExtractionRequest ──[Envelope]──► ExtractorAgent ──[Envelope]──► ExtractionResult
                                                                       │
InvestigationRequest ◄──[Envelope]─────────────────────────────────────┘
         │
         ▼
InvestigatorAgent ──[Envelope]──► InvestigationResult
                                          │
ReviewRequest ◄──[Envelope]───────────────┘
      │
      ▼
ReviewerAgent ──[Envelope]──► ReviewResult (APPROVE / REJECT / REWORK)
```

Each envelope strictly verifies:
- Registered `contract_type`
- Schema version compatibility
- Sender and recipient identity
- Runtime Pydantic payload type validation (via `unwrap[T](ModelClass)`)

---

## 7. Provider Abstraction & Deterministic Testing

- **`LLMProvider` Protocol**: Provider-agnostic interface consuming `LLMRequest` and yielding `LLMResponse` with token and latency metadata.
- **`DeterministicProvider`**: Scriptable in-memory provider supporting pre-queued responses (`push_json`, `push_response`, `push_error`, `push_timeout`).
- **Zero Live Dependencies**: All unit and acceptance tests execute 100% offline without network calls or API keys.

---

## 8. Bounded Resilience: Retries and Rework

### Retries
- Bounded attempt loop (`max_attempts` configured per agent, default 3).
- Structured error categorization: `PROVIDER_ERROR`, `TIMEOUT`, `VALIDATION_ERROR`, `MALFORMED_OUTPUT`.
- Transient errors trigger retry; non-retryable errors or budget exhaustion fail immediately.
- Attempt count is recorded on each typed `AgentExecutionRecord`.

### Rework Loop
- Reviewer can emit `decision="REWORK"` with `rework_feedback`.
- Re-routes workflow to `REWORK_LOOP` → `INVESTIGATION` → `REVIEW`.
- Hard ceiling at `max_rework_cycles` (default 3).
- Exceeding the rework limit deterministically transitions the workflow to `WorkflowState.FAILED` or `ESCALATION` via `Trigger.REWORK_EXHAUSTED`.

---

## 9. Trust Boundaries & Error Taxonomy

### Trust Boundary
- **Untrusted Content**: Claim descriptions, uploaded documents, external evidence, and model-generated strings. These are quarantined between `UNTRUSTED_DOCUMENT_CONTENT_BEGIN` and `UNTRUSTED_DOCUMENT_CONTENT_END` markers in prompts.
- **Trusted Control Plane**: Workflow states, state transitions, budget ceilings, retry counts, rework counts, and actor authorizations.

### Error Taxonomy (`casefile.workflow.errors`)
- `WorkflowError`: Base exception
- `InvalidTransitionError`: Attempted illegal transition
- `TerminalStateError`: Attempted transition from immutable terminal state
- `ActorNotAllowedError`: Actor lacks permission for transition
- `ReworkLimitError`: Reviewer rework cycles exceeded
- `RetryExhaustedError`: Agent retries exhausted
- `MalformedOutputError`: Unparseable model output
- `ContractValidationError`: Payload schema mismatch
- `WorkflowExecutionError`: General workflow failure

---

## 10. Acceptance Test Matrix (Items A–AB)

| Item | Requirement | Verification Method | Status |
|---|---|---|:---:|
| **A** | Workflow State Validation | Verified all 14 states, active vs terminal flags | PASS |
| **B** | Transition Table | Verified all legal transitions across the lifecycle | PASS |
| **C** | Invalid Transition Rejection | Verified invalid transitions raise `InvalidTransitionError` | PASS |
| **D** | Terminal State Immutability | Verified all 8 terminal states reject subsequent transitions | PASS |
| **E** | Workflow Context Typing | Verified strongly typed `WorkflowContext` & document isolation | PASS |
| **F** | Supervisor Routing | Verified deterministic supervisor routing for all states | PASS |
| **G** | LangGraph Graph Construction | Verified state graph compilation and node topology | PASS |
| **H** | Graph Execution | Verified full LangGraph execution using deterministic agents | PASS |
| **I** | Agent Interface | Verified typed input/output and execution records | PASS |
| **J** | Deterministic Provider | Verified deterministic provider response queueing | PASS |
| **K** | Provider Error | Verified provider error classification and retry handling | PASS |
| **L** | Provider Timeout | Verified provider timeout handling and error category | PASS |
| **M** | Malformed Output | Verified raw text / malformed JSON rejected with retry | PASS |
| **N** | Schema Validation | Verified missing fields and invalid types raise errors | PASS |
| **O** | Extractor Behavior | Verified ExtractorAgent parsing and structured output | PASS |
| **P** | Investigator Behavior | Verified InvestigatorAgent evidence gathering and summary | PASS |
| **Q** | Reviewer Behavior | Verified ReviewerAgent evaluation and rework feedback | PASS |
| **R** | Typed Handoffs | Verified `ContractEnvelope` wrapping, validation, and unwrapping | PASS |
| **S** | Prompt Versioning | Verified versioned prompts and prompt retrieval | PASS |
| **T** | Trust Boundary | Verified untrusted content markers quarantine claim text | PASS |
| **U** | Retry Bounds | Verified bounded retry ceiling prevents infinite attempts | PASS |
| **V** | Rework Bounds | Verified bounded rework ceiling (3 cycles max) | PASS |
| **W** | Error Taxonomy | Verified complete typed exception hierarchy | PASS |
| **X** | Execution Identity Propagation | Verified `workflow_run_id` and `correlation_id` across steps | PASS |
| **Y** | Lifecycle Hooks | Verified emission of all lifecycle hook events | PASS |
| **Z** | Full Nominal Lifecycle | Verified end-to-end nominal workflow via `WorkflowRunner` | PASS |
| **AA** | Full Rework Lifecycle | Verified complete rework loop returning to investigation | PASS |
| **AB** | Terminal Failure Lifecycle | Verified workflow reaches terminal failure upon agent error | PASS |

---

## 11. End-to-End Scenarios

| Scenario | Objective | Observed Result | Status |
|---|---|---|:---:|
| **Scenario 1** | Nominal Workflow: Intake → Extractor → Investigator → Reviewer → HUMAN_APPROVAL | Completed all transitions; all 3 agents executed once; parked at HUMAN_APPROVAL | PASS |
| **Scenario 2** | Review Rework: Reviewer requests rework; Investigator re-executes; Reviewer approves | Rework loop executed cleanly; rework_count incremented; terminated at HUMAN_APPROVAL | PASS |
| **Scenario 3** | Provider Retry: One deterministic failure followed by success | Provider failed on attempt 1; retried and succeeded on attempt 2; attempt_number=2 | PASS |
| **Scenario 4** | Malformed Output: Provider yields invalid JSON | Validation failed; error logged; unparseable output prevented from entering state | PASS |
| **Scenario 5** | Terminal Safety: Attempt transition on already terminated workflow | Transition raised `TerminalStateError`; state and execution history remained unchanged | PASS |
| **Scenario 6** | Full Failure: Continuous provider failures until retry exhaustion | Retry exhausted after max attempts; workflow transitioned to terminal FAILED state | PASS |

---

## 12. Quality Gates & Scope Verification

| Gate | Requirement | Actual Result | Status |
|---|---|---|:---:|
| **Pytest Unit Suite** | All tests pass | 540 passed, 0 failed, 1 warning (36.71s) | PASS |
| **Acceptance Matrix** | Matrix A–AB & Scenarios 1–6 pass | 34 passed, 0 failed | PASS |
| **Code Formatting** | Black check | 161 files checked; 0 files modified | PASS |
| **Linting** | Ruff check | 0 errors across entire codebase | PASS |
| **Type Checking** | Strict Mypy on `src/` | 86 source files checked; 0 errors | PASS |
| **Project Packaging** | Poetry check | Validated; exit code 0 | PASS |
| **System Health** | `casefile healthcheck` | CLI command executed cleanly; exit code 0 | PASS |
| **Network Isolation** | No real LLM calls in tests | Verified: all tests use `DeterministicProvider` | PASS |
| **Scope Boundaries** | No PostgreSQL/Redis persistence implementations | Confirmed: all Phase 2 execution in-memory / protocol-level | PASS |
| **LLM Token Check** | No forbidden LLM imports in `workflow/` | Verified: `test_no_llm_imports_in_workflow_package` passed | PASS |

---

## 13. Phase Boundary Confirmation

Phase 2 deliverables are complete and verified. The following items remain deferred to subsequent phases as planned:
- Phase 3 (Consolidated): Secure Tool Layer, Tool Registry, and Tool Caching.
- Phase 4 (Consolidated): Durable Persistence (SQLite repositories, checkpointing, replay).
- Later Phases: Budget Engine integration, OpenTelemetry exporter live wiring, Human-in-the-Loop web console, API deployment.

**PHASE 2 GATE: PASS**
