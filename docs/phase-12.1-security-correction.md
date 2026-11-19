# CASEFILE — Phase 12.1 Critical Security Correction Completion Report

**Milestone**: Phase 12.1 Critical Security Correction: Partial Approval Authoritative Claim Amount Validation  
**Date**: November 7, 2026  
**Status**: COMPLETE / SECURITY GATE: PASS  

---

## 1. Root Cause Analysis

During the independent Phase 12.1 audit, a blocking financial authorization vulnerability was discovered in the partial approval pipeline:

1. **Unbounded/Missing Requested Amount in `ApprovalDecision`**:
   The `ApprovalDecision` model permitted `requested_amount=None` for `ApprovalVerdict.APPROVE_PARTIAL`. Without an authoritative ceiling, `approved_amount` validation only checked for non-negativity without enforcing `approved_amount <= requested_amount`.
2. **Missing Authoritative Database Lookup in `ApiService`**:
   `ApiService.decide_approval()` accepted `approved_amount` from the caller and constructed `ApprovalDecision` without resolving the underlying `ClaimRecord` from the database. It did not extract the true, authoritative claim amount to bind the decision.
3. **Exploit Vector**:
   An attacker or compromised operator could submit a partial approval command for a nominal $100.00 claim specifying `approved_amount=Decimal("1000000.00")`. Because no requested amount was validated against persisted records, this $1,000,000 payout was authorized and transitioned into `WorkflowState.APPROVED`.
4. **Missing Claim Persistence in Intake**:
   In addition, `ApiService.submit_claim` executed workflow runs without persisting the `ClaimRecord` aggregate into the `claims` table, creating an inconsistency where approval requests existed in `human_approvals` without a backing authoritative claim row in `claims`.

---

## 2. Files Modified and Exact Validation Changes

### A. `src/casefile/approval/model.py`
- Added `VALIDATION_ERROR = "VALIDATION_ERROR"` to `ApprovalErrorCode`.
- Added `@field_validator("requested_amount")` enforcing positive value (`> Decimal("0.00")`) and $\le 2$ decimal fractional digits.
- Enhanced `@model_validator(mode="after") def _validate_decision_amount`:
  - For `ApprovalVerdict.APPROVE_PARTIAL`:
    - `requested_amount` is strictly required and must not be `None`.
    - `requested_amount` must be strictly positive (`> Decimal("0.00")`).
    - `approved_amount` is strictly required and must not be `None`.
    - `approved_amount` must be strictly positive (`> Decimal("0.00")`).
    - `approved_amount <= requested_amount` is strictly enforced.
    - If `approved_amount > requested_amount`, raises `ValueError("approved_amount (...) cannot exceed requested_amount (...)")`.
  - Preserved backward compatibility: For full `APPROVE` and `REJECT`, `approved_amount` is prohibited and `requested_amount` is optional.

### B. `src/casefile/approval/service.py`
- Added defense-in-depth authoritative claim amount verification within the atomic Unit of Work transaction in `_decide_inner()`:
  - When `command.verdict == ApprovalVerdict.APPROVE_PARTIAL`:
    - Fetches persisted `claim_row = uow.session.get(ClaimRecord, str(row.claim_id))`.
    - If `claim_row is None`, logs `AuditResult.FAILURE` and raises `ApprovalError(ApprovalErrorCode.NOT_FOUND)`.
    - If `claim_row.claim_amount is None or claim_row.claim_amount <= Decimal("0.00")`, logs `AuditResult.FAILURE` and raises `ApprovalError(ApprovalErrorCode.VALIDATION_ERROR)`.
    - Verifies `command.approved_amount <= claim_row.claim_amount`. If violated, logs `AuditResult.FAILURE` and raises `ApprovalError(ApprovalErrorCode.VALIDATION_ERROR)`.

### C. `src/casefile/api/service.py`
- In `submit_claim()`:
  - Persists the authoritative `Claim` entity (including `language` and `batch_id`) into `uow.claims` before running the workflow, ensuring every workflow has a backing durable claim record.
- In `decide_approval()`:
  - Enforces authoritative claim lookup: resolves `claim_row = uow.session.get(ClaimRecord, str(row.claim_id))`.
  - For `APPROVE_PARTIAL`: verifies `claim_row` exists and has `claim_amount > 0.00`. If absent, raises `ApprovalError(ApprovalErrorCode.NOT_FOUND)`. If invalid, raises `ApprovalError(ApprovalErrorCode.VALIDATION_ERROR)`.
  - Passes `requested_amount=authoritative_claim_amount` to `ApprovalDecision`.
  - Protects `ApprovalActor` instantiation in `try/except` to convert unauthorized roles (such as `VIEWER`) into typed `ApprovalError(ApprovalErrorCode.UNAUTHORIZED)`.
  - Returns typed `ApprovalDecisionResponse` containing `verdict=verdict.value` and `approved_amount=approved_amount`.

### D. `src/casefile/api/schemas/models.py`
- Updated `ApprovalDecisionResponse`:
  - `approved_amount: Decimal | None = None` (strongly typed financial amount).
  - `verdict: str | None = None` (explicit decision verdict metadata).

### E. `tests/unit/test_phase12_foundation.py`
- Updated test cases to supply valid `requested_amount` for `APPROVE_PARTIAL` decision validations.
- Added tests verifying that `requested_amount` is required, positive, and bounded to $\le 2$ decimal places.

