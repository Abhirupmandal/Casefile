# Phase 7 Completion Report: REST API + Operations Console

## 1. Executive Summary

Phase 7 establishes the operator-facing application layer for the CASEFILE Multi-Agent Insurance Claim Orchestration Platform. It exposes the domain workflows through a versioned FastAPI REST API (`/api/v1/...`) and a high-fidelity Operations Console built in React and TypeScript. 

The system enforces strict server-side Role-Based Access Control (RBAC), sliding-window rate limiting, request idempotency, uniform structured error responses, and privacy-preserving observability. The Operations Console provides interactive workflow inspection, chronological timeline tracking, agent and tool telemetry, a Human-in-the-Loop (HITL) approval queue, a safe deterministic replay simulator, evaluation benchmark inspection, and system health probes.

All 29 Phase 7 acceptance categories passed with 100% test coverage. Regression testing across all completed phases (Phase 2 through Phase 7) verified zero regressions across 289 tests.

---

## 2. API Architecture

The API layer is structured in `src/casefile/api/` with strict domain boundaries:
- **`app.py`**: FastAPI factory, OpenAPI metadata, CORS middleware, structured error handler registration, and `/console` static mounting.
- **`auth.py`**: Authentication protocol, Principal abstraction, Role definitions, and FastAPI dependency checkers.
- **`dependencies.py`**: Dependency injection providers for `ApiService`, `AppConfig`, `RateLimiter`, and `IdempotencyStore`.
- **`errors.py`**: Uniform exception handlers mapping domain exceptions to standard structured API errors.
- **`idempotency.py`**: Thread-safe in-memory idempotency cache preventing duplicate workflow runs or conflicting re-execution.
- **`middleware.py`**: Observability middleware capturing request ID, correlation ID, method, path, status, and duration while sanitizing credentials and prompt inputs.
- **`rate_limit.py`**: Sliding-window rate limiter protecting mutation routes (`/claims`, `/approvals/.../decision`, `/replay`).
- **`service.py`**: Application coordinator integrating domain services, UnitOfWork, approval engine, checkpoint store, and workflow runner without bypassing business rules.
- **`routes/`**: Modular route handlers for health, claims, workflows, approvals, checkpoints, replay, agents, tools, evaluations, audit, and metrics.
- **`schemas/models.py`**: Strongly-typed Pydantic request and response schemas isolating internal domain ORM models.

---

## 3. Endpoint Catalog

| Method | Endpoint | Authorized Roles | Purpose |
|---|---|---|---|
| `GET` | `/health` | Public | Liveness probe returning process status |
| `GET` | `/ready` | Public | Readiness probe validating persistence dependencies |
| `POST` | `/api/v1/claims` | `OPERATOR`, `ADMIN` | Submit and execute new claim adjudication |
| `GET` | `/api/v1/workflows` | `VIEWER`+ | List summaries of all workflow executions |
| `GET` | `/api/v1/workflows/{id}` | `VIEWER`+ | Operational details of a specific workflow |
| `GET` | `/api/v1/workflows/{id}/history` | `VIEWER`+ | Append-only state transition audit log |
| `GET` | `/api/v1/workflows/{id}/agents` | `OPERATOR`, `ADMIN` | Telemetry from Extractor, Investigator, and Reviewer |
| `GET` | `/api/v1/workflows/{id}/tools` | `OPERATOR`, `ADMIN` | Invocations and execution times of external tools |
| `GET` | `/api/v1/workflows/{id}/checkpoints` | `OPERATOR`, `ADMIN` | Checkpoint metadata, hashes, and resumability |
| `POST` | `/api/v1/workflows/{id}/replay` | `OPERATOR`, `ADMIN` | Trigger deterministic simulation replay |
| `GET` | `/api/v1/approvals` | `CLAIM_REVIEWER`+ | Approval queue with status filtering |
| `GET` | `/api/v1/approvals/{id}` | `CLAIM_REVIEWER`+ | Specific approval recommendation details |
| `POST` | `/api/v1/approvals/{id}/decision` | `CLAIM_REVIEWER`+ | Authoritative human approval decision |
| `GET` | `/api/v1/evaluations` | `VIEWER`+ | Benchmark results and 22 system metrics |
| `GET` | `/api/v1/evaluations/cases` | `VIEWER`+ | List all 30 evaluation test cases |
| `GET` | `/api/v1/evaluations/cases/{id}` | `VIEWER`+ | Details for a single evaluation scenario |
| `GET` | `/api/v1/audit` | `ADMIN` | Chronological immutable system audit logs |
| `GET` | `/api/v1/metrics` | `VIEWER`+ | Aggregated KPIs for operations dashboard |

---

## 4. Authentication Model

Authentication is abstracted through `AuthProvider`:
- **`Principal`**: Contains `principal_id`, `role`, `display_name`, `authentication_method`, and granted `permissions`.
- **`DevAuthProvider`**: Deterministic test provider mapping pre-configured developer tokens to discrete roles without external network calls or storing plaintext credentials.
- **FastAPI Dependency Injection**: Routes declare role requirements via `require_roles(...)`, which extracts bearer tokens and validates permissions server-side.

