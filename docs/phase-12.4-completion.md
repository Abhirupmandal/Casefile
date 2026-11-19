# Phase 12.4 Completion Report: Partial Approval, Counter-Offers & Negotiation Workflow

**Milestone**: Phase 12.4 — Partial Approval, Counter-Offers & Negotiation Workflow  
**Project**: CASEFILE — Multi-Agent Insurance Claim Orchestration Platform  
**Status**: COMPLETE — READY FOR INDEPENDENT AUDIT  
**Date**: November 2026  

---

## 1. Executive Summary

Phase 12.4 completes the end-to-end lifecycle for partial claim approvals, insurer-generated counter-offers, claimant negotiation responses, and human-supervised financial settlement.

Building upon the authoritative amount validation and SQL foundations established in Phase 12.1 and the human-approval routing invariants established in Phase 12.3, Phase 12.4 introduces:
- A dedicated, audited `counter_offers` domain lifecycle managed by `CounterOfferService` and backed by `CounterOfferRecord` in persistence.
- Strict multi-turn negotiation version chains bounded to exactly 3 total counter-offers per claim (1 initial offer + up to 2 revisions).
- A secured FastAPI counter-offer router with complete RBAC and tenant/claimant identity isolation.
- Absolute enforcement of separation of duties: AI agents cannot propose or authorize counter-offers; claimants cannot authorize payouts or access competing claimant records; settlements require explicit human insurer authorization.
- Alembic migration `0011_counter_offers.py` adding the `counter_offers` table with composite unique sequence constraints and foreign keys to claims and workflows.
- Comprehensive test coverage across unit contracts, workflow integration, and adversarial security invariants (26 dedicated tests, all passing with 100% success).

---

## 2. Architecture & Design Decisions

### 2.1 Separation of Duties & Human-in-the-Loop Settlement
- **AI Agents**: AI agents (Supervisor, Extractor, Investigator, Reviewer) can evaluate evidence and identify disputable items, but **NEVER** generate legal counter-offers or authorize financial disbursements.
- **Claim Reviewers (Insurers)**: Only authenticated human reviewers (`CLAIM_REVIEWER`, `ADMIN`) can create counter-offers, withdraw active offers, or execute the final settlement payout.
- **Claimants**: Claimants (`Role.CLAIMANT`) authenticate via bearer tokens and can view offers on their own claims and respond (`ACCEPT`, `REJECT`, `REQUEST_REVISION`). Claimant acceptance **never triggers automatic payout**; it transitions the offer to `ACCEPTED` and the claim to `HUMAN_APPROVAL`, requiring an authorized insurer reviewer to execute `settle_counter_offer`.

### 2.2 Counter-Offer State Lifecycle
Counter-offers transition through deterministic, immutable states:
```
                ┌──────────────────────────────────────┐
                │          PENDING (Active)            │
                └──┬─────────────┬─────────────────┬───┘
                   │             │                 │
            Claimant       Claimant          Insurer / System
            Accepts        Rejects           Withdraws / Expires / Supersedes
                   │             │                 │
                   ▼             ▼                 ▼
              ACCEPTED       REJECTED      WITHDRAWN / EXPIRED / SUPERSEDED
                   │
         Insurer Executes
            Settlement
                   │
                   ▼
               SETTLED (APPROVED)
```

### 2.3 Negotiation Sequences & Version Chaining
- Each counter-offer receives a strictly monotonic `sequence_number` (1, 2, 3...) per claim.
- When a claimant requests a revision (`REQUEST_REVISION`) and the insurer issues a revised offer, the prior offer is linked via `superseded_by_offer_id` and marked `SUPERSEDED`.
- Negotiation is bounded by `MAX_COUNTER_OFFERS_PER_CLAIM = 3` (1 initial counter-offer + up to 2 revisions = 3 total counter-offers per claim). Attempts to create a 4th offer fail closed with `NEGOTIATION_LIMIT_EXCEEDED`, requiring direct adjuster intervention.

---

## 3. Database Changes & Migration Details

