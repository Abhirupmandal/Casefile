# CASEFILE — Phase 12.1 Completion Report
## Domain Models, Typed Contracts & Database Foundation

**Milestone Status**: PASS  
**Date**: November 7, 2026  
**Git Branch**: `main`  
**Git Local HEAD**: `85739af` (Synchronized with origin/main; no commits or pushes performed)  
**Scope**: Phase 12.1 Only (Domain Models, Typed Contracts, and Database Foundation)

---

## 1. Executive Summary

Phase 12.1 establishes the shared domain model, typed contract, and persistence foundation for the advanced capabilities scheduled in CASEFILE Phase 12 (Multi-language intake, advanced fraud signals, partial approval workflows, batch processing, counterfactual explainability, and active learning).

All additions preserve existing Phase 0–11 behaviors with 100% backward compatibility:
- Existing claims without language or batch metadata default cleanly (`language="en"`, `batch_id=None`).
- Existing approval workflows continue to support standard full `APPROVE` and `REJECT` verdicts.
- The human-in-the-loop (HITL) safety boundary is strictly preserved: `WorkflowState.APPROVED` is reachable **exclusively** by human actors (`AgentType.HUMAN`) through `Trigger.APPROVAL_GRANTED`. Neither automated agents, reviewer recommendations, nor auto-approval policies can transition directly to final approval or authorize claim payouts.
- Review recommendations for partial settlements (`APPROVE_PARTIAL`) park the workflow deterministically in `WorkflowState.HUMAN_APPROVAL`, requiring an authorized human decision with explicit payout bounds validation.
- Database migration `0009_phase12_advanced_features` descends directly from head revision `0008_phase5_approval_metadata` and supports bidirectional execution (`upgrade` and `downgrade`) with zero data loss for existing claims and approval records.

---

## 2. Inventory of Modified and Created Files

### Modified Tracked Files
- `src/casefile/models/contracts.py`: Added language normalization & validation (`SupportedLanguage`, `SUPPORTED_LANGUAGE_CODES`, `validate_language_code`), enriched `ClaimInput` with optional `language` and `batch_id`, added `CounterOffer`, `DisasterContext`, `BatchSubmissionRequest`, `DecisionExplanation`, `CounterfactualScenario`, `ApprovalFeedback`, and updated `ReviewResult` to support `APPROVE_PARTIAL` and `CounterOffer`.
- `src/casefile/models/domain.py`: Added `language: str` (default `"en"`) and `batch_id: UUID | None` to `Claim`; added `approved_amount: Money | None` with positive-amount invariants to `HumanApproval`.
- `src/casefile/approval/model.py`: Added `ApprovalVerdict.APPROVE_PARTIAL`; updated `ApprovalDecision` to validate positive `approved_amount`, non-exceedance of `requested_amount`, and max 2 decimal places; updated `ApprovalStateMachine.target_status_for_verdict` to map `APPROVE_PARTIAL` to `ApprovalStatus.APPROVED`.
- `src/casefile/approval/service.py`: Updated `_DECISION_STATUS` and `_status_matches_verdict` for `APPROVE_PARTIAL`; persisted `approved_amount` in `HumanApprovalRecord`.
- `src/casefile/workflow/runner.py`: Updated `_step_reviewer` to route `APPROVE_PARTIAL` to `HUMAN_APPROVAL`; updated `apply_human_decision` to handle `APPROVE_PARTIAL` and record `approved_amount` into workflow metadata.
- `src/casefile/workflow/graph.py`: Configured graph node transitions to route `APPROVE_PARTIAL` to `human_approval`.
- `src/casefile/models/persistence.py`: Added `language` and `batch_id` to `ClaimRecord`; added `approved_amount` to `HumanApprovalRecord`; defined SQLAlchemy tables for `BatchRecord`, `ApprovalFeedbackRecord`, and `CounterfactualScenarioRecord`.
- `src/casefile/storage/mappings.py`: Updated bidirectional mappers (`claim_to_record`, `record_to_claim`, `approval_to_record`, `record_to_approval`) to preserve `language`, `batch_id`, and `approved_amount`.
- `src/casefile/models/__init__.py`: Exported all new Phase 12 domain models and contracts.
- `src/casefile/api/schemas/models.py`: Added `language` and `batch_id` to `ClaimSubmissionRequest`; added `approved_amount` to `ApprovalDecisionRequest` and `ApprovalDecisionResponse`.
- `src/casefile/api/service.py`: Forwarded `language`, `batch_id`, and `approved_amount` in API claim intake and approval decision handlers.
- `src/casefile/api/routes/approvals.py`: Propagated `approved_amount` from request body to `ApprovalDecision`.

