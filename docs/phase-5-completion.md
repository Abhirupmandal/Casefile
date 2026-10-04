# Phase 5 Completion Report: Human-in-the-Loop + Observability

## 1. Executive Summary

Phase 5 of the CASEFILE Multi-Agent Insurance Claim Orchestration Platform implements:
1. **Human-in-the-Loop (HITL) Approval Architecture**: A hard control-plane security boundary ensuring LLMs, agents, supervisors, and tools cannot approve, reject, or bypass human adjudication.
2. **Role-Based Authorization & Separation of Duties**: Multi-role human authorization (`CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`) with strict requester != decider enforcement.
3. **Atomic Persistence & Full Audit Trail**: SQLite/SQLAlchemy single-statement updates preventing race conditions, complemented by durable, secret-safe audit logs.
4. **Production-Ready OpenTelemetry Observability**: End-to-end distributed tracing across workflow → supervisor → agent → tool → persistence → checkpoint → approval, structured metrics catalog, privacy-safe sanitization, and graceful degradation during collector outages.

All capabilities from Phases 0–4 (deterministic routing, checkpointing, replay mismatch detection, multidimensional budgets, token/cost accounting, and bounded termination) are preserved without regression.

---

## 2. Files Created

- `src/casefile/approval/model.py`: Strongly typed Pydantic v2 models (`ApprovalRequest`, `ApprovalDecision`, `ApprovalActor`, `HumanActor`, `ApprovalAuditRecord`, `ApprovalStatus`, `ApprovalOutcome`, `DecisionResult`), state machine validator, and authorizer.
- `src/casefile/approval/service.py`: Transactional approval service with single-statement UPDATEs, idempotency, versioning, expiry, and audit capture.
- `migrations/versions/0008_phase5_approval_metadata.py`: Alembic migration adding `deadline`, `requested_by`, and `required_role` columns to `human_approvals`.
- `tests/unit/test_phase5_acceptance.py`: Acceptance test suite covering Matrix A–CL and End-to-End Scenarios 1–8 (98 test cases).
- `docs/phase-5-completion.md`: This comprehensive completion report.

---

## 3. Files Modified

- `src/casefile/models/domain.py`: Added `requested_by` and `required_role` fields to `HumanApprovalRequest`.
- `src/casefile/models/persistence.py`: Added `deadline`, `requested_by`, and `required_role` columns to `HumanApprovalRecord`, with `decision`, `decided_by`, and `decision_reason` property aliases.
- `src/casefile/storage/mappings.py`: Updated `request_to_record` and `record_to_approval` to map approval request metadata.
- `src/casefile/observability/context.py`: Added `casefile.exec_mode` alongside `casefile.mode` for span attributes and context tagging.
- `src/casefile/observability/spans.py`: Added span constants and attribute helper mappings.
- `src/casefile/observability/bridge.py`: Added fallback handling for absent meter/tracer instruments.
- `src/casefile/observability/sanitize.py`: Implemented robust privacy sanitization and attribute redaction.
- `src/casefile/workflow/runner.py`: Integrated `approval_service`, parked state handling at `HUMAN_APPROVAL`, `apply_human_decision`, `handle_approval_timeout`, and automatic model-usage telemetry.
- `src/casefile/checkpoint/replay.py`: Resolved variable type scoping in transition comparison loop.
- `docs/approval.md`: Documented Phase 5 HITL architecture.
- `docs/observability.md`: Documented Phase 5 observability architecture.
- `docs/architecture.md`: Updated architecture overview for Phase 5.

---

## 4. Approval Architecture

- **Hard Boundary**: When a Reviewer recommends approval, the workflow engine transitions to `HUMAN_APPROVAL` and parks. The LLM cannot complete the workflow.
- **Explicit Decision Contract**: Decisions are submitted as strongly typed `ApprovalDecision` commands, requiring non-empty actor identities, roles, and decision reasons.
- **Race Condition Prevention**: Single-statement SQL updates ensure that concurrent deciders race safely: exactly one wins (`DECIDED`), and subsequent attempts return deterministic `IDEMPOTENT_REPLAY` (if keys match) or `CONFLICT`.

---

## 5. Authorization Matrix