### F. `tests/security/test_partial_approval_security.py`
- Created comprehensive 16-test adversarial security test suite verifying all boundary conditions, exploit reproduction, unauthorized actors, tamper detection, and audit trails.

---

## 3. Exploit Reproduction & Rejection Verification

### The Adversarial Exploit Scenario
```python
sub_resp = svc.submit_claim(
    ClaimSubmissionRequest(
        policy_id="POL-EXPLOIT-01",
        claimant_name="Target Claim",
        incident_date="2026-06-01",
        claim_amount=Decimal("100.00"),
        description="Small door ding claim",
    )
)
approvals = svc.list_approvals()
app_id = UUID(approvals.approvals[0].approval_id)

# Malicious exploit attempt: $1,000,000 payout on $100 claim
with pytest.raises(ApprovalError) as exc_info:
    svc.decide_approval(
        app_id,
        reviewer,
        "APPROVE_PARTIAL",
        reason="Malicious unauthorized 1M payout exploit",
        approved_amount=Decimal("1000000.00"),
    )
assert exc_info.value.code == ApprovalErrorCode.VALIDATION_ERROR
assert "cannot exceed" in str(exc_info.value)
```

### Verification Result
- **Result**: `PASSED`
- The malicious request was rejected at the `ApprovalDecision` validation boundary before database mutation.
- The workflow run remained securely parked in `WorkflowState.HUMAN_APPROVAL`.
- No state transition to `WorkflowState.APPROVED` occurred.
- Database records remained uncorrupted.

---

## 4. Test Suite Execution & Regression Audit

All required regression and acceptance suites were executed from the working tree without live network dependencies.

| Suite | Path | Tests | Result | Exit Code |
|-------|------|-------|--------|-----------|
| Partial Approval Security (Adversarial) | `tests/security/test_partial_approval_security.py` | 16 | **16 passed** | 0 |
| Phase 12.1 Foundation | `tests/unit/test_phase12_foundation.py` | 36 | **36 passed** | 0 |
| Optimization & Scaling Safety | `tests/unit/test_optimization_scaling.py` | 22 | **22 passed** | 0 |
| Phase 8 & Security Suite | `tests/security/` | 63 | **63 passed** | 0 |
| Approval Lifecycle Integration | `tests/integration/test_approval_lifecycle.py` | 12 | **12 passed** | 0 |
| Phase 9 Integration & Acceptance | `tests/unit/test_phase9_acceptance.py` | 23 | **23 passed** | 0 |
| Phase 5 Acceptance & Observability | `tests/unit/test_phase5_acceptance.py` | 98 | **98 passed** | 0 |
| Phase 2 & Phase 3 Acceptance | `tests/unit/test_phase2_acceptance.py` + `test_phase3_acceptance.py` | 86 | **86 passed** | 0 |
| Phase 4, 6 & Phase 7 Acceptance | `tests/unit/test_phase4_acceptance.py` + `test_phase6_acceptance.py` + `test_phase7_acceptance.py` | 105 | **105 passed** | 0 |
| **Total** | | **461** | **461 passed** | **0** |

---

## 5. Static Analysis & Type Checking

1. **Black Code Formatter**:
   - Command: `poetry run black --check src tests`
   - Output: `All done! 202 files would be left unchanged.`
   - Status: **PASS (Exit 0)**
2. **Ruff Linter**:
   - Command: `poetry run ruff check src tests`
   - Output: `All checks passed!`
   - Status: **PASS (Exit 0)**
3. **Mypy Type Checker**:
   - Command: `poetry run mypy src tests/unit/test_phase12_foundation.py tests/security/test_partial_approval_security.py`
   - Output: `Success: no issues found in 130 source files`
   - Status: **PASS (Exit 0)**
4. **Poetry Dependency & Configuration Check**:
   - Command: `poetry check`
   - Output: `All dependencies and project metadata valid.`
   - Status: **PASS (Exit 0)**

---

## 6. HITL & Financial Safety Invariant Verification

- **HITL Boundary Invariant**: No automated agent (Supervisor, Extractor, Investigator, Reviewer) or automated policy can authorize a claim payout or fire `Trigger.APPROVAL_GRANTED`.
- **Ceiling Enforcement Invariant**: Every `APPROVE_PARTIAL` decision is authoritatively capped by the persisted claim amount in SQLite (`approved_amount <= requested_amount`).
- **Fail-Closed Invariant**: If a claim record is missing, corrupted, or carries a non-positive amount, any partial approval attempt fails closed immediately with `ApprovalErrorCode.NOT_FOUND` or `ApprovalErrorCode.VALIDATION_ERROR`.
- **Role & Separation of Duties Invariant**: Unauthorized roles (`VIEWER`, `OPERATOR`) cannot decide approvals; claim requesters cannot approve their own claims.
- **Audit Immutability**: All approval decisions, rejections, and attempts are immutably logged to the audit repository with precise timestamps, actor identities, and authorized amounts.

---

## 7. Scope & Remaining Limitations

This milestone strictly resolved the financial authorization defect in Phase 12.1. In accordance with milestone boundaries:
- No Phase 12.2 features (translation providers, multilingual extraction, disaster detection) were implemented.
- No Git commit or push has been performed.
- All pre-existing uncommitted work in the working tree has been preserved intact.

---

## 8. Final Gate Assessment

**PHASE 12.1 SECURITY GATE: PASS**