### Created Untracked Files
- `migrations/versions/0009_phase12_advanced_features.py`: Alembic migration revision adding columns and creating new Phase 12 tables.
- `tests/unit/test_phase12_foundation.py`: 33 unit and acceptance tests covering contracts, invariants, persistence, safety boundaries, and migration reversibility.
- `docs/phase-12.1-completion.md`: This completion report.

---

## 3. Contracts, Domain Models & Persistence Breakdown

### Multi-Language & Batch Metadata
- **Languages**: Supported codes are `en` (English), `es` (Spanish), `fr` (French), and `de` (German). Validation normalizes whitespace, lowercase, and rejects unsupported codes with a descriptive `ValueError`.
- **ClaimInput**: `language: str = "en"` and `batch_id: UUID | None = None`. Backward compatible with existing payloads.
- **Claim Domain Entity**: Retains `language` and `batch_id`.
- **ClaimRecord**: Maps `language` (`VARCHAR(8)`, default `'en'`) and `batch_id` (`VARCHAR(36)`, nullable, indexed).

### Partial Approval & Payout Invariants
- **ApprovalVerdict**: Added `APPROVE_PARTIAL = "APPROVE_PARTIAL"`.
- **ApprovalDecision Validation**:
  - `approved_amount` is required when verdict is `APPROVE_PARTIAL`.
  - `approved_amount` must be strictly positive (`> 0.00`).
  - `approved_amount` cannot exceed `requested_amount` when known.
  - `approved_amount` cannot carry more than 2 decimal places.
  - `approved_amount` is strictly disallowed for `REJECT` verdicts.
- **HumanApproval Entity & Record**: Holds `approved_amount` (`NUMERIC(12, 2)`, nullable).

### Shared Phase 12 Contracts
- **`CounterOffer`**: Encapsulates negotiated settlement amounts (`claim_id`, `original_amount`, `proposed_amount`, `reduction_reason`, `disputed_items`). Validates that `proposed_amount <= original_amount` and decimal precision <= 2.
- **`DisasterContext`**: Encapsulates catastrophe-related data (`disaster_id`, `disaster_name`, `disaster_type`, `geographic_zone`, `declared_date`, `emergency_deductible_policy`, `shared_notes`).
- **`BatchSubmissionRequest`**: Manages grouped claim intake with `batch_id`, `batch_name`, `claims` list (automatically assigning `batch_id` to enrolled claims), and optional `DisasterContext`.
- **`CounterfactualScenario`**: Captures sensitivity factor analysis (`factor_name`, `current_value`, `counterfactual_value`, `hypothetical_outcome`, `explanation`).
- **`DecisionExplanation`**: Aggregates plain-language explanation (`summary`, `key_factors`, `evidence_citations`, `counterfactuals`).
- **`ApprovalFeedback`**: Adjuster feedback contract for active learning (`feedback_id`, `approval_id`, `claim_id`, `actor_id`, `category`, `adjuster_notes`, `overridden_recommendation`, `sanitized_sample`).
- **`ReviewResult`**: Supports `decision: Literal["APPROVE", "APPROVE_PARTIAL", "REJECT", "REWORK"]`, optional `counter_offer`, and optional `explanation_brief`.

### Database Tables Added
- **`batches`**: `batch_id` (PK), `batch_name`, `status`, `disaster_id`, `disaster_name`, `disaster_type`, `total_claims`, `created_at`, `updated_at`.
- **`approval_feedback`**: `feedback_id` (PK), `approval_id` (FK to `human_approvals.approval_id`), `claim_id`, `actor_id`, `category`, `adjuster_notes`, `overridden_recommendation`, `sanitized_sample_json`, `created_at`.
- **`counterfactual_scenarios`**: `scenario_id` (PK), `workflow_run_id`, `claim_id`, `factor_name`, `current_value`, `counterfactual_value`, `hypothetical_outcome`, `explanation`, `created_at`.

---

## 4. Invariants, Validation Rules & Error Cases

