# CASEFILE — Phase 12 Advanced Features: Implementation Plan

**Document Version**: 1.0.0  
**Status**: DRAFT / AWAITING APPROVAL  
**Target Phase**: Phase 12 — Advanced Features  
**Baseline Commit**: `85739af`  
**Prerequisites**: Phases 0–11 Complete (Reported PASS)  

---

## 1. Executive Summary

Phase 12 ("Advanced Features") represents the culmination of the CASEFILE multi-agent insurance claim adjudication platform. Building on the verified foundation of deterministic orchestration (Phases 1–4), offline evaluation and testing (Phase 5/11), OpenTelemetry observability (Phase 6/10), security and sandboxing (Phase 7), containerized deployment (Phase 8), real tool integration and distributed caching (Phase 9), and the audited Human-in-the-Loop (HITL) safety architecture, Phase 12 introduces advanced operational and cognitive capabilities:

1. **Multi-Language Support**: Document translation and multilingual claim extraction (Spanish, French, German) with strict evidentiary provenance.
2. **Advanced Fraud Detection**: ML-based fraud scoring models and cross-referencing with external fraud database protocols (e.g. ISO ClaimSearch).
3. **Partial Approval & Counter-Offers**: Structured counter-offer generation, partial payout recommendations, and human-authorized partial disbursements.
4. **Batch Processing & Disaster Scenarios**: Shared-context batch ingestion and bulk adjudication for catastrophic loss events with isolated claim budgets.
5. **Decision Explainability & Counterfactuals**: Human-readable adjudication briefs, key evidence attribution, and counterfactual analysis ("what if X were different").
6. **Active Learning & Continuous Improvement**: Structured human adjuster feedback capture, confidential PII redaction, and prompt refinement pipelines.

> [!IMPORTANT]
> **Safety Architecture Invariant**: Under no circumstances does Phase 12 permit autonomous claim approval, autonomous partial payout, or autonomous rejection. All final financial disbursements and partial approvals require explicit authorized human decisions. ML fraud scores, translation outputs, and agent counter-offers remain strictly advisory.

---

## 2. Requirement Inventory (Roadmap Analysis)

Per `docs/roadmap.md` (§ Phase 12), the official requirements are:

| Area | Roadmap Item | Formal Specification |
|---|---|---|
| **1. Multi-Language Support** | Translate claim documents before extraction | Ingest claim documents in Spanish, French, and German; run pre-extraction translation via typed translation protocols; preserve original and translated text for evidentiary chain-of-custody. |
| | Support for Spanish, French, German claims | Update extraction prompts and schema models to accept and identify source language, locale metadata, and translation confidence. |
| **2. Advanced Fraud Detection** | Integrate ML-based fraud scoring models | Introduce typed ML model scoring interfaces (supervised anomaly / risk estimation) complementing heuristic indicator weights. |
| | Cross-reference external fraud databases | Implement external fraud database protocol (e.g., ISO ClaimSearch / NICB) with resilient HTTP adapters, caching, and rate limiting. |
| **3. Partial Approval** | Support partial claim approval | Permit human adjusters and reviewer recommendations to approve a specific dollar amount less than the requested total (e.g., approve \$10,000 out of \$15,000). |
| | Agent negotiations (counter-offers) | Investigator and Reviewer analyze line-item discrepancies to propose structured counter-offers with itemized explanations. |
| **4. Batch Processing** | Process multiple claims in batch | Bulk claim intake API and background batch coordinator maintaining individual claim isolation. |
| | Bulk claim adjudication for disaster scenarios | Cluster related claims sharing a common catastrophic incident (e.g., hurricane, hail, wildfire) with shared event context while strictly isolating individual claim budgets. |
| **5. Explainability** | Human-readable explanation of decision | Generate plain-language executive summaries synthesizing extraction facts, policy conditions, and investigation findings. |
| | Highlight key evidence that influenced decision | Explicit evidentiary attribution mapping each decision criterion to primary source document citations. |
| | Counterfactual explanations | Compute "what-if" counterfactual scenarios showing the minimal fact changes required to alter the adjudication recommendation. |
| **6. Active Learning** | Capture human reviewer feedback | Structured feedback capture on human approval/rejection decisions (error categorization, adjuster notes, discrepancy tagging). |
| | Fine-tune agents / prompt corrections | Export anonymized, PII-sanitized correction datasets for continuous prompt refinement and evaluation regression benchmarking. |
| | Continuous improvement loop | Evaluation harness integration tracking model agreement with human adjusters over time. |