| Role | Threshold Level | Can Approve Claim Reviewer Level | Can Approve Senior Reviewer Level | Can Approve Supervisor Level | Can Impersonate Human |
|---|---|---|---|---|---|
| `CLAIM_REVIEWER` | 1 | ✅ Yes | ❌ No | ❌ No | ❌ N/A (Human) |
| `SENIOR_REVIEWER` | 2 | ✅ Yes | ✅ Yes | ❌ No | ❌ N/A (Human) |
| `CLAIM_SUPERVISOR` | 3 | ✅ Yes | ✅ Yes | ✅ Yes | ❌ N/A (Human) |
| `ADMIN` | 4 | ✅ Yes | ✅ Yes | ✅ Yes | ❌ N/A (Human) |
| `AGENT` / `SUPERVISOR` / `TOOL` / `MODEL` | 0 | ❌ Strictly Forbidden | ❌ Strictly Forbidden | ❌ Strictly Forbidden | ❌ Strictly Forbidden |

---

## 6. Approval State Machine

```
               ┌─────────────┐
               │   PENDING   │
               └──────┬──────┘
        ┌─────────────┼─────────────┬─────────────┐
        ▼             ▼             ▼             ▼
  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
  │ APPROVED  │ │ REJECTED  │ │  EXPIRED  │ │ CANCELLED │
  └───────────┘ └───────────┘ └───────────┘ └───────────┘
   (Terminal)    (Terminal)    (Terminal)    (Terminal)
```

- Invalid transitions (e.g. `APPROVED → REJECTED`, `EXPIRED → APPROVED`, `REJECTED → APPROVED`) raise typed `InvalidApprovalTransitionError` (`ApprovalErrorCode.CONFLICT`).
- Terminal states are immutable.

---

## 7. Audit Trail

- Events recorded: `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`, `APPROVAL_EXPIRED`, `APPROVAL_CANCELLED`, and `UNAUTHORIZED_ATTEMPT`.
- Metadata captured: `event_id`, `event_type`, `actor`, `timestamp`, `workflow_run_id`, `approval_id`, `correlation_id`, `action`, `result`, and sanitized details.
- Secret-safety: Zero API keys, authorization tokens, passwords, credit card numbers, or raw claim documents are ever persisted in audit logs.

---

## 8. Persistence

- Dual domain/persistence model: Pure Pydantic models for domain layers; SQLAlchemy ORM models (`HumanApprovalRecord`, `AuditEventRecord`) for storage.
- Alembic migration `0008_phase5_approval_metadata.py` verified and applied.
- Storage operations executed within single Unit of Work transactions.

---

## 9. Workflow Integration

- Nominal approval path: `RECEIVED → EXTRACTION → INVESTIGATION → REVIEW → HUMAN_APPROVAL → APPROVED`.
- Nominal rejection path: `RECEIVED → EXTRACTION → INVESTIGATION → REVIEW → HUMAN_APPROVAL → REJECTED`.
- Expiration path: `HUMAN_APPROVAL → ESCALATION` (via `APPROVAL_TIMED_OUT` trigger).
- Workflow parking: Engine does not auto-advance past `HUMAN_APPROVAL` until `apply_human_decision` is explicitly invoked by an authorized external human controller.

---

## 10. Checkpoint Integration

- Atomic approval creation: A `HUMAN_WAIT` checkpoint is created atomically with the `ApprovalRequest`.
- Resume safety: Calling `runner.resume(...)` from a parked `HUMAN_APPROVAL` checkpoint preserves the pending approval, original deadline, and recommendation without auto-approving or consuming steps.

---

## 11. Replay Safety

- Deterministic replay mode executes strictly offline with live providers disabled.
- Recorded approval decisions are replayed as deterministic historical events.
- Replay never prompts humans, never mutates stored approval rows, and never generates live approval requests.

---

## 12. OpenTelemetry Architecture

- **Instrumentation**: OpenTelemetry Tracing SDK and Metrics SDK.
- **Provider Abstraction**: `Observability` container supports live OTLP export, in-memory testing (`InMemorySpanExporter`), and complete no-op disabling (`disabled()`).
- **Resilience**: A broken collector, connection failure, or exporter exception is caught and suppressed by `OtelTracer` and `maybe_span`; business logic is completely isolated from telemetry failures.

---

## 13. Span Model

Standard, stable span names:
- `casefile.workflow`: Workflow transition boundaries
- `casefile.agent`: Agent invocation and lifecycle
- `casefile.tool`: External tool execution
- `casefile.persistence`: Unit of Work and repository transactions
- `casefile.checkpoint`: Checkpoint creation, verification, and restoration
- `casefile.budget`: Budget pre-flight checks and model accounting
- `casefile.approval`: Approval creation, decision, and expiration

All spans propagate `workflow_run_id`, `execution_id`, `correlation_id`, and `casefile.mode` (`LIVE` vs `REPLAY`).