| Invariant / Rule | Location | Enforcement Mechanism |
|---|---|---|
| Language code normalization | `contracts.py` | Whitespace trimmed, lowercase converted, validated against `SUPPORTED_LANGUAGE_CODES`. |
| Payout positive value | `model.py`, `domain.py` | `approved_amount > Decimal("0.00")` enforced by `@field_validator` and domain validator. |
| Payout upper bound | `model.py` | `approved_amount <= requested_amount` enforced by `@model_validator`. |
| Currency precision | `model.py`, `contracts.py` | Exponent `< -2` rejected with `ValueError("cannot exceed 2 decimal places")`. |
| Reject verdict purity | `model.py` | `approved_amount` must be `None` when verdict is `REJECT`. |
| CounterOffer bound | `contracts.py` | `proposed_amount <= original_amount` enforced by validator. |
| Non-human actor fencing | `transitions.py` | `Trigger.APPROVAL_GRANTED` allowed actors: `{AgentType.HUMAN}` exclusively. |

---

## 5. Migration Analysis

- **Revision Identifier**: `0009_phase12_advanced_features`
- **Parent Revision**: `0008_phase5_approval_metadata`
- **Reversibility**:
  - `upgrade()` adds `language` and `batch_id` to `claims`; adds `approved_amount` to `human_approvals`; creates `batches`, `approval_feedback`, and `counterfactual_scenarios`.
  - `downgrade()` drops `counterfactual_scenarios`, `approval_feedback`, and `batches`; drops `approved_amount` from `human_approvals`; drops `batch_id` and `language` from `claims`.
  - Leverages Alembic `batch_alter_table` to guarantee compatibility with SQLite table rebuild constraints as well as PostgreSQL.
- **Data Preservation**: Verified by `test_existing_records_survive_migration`: existing rows in `claims` and `human_approvals` prior to migration retain their primary keys, status, and claim amounts, with `language` defaulting to `'en'` and `approved_amount` defaulting to `None`.

---

## 6. Safety Architecture Verification

1. **Human Actor Exclusivity**:
   - `TRANSITION_TABLE[(WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_GRANTED)]` specifies `allowed_actors = frozenset({AgentType.HUMAN})`.
   - `test_agent_cannot_fire_approval_granted` asserts that `AgentType.SUPERVISOR`, `AgentType.REVIEWER`, `AgentType.INVESTIGATOR`, and `AgentType.SYSTEM` raise `ActorNotAllowedError`.
2. **Reviewer Recommendation Boundary**:
   - When a Reviewer recommends `APPROVE_PARTIAL`, the workflow routes through `Trigger.REVIEW_CONCLUDED` into `WorkflowState.HUMAN_APPROVAL`.
   - The workflow **never** reaches `WorkflowState.APPROVED` autonomously.
3. **Execution Payout Authorization**:
   - Only when an authorized human adjuster submits an `ApprovalDecision(verdict=APPROVE_PARTIAL, approved_amount=...)` does the state machine transition to `APPROVED` and record the authorized payout amount in workflow metadata.

---

## 7. Test Execution Evidence

### Phase 12.1 Acceptance Suite (`tests/unit/test_phase12_foundation.py`)
- **Total Tests**: 33
- **Passed**: 33 (100%)
- **Execution Time**: ~3.11s