### 3.1 Migration `0011_counter_offers.py`
Alembic migration `0011_counter_offers.py` was created, branching from `0010_fraud_assessments`:
- **Table Name**: `counter_offers`
- **Columns**:
  - `counter_offer_id`: UUID (Primary Key)
  - `claim_id`: UUID (Foreign Key to `claims.claim_id`, indexed)
  - `workflow_run_id`: UUID (Foreign Key to `workflow_runs.workflow_run_id`, nullable, indexed)
  - `sequence_number`: Integer (Not Null, sequence per claim)
  - `original_amount`: Numeric(12, 2) (Not Null, authoritative claim ceiling)
  - `previously_approved_amount`: Numeric(12, 2) (Nullable, prior partial approval base)
  - `proposed_amount`: Numeric(12, 2) (Not Null, offer value)
  - `currency`: String(3) (Not Null, default "USD")
  - `status`: String(32) (Not Null, default "PENDING", indexed)
  - `reduction_reason`: Text (Not Null, rationale for reduction)
  - `disputed_items`: JSON (Not Null, line-item itemization)
  - `created_by`: String(128) (Not Null, actor ID of insurer creator)
  - `created_at`: DateTime(timezone=True) (Not Null)
  - `expires_at`: DateTime(timezone=True) (Not Null, expiration deadline)
  - `responded_at`: DateTime(timezone=True) (Nullable)
  - `response_action`: String(32) (Nullable: ACCEPT, REJECT, REQUEST_REVISION)
  - `response_reason`: Text (Nullable, claimant feedback/rationale)
  - `responded_by`: String(128) (Nullable, claimant ID)
  - `superseded_by_offer_id`: UUID (Foreign Key to `counter_offers.counter_offer_id`, nullable)
- **Constraints & Indexes**:
  - `uq_counter_offers_claim_seq`: UniqueConstraint(`claim_id`, `sequence_number`)
  - Indexes on `claim_id`, `workflow_run_id`, `status`
- **Bidirectional Migration**: Verified via migration test `test_alembic_migration_0011_upgrade_and_downgrade`.

---

## 4. Contract & Model Definitions

### 4.1 Enums (`src/casefile/models/contracts.py`)
```python
class CounterOfferStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"

class CounterOfferResponseAction(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"
```

### 4.2 Domain Model (`CounterOffer`)
```python
class CounterOffer(BaseModel):
    counter_offer_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    workflow_run_id: UUID | None = None
    sequence_number: int = 1
    original_amount: Decimal
    previously_approved_amount: Decimal | None = None
    proposed_amount: Decimal
    currency: str = "USD"
    status: CounterOfferStatus = CounterOfferStatus.PENDING
    reduction_reason: str
    disputed_items: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    responded_at: datetime | None = None
    response_action: CounterOfferResponseAction | None = None
    response_reason: str | None = None
    responded_by: str | None = None
    superseded_by_offer_id: UUID | None = None
```

### 4.3 Audit Event Types (`src/casefile/models/domain.py`)
Added structured event types to `AuditEventType`:
- `COUNTER_OFFER_CREATED`
- `COUNTER_OFFER_ACCEPTED`
- `COUNTER_OFFER_REJECTED`
- `COUNTER_OFFER_REVISION_REQUESTED`
- `COUNTER_OFFER_SUPERSEDED`
- `COUNTER_OFFER_WITHDRAWN`
- `COUNTER_OFFER_EXPIRED`
- `COUNTER_OFFER_SETTLED`

---

## 5. API Endpoints & Request/Response Samples

The router is mounted under `/api/v1/claims/{claim_id}/counter-offers`:

| Method | Endpoint | Authorized Roles | Description |
|--------|----------|-------------------|-------------|
| `POST` | `/` | `CLAIM_REVIEWER`, `ADMIN` | Create a new counter-offer |
| `GET` | `/` | `CLAIMANT` (owner), `REVIEWER`, `ADMIN` | List all counter-offers for a claim |
| `GET` | `/active` | `CLAIMANT` (owner), `REVIEWER`, `ADMIN` | Retrieve currently pending counter-offer |
| `GET` | `/{offer_id}` | `CLAIMANT` (owner), `REVIEWER`, `ADMIN` | Retrieve specific counter-offer |
| `POST` | `/{offer_id}/respond` | `CLAIMANT` (owner only) | Submit claimant response (`ACCEPT`, `REJECT`, `REQUEST_REVISION`) |
| `POST` | `/{offer_id}/withdraw` | `CLAIM_REVIEWER`, `ADMIN` | Withdraw a pending counter-offer |
| `POST` | `/{offer_id}/settle` | `CLAIM_REVIEWER`, `ADMIN` | Authorize final payout for accepted offer |

### Request Sample: Create Counter-Offer (`POST /api/v1/claims/{claim_id}/counter-offers`)
```json
{
  "proposed_amount": 7500.00,
  "reason": "Depreciation applied to paint labor and structural blending",
  "disputed_items": [
    {"item": "Paint Labor", "claimed": 2500.00, "allowed": 1500.00, "disallowed": 1000.00}
  ],
  "expires_in_days": 14
}
```