---

## 3. Existing Implementation Audit

Every component of the CASEFILE codebase was inspected to determine current capabilities and avoid duplicate engineering:

| Feature Area | Classification | Existing Infrastructure & Findings |
|---|---|---|
| **1. Multi-Language Support** | **NOT IMPLEMENTED** | `ClaimInput` has no `language` or `locale` field. `DocumentRetrievalTool` returns raw content without language detection or translation. Extractor prompt assumes English. No translation protocols exist in `src/casefile/tools/adapters/`. |
| **2. Advanced Fraud Detection** | **PARTIALLY IMPLEMENTED** | `FraudSignalLookupTool` exists in `src/casefile/tools/fraud.py` using static rule heuristics and fixture store lookups. `FraudServiceClientProtocol` added in Phase 9 supports HTTP scoring, but no ML model interface or external fraud database adapter (e.g., ISO ClaimSearch) is implemented. |
| **3. Partial Approval** | **PARTIALLY IMPLEMENTED** | `RecommendationType.APPROVE_PARTIAL` exists in `src/casefile/models/domain.py:110` and `Recommendation` has an `estimated_payout: Money | None` field. However, `ApprovalVerdict` only admits `APPROVE` and `REJECT`, `ApprovalDecision` lacks an `approved_amount` field, `ReviewResult.decision` is `Literal["APPROVE", "REJECT", "REWORK"]`, and no negotiation/counter-offer logic exists. |
| **4. Batch Processing** | **NOT IMPLEMENTED** | Only single-claim submission exists (`POST /api/v1/claims`). No batch schema, no batch runner, no disaster event context model, and no bulk adjudication coordinator exist. |
| **5. Explainability** | **PARTIALLY IMPLEMENTED** | `ReviewResult.reasoning` and `AutoApprovalDecision.reasons` provide unstructured free-text explanations. However, no structured decision brief, no citation attribution model, and no counterfactual generator exist. |
| **6. Active Learning** | **PARTIALLY IMPLEMENTED** | `ApprovalDecision.reason` captures unstructured text. No structured feedback schema, no feedback persistence table, no PII-scrubbed training exporter, and no evaluation fine-tuning harness exist. |

---

## 4. Implementation Dependencies & System Architecture

### A. Feature Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│  Milestone 12.1: Domain Models, Contracts & Migration   │
│  (Language, Partial Payout, Batch, Explain, Feedback)  │
└──────┬──────────────────────┬───────────────────┬──────┘
       │                      │                   │
       ▼                      ▼                   ▼
┌──────────────┐       ┌──────────────┐    ┌──────────────┐
│Milestone 12.2│       │Milestone 12.3│    │Milestone 12.4│
│Multi-Language│       │Advanced Fraud│    │Partial Appro-│
│ Translation  │       │& Database ML │    │val & Counter │
└──────┬───────┘       └──────┬───────┘    └──────┬───────┘
       │                      │                   │
       └──────────────┬───────┴───────────────────┘
                      ▼
       ┌──────────────────────────────┐
       │Milestone 12.5: Explainability│
       │ & Counterfactual Engine      │
       └──────────────┬───────────────┘
                      │
       ┌──────────────┴───────────────┐
       ▼                              ▼
┌──────────────┐               ┌──────────────┐
│Milestone 12.6│               │Milestone 12.7│
│Batch & Disas-│               │Active Learn- │
│ter Context   │               │ing & Feedback│
└──────┬───────┘               └──────┬───────┘
       │                              │
       └──────────────┬───────────────┘
                      ▼
       ┌──────────────────────────────┐
       │Milestone 12.8: Quality Gate, │
       │Verification & Hardening      │
       └──────────────────────────────┘