---

## 14. Metrics

Exposed metrics:
1. `casefile.workflow.executions`
2. `casefile.workflow.failures`
3. `casefile.workflow.duration_seconds`
4. `casefile.agent.executions`
5. `casefile.agent.failures`
6. `casefile.tool.invocations`
7. `casefile.tool.failures`
8. `casefile.retries`
9. `casefile.rework.cycles`
10. `casefile.budget.exhausted`
11. `casefile.step.exhausted`
12. `casefile.checkpoints.created`
13. `casefile.replay.mismatches`
14. `casefile.approval.requests`
15. `casefile.approval.latency_seconds`
16. `casefile.approval.decisions`

All metrics enforce low-cardinality labels (no IDs or free-text claim payloads).

---

## 15. Sanitization

- Deny-list filters: `password`, `secret`, `token`, `key`, `ssn`, `tax_id`, `credit_card`, `statement`, `document`, `raw_text`.
- Value truncation: String values longer than 256 characters are automatically capped with `...[TRUNCATED]`.
- Regex matching: Automatically redacts SSNs (`\d{3}-\d{2}-\d{4}`) and credit card numbers (`\d{4}-\d{4}-\d{4}-\d{4}`).

---

## 16. OTLP / Jaeger Behavior

- Configurable via `ObservabilityConfig` (`otlp_endpoint`, `environment`, `service_name`).
- Unit tests run with in-memory exporters without requiring local Jaeger services.
- If Jaeger or the OTLP daemon is unreachable, the system silently drops telemetry without degrading claim processing throughput or correctness.

---

## 17. Acceptance Matrix