### Response Sample: Counter-Offer Response (`POST /api/v1/claims/{claim_id}/counter-offers/{offer_id}/respond`)
```json
{
  "action": "ACCEPT",
  "reason": "Agreed to settlement with revised labor costs"
}
```

### Response Sample: Settlement Execution (`POST /api/v1/claims/{claim_id}/counter-offers/{offer_id}/settle`)
```json
{
  "offer_id": "4b68e987-9bb3-47a3-86b1-4122d25f2991",
  "claim_id": "9961f364-c5a3-4b95-a1c1-4191d9d93540",
  "workflow_run_id": "0d257321-df62-42fe-bd18-47c3e53d5a42",
  "settled_amount": "7500.00",
  "status": "APPROVED",
  "settled_by": "rev-01",
  "settled_at": "2026-11-08T03:52:56.123456+00:00",
  "approval_id": "80fd9e85-7f02-4fc8-9f33-cbe6b1853612"
}
```

---

## 6. Authorization & Role Separation Matrix

| Actor / Role | Create Offer | View Own Offer | View Other's Offer | Respond to Offer | Withdraw Offer | Settle Offer |
|---|---|---|---|---|---|---|
| `Role.CLAIMANT` (Claim Owner) | ❌ 403 | ✅ 200 | ❌ 403 | ✅ 200 | ❌ 403 | ❌ 403 |
| `Role.CLAIMANT` (Non-Owner) | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 |
| `Role.CLAIM_REVIEWER` | ✅ 200 | ✅ 200 | ✅ 200 | ❌ 403 | ✅ 200 | ✅ 200 |
| `Role.ADMIN` | ✅ 200 | ✅ 200 | ✅ 200 | ❌ 403 | ✅ 200 | ✅ 200 |
| `Role.OPERATOR` / `Role.VIEWER` | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 |
| AI Agent Identities (`SUPERVISOR`, `EXTRACTOR`, `INVESTIGATOR`, `REVIEWER`) | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 | ❌ 403 |

---

## 7. Security Invariant Verification

Phase 12.4 was subjected to rigorous adversarial testing:

1. **Phase 12.1 Exploit Regression ($100 Claim / $1,000,000 Offer)**:
   - Persisted claim amount ($100.00) acts as an authoritative, tamper-proof ceiling.
   - Any counter-offer with `proposed_amount > authoritative_amount` fails closed with HTTP 422 / `CounterOfferErrorCode.VALIDATION_ERROR`.
   - Verified by `test_million_dollar_offer_on_hundred_dollar_claim_rejected`.
2. **Claimant Tenant Isolation**:
   - Claimant A authenticated with token `dev-claimant-token` (principal `CLM-001`) cannot view or respond to Claimant B's (`CLM-002`) counter-offer. Fails closed with HTTP 403.
   - Verified by `test_claimant_cannot_access_or_respond_to_another_claimants_offer`.
3. **Separation of Duties**:
   - Claimants attempting to create counter-offers on their own claims fail closed with HTTP 403.
   - Verified by `test_claimant_cannot_create_counter_offer_on_own_claim`.
4. **No Autonomous Agent Financial Authority**:
   - Agent identities attempting to create or settle counter-offers fail closed with HTTP 403.
   - Verified by `test_agent_cannot_create_or_settle_offer`.
5. **No Automatic Payout on Claimant Acceptance**:
   - When a claimant accepts an offer, the workflow state remains `HUMAN_APPROVAL`. Payout authorization requires an explicit call to `settle_counter_offer` by an authorized reviewer.
   - Verified by `test_claimant_acceptance_cannot_bypass_insurer_authorization`.
6. **Numeric & Currency Sanitization**:
   - Negative amounts, zero amounts, NaN, and positive/negative infinity are rejected at schema validation.
   - Verified by `test_negative_zero_nan_infinite_amounts_rejected`.
7. **Strict Idempotency & Conflict Prevention**:
   - Duplicate acceptance attempts on already accepted or settled offers return HTTP 409 / `CONFLICT`.
   - Attempts to respond to superseded or expired offers return HTTP 409 / HTTP 410.
   - Verified by `test_duplicate_acceptance_rejected` and `test_acceptance_of_superseded_offer_rejected`.

---

## 8. State Machine & Lifecycle Behavior

1. **Offer Creation**:
   - Pre-condition: Workflow run exists and claim is in `HUMAN_APPROVAL` state.
   - Insurer creates offer #1. Previous pending offers (if any) are superseded.
   - Offer is in `PENDING` state with expiration timestamp.
2. **Claimant Rejection**:
   - Claimant responds with `REJECT`. Offer transitions to `REJECTED`.
   - Claim remains in `HUMAN_APPROVAL` state for insurer adjuster follow-up.
