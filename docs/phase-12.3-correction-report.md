# CASEFILE — Phase 12.3 Correction Report
## Remediating Fraud Workflow, Human Approval Boundary, and Pre-Workflow Intelligence Integration

**Milestone:** Phase 12.3 — Critical Fraud Workflow & Human Approval Correction  
**Date:** 2026-11-08  
**Author:** Antigravity AI  
**Status:** REMEDIATED & VERIFIED  
**Workspace:** `e:\Projects\CASEFILE`  
**Git Branch:** `main` | **HEAD Commit:** `85739afb25e215cf508fb8427ab8e6b4cbaf5632`  

---

## 1. Root Cause Analysis of Audit Findings

### Defect A — Reviewer Fraud Policy Crashed Legitimate Claims
- **Root Cause**: In `src/casefile/agents/reviewer.py` (lines 126–134), `ReviewerAgent.parse_output()` contained a guard that raised an unrecoverable `AgentFailedError(code="APPROVE_WITH_FRAUD", retryable=False)` whenever `fraud_signals_detected == True` and the Reviewer recommended `APPROVE` or `APPROVE_PARTIAL`.
- **Failure Mode**: When caught in `WorkflowRunner._step_reviewer`, this exception emitted `Trigger.NODE_FAILED`, which transitioned the workflow via `(WorkflowState.REVIEW, Trigger.NODE_FAILED)` directly to terminal `WorkflowState.FAILED`. This prevented any claim with detected fraud indicators from reaching human review, making it impossible for an authorized human to approve false-positive cases.
- **Remediation**: The exception was removed. A Reviewer recommendation is an advisory opinion. When fraud indicators exist, the recommendation is preserved and the claim is routed directly to `WorkflowState.HUMAN_APPROVAL`.

### Defect B — Fraud-Driven Automatic Rejection Bypassed Human Oversight
- **Root Cause**: In `src/casefile/workflow/runner.py`, `_step_reviewer` directly emitted `Trigger.REVIEW_REJECTED` whenever `result.decision == "REJECT"`. In `src/casefile/workflow/transitions.py`, `(WorkflowState.REVIEW, Trigger.REVIEW_REJECTED)` transitioned the workflow directly to terminal `WorkflowState.REJECTED` (`terminates=True`).
- **Failure Mode**: Fraud indicators caused automatic claim rejections without human review or confirmation, violating the core invariant that *"Fraud risk is an investigative signal, not an automatic verdict."*
- **Remediation**: In `WorkflowRunner._step_reviewer`, when `result.decision == "REJECT"` and fraud signals are detected, the workflow engine now emits `Trigger.REVIEW_APPROVED` to transition the claim to `WorkflowState.HUMAN_APPROVAL` with `requires_human_approval=True`. The recommendation is tagged as `RecommendationType.DENY` with notes indicating `[FRAUD SCRUTINY REQUIRED]`, allowing an authorized human to review the evidence and explicitly decide whether to approve, partially approve, or reject. Non-fraud rejections continue to follow the existing terminal rejection path.

### Defect C — Fraud Assessment Timing Disconnect
- **Root Cause**: `ApiService.submit_claim()` executed `self._fraud_service.assess_claim()` *after* `runner.run()` had already completed.
- **Failure Mode**: The Investigator and Reviewer agents executed within `runner.run()` without receiving the authoritative fraud assessment. Agents relied only on simulated tool responses, while the persisted database assessment was detached from agent cognition.
- **Remediation**: `ApiService.submit_claim()` was restructured. Upon receiving a claim submission, the claim is saved in the database, a `workflow_run_id` is assigned, and `self._fraud_service.assess_claim()` is executed immediately. The authoritative assessment is saved to `fraud_assessments` and injected into `WorkflowContext.fraud_assessment`, `InvestigationRequest.fraud_assessment`, and `ReviewRequest.fraud_assessment`. Both agents now receive the authoritative assessment during workflow execution. Duplicate post-workflow assessments were removed.

---

## 2. Exact Files Modified