| Item | Requirement Description | Verification Method | Status |
|---|---|---|---|
| A | ApprovalRequest Pydantic model validation | `test_matrix_a_approval_request_model` | PASS |
| B | ApprovalDecision Pydantic model validation | `test_matrix_b_approval_decision_model` | PASS |
| C | ApprovalStatus lifecycle enum definitions | `test_matrix_c_approval_status_enum` | PASS |
| D | ApprovalActor model with non-empty identity & role | `test_matrix_d_approval_actor_model_and_validation` | PASS |
| E | ApprovalAuditRecord with correlation & action details | `test_matrix_e_approval_audit_record_model` | PASS |
| F | Immutability / frozen semantics on domain models | `test_matrix_f_immutability_of_domain_models` | PASS |
| G | Primary contracts reject untyped dictionaries | `test_matrix_g_no_untyped_dicts_as_primary_contracts` | PASS |
| H | Rejection of past/invalid deadlines | `test_matrix_h_invalid_deadline_rejected` | PASS |
| I | PENDING → APPROVED state machine transition | `test_matrix_i_transition_pending_to_approved` | PASS |
| J | PENDING → REJECTED state machine transition | `test_matrix_j_transition_pending_to_rejected` | PASS |
| K | PENDING → EXPIRED state machine transition | `test_matrix_k_transition_pending_to_expired` | PASS |
| L | PENDING → CANCELLED state machine transition | `test_matrix_l_transition_pending_to_cancelled` | PASS |
| M | Invalid transitions raise typed error | `test_matrix_m_invalid_transitions_raise_typed_error` | PASS |
| N | Terminal approval states are immutable | `test_matrix_n_terminal_states_immutable` | PASS |
| O | Typed role definitions hierarchy | `test_matrix_o_typed_role_definitions` | PASS |
| P | Authorized human roles meet approval thresholds | `test_matrix_p_authorized_human_roles_succeed` | PASS |
| Q | Insufficient human role fails authorization | `test_matrix_q_insufficient_role_fails` | PASS |
| R | Non-human actors strictly prohibited | `test_matrix_r_non_human_actors_prohibited` | PASS |
| S | Workflow supervisor agent strictly prohibited | `test_matrix_s_workflow_supervisor_prohibited` | PASS |
| T | Specialist agents prohibited from approval | `test_matrix_t_specialist_agents_prohibited` | PASS |
| U | Separation of duties model check | `test_matrix_u_separation_of_duties_requester_cannot_approve` | PASS |
| V | Requester != decider enforced on distinct actors | `test_matrix_v_distinct_decider_and_requester_succeeds` | PASS |
| W | Actor identity cannot be empty/anonymous | `test_matrix_w_actor_identity_required` | PASS |
| X | Impersonated or spoofed actor IDs rejected | `test_matrix_x_impersonated_actor_rejected` | PASS |
| Y | Separation of duties enforced in service | `test_matrix_y_separation_of_duties_in_service` | PASS |
| Z | Distinct authorized actor decision succeeds | `test_matrix_z_separation_of_duties_succeeds_for_distinct_actor` | PASS |
| AA | Approval request persisted with metadata | `test_matrix_aa_persist_approval_request_with_metadata` | PASS |
| AB | Approval decision persisted atomically in database | `test_matrix_ab_persist_approval_decision` | PASS |
| AC | Audit trail persisted for request and decision | `test_matrix_ac_persist_approval_audit_trail` | PASS |
| AD | Audit trail never stores secrets or credentials | `test_matrix_ad_audit_trail_never_stores_secrets` | PASS |
| AE | Domain and persistence model separation | `test_matrix_ae_domain_persistence_separation` | PASS |
| AF | Alembic migration verification | `test_matrix_af_database_migration_applied` | PASS |
| AG | Reviewer recommendation APPROVE parks in HUMAN_APPROVAL | `test_matrix_ag_reviewer_recommends_approval_parks_in_human_approval` | PASS |
| AH | Workflow does not automatically transition past HUMAN_APPROVAL | `test_matrix_ah_no_automatic_terminal_approved_state` | PASS |
| AI | Authorized human approval transitions to APPROVED | `test_matrix_ai_authorized_human_approval_advances_workflow` | PASS |
| AJ | Authorized human rejection transitions to REJECTED | `test_matrix_aj_authorized_human_rejection_advances_workflow` | PASS |
| AK | Approval expiration transitions workflow to ESCALATION | `test_matrix_ak_approval_expiration_transitions_workflow` | PASS |
| AL | Applying human decision to non-approval state raises error | `test_matrix_al_cannot_apply_human_decision_to_non_approval_state` | PASS |
| AM | Checkpoint persisted upon entering HUMAN_APPROVAL | `test_matrix_am_checkpoint_persists_on_human_approval_entry` | PASS |
| AN | Checkpoint kind is HUMAN_WAIT | `test_matrix_an_checkpoint_kind_is_human_wait` | PASS |
| AO | Resumed workflow preserves pending approval without auto-decision | `test_matrix_ao_resumed_workflow_preserves_pending_approval` | PASS |
| AP | Checkpoint carries recommendation and deadline | `test_matrix_ap_checkpoint_contains_recommendation_and_deadline` | PASS |
| AQ | Resume in terminal state preserves immutability | `test_matrix_aq_resume_terminal_state_preserves_immutability` | PASS |
| AR | Resumed workflow accepts subsequent authorized decision | `test_matrix_ar_resumed_workflow_can_be_approved_by_human` | PASS |
| AS | Replay treats original approval as recorded event | `test_matrix_as_replay_uses_recorded_decision` | PASS |
| AT | Replay does not mutate original approval records | `test_matrix_at_replay_does_not_mutate_approval_records` | PASS |
| AU | Replay never contacts live human deciders | `test_matrix_au_replay_never_prompts_human` | PASS |
| AV | Replay does not create new approval requests | `test_matrix_av_replay_does_not_create_approval_request` | PASS |
| AW | Replay mode explicitly tagged in TraceContext | `test_matrix_aw_replay_mode_tagged_in_trace_context` | PASS |
| AX | Replay mismatch detected if approval was tampered | `test_matrix_ax_replay_mismatch_detected_on_tampered_approval` | PASS |
| AY | OpenTelemetry tracer configuration | `test_matrix_ay_opentelemetry_tracer_initialization` | PASS |
| AZ | Standard span name catalog adherence | `test_matrix_az_standard_span_names` | PASS |
| BA | Workflow transition span instrumentation | `test_matrix_ba_workflow_transition_span` | PASS |
| BB | Agent execution span instrumentation | `test_matrix_bb_agent_execution_span` | PASS |
| BC | Tool execution span instrumentation | `test_matrix_bc_tool_execution_span` | PASS |
| BD | Checkpoint operation span instrumentation | `test_matrix_bd_checkpoint_span` | PASS |
| BE | TraceContext propagation across operations | `test_matrix_be_trace_context_propagation` | PASS |
| BF | Context correlation contains workflow_run_id | `test_matrix_bf_trace_correlation_contains_workflow_id` | PASS |
| BG | Workflow run correlates to specialist agent spans | `test_matrix_bg_workflow_to_agent_correlation` | PASS |
| BH | Agent span correlates to tool invocation span | `test_matrix_bh_agent_to_tool_correlation` | PASS |
| BI | Approval operation correlates with persistence span | `test_matrix_bi_approval_to_persistence_correlation` | PASS |
| BJ | Checkpoint span correlation | `test_matrix_bj_checkpoint_correlation` | PASS |
| BK | Metrics counters initialized correctly | `test_matrix_bk_metrics_initialization` | PASS |
| BL | Workflow execution counter increments | `test_matrix_bl_workflow_counter_increments` | PASS |
| BM | Tool invocation and failure metrics emitted | `test_matrix_bm_tool_metrics_emitted` | PASS |
| BN | Approval decision metric increments | `test_matrix_bn_approval_metrics_emitted` | PASS |
| BO | Budget token/cost metrics integrate with engine | `test_matrix_bo_budget_metrics_integrated` | PASS |
| BP | Metrics labels contain zero sensitive data | `test_matrix_bp_metrics_contain_no_sensitive_labels` | PASS |
| BQ | Sanitizer redacts API keys and passwords | `test_matrix_bq_sanitize_api_keys_and_passwords` | PASS |
| BR | Sanitizer redacts credit cards and SSNs | `test_matrix_br_sanitize_financial_and_pii` | PASS |
| BS | Sanitizer truncates long free-form text | `test_matrix_bs_sanitize_truncates_long_strings` | PASS |
| BT | Sanitizer strips raw claim documents | `test_matrix_bt_sanitize_raw_documents` | PASS |
| BU | Span attributes are cleaned by sanitizer | `test_matrix_bu_span_attributes_sanitized` | PASS |
| BV | Audit event payloads contain no credentials | `test_matrix_bv_audit_payloads_sanitized` | PASS |
| BW | Telemetry backend failure does not crash workflow | `test_matrix_bw_telemetry_outage_fails_safe` | PASS |
| BX | Corrupt telemetry payload does not disrupt engine | `test_matrix_bx_corrupt_telemetry_does_not_abort_workflow` | PASS |
| BY | In-memory test exporter functions without external infra | `test_matrix_by_in_memory_exporter_offline_testing` | PASS |
| BZ | Tracer degrades gracefully to no-op when disabled | `test_matrix_bz_disabled_telemetry_clean_execution` | PASS |
| CA | Scenario 1: Nominal human approval to APPROVED | `test_scenario_1_nominal_human_approval` | PASS |
| CB | Scenario 2: Human rejection to REJECTED | `test_scenario_2_human_rejection` | PASS |
| CC | Scenario 3: Unauthorized agent decision rejected | `test_scenario_3_unauthorized_approval` | PASS |
| CD | Scenario 4: Approval expiration past deadline | `test_scenario_4_approval_expiration` | PASS |
| CE | Scenario 5: Resume pending approval preserved | `test_scenario_5_resume_pending_approval` | PASS |
| CF | Scenario 6: Full trace correlation workflow→approval | `test_scenario_6_observability_trace` | PASS |
| CG | Security 1: Agent approval attempt fails safely | `test_security_1_agent_approval_attempt` | PASS |
| CH | Security 2: Supervisor approval attempt fails safely | `test_security_2_supervisor_approval_attempt` | PASS |
| CI | Security 3: Unauthorized human role fails safely | `test_security_3_unauthorized_human_role` | PASS |
| CJ | Security 4: Duplicate approval idempotent replay | `test_security_4_duplicate_approval` | PASS |
| CK | Security 5: Decision after rejection fails safely | `test_security_5_decision_after_rejection` | PASS |
| CL | Security 6: Decision after approval fails safely | `test_security_6_decision_after_approval` | PASS |

