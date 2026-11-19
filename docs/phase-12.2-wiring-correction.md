# CASEFILE — Phase 12.2 Production Wiring Correction Report
## ApiService Translation Client Dependency Injection & End-to-End Multilingual Verification

**Date**: 2026-11-07  
**Milestone**: Phase 12.2 Production Wiring Correction  
**Status**: COMPLETE — ALL AUDIT GAPS CLOSED & VERIFIED  
**Repository State**: Branch `main`, HEAD `85739af`, 0 staged, 0 commits, 0 pushes  

---

## 1. Executive Summary & Defect Remediation

During the independent verification audit of Phase 12.2 (*Multi-Language Document Intake, Translation & Normalization*), a production integration gap was identified:
- **Audit Finding**: `ApiService` constructed default agents via `_make_deterministic_agents()` and the default tool registry via `create_default_registry()` without accepting or propagating a `translation_client`.
- **Impact**: While lower-level components (`DocumentRetrievalTool`, `ExtractorAgent`, `WorkflowRunner`) supported multilingual translation when manually assembled, non-English claims submitted via the official entrypoint `ApiService.submit_claim()` failed with `TRANSLATION_UNAVAILABLE` unless callers bypassed `ApiService`.
- **Remediation**:
  1. Updated `ApiService.__init__` to accept an optional `translation_client: TranslationServiceClientProtocol | None = None`.
  2. Stored `self._translation_client` and exposed it via `@property def translation_client(self) -> TranslationServiceClientProtocol | None`.
  3. Injected `translation_client=self._translation_client` into `create_default_registry()`.
  4. Updated `_make_deterministic_agents(translation_client=...)` and called it in `ApiService.submit_claim()` with `translation_client=self._translation_client`.
  5. Preserved strict fail-closed behavior: claims submitted in non-English languages without a configured translation client continue to fail closed cleanly to `WorkflowState.FAILED`.
  6. Added comprehensive end-to-end integration tests in `tests/integration/test_multilingual_api.py` covering all intake paths, fault injection, provenance retention, and PII containment.

---

## 2. Technical Implementation

### A. `ApiService` Constructor & Dependency Injection (`src/casefile/api/service.py`)

```python
def _make_deterministic_agents(
    translation_client: TranslationServiceClientProtocol | None = None,
) -> tuple[ExtractorAgent, InvestigatorAgent, ReviewerAgent]:
    """Build default deterministic agents for workflow execution."""
    p_ext = SmartDefaultProvider(provider_name="extractor-prov")
    p_inv = SmartDefaultProvider(provider_name="investigator-prov")
    p_rev = SmartDefaultProvider(provider_name="reviewer-prov")
    return (
        ExtractorAgent(provider=p_ext, translation_client=translation_client),
        InvestigatorAgent(provider=p_inv),
        ReviewerAgent(provider=p_rev),
    )


class ApiService:
    def __init__(
        self,
        engine: Engine | None = None,
        database_url: str = "sqlite:///./casefile.db",
        translation_client: TranslationServiceClientProtocol | None = None,
    ) -> None:
        ...
        self._translation_client = translation_client
        self._tool_registry = create_default_registry(translation_client=self._translation_client)

    @property
    def translation_client(self) -> TranslationServiceClientProtocol | None:
        return self._translation_client
```

### B. Execution Flow in `ApiService.submit_claim`

In `ApiService.submit_claim(request)`:
```python
    extractor, investigator, reviewer = _make_deterministic_agents(
        translation_client=self._translation_client
    )

    runner = WorkflowRunner(
        extractor,
        investigator,
        reviewer,
        checkpoint_repo=self._checkpoint_repo,
        uow_factory=self._uow,
        approval_service=self._approval_service,
        tool_registry=self._tool_registry,
    )
```

Both the `ExtractorAgent` and the tool registry's `DocumentRetrievalTool` receive `self._translation_client`, providing complete end-to-end translation support through the unified API facade.

---

## 3. Integration Test Verification (`tests/integration/test_multilingual_api.py`)

A dedicated integration test suite verifies the complete API layer:

| Test Case | Scenario / Invariant | Status |
|---|---|:---:|
| `test_api_service_spanish_claim_with_injected_client` | Spanish claim normalizes to English, records translation metadata, and proceeds safely to `HUMAN_APPROVAL` | **PASSED** |
| `test_api_service_french_and_german_claims_with_injected_client` | French and German claims normalize properly and proceed through the full workflow | **PASSED** |
| `test_api_service_english_claim_without_translation_client` | English claim without a translation client succeeds backward-compatibly | **PASSED** |
| `test_api_service_non_english_claim_without_translation_client_fails_closed` | Spanish claim without translation client fails closed to `WorkflowState.FAILED` (no silent pass-through) | **PASSED** |
| `test_api_service_translation_error_fails_closed` | Translation service timeout / 503 fails closed safely to `WorkflowState.FAILED` | **PASSED** |
| `test_api_service_translation_provenance_and_hashes_retained` | Dual SHA-256 hashes and translation provenance survive in workflow checkpoint records | **PASSED** |
| `test_api_service_no_pii_in_audit_logs` | Raw foreign text and sensitive document details do not leak into audit event summaries or attributes | **PASSED** |

---

## 4. Full Milestone Regression Suite Results

All 473 tests pass with 0 failures:

| Test Suite | Command | Exit Code | Tests Passed | Status |
|---|---|:---:|:---:|:---:|
| **Phase 12.2 API Multilingual Integration** | `pytest tests/integration/test_multilingual_api.py -v` | **0** | **7 passed** | **PASS** |
| **Phase 12.2 Multilingual Pipeline** | `pytest tests/unit/test_multilingual_pipeline.py -v` | **0** | **21 passed** | **PASS** |
| **Phase 12.1 Foundation** | `pytest tests/unit/test_phase12_foundation.py -v` | **0** | **36 passed** | **PASS** |
| **Partial Approval Security** | `pytest tests/security/test_partial_approval_security.py -v` | **0** | **16 passed** | **PASS** |
| **Optimization & Scaling Safety** | `pytest tests/unit/test_optimization_scaling.py -v` | **0** | **22 passed** | **PASS** |
| **Phase 8 Security Acceptance** | `pytest tests/security/ -k "not partial" -q` | **0** | **47 passed** | **PASS** |
| **Approval Lifecycle Integration** | `pytest tests/integration/test_approval_lifecycle.py -v` | **0** | **12 passed** | **PASS** |
| **Phase 9 External Integration** | `pytest tests/unit/test_phase9_acceptance.py -v` | **0** | **23 passed** | **PASS** |
| **Phase 5 Acceptance** | `pytest tests/unit/test_phase5_acceptance.py -v` | **0** | **98 passed** | **PASS** |
| **Phase 2 & 3 Acceptance** | `pytest tests/unit/test_phase2_acceptance.py tests/unit/test_phase3_acceptance.py -v` | **0** | **86 passed** | **PASS** |
| **Phase 4, 6, 7 Acceptance** | `pytest tests/unit/test_phase4_acceptance.py tests/unit/test_phase6_acceptance.py tests/unit/test_phase7_acceptance.py -v` | **0** | **105 passed** | **PASS** |
| **Total Verified Acceptance Tests** | — | — | **473 passed** | **100% PASS** |

---

## 5. Static Analysis & Quality Verification

| Check | Tool / Command | Exit Code | Outcome |
|---|---|:---:|---|
| Code Formatting | `poetry run black --check src tests` | **0** | All 210 files clean and compliant |
| Linting & Code Style | `poetry run ruff check src tests` | **0** | All checks passed; 0 lint errors |
| Static Type Checking | `poetry run mypy src` | **0** | Success: 0 issues across 134 source files |
| Package Configuration | `poetry check` | **0** | Valid configuration |

---

## 6. Repository Integrity & Safety Verification

- **Branch**: `main`
- **Local HEAD**: `85739afb25e215cf508fb8427ab8e6b4cbaf5632` (synchronized with `origin/main`)
- **Staged Changes**: `0` (clean staging area)
- **Commits / Pushes**: `0` commits or pushes executed
- **Safety Invariants**:
  - Authoritative claim amount validation: Intact (`test_partial_approval_security.py` passes 16/16).
  - Human approval gate: Intact (claims transition to `HUMAN_APPROVAL`, never directly to `APPROVED`).
  - Separation of duties & RBAC: Intact.
  - Fail-closed multilingual routing: Intact (claims without translation provider fail safely to `FAILED`).