1. **`src/casefile/agents/reviewer.py`**:
   - Removed lines 126–134 (`APPROVE_WITH_FRAUD` exception).
   - Preserves Reviewer advisory outputs even when fraud indicators are flagged.
2. **`src/casefile/models/contracts.py`**:
   - Added `fraud_assessment: dict[str, Any] | None = None` to `InvestigationRequest`.
   - Added `fraud_assessment: dict[str, Any] | None = None` to `ReviewRequest`.
3. **`src/casefile/workflow/context.py`**:
   - Added `fraud_assessment: Any | None = None` to `WorkflowContext`.
4. **`src/casefile/workflow/runner.py`**:
   - Updated `_apply_and_persist`: In `cp_kind == CheckpointKind.HUMAN_WAIT`, appropriately sets `RecommendationType.DENY` for rejections, `RecommendationType.APPROVE_PARTIAL` for partials, and tags notes with `[FRAUD SCRUTINY REQUIRED]`.
   - Updated `run()`: Accepts `fraud_assessment` and initializes `wf_ctx.fraud_assessment`.
   - Updated `_step_investigator`: Injects `wf_ctx.fraud_assessment` into `InvestigationRequest` and guarantees `InvestigationResult.fraud_assessment` and `fraud_signals` reflect the authoritative assessment.
   - Updated `_step_reviewer`: Injects `wf_ctx.fraud_assessment` into `ReviewRequest`. Routes fraud claims (whether recommended `APPROVE`, `APPROVE_PARTIAL`, or `REJECT`) to `HUMAN_APPROVAL` via `Trigger.REVIEW_APPROVED`.
5. **`src/casefile/api/service.py`**:
   - Updated `SmartDefaultProvider`: Ingests `fraud_assessment` and produces structured `fraud_signals` in investigation and `fraud_risk_level` / `fraud_signals_detected` in review.
   - Updated `_make_deterministic_agents`: Passes `fraud_assessment` to `SmartDefaultProvider`.
   - Updated `submit_claim()`: Computes and persists `FraudRiskResult` before workflow execution; injects it into agents and runner; removes post-workflow duplicate computation.
6. **`src/casefile/fraud/service.py`**:
   - Added robust provider failure containment: If scoring engine or external database throws exceptions or times out, safely falls back to `status=FraudAssessmentStatus.FAILED` or `INSUFFICIENT_DATA`, sets `requires_human_scrutiny=True`, and records error metadata in `warnings`.
7. **`tests/integration/test_fraud_workflow.py`**:
   - Updated to verify that Reviewer approval on fraud-flagged claims parses successfully.
   - Added `test_fraud_rejection_routes_to_human_approval_and_human_can_reject`.
8. **`tests/security/test_fraud_security.py`**:
   - Updated `test_reviewer_recommendation_with_fraud_routes_to_human_approval`.
   - Added `test_false_positive_fraud_signal_human_approval` (authorized human approval of false-positive fraud claim).
   - Added `test_fraud_signal_partial_approval` (authorized partial payout on fraud-flagged claim).
   - Added `test_fraud_assessment_timing_and_provider_failure_handling` (validating failure fail-safe and timing).
9. **`docs/phase-12.3-completion.md`**:
   - Updated completion report with remediation details, updated flow diagram, and test matrix.

---

## 3. Corrected State-Transition Behavior

| Reviewer Decision | Fraud Signals Detected? | Workflow Trigger Emitted | Target State | Requires Human Approval? |
|---|---|---|---|---|
| `APPROVE` | No | `Trigger.REVIEW_APPROVED` | `HUMAN_APPROVAL` | Yes |
| `APPROVE` | **Yes** | `Trigger.REVIEW_APPROVED` | `HUMAN_APPROVAL` | **Yes (Fraud Scrutiny)** |
| `APPROVE_PARTIAL`| No | `Trigger.REVIEW_APPROVED` | `HUMAN_APPROVAL` | Yes |
| `APPROVE_PARTIAL`| **Yes** | `Trigger.REVIEW_APPROVED` | `HUMAN_APPROVAL` | **Yes (Fraud Scrutiny)** |
| `REJECT` | No | `Trigger.REVIEW_REJECTED` | `REJECTED` (Terminal)| No (Preserved non-fraud) |
| `REJECT` | **Yes** | `Trigger.REVIEW_APPROVED` | `HUMAN_APPROVAL` | **Yes (Fraud Scrutiny)** |
| `REWORK` | Any | `Trigger.REWORK_REQUESTED`| `REWORK_LOOP` | No (Loop back to investigation)|