---

## 18. End-to-End Scenarios

- **Scenario 1 (Nominal HITL)**: Workflow reaches `HUMAN_APPROVAL`, `ApprovalRequest` created, authorized `CLAIM_REVIEWER` approves → workflow reaches `APPROVED`. Verified: audit trail, checkpoints, and spans emitted. (PASS)
- **Scenario 2 (Human Rejection)**: Workflow reaches `HUMAN_APPROVAL`, authorized `CLAIM_SUPERVISOR` rejects → workflow reaches terminal `REJECTED`. (PASS)
- **Scenario 3 (Unauthorized Approval)**: Agent attempts approval → rejected with `UNAUTHORIZED`, audit recorded, workflow remains parked in `HUMAN_APPROVAL`. (PASS)
- **Scenario 4 (Approval Expiration)**: Approval passes deadline → transitioned to `EXPIRED`, subsequent decision attempts blocked. (PASS)
- **Scenario 5 (Resume Pending Approval)**: Workflow parked in `HUMAN_APPROVAL` is reconstructed and resumed → approval remains pending without auto-approval. Subsequent human decision approved cleanly. (PASS)
- **Scenario 6 (Observability Trace)**: Workflow execution generates end-to-end trace with spans linked across workflow, agents, tools, checkpoints, and approvals. (PASS)
- **Scenario 7 (Sensitive Telemetry)**: Synthetic credentials, SSNs, credit cards, and raw documents injected into attributes are verified to be stripped/redacted. (PASS)
- **Scenario 8 (Telemetry Backend Unavailable)**: Telemetry backend disabled/failing; workflow executes to completion with 100% semantic fidelity. (PASS)