---

## 5. RBAC Matrix

| Role | Workflow Read | Claim Submit | Telemetry Read | Checkpoint/Replay | Approval Read/Decide | Audit Read |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `VIEWER` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `OPERATOR` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `CLAIM_REVIEWER` | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| `SENIOR_REVIEWER` | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| `CLAIM_SUPERVISOR`| ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| `ADMIN` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

*Note: Autonomous agents are strictly excluded from human approval roles.*

---

## 6. Idempotency Design

Mutation endpoints declare support for `Idempotency-Key`:
- Request bodies are SHA-256 hashed.
- If an operation with the same key and payload hash has completed, the cached response is immediately returned.
- If a conflicting payload is sent with an existing key, the server rejects the request with `HTTP 409 Conflict` (`IDEMPOTENCY_CONFLICT`).

---

## 7. Rate Limiting

The `InMemoryRateLimiter` enforces sliding-window ceilings:
- `/claims`: 60 requests / minute
- `/approvals/.../decision`: 30 requests / minute
- `/replay`: 10 requests / minute
- When exhausted, requests return `HTTP 429 Too Many Requests` with a calculated `Retry-After` header. Rate limiting checks execute before domain logic, preventing workflow state corruption.

---

## 8. Error Model

All unhandled and domain exceptions are sanitized and mapped into structured JSON responses:
```json
{
  "error": {
    "code": "<STABLE_ERROR_CODE>",
    "message": "<HUMAN_READABLE_MESSAGE>",
    "request_id": "<UUID>",
    "correlation_id": "<UUID>",
    "details": {}
  }
}
```
No Python tracebacks, database schema errors, raw prompts, or credentials leak to API clients.

---

## 9. Replay API Safety

Replays triggered via `POST /api/v1/workflows/{id}/replay`:
- Produce a distinct `replay_id`.
- Replay strictly against persisted state and recorded tool outcomes.
- Do not make external network requests or execute live tools.
- Never trigger HITL requests or execute payouts.
- Do not mutate the original workflow run record.
- Flag the result with `is_simulation: true`.

---

## 10. Approval API Safety

Approval decisions submitted via `POST /api/v1/approvals/{id}/decision`:
- Require human roles (`CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`).
- Agents, viewers, and operators are barred at the route dependency layer.
- Enforce domain validation: prevent self-approval, reject decisions on terminal runs, and reject expired approvals.

---

## 11. Observability

The `ObservabilityMiddleware` instruments each request:
- Propagates or generates `X-Request-ID` and `X-Correlation-ID`.
- Records method, path, HTTP status, duration, and error codes in OpenTelemetry spans.
- Automatically redacts sensitive headers (Authorization, Cookie), raw claim document bytes, and prompts.

---

## 12. Operations Console Architecture

The frontend is located in `ui/`:
- **Tech Stack**: React 18, TypeScript, Vite, Lucide-react, Vanilla CSS.
- **Theme**: Glassmorphism dark aesthetic (`Outfit` & `JetBrains Mono` fonts).
- **Architecture**:
  - `Navbar.tsx`: Brand, role switcher, navigation, real-time health indicator.
  - `DashboardView.tsx`: Real-time KPIs, active counters, claim submission modal.
  - `WorkflowsView.tsx`: Tabular registry with state filters and search.
  - `WorkflowDetailView.tsx`: Progression stepper, transitions timeline, agent telemetry, tool calls, checkpoints, budget meters.
  - `ApprovalQueueView.tsx`: HITL adjudication queue with role-aware decision modal.
  - `ReplayView.tsx`: Simulation runner and side-by-side verification comparator.
  - `EvaluationsView.tsx`: 30 benchmark cases and 22 system resilience metrics.
  - `HealthView.tsx`: Diagnostic liveness and readiness inspector.

---

## 13. Test Results

### Backend Acceptance Tests (`tests/unit/test_phase7_acceptance.py`)
- Category A (Application creation): PASS
- Category B (API versioning): PASS
- Category C (Claim submission): PASS
- Category D (Workflow status): PASS
- Category E (Workflow history): PASS
- Category F (Agent visibility): PASS
- Category G (Tool visibility): PASS
- Category H (Checkpoint visibility): PASS
- Category I (Replay): PASS
- Category J (Replay isolation): PASS
- Category K (Approval queue): PASS
- Category L (Approval authorization): PASS
- Category M (Approval decision): PASS
- Category N (Self-approval protection): PASS
- Category O (Expired approval protection): PASS
- Category P (Authentication): PASS
- Category Q (Role authorization): PASS
- Category R (Structured errors): PASS
- Category S (Idempotency): PASS
- Category T (Rate limiting): PASS
- Category U (Health): PASS
- Category V (Readiness): PASS
- Category W (API observability): PASS
- Category X (Telemetry sanitization): PASS
- Category Y (OpenAPI): PASS
- Category Z (Evaluation endpoint): PASS
- Category AA (Audit endpoint): PASS
- Category AB (Console data contracts): PASS
- Category AC (E2E Complete Flow): PASS

**Total Phase 7 Acceptance: 29 passed, 0 failed.**

