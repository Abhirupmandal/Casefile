# Phase 12.5 Completion Report: Explainability & Counterfactual Intelligence

**Milestone**: Phase 12.5 — Explainability, Evidentiary Reasoning & Counterfactual Intelligence  
**Project**: CASEFILE — Multi-Agent Insurance Claim Orchestration Platform  
**Status**: COMPLETE — READY FOR INDEPENDENT AUDIT  
**Date**: November 2026  

---

## 1. Executive Summary

Phase 12.5 implements a trustworthy adjudication explainability subsystem and deterministic counterfactual intelligence engine that assists human claim adjusters, senior supervisors, and compliance auditors in understanding multi-agent claim evaluations.

Building on the foundation of structured contracts, ML fraud risk intelligence (Phase 12.3), and counter-offer negotiation workflows (Phase 12.4), Phase 12.5 delivers:

1. **Deterministic Evidentiary Explainability**: Synthesizes transparent, human-readable adjudication briefs with verified citations across claim documents, policy terms, damage line items, and external verification tools.
2. **Itemized Risk Factor Attribution**: Decomposes recommendations into typed `FactorContribution` items categorized as reducing, increasing, or neutral to claim risk with bounded weights.
3. **Bounded Sensitivity Counterfactuals**: Explores hypothetical scenarios ("what if damage exceeded limits", "what if prior claim frequency were high", "what if fraud score crossed threshold") to clarify decision boundaries without speculative hallucination.
4. **Advisory Invariant & Human Primacy**: Counterfactual sensitivity analysis is strictly advisory. Counterfactuals **NEVER** alter authoritative claim amounts, execute settlements, or bypass human approval gates.
5. **Separation of Duties & Role-Based Sanitization**: Claimants inspecting adjudication results receive a sanitized view via `to_claimant_view()`, stripping internal ML feature weights, confidential fraud thresholds, syndicate blacklist matches, and internal rules.
6. **Durable Persistence & REST Endpoints**: Backed by `decision_explanations` table via Alembic migration `0012_decision_explanations.py`, UnitOfWork repository bindings, and authenticated REST endpoints:
   - `GET /api/v1/workflows/{workflow_run_id}/explain`
   - `GET /api/v1/claims/{claim_id}/explain`
7. **Comprehensive Test Verification**: 12 dedicated unit, integration, and adversarial security tests, plus full 34-test Phase 12 regression battery, all passing with zero regressions.

---

## 2. Architecture & Design Principles

### 2.1 The Explainability Pipeline

```
                                  Workflow Context
                (Extraction + Investigation + Review + Fraud Assessment)
                                          │
                                          ▼
                             ┌────────────────────────┐
                             │   ExplanationEngine    │
                             └────────────┬───────────┘
                                          │
                  ┌───────────────────────┼────────────────────────┐
                  ▼                       ▼                        ▼
       ┌────────────────────┐   ┌───────────────────┐   ┌────────────────────┐
       │   Factor Weights   │   │ Evidence Citations│   │  Counterfactuals   │
       │ FactorContribution │   │ Documents, Policy,│   │  Sensitivity What- │
       │ (Feature impacts)  │   │ Estimates, Tools  │   │  If Scenarios      │
       └────────────────────┘   └───────────────────┘   └────────────────────┘
                  │                       │                        │
                  └───────────────────────┼────────────────────────┘
                                          ▼
                             ┌────────────────────────┐
                             │   DecisionExplanation  │
                             └────────────┬───────────┘
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
             [Internal Adjuster View]              [Claimant Safe View]
             Full ML weights & scores              `to_claimant_view()`
             SIU and database matches              Sanitized, no weights
```

### 2.2 Security Invariants & Human-in-the-Loop Safeguards

1. **Advisory-Only Invariant**: Counterfactual scenarios explore hypothetical sensitivity boundaries. Under no circumstances does a counterfactual scenario mutate `ClaimRecord.claim_amount`, alter `WorkflowRunRecord.current_state`, or authorize financial payouts.
2. **Tenant & Claimant Isolation**: The API verifies ownership on every request. If a principal with `Role.CLAIMANT` queries an explanation for a claim owned by another customer, the service immediately returns `HTTP 403 Forbidden`.
3. **Data Redaction & Sanitization**:
   - `factor_contributions`: Internal mathematical weights and neural/XGBoost feature contributions are stripped completely from claimant views.
   - `assessment_id`: ML fraud assessment identifiers are nulled.
   - `evidence_citations`: References to internal blacklists, syndicate syndication rules, or secret detection criteria are filtered out.
   - `counterfactuals`: Hypothetical scenarios referencing fraud, anomaly, or blacklist terms are omitted.

---

## 3. Database Schema & Migration

### 3.1 Migration `0012_decision_explanations.py`