---

## 19. Test Results

- Phase 5 Acceptance Suite: `98 passed in 20.16s` (`tests/unit/test_phase5_acceptance.py`)
- Phase 2, 3, 4 Regression Suites: `142 passed in 22.45s` (`test_phase2_acceptance.py`, `test_phase3_acceptance.py`, `test_phase4_acceptance.py`)
- Total Verified Tests: `240 passed in 37.49s`

---

## 20. Coverage

- Total source code statements: 8,158
- Total missed statements: 3,011
- Combined branch/line coverage across core domains: **63%**
- Specific critical domain coverage:
  - `src/casefile/approval/model.py`: 92%
  - `src/casefile/approval/service.py`: 68%
  - `src/casefile/checkpoint/model.py`: 98%
  - `src/casefile/checkpoint/replay.py`: 74%
  - `src/casefile/observability/sanitize.py`: 94%
  - `src/casefile/observability/context.py`: 89%
  - `src/casefile/observability/tracer.py`: 79%
  - `src/casefile/models/domain.py`: 91%
  - `src/casefile/models/persistence.py`: 100%
  - `src/casefile/workflow/runner.py`: 75%
  - `src/casefile/workflow/supervisor.py`: 98%
  - `src/casefile/workflow/transitions.py`: 99%

---

## 21. Quality Gates

- **Black**: PASS (`poetry run black --check src tests` — 140 files checked)
- **Ruff**: PASS (`poetry run ruff check src tests` — 0 errors)
- **Mypy**: PASS (`poetry run mypy src` — 0 issues across 90 source files)
- **Poetry**: PASS (`poetry check` — valid package definition)

---

## 22. Dependency Changes

None. Zero external dependencies added to `pyproject.toml`.

---

## 23. Known Limitations

- Real OTLP networking requires a running Jaeger collector or OpenTelemetry collector daemon when configured. In-memory and disabled modes are used for offline environments.
- Expiration check is invoked on demand (`expire` or `expire_if_past_due`) or via workflow runner timeout handling; background cron-based expiration sweeps are deferred.

---

## 24. Deferred Functionality

Strictly out of Phase 5 scope and deferred to later phases:
- REST API / FastAPI endpoints (Phase 6+)
- WebSockets and UI frontend (Phase 6+)
- Operations console and claim dashboard
- Kubernetes manifests and cloud infrastructure
- Evaluation scenario runner (`ScenarioRunner` CLI / benchmarks)
- Production payment execution / payout rails
- External SSO or third-party approval identity providers

---

## 25. Git Status

All changes are staged/unstaged in the working tree. No commit was made. No unrelated files were removed, modified, or cleaned.

---

## 26. Exact Phase-Boundary Confirmation

- [x] IMPLEMENTED: Human approval domain models, authorizer, and state machine
- [x] IMPLEMENTED: Separation of duties and role hierarchy
- [x] IMPLEMENTED: Alembic migration and SQLAlchemy approval persistence
- [x] IMPLEMENTED: Checkpoint and resume integration with `HUMAN_APPROVAL`
- [x] IMPLEMENTED: Replay immutability and offline historical replay safety
- [x] IMPLEMENTED: Distributed tracing architecture and standard span catalog
- [x] IMPLEMENTED: 16 structured metrics instruments
- [x] IMPLEMENTED: Centralized privacy sanitizer
- [x] IMPLEMENTED: Fail-safe provider with in-memory test exporter
- [x] VERIFIED: 98 Phase 5 acceptance tests passed
- [x] VERIFIED: 142 Phase 2, 3, 4 regression tests passed
- [x] VERIFIED: Black, Ruff, Mypy, and Poetry quality gates passed
- [x] DEFERRED: REST API, WebSockets, Frontend, Payouts, Kubernetes (Phase 6+)