### Frontend Tests (`ui/src/test/App.test.tsx`)
- `renders DashboardView with real operational KPI metrics`: PASS
- `enforces RBAC in ApprovalQueueView`: PASS
- `renders ReplayView with explicit simulation warning`: PASS
- `renders EvaluationsView and health diagnostic views`: PASS

**Total Frontend Tests: 4 passed, 0 failed.**

---

## 14. Regression Results

Full multi-phase regression suite (`Phase 2 through Phase 7`):
- `test_phase2_acceptance.py`: 34 passed
- `test_phase3_acceptance.py`: 52 passed
- `test_phase4_acceptance.py`: 56 passed
- `test_phase5_acceptance.py`: 98 passed
- `test_phase6_acceptance.py`: 20 passed
- `test_phase7_acceptance.py`: 29 passed

**Combined Acceptance Tests: 289 passed, 0 failed (100% pass rate).**

Evaluation Core Tests (`test_evaluation_core.py`):
**90 passed, 0 failed.**

CLI Evaluation Runner (`python -m casefile.evaluation`):
**15 golden scenarios passed, 100% scenario pass rate, 100% invariant pass rate.**

---

## 15. Static Analysis

- `poetry run ruff check src tests`: PASS (All checks passed)
- `poetry run black --check src tests`: PASS (178 files would be left unchanged)
- `poetry run mypy src`: PASS (Success: no issues found in 119 source files)
- `poetry check`: PASS (Dependencies valid)

---

## 16. Frontend Build Results

- `tsc`: PASS (0 errors)
- `vite build`: PASS (`ui/dist/` generated in 28.24s)
  - `dist/index.html`: 0.93 kB
  - `dist/assets/index-Ddq6VbkZ.css`: 5.20 kB
  - `dist/assets/index-DcWQbH5V.js`: 215.35 kB

---

## 17. Security Verification

- **Viewer Access**: Verified that `VIEWER` cannot submit claims, cannot inspect tool arguments, and cannot decide approvals (`HTTP 403`).
- **Operator Access**: Verified that `OPERATOR` cannot approve claims (`HTTP 403`).
- **Unauthenticated Requests**: Missing or malformed tokens return `HTTP 401`.
- **Self-Approval Guard**: A submitting operator cannot decide their own claim (`HTTP 403`).
- **Expired Approval Guard**: Expired approvals reject decisions (`HTTP 409`).
- **Sanitization**: Verified that API responses never contain raw authorization headers, secret keys, or database stack traces.

---

## 18. Files Changed

### Backend API
- `src/casefile/api/__init__.py`
- `src/casefile/api/app.py`
- `src/casefile/api/auth.py`
- `src/casefile/api/dependencies.py`
- `src/casefile/api/errors.py`
- `src/casefile/api/idempotency.py`
- `src/casefile/api/middleware.py`
- `src/casefile/api/rate_limit.py`
- `src/casefile/api/service.py`
- `src/casefile/api/schemas/__init__.py`
- `src/casefile/api/schemas/models.py`
- `src/casefile/api/routes/__init__.py`
- `src/casefile/api/routes/health.py`
- `src/casefile/api/routes/claims.py`
- `src/casefile/api/routes/workflows.py`
- `src/casefile/api/routes/approvals.py`
- `src/casefile/api/routes/checkpoints.py`
- `src/casefile/api/routes/replay.py`
- `src/casefile/api/routes/agents.py`
- `src/casefile/api/routes/tools.py`
- `src/casefile/api/routes/evaluations.py`
- `src/casefile/api/routes/audit.py`
- `src/casefile/api/routes/metrics.py`

### Operations Console Frontend
- `ui/package.json`
- `ui/tsconfig.json`
- `ui/tsconfig.node.json`
- `ui/vite.config.ts`
- `ui/index.html`
- `ui/src/index.css`
- `ui/src/main.tsx`
- `ui/src/App.tsx`
- `ui/src/types/api.ts`
- `ui/src/services/api.ts`
- `ui/src/components/Navbar.tsx`
- `ui/src/components/DashboardView.tsx`
- `ui/src/components/WorkflowsView.tsx`
- `ui/src/components/WorkflowDetailView.tsx`
- `ui/src/components/ApprovalQueueView.tsx`
- `ui/src/components/ReplayView.tsx`
- `ui/src/components/EvaluationsView.tsx`
- `ui/src/components/HealthView.tsx`
- `ui/src/test/setup.ts`
- `ui/src/test/App.test.tsx`

### Tests & Documentation
- `tests/unit/test_phase7_acceptance.py`
- `docs/api.md`
- `docs/authentication.md`
- `docs/operations-console.md`
- `docs/phase-7-completion.md`

---

## 19. Known Limitations

- In-memory rate limiting and idempotency stores are single-instance; distributed Redis backends are deferred to Phase 8.
- Authentication uses the deterministic `DevAuthProvider` abstraction; production OIDC/OAuth2 providers are deferred to Phase 8.
- Real insurance carrier core systems and live payment settlement gateways are out of scope.

---

## 20. Final Gate

```
============================================================
PHASE 7 GATE: PASS
============================================================
```