```
tests/unit/test_phase12_foundation.py::TestMultiLanguageAndBatchContracts::test_supported_languages_en_es_fr_de PASSED
tests/unit/test_phase12_foundation.py::TestMultiLanguageAndBatchContracts::test_language_normalization PASSED
tests/unit/test_phase12_foundation.py::TestMultiLanguageAndBatchContracts::test_unsupported_language_raises_validation_error PASSED
tests/unit/test_phase12_foundation.py::TestMultiLanguageAndBatchContracts::test_claim_input_default_language_is_en PASSED
tests/unit/test_phase12_foundation.py::TestMultiLanguageAndBatchContracts::test_claim_input_with_optional_batch_id PASSED
tests/unit/test_phase12_foundation.py::TestMultiLanguageAndBatchContracts::test_claim_domain_model_supports_language_and_batch PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_approval_verdict_approve_partial_exists PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_approve_partial_requires_positive_approved_amount PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_approved_amount_cannot_exceed_requested_amount PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_approved_amount_decimal_precision PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_reject_verdict_cannot_carry_approved_amount PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_existing_full_approve_compatibility PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_existing_reject_compatibility PASSED
tests/unit/test_phase12_foundation.py::TestPartialApprovalContracts::test_actor_must_be_human_for_decision PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_counter_offer_contract PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_counter_offer_proposed_exceeds_original_raises PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_disaster_context_contract PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_batch_submission_request_contract PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_counterfactual_scenario_contract PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_decision_explanation_contract PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_approval_feedback_contract PASSED
tests/unit/test_phase12_foundation.py::TestSharedPhase12Contracts::test_review_result_with_partial_decision PASSED
tests/unit/test_phase12_foundation.py::TestDatabaseFoundation::test_sqlalchemy_model_declarations PASSED
tests/unit/test_phase12_foundation.py::TestDatabaseFoundation::test_claim_mapping_roundtrip PASSED
tests/unit/test_phase12_foundation.py::TestDatabaseFoundation::test_approval_mapping_roundtrip_with_partial_amount PASSED
tests/unit/test_phase12_foundation.py::TestDatabaseFoundation::test_batch_record_persistence PASSED
tests/unit/test_phase12_foundation.py::TestDatabaseFoundation::test_approval_feedback_persistence PASSED
tests/unit/test_phase12_foundation.py::TestSafetyArchitectureInvariants::test_workflow_state_approved_requires_human_actor PASSED
tests/unit/test_phase12_foundation.py::TestSafetyArchitectureInvariants::test_agent_cannot_fire_approval_granted PASSED
tests/unit/test_phase12_foundation.py::TestSafetyArchitectureInvariants::test_partial_approval_routes_through_human_approval_gate PASSED
tests/unit/test_phase12_foundation.py::TestSafetyArchitectureInvariants::test_human_partial_approval_decision_transitions_to_approved_with_metadata PASSED
tests/unit/test_phase12_foundation.py::TestAlembicMigrationReversibility::test_migration_upgrade_and_downgrade PASSED
tests/unit/test_phase12_foundation.py::TestAlembicMigrationReversibility::test_existing_records_survive_migration PASSED
```

---

## 8. Regression Verification Results (Phases 0–11)

| Suite | Focus | Tests | Status |
|---|---|---|---|
| `tests/unit/test_phase12_foundation.py` | Phase 12.1 Acceptance & Invariants | 33 | **33 PASSED** |
| `tests/unit/test_optimization_scaling.py` | Auto-Approval Policy & HITL Safety | 22 | **22 PASSED** |
| `tests/security/` | Security, RBAC, Headers, Injection, Auth | 47 | **47 PASSED** |
| `tests/integration/test_approval_lifecycle.py` | Approval Lifecycle Integration | 12 | **12 PASSED** |
| `tests/unit/test_phase9_acceptance.py` | External Services & Tool Integration | 23 | **23 PASSED** |
| `tests/unit/test_phase5_acceptance.py` | Phase 5 HITL, Metrics, Observability | 98 | **98 PASSED** |
| `tests/unit/test_phase2_acceptance.py` & `phase3` | Core Workflow & Tool Registry | 86 | **86 PASSED** |
| `tests/unit/test_phase4,6,7_acceptance.py` | Agents, Evaluation, & API Console | 105 | **105 PASSED** |
| **Total Tests Verified** | | **426** | **426 PASSED (100%)** |

---

## 9. Linters and Type Checkers

- **Poetry Validation**: `poetry check` passed without structural configuration issues.
- **Code Formatter**: `poetry run black --check src tests` — 201 files checked, 0 reformatted, 100% compliant.
- **Code Linter**: `poetry run ruff check src tests` — All checks passed, 0 errors.
- **Static Type Checker**: `poetry run mypy src tests/unit/test_phase12_foundation.py` — Success: no issues found in 129 source files.

---

## 10. Git Status and Working Tree State

- **Branch**: `main`
- **Local HEAD**: `85739af`
- **Upstream Sync**: Synchronized with `origin/main`
- **Staged Changes**: 0
- **Commits / Pushes**: 0 created
- **Preservation**: All Phase 9, Optimization & Scaling, and Phase 12.1 changes remain intact in the working tree.

---

## 11. Handoff & Next Steps

Phase 12.1 is **COMPLETE** and verified.

The domain contracts, Pydantic validations, SQLAlchemy models, and Alembic migrations required for Phase 12 are fully established.

**Ready for Next Milestone**:
- **Phase 12.2: Multi-Language Intake & Normalization** (Translation service abstraction, language detection, document translation pipeline, and localized prompt formatting).
- Proceed only upon user authorization.