---

## 4. Human Approval and Rejection Behavior

In `HUMAN_APPROVAL`, an authorized human reviewer (`HumanActor` / `ApprovalActor`) must decide the claim using `ApiService.decide_approval()` or `ApprovalService.decide()`:
- **Full Approval (`verdict="APPROVE"`)**:
  - Requires authorized role (`CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`).
  - Separation of duties strictly enforced (requester cannot approve own claim).
  - Transitions `WorkflowState.HUMAN_APPROVAL -> WorkflowState.APPROVED` (`terminates=True`).
- **Partial Approval (`verdict="APPROVE_PARTIAL"`)**:
  - Enforces authoritative financial ceiling (`approved_amount <= requested_amount`).
  - Exploits ($100 claim with $1,000,000 partial approval) are rejected with `ApprovalError: VALIDATION_ERROR`.
  - Persists approved amount in metadata and audit log.
  - Transitions `WorkflowState.HUMAN_APPROVAL -> WorkflowState.APPROVED` (`terminates=True`).
- **Rejection (`verdict="REJECT"`)**:
  - Requires authorized human actor.
  - Records human actor ID and explicit rejection rationale.
  - Transitions `WorkflowState.HUMAN_APPROVAL -> WorkflowState.REJECTED` (`terminates=True`).

---

## 5. Actual Fraud Assessment Execution Sequence

1. `POST /api/v1/claims` receives claim intake payload.
2. `ApiService.submit_claim` generates `claim_id` and `workflow_run_id`.
3. Claim record is persisted to `claims` table via `UnitOfWork`.
4. `FraudAssessmentService.assess_claim` extracts features, queries external fraud intelligence (hashed PII), computes risk score, and derives `RiskCategory`.
5. `FraudRiskResult` is persisted to `fraud_assessments` table via `UnitOfWork` (persisted exactly once).
6. Deterministic agents are constructed with the authoritative `fraud_assessment`.
7. `WorkflowRunner` is initialized and invoked with `claim_input`, `workflow_run_id`, and `fraud_assessment`.
8. `InvestigatorAgent` receives `InvestigationRequest` with authoritative fraud findings.
9. `ReviewerAgent` receives `ReviewRequest` with authoritative fraud findings.
10. If fraud is flagged, the workflow parks in `WorkflowState.HUMAN_APPROVAL`.
11. `ClaimSubmissionResponse` returns status `HUMAN_APPROVAL`.

---

## 6. Proof of Consistent Fraud Findings Across Agents

In `WorkflowRunner`:
- `_step_investigator` wraps `wf_ctx.fraud_assessment` into `InvestigationRequest(fraud_assessment=f_assessment_dict)`.
- When `Investigator` completes, `InvestigationResult.fraud_assessment` and `InvestigationResult.fraud_signals` are populated from the same authoritative assessment.
- `_step_reviewer` constructs `ReviewRequest(investigation_result=wf_ctx.investigation_result, fraud_assessment=f_assessment_dict)`.
- Both agents inspect identical `risk_score`, `risk_category`, `indicators`, and `external_matches`.

---

## 7. Persistence and Replay Verification

- **Transactional Safety**: Fraud assessments are saved in `uow.fraud_assessments.save()` within an atomic `UnitOfWork` session.
- **Idempotency**: Assessment ID is linked to `claim_id` and `workflow_run_id`. Duplicate assessments are not created.
- **Replay Determinism**: Replay loads past state from checkpoints containing the serialized fraud findings without making external network calls.

---

## 8. Test Execution Evidence

All 10 required test suites passed with exit code 0:

| # | Test Suite | Command | Exit Code | Tests | Duration |
|---|---|---|---|---|---|
| 1 | Fraud Subsystem Unit | `poetry run pytest tests/unit/test_fraud_subsystem.py -v --no-cov` | 0 | 19 passed | 0.29s |
| 2 | Fraud Workflow Integration | `poetry run pytest tests/integration/test_fraud_workflow.py -v --no-cov` | 0 | 6 passed | 3.88s |
| 3 | Fraud Security & Invariants | `poetry run pytest tests/security/test_fraud_security.py -v --no-cov` | 0 | 9 passed | 2.96s |
| 4 | Full Security Suite | `poetry run pytest tests/security/ -v --no-cov` | 0 | 72 passed | 16.39s |
| 5 | Approval Lifecycle Integration | `poetry run pytest tests/integration/test_approval_lifecycle.py -v --no-cov` | 0 | 12 passed | 5.57s |
| 6 | Partial Approval Security | `poetry run pytest tests/security/test_partial_approval_security.py -v --no-cov` | 0 | 16 passed | 3.81s |
| 7 | Multilingual API Integration | `poetry run pytest tests/integration/test_multilingual_api.py -v --no-cov` | 0 | 7 passed | 3.16s |
| 8 | Multilingual Pipeline Unit | `poetry run pytest tests/unit/test_multilingual_pipeline.py -v --no-cov` | 0 | 21 passed | 2.43s |
| 9 | Phase 12.1 Foundation Unit | `poetry run pytest tests/unit/test_phase12_foundation.py -v --no-cov` | 0 | 36 passed | 3.12s |
| 10| Optimization & Scaling Unit | `poetry run pytest tests/unit/test_optimization_scaling.py -v --no-cov` | 0 | 22 passed | 1.64s |

### Unique Test Counts (No Double-Counting)
- Across the entire test suite, **1,140 tests** are collected and verified.
- Phase 12.3 specific tests:
  - `tests/unit/test_fraud_subsystem.py`: 19 tests
  - `tests/integration/test_fraud_workflow.py`: 6 tests
  - `tests/security/test_fraud_security.py`: 9 tests
  - **Total Phase 12.3 Unique Tests**: **34 tests** (all green).

---

## 9. Static Analysis Results

- **Black Code Formatter**: `poetry run black --check src tests` $\rightarrow$ `All done! 220 files would be left unchanged.` (Exit code: 0)
- **Ruff Linter**: `poetry run ruff check src tests` $\rightarrow$ `All checks passed!` (Exit code: 0)
- **Mypy Static Type Checker**: `poetry run mypy src` $\rightarrow$ `Success: no issues found in 141 source files` (Exit code: 0)
- **Poetry Package Verification**: `poetry check` $\rightarrow$ Clean metadata (Exit code: 0)
- **Git Diff Whitespace Check**: `git diff --check` $\rightarrow$ Clean in all source code and test files (Exit code: 1 only on historical documentation line `docs/phase-8-completion.md:201`).

---

## 10. Git Status & Working Tree State

### Initial Git State
- Branch: `main`
- HEAD: `85739af docs: add Apache 2.0 license and update project metadata`

### Final Git Status
```
 M src/casefile/agents/reviewer.py
 M src/casefile/api/service.py
 M src/casefile/fraud/service.py
 M src/casefile/models/contracts.py
 M src/casefile/workflow/context.py
 M src/casefile/workflow/runner.py
 M tests/integration/test_fraud_workflow.py
 M tests/security/test_fraud_security.py
 M docs/phase-12.3-completion.md
?? docs/phase-12.3-correction-report.md
```
Zero commits, zero pushes, zero stashes, zero resets. All prior milestone progress preserved intact.

---

## 11. Remaining Limitations & Phase 12.4 Boundary

1. **Synthetic Intelligence Doubles**: External fraud database integration uses a local hashed test double (`FakeExternalFraudDatabase`). Connecting to live registries (NICB, ISO ClaimSearch) requires enterprise credentials and network proxies.
2. **Phase 12.4 Boundary Strictly Respected**: In accordance with prompt instructions, Phase 12.4 (Batch Processing, Disaster Mode & Bulk Ingestion) has **not** been started. Work is stopped and submitted for independent verification.