3. **Claimant Revision Request**:
   - Claimant responds with `REQUEST_REVISION` providing reasoning and optional counter-amount.
   - Offer records revision feedback. Insurer reviews feedback and creates offer #2 (sequence 2), linking offer #1 via `superseded_by_offer_id`.
4. **Claimant Acceptance & Settlement**:
   - Claimant responds with `ACCEPT`. Offer transitions to `ACCEPTED`.
   - Insurer calls `settle_counter_offer`.
   - An audited `HumanApprovalRecord` is written with `ApprovalVerdict.APPROVE_PARTIAL` for `settled_amount`.
   - The workflow state transitions to `APPROVED` with terminal reason `"Settled via counter-offer: {reason}"`.

---

## 9. Test Suite Execution Results

### 9.1 Phase 12.4 Dedicated Test Battery
- `tests/unit/test_counter_offers.py`: **11 passed**
  - Contract validation, ceiling checks, decimal precision, expiration, response models, sequence versioning, and negotiation turn limits.
- `tests/integration/test_counter_offer_workflow.py`: **5 passed**
  - End-to-end multi-turn negotiation, claimant rejection, idempotency, and Alembic migration `0011` upgrade/downgrade.
- `tests/security/test_counter_offer_security.py`: **10 passed**
  - 401 unauthenticated, 403 claimant isolation, 403 unauthorized roles, 403 separation of duties, exploit ceiling protection, numeric bounds, 409 duplicate acceptance, superseded rejections, and agent identity lockouts.
- **Phase 12.4 Dedicated Total**: **26 tests, 26 passed (100% success)**.

### 9.2 Complete Phase 12 Regression Battery
- `tests/unit/test_counter_offers.py` (11 passed)
- `tests/integration/test_counter_offer_workflow.py` (5 passed)
- `tests/security/test_counter_offer_security.py` (10 passed)
- `tests/unit/test_fraud_subsystem.py` (22 passed)
- `tests/integration/test_fraud_workflow.py` (8 passed)
- `tests/security/test_fraud_security.py` (11 passed)
- `tests/security/test_partial_approval_security.py` (16 passed)
- `tests/unit/test_multilingual_pipeline.py` (14 passed)
- `tests/integration/test_multilingual_api.py` (7 passed)
- **Combined Phase 12 Battery**: **104 tests, 104 passed (100% success)**.

### 9.3 Integration & Security Suites
- `tests/integration/` + `tests/security/`: **213 passed, 2 skipped, 0 failed**.

---

## 10. Operational Considerations

1. **Persistence & Recovery**:
   - All counter-offers are persisted in SQLite via SQLAlchemy UoW pattern within transaction boundaries.
   - If an API instance restarts while a counter-offer is pending, its full state, timestamps, and sequence numbers are restored without data loss.
2. **Audit Trails**:
   - Every mutation produces structured append-only `AuditEvent` records with non-PII payloads, capturing `claim_id`, `offer_id`, `actor_id`, `sequence_number`, and financial amounts.
3. **Observability**:
   - Counter-offer operations are traced using OpenTelemetry spans with standardized attributes (`counter_offer.id`, `counter_offer.amount`, `counter_offer.action`).

---

## 11. Verification Checklist

- [x] Authoritative claim amount ceiling enforced (no caller override; $100 claim with $1M offer rejected).
- [x] Counter-offer domain contracts (`CounterOffer`, `CounterOfferResponse`, `CounterOfferSettlement`) validated with Pydantic.
- [x] Database schema & Alembic migration `0011_counter_offers.py` with unique sequence constraints and foreign keys.
- [x] SqlCounterOfferRepository and UnitOfWork integration with clean transaction management.
- [x] CounterOfferService implementing full state lifecycle, expiration, revision chaining, and sequence limits.
- [x] Role-Based Access Control enforcing strict separation of duties (Claimants vs Reviewers vs Agents).
- [x] Claimant tenant isolation preventing cross-claimant data inspection or offer acceptance.
- [x] Claimant acceptance does not trigger automatic payout; requires human insurer settlement authorization.
- [x] Multi-turn negotiation bounded to exactly 3 total counter-offers per claim (1 initial offer + up to 2 revisions; 4th offer rejected).
- [x] Comprehensive test suites (unit, integration, security) with 100% passing tests.
- [x] Clean static analysis: `ruff check` (0 errors), `black --check` (0 issues), `mypy src` (143 source files clean).
- [x] Documentation updated: `docs/roadmap.md` and `docs/phase-12.4-completion.md`.

---
**STATUS: IMPLEMENTATION COMPLETE — READY FOR INDEPENDENT AUDIT**