```

### B. Required Domain Model Changes (`src/casefile/models/`)
- `ClaimInput`: Add optional `language: str = "en"` and `batch_id: UUID | None = None`.
- `ClaimDocumentReference`: Add `original_language: str`, `translated_content: str | None`, `translation_confidence: float | None`.
- `ApprovalVerdict`: Add `APPROVE_PARTIAL = "APPROVE_PARTIAL"`.
- `ApprovalDecision`: Add `approved_amount: Decimal | None = None` (strictly validated against `requested_amount`).
- `ReviewResult`: Support `decision: Literal["APPROVE", "APPROVE_PARTIAL", "REJECT", "REWORK"]`, add `counter_offer: CounterOffer | None = None`, and `explanation_brief: DecisionExplanation | None = None`.
- New models: `CounterOffer`, `DisasterContext`, `BatchSubmissionRequest`, `DecisionExplanation`, `CounterfactualScenario`, `ApprovalFeedback`.

### C. Required Database Migrations (`migrations/versions/`)
- Migration `0009_phase12_advanced_features.py`:
  - `claims`: Add `language` (VARCHAR(10)), `batch_id` (UUID nullable, indexed).
  - `human_approvals`: Add `approved_amount` (NUMERIC(12, 2) nullable), `verdict` enum expansion.
  - New table `batches`: `batch_id` (UUID PK), `name` (VARCHAR), `disaster_type` (VARCHAR), `shared_context` (TEXT), `total_claims` (INTEGER), `status` (VARCHAR), `created_at` (DATETIME).
  - New table `approval_feedback`: `feedback_id` (UUID PK), `approval_id` (UUID FK), `claim_id` (UUID FK), `actor_id` (VARCHAR), `category` (VARCHAR), `notes` (TEXT), `sanitized_sample` (JSON), `created_at` (DATETIME).
  - New table `counterfactual_scenarios`: `scenario_id` (UUID PK), `workflow_run_id` (UUID FK), `factor_changed` (VARCHAR), `simulated_outcome` (VARCHAR), `delta_description` (TEXT).

### D. API Changes (`src/casefile/api/`)
- `POST /api/v1/claims/batch`: Bulk claim submission returning batch ID and claim enrollment tokens.
- `GET /api/v1/batches/{batch_id}`: Batch processing progress and status summary.
- `POST /api/v1/approvals/{approval_id}/decision`: Accept `decision: "APPROVE_PARTIAL"` with required `approved_amount`.
- `POST /api/v1/approvals/{approval_id}/feedback`: Capture structured adjuster feedback.
- `GET /api/v1/workflows/{workflow_id}/explain`: Retrieve structured decision explanation, citations, and counterfactuals.

### E. Security & Privacy Implications
- **Claim Isolation in Batches**: A batch failure or poisoned claim must never corrupt neighboring claims. Rate limiters and budget envelopes remain strictly evaluated per-claim.
- **PII Quarantine in Active Learning**: Training export data must sanitize names, policies, locations, monetary amounts, and phone numbers before writing to `approval_feedback.sanitized_sample`.
- **Untrusted Translation Tagging**: Machine-translated text is labeled untrusted and quarantined under `UNTRUSTED_BEGIN`/`UNTRUSTED_END` boundaries to prevent prompt injection via translated payloads.

---

## 5. Implementation Sequence & Milestones

### Milestone 12.1: Domain Models, Contracts & Database Foundation
- **Official Requirement**: Foundation for all Phase 12 contracts, data models, and persistence schema.
- **Scope**:
  - Update `src/casefile/models/contracts.py` with multi-language fields, `CounterOffer`, `DecisionExplanation`, `BatchSubmission`, and `ApprovalFeedback`.
  - Update `src/casefile/approval/model.py` to support `ApprovalVerdict.APPROVE_PARTIAL` and `approved_amount` validation.
  - Create Alembic migration `0009_phase12_advanced_features.py` adding `batches`, `approval_feedback`, and modifying `claims` and `human_approvals`.
  - Update SQLAlchemy persistence models in `src/casefile/models/persistence.py`.
- **Dependencies**: None (builds on Phase 11 baseline).
- **Files Expected to Change**:
  - `src/casefile/models/contracts.py`
  - `src/casefile/models/domain.py`
  - `src/casefile/models/persistence.py`
  - `src/casefile/approval/model.py`
  - `migrations/versions/0009_phase12_advanced_features.py`
- **Acceptance Criteria**:
  - `ApprovalDecision` validates that `APPROVE_PARTIAL` requires `approved_amount > 0` and `approved_amount <= requested_amount`.
  - Alembic migrations apply and roll back cleanly (`alembic upgrade head`, `alembic downgrade -1`).
- **Tests**: `tests/unit/test_phase12_models.py` (contract validation, immutability, migration test).
- **Regression Suites**: Full Phase 2–11 acceptance suites green.
- **Security**: No secrets or unvalidated numeric bounds admitted in contract models.
- **Completion Evidence**: Migration exit code 0; unit test pass rate 100%.

---

### Milestone 12.2: Multi-Language Document Translation & Ingestion
- **Official Requirement**: Translate claim documents before extraction (Spanish, French, German).
- **Scope**:
  - Define `TranslationServiceClientProtocol` in `src/casefile/tools/adapters/protocols.py`.
  - Implement resilient `TranslationAdapter` with bounded retries, cache TTL (86,400s), and language detection.
  - Implement deterministic offline test fake (`FakeTranslationServiceClient`).
  - Update `DocumentRetrievalTool` to optionally translate content to English while preserving raw source content and reporting detected language.
  - Update `ExtractorAgent` prompt and extraction pipeline to recognize non-English source material, logging translation provenance in `ExtractionResult`.
- **Dependencies**: Milestone 12.1.
- **Files Expected to Change**:
  - `src/casefile/tools/adapters/protocols.py`
  - `src/casefile/tools/adapters/http_client.py`
  - `src/casefile/tools/adapters/fakes.py`
  - `src/casefile/tools/document.py`
  - `src/casefile/agents/extractor.py`
  - `src/casefile/agents/prompts.py`
  - `src/casefile/config.py`
- **Acceptance Criteria**:
  - Documents in Spanish (`es`), French (`fr`), and German (`de`) are translated before LLM extraction.
  - Both original and translated documents are preserved with SHA-256 hashes for evidentiary integrity.
  - Translated text is strictly quarantined under `UNTRUSTED_BEGIN` delimiters in prompts.
- **Tests**: `tests/unit/test_multilingual_pipeline.py` (translation adapter, fake client, Extractor integration).
- **Regression Suites**: Phase 5 acceptance, Phase 9 acceptance.
- **Security**: Prompt injection attempts embedded in foreign language documents are neutralized.
- **Completion Evidence**: Successful extraction from multilingual sample claims with translation provenance.

---

### Milestone 12.3: Advanced Fraud Detection & External Database Integration
- **Official Requirement**: Integrate ML-based fraud scoring models and cross-reference external fraud databases.
- **Scope**:
  - Define `MLFraudModelProtocol` and `ExternalFraudDatabaseProtocol` (e.g. ISO ClaimSearch).
  - Implement `MLFraudScorer` (tabular feature vector evaluator calculating anomaly risk).
  - Implement `ExternalFraudDbAdapter` (external prior-loss cross-referencing).
  - Implement offline deterministic test doubles with configurable risk indicators.
  - Update `FraudSignalLookupTool` in `src/casefile/tools/fraud.py` to evaluate both heuristic rules, ML model score, and external database match indicators.
  - Update `InvestigatorAgent` and `ReviewerAgent` to synthesize multi-modal fraud scores.
- **Dependencies**: Milestone 12.1.
- **Files Expected to Change**:
  - `src/casefile/tools/adapters/protocols.py`
  - `src/casefile/tools/adapters/http_client.py`
  - `src/casefile/tools/adapters/fakes.py`
  - `src/casefile/tools/fraud.py`
  - `src/casefile/agents/investigator.py`
  - `src/casefile/agents/reviewer.py`
  - `src/casefile/config.py`
- **Acceptance Criteria**:
  - ML fraud score is normalized between 0.0 and 1.0 with feature contribution factors.
  - External database matches report prior loss records, dates, and amounts without leaking third-party PII.
  - Fraud score CANNOT autonomously reject claims or bypass human review (advisory only).
- **Tests**: `tests/unit/test_advanced_fraud.py` (ML evaluator, ISO database fake, fraud tool integration).
- **Regression Suites**: Phase 7 security suite, Phase 9 acceptance suite.
- **Security**: Read-only fraud database access; tokenized claim refs; no raw SSNs transmitted.
- **Completion Evidence**: Verified fraud detection against high-risk synthetic claims without false rejections.

---

### Milestone 12.4: Partial Approval, Counter-Offers & Financial Controls
- **Official Requirement**: Support partial claim approval and agent negotiation counter-offers.
- **Scope**:
  - Update `InvestigatorAgent` to compute disputed line-item breakdowns (e.g., unauthorized labor rate, pre-existing wear).
  - Update `ReviewerAgent` to recommend `APPROVE_PARTIAL` with a structured `CounterOffer` (proposed amount, itemized reductions, justification).
  - Update `TRANSITION_TABLE` and `WorkflowEngine`:
    - `(WorkflowState.REVIEW, Trigger.REVIEW_APPROVED)` routes to `HUMAN_APPROVAL` regardless of full vs. partial recommendation.
    - `(WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_GRANTED)` supports partial authorization when executed by `AgentType.HUMAN`.
  - Update `WorkflowRunner.apply_human_decision` and `ApprovalService.decide_approval` to record `approved_amount`.
  - Update `BudgetEngine` to track final authorized payout vs. requested claim amount.
- **Dependencies**: Milestone 12.1.
- **Files Expected to Change**:
  - `src/casefile/agents/investigator.py`
  - `src/casefile/agents/reviewer.py`
  - `src/casefile/workflow/transitions.py`
  - `src/casefile/workflow/runner.py`
  - `src/casefile/approval/service.py`
  - `src/casefile/api/routes/approvals.py`
  - `src/casefile/api/schemas/models.py`
- **Acceptance Criteria**:
  - Reviewer can emit `APPROVE_PARTIAL` with itemized counter-offer.
  - Human adjuster can approve requested amount, approve counter-offered amount, or set a custom partial amount.
  - An attempt to approve an amount greater than the requested claim amount is strictly rejected with a 422/ValueError.
  - No automated process can authorize partial approval without an authorized `HumanActor`.
- **Tests**: `tests/unit/test_partial_approval.py` (counter-offer generation, human partial decision, limit validation).
- **Regression Suites**: Optimization & scaling safety suite (22 tests), approval lifecycle integration (12 tests).
- **Security**: Immutable financial audit log; authorized amount cannot exceed requested amount.
- **Completion Evidence**: End-to-end partial approval workflow verified from counter-offer to human approval.

---

### Milestone 12.5: Decision Explainability & Counterfactual Analysis
- **Official Requirement**: Human-readable decision explanations, key evidence highlights, and counterfactuals.
- **Scope**:
  - Implement `ExplanationEngine` in `src/casefile/workflow/explainability.py`:
    - **Executive Summary**: Synthesizes facts, coverage, findings, and decision in plain business language.
    - **Evidentiary Citations**: Links each finding to specific document IDs, line items, and policy clauses.
    - **Counterfactual Scenarios**: Computes sensitivity analysis (e.g., "If repair cost had been < \$3,000, claim would have been eligible for fast-track triage; If prior claim history contained > 2 losses, fraud risk would increase to HIGH").
  - Expose explainability payload via `ReviewResult.explanation_brief` and `GET /api/v1/workflows/{workflow_id}/explain`.
- **Dependencies**: Milestones 12.1, 12.3, 12.4.
- **Files Expected to Change**:
  - `src/casefile/workflow/explainability.py` (new)
  - `src/casefile/agents/reviewer.py`
  - `src/casefile/api/routes/workflows.py`
  - `src/casefile/api/service.py`
  - `src/casefile/api/schemas/models.py`
- **Acceptance Criteria**:
  - Explanations are 100% deterministic based on snapshot data (replayable).
  - Every conclusion cites at least one verified evidence reference.
  - Counterfactual generator produces at least two valid sensitivity scenarios per evaluated claim.
- **Tests**: `tests/unit/test_explainability.py` (brief generation, citation verification, counterfactual logic).
- **Regression Suites**: Phase 10 observability tests, Phase 4 replay tests.
- **Security**: Decision briefs redact sensitive claimant PII and internal API tokens.
- **Completion Evidence**: Complete explainability brief verified against golden claims G01–G06.

---

### Milestone 12.6: Batch Processing & Disaster Scenario Adjudication
- **Official Requirement**: Bulk claim processing and disaster scenario clustering with shared context.
- **Scope**:
  - Implement `BatchCoordinator` in `src/casefile/workflow/batch.py`:
    - Intake of N claims in a single submission (`POST /api/v1/claims/batch`).
    - Attach optional `DisasterContext` (incident type, geographic zone, weather event ID, emergency deductible policy).
    - Dispatch individual claims through standard `WorkflowRunner` while injecting shared disaster context into Investigator tool queries.
    - Track batch completion rate, aggregates (total requested vs. approved), and terminal state distributions.
  - Strictly enforce isolated `BudgetState` per claim within the batch.
- **Dependencies**: Milestones 12.1, 12.4.
- **Files Expected to Change**:
  - `src/casefile/workflow/batch.py` (new)
  - `src/casefile/api/routes/claims.py`
  - `src/casefile/api/routes/workflows.py`
  - `src/casefile/api/service.py`
  - `src/casefile/api/schemas/models.py`
  - `src/casefile/storage/repositories.py`
- **Acceptance Criteria**:
  - Batches of up to 50 claims execute concurrently with isolated failure boundaries (one claim failure does not crash the batch).
  - Disaster event context is propagated to investigator tools without cross-claim data leakage.
  - Batch metrics accurately aggregate approved, rejected, and parked approval counts.
- **Tests**: `tests/integration/test_batch_orchestration.py` (batch intake, concurrent processing, disaster scenario).
- **Regression Suites**: Phase 3 budget enforcement tests, Phase 8 security suite.
- **Security**: Cross-claim isolation; no claim in a batch can view another claim's documents or financial data.
- **Completion Evidence**: 20-claim synthetic disaster batch completes with 100% budget compliance and zero cross-leakage.

---

### Milestone 12.7: Active Learning & Feedback Loop with PII Redaction
- **Official Requirement**: Capture human reviewer feedback, fine-tune agents, and continuous improvement loop.
- **Scope**:
  - Implement `FeedbackService` in `src/casefile/approval/feedback.py`:
    - Capture structured feedback during human decision (`error_category`, `adjuster_notes`, `overridden_recommendation`).
    - Built-in PII Anonymizer: Automatically strips names, addresses, phone numbers, policy numbers, and VINs using `ObservabilitySanitizer` patterns.
    - Persist sanitized feedback in `approval_feedback` table.
  - Implement dataset export utility (`src/casefile/evaluation/active_learning.py`):
    - Export fine-tuning/few-shot pairs (claim context -> human-corrected decision).
    - Drift detection: Compare agent agreement with human adjusters over rolling time windows.
- **Dependencies**: Milestones 12.1, 12.4, 12.5.
- **Files Expected to Change**:
  - `src/casefile/approval/feedback.py` (new)
  - `src/casefile/evaluation/active_learning.py` (new)
  - `src/casefile/approval/service.py`
  - `src/casefile/api/routes/approvals.py`
  - `src/casefile/api/service.py`
- **Acceptance Criteria**:
  - Human adjuster can submit feedback alongside or after an approval decision.
  - Sanitizer guarantees zero PII or credentials in exported dataset samples.
  - Evaluation harness can ingest exported feedback to run regression benchmarks against updated prompts.
- **Tests**: `tests/unit/test_active_learning.py` (feedback submission, PII sanitizer, export pipeline).
- **Regression Suites**: Phase 7 security suite, Phase 11 evaluation invariant suite.
- **Security**: Mandatory PII scrubbing; feedback submissions require authenticated `HumanActor` credentials.
- **Completion Evidence**: Exported training dataset verified to contain zero sensitive PII across 50 simulated feedback records.

---

### Milestone 12.8: Phase 12 Completion Gate & Verification
- **Official Requirement**: Comprehensive regression verification, quality gate signoff, and completion report.
- **Scope**:
  - Run complete unit, integration, and security test suites across all 12 project phases.
  - Run linting (`black`, `ruff`), type checking (`mypy`), and package integrity (`poetry check`).
  - Run evaluation CLI (`casefile evaluate`) against golden scenarios.
  - Produce comprehensive completion report at `docs/phase-12-completion.md`.
- **Dependencies**: Milestones 12.1–12.7.
- **Files Expected to Change**:
  - `docs/phase-12-completion.md` (new)
  - `docs/roadmap.md` (status updates)
- **Acceptance Criteria**:
  - 100% test pass rate across all suites (>700 tests).
  - Zero mypy type issues; zero ruff lint errors; zero black formatting discrepancies.
  - `casefile evaluate` passes with 100% invariant compliance.
  - All Phase 12 safety guarantees verified.
- **Completion Evidence**: Published `docs/phase-12-completion.md` reporting PASS.

---

## 6. Prompt Estimation & Execution Roadmap

To prevent context window fragmentation and avoid unnecessary micro-prompts, the 8 implementation milestones are structured into **8 distinct major execution prompts**:

| Prompt # | Milestone | Major Deliverables | Estimated Tests |
|---|---|---|---|
| **Prompt 1** | Milestone 12.1 | Domain models, typed contracts, Alembic migration 0009, persistence tables | ~25 unit tests |
| **Prompt 2** | Milestone 12.2 | Translation protocol, resilient adapter, fake client, multilingual Extractor | ~20 unit tests |
| **Prompt 3** | Milestone 12.3 | ML fraud scorer, external fraud DB adapter, multi-modal fraud tool integration | ~25 unit tests |
| **Prompt 4** | Milestone 12.4 | Counter-offer generation, partial approval contracts, human payout bounds, runner | ~25 unit/int tests |
| **Prompt 5** | Milestone 12.5 | Explainability engine, evidentiary citations, counterfactual analysis, API endpoint | ~20 unit tests |
| **Prompt 6** | Milestone 12.6 | Batch coordinator, disaster context, concurrent runner, batch API routes | ~20 int tests |
| **Prompt 7** | Milestone 12.7 | Feedback capture service, PII sanitizer, active learning dataset exporter | ~20 unit tests |
| **Prompt 8** | Milestone 12.8 | Full system regression sweep, evaluation CLI, docs/phase-12-completion.md | All suites (~750 tests) |

**Total Estimated Implementation Prompts**: **8 prompts**.

---

## 7. Safety Architecture & Non-Negotiable Invariants

Throughout all Phase 12 implementation prompts, the following architectural invariants are strictly enforced:

1. **Human Authorization Invariant**:
   - `WorkflowState.APPROVED` is accessible **exclusively** via `(WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_GRANTED)` with `actor = AgentType.HUMAN`.
   - No automated agent, ML model, translation service, or batch runner can authorize claim payment.
2. **Partial Payout Invariant**:
   - `APPROVE_PARTIAL` is an advisory recommendation when generated by agents.
   - Payout authorization occurs only when an authorized human submits an `ApprovalDecision` specifying `approved_amount`.
   - `approved_amount` must strictly satisfy: `0.00 < approved_amount <= requested_amount`.
3. **Advisory Fraud Scoring**:
   - Fraud scores (ML or database cross-match) do not possess state transition authority. High fraud risk routes the claim to the reviewer and human adjuster with flags attached, never to terminal rejection automatically.
4. **Active Learning Privacy Fence**:
   - Training datasets and prompt refinement exemplars must undergo mandatory PII scrub before writing. No real names, addresses, policy numbers, or financial identifiers may be stored in plain text.
5. **Batch Isolation & Budget Independence**:
   - Every claim enrolled in a batch is governed by an independent `BudgetState`. Budget exhaustion on one claim terminates only that claim with `BUDGET_EXHAUSTED`, leaving the remaining claims in the batch unaffected.
6. **Evidentiary Provenance**:
   - Machine translation stores the raw original document alongside the translated text, recording source language, translation timestamp, and SHA-256 digests in audit records.

---

## 8. Definition of Done (Phase 12 Completion Gate)

Phase 12 will be accepted if and only if:
- [ ] All 6 Phase 12 capability areas are implemented per specification.
- [ ] Alembic migration `0009_phase12_advanced_features.py` executes cleanly on fresh and existing databases.
- [ ] Multilingual ingestion successfully processes Spanish, French, and German claims.
- [ ] ML fraud scoring and external fraud database adapters operate offline with deterministic test doubles.
- [ ] Partial approval and counter-offers are verified end-to-end with human financial authorization.
- [ ] Explainability engine generates structured briefs and counterfactuals.
- [ ] Batch coordinator executes 20+ concurrent disaster claims with isolated budgets.
- [ ] Active learning feedback captures adjuster feedback with 100% PII scrub verification.
- [ ] Full quality gate passes:
  - `poetry run black --check src tests`
  - `poetry run ruff check src tests`
  - `poetry run mypy src tests`
  - `poetry check`
  - `pytest -v` (>700 tests passed, 0 failures, 0 regressions)
- [ ] `docs/phase-12-completion.md` is published with verifiable execution proof.

---

**Plan Prepared By**: CASEFILE Engineering  
**Date**: 2026-11-07  
**Status**: Ready for User Review and Approval