- **Revision ID**: `0012_decision_explanations`
- **Revises**: `0011_counter_offers`
- **Table Name**: `decision_explanations`
- **Columns**:
  - `explanation_id`: String(36) (Primary Key)
  - `workflow_run_id`: String(36) (Not Null, Foreign Key to `workflow_runs`, indexed)
  - `claim_id`: String(36) (Not Null, Foreign Key to `claims`, indexed)
  - `recommendation`: String(32) (Not Null, default "APPROVE")
  - `confidence_score`: Numeric(5, 4) (Not Null)
  - `summary`: Text (Not Null, executive brief)
  - `key_factors_json`: Text (Not Null, JSON array of top factors)
  - `positive_factors_json`: Text (Not Null, JSON array)
  - `negative_factors_json`: Text (Not Null, JSON array)
  - `evidence_citations_json`: Text (Not Null, JSON array of cited sources)
  - `factor_contributions_json`: Text (Not Null, JSON array of feature contributions)
  - `missing_information_json`: Text (Not Null, JSON array of identified gaps)
  - `model_name`: String(64) (Not Null, default "casefile-explanation-engine")
  - `model_version`: String(32) (Not Null, default "1.0.0")
  - `assessment_id`: String(36) (Nullable, linked fraud assessment)
  - `created_at`: DateTime (Not Null, UTC timestamp)

Reversibility tested and verified: cleanly upgrades to head and downgrades back to `0011_counter_offers`.

---

## 4. Key Components Implemented

| Component | File Path | Responsibilities |
| :--- | :--- | :--- |
| `FactorContribution` & `DecisionExplanation` | `src/casefile/models/contracts.py` | Typed Pydantic domain models for feature attribution, citations, sensitivity scenarios, and `to_claimant_view()`. |
| `DecisionExplanationRecord` | `src/casefile/models/persistence.py` | SQLAlchemy ORM model with strict typing and JSON column serialization. |
| Mappings | `src/casefile/storage/mappings.py` | Bi-directional translation between domain models and database records. |
| Repositories | `src/casefile/storage/repositories.py` | `ExplanationRepository` protocol and `SqlExplanationRepository` implementation. |
| Unit of Work | `src/casefile/storage/unit_of_work.py` | `uow.explanations` repository binding and transactional scoping. |
| Explanation Engine | `src/casefile/workflow/explainability.py` | Adjudication brief synthesis, citation extraction, factor contribution weighting, and counterfactual sensitivity generator. |
| Workflow Runner Hook | `src/casefile/workflow/runner.py` | Executes `ExplanationEngine` post-review step, attaches brief to `ReviewResult.explanation_brief`, persists to database, and records `AuditEventType.EXPLANATION_GENERATED`. |
| API Schemas & Service | `src/casefile/api/schemas/models.py`, `src/casefile/api/service.py` | Serialization models, `get_workflow_explanation`, `get_claim_explanation`, and claimant tenant isolation. |
| REST Endpoints | `src/casefile/api/routes/workflows.py`, `src/casefile/api/routes/claims.py` | `GET /api/v1/workflows/{workflow_run_id}/explain` and `GET /api/v1/claims/{claim_id}/explain`. |

---

## 5. Verification & Test Suite Summary

### 5.1 Dedicated Phase 12.5 Test Suites

| Test Suite | Tests | Result | Coverage Area |
| :--- | :---: | :---: | :--- |
| `tests/unit/test_explainability.py` | 6 | **6 PASSED** | Unit tests for factor weighting, citations, counterfactual generation, claimant redaction, persistence round-trip, and empty context safety. |
| `tests/integration/test_explainability_workflow.py` | 3 | **3 PASSED** | End-to-end workflow runner execution, DB persistence, audit event verification, claimant sanitization, and Alembic 0012 upgrade/downgrade. |
| `tests/security/test_explainability_security.py` | 3 | **3 PASSED** | Advisory-only financial invariance, claimant tenant isolation (HTTP 403), and mathematical weight leakage prevention. |

### 5.2 Phase 12 Regression Battery

Execution of all combined Phase 12 tests (`test_counter_offers.py`, `test_counter_offer_workflow.py`, `test_fraud_workflow.py`, `test_explainability.py`, `test_explainability_workflow.py`, `test_explainability_security.py`):
```
======================== 34 passed, 1 warning in 6.98s ========================
```

### 5.3 Static Quality & Invariant Checks

- **Ruff**: Zero errors (`All checks passed!`).
- **Black**: Checked and compliant (`13 files would be left unchanged.`).
- **Mypy**: Typed and validated (`Success: no issues found in 9 source files`).
- **Git Diff**: Zero trailing whitespace introduced in any Phase 12.5 files.

---

## 6. Phase 12.5 Completion Signoff

Phase 12.5 is **fully implemented**, **rigorously tested**, and **ready for independent audit**.

