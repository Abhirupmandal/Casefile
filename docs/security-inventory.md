# CASEFILE Security Inventory (Phase 8)

**Date**: 2026-10-05  
**Auditor**: CASEFILE Security Engineering / Antigravity Agent  
**Scope**: Full application stack (API, domain, workflow engine, persistence, human approval, replay, observability, containerization, dependencies).

---

## Executive Summary

This security inventory documents actual findings discovered during the comprehensive audit of CASEFILE Phase 0 through Phase 7. Each component is evaluated across its trust boundaries, authorization gates, and operational configurations. Vulnerabilities and weaknesses are classified by risk tier (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) without speculation or fabricated issues.

---

## 1. Inventory & Risk Classification

### 1.1 API Authentication
- **Current State**: Phase 7 implemented `AuthProvider` protocol with `DevAuthProvider`, supporting Bearer tokens (`DEV_TOKENS`), API keys, and explicit HTTP headers (`X-Principal-Id`, `X-Principal-Role`).
- **Finding SEC-01**: **`X-Principal-Role` spoofing via headers in production**. In `DevAuthProvider`, any caller sending `X-Principal-Id` and `X-Principal-Role: ADMIN` is granted administrative access without cryptographic validation or shared secret checks.
  - **Risk**: `CRITICAL`
  - **Remediation**: In production mode (`environment != "development" and environment != "test"`), header-based identity spoofing and static dev tokens must be strictly disabled or rejected unless authenticated via a production provider/verified token mechanism.
- **Finding SEC-02**: **Static development tokens in memory**. Tokens like `dev-admin-token` are hardcoded in `DEV_TOKENS`.
  - **Risk**: `HIGH`
  - **Remediation**: Isolate `DEV_TOKENS` strictly to `development` and `test` environments. Fail closed if accessed in `production`.

### 1.2 RBAC & Authorization
- **Current State**: 6 roles (`VIEWER`, `OPERATOR`, `CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`) mapped to granular permissions (`ROLE_PERMISSIONS`). Fast-fail dependency `require_roles` and `require_permission` enforced on routes.
- **Finding SEC-03**: **Role boundaries are strictly server-side, but lack central security request limits**. Viewer and Operator cannot decide approvals or access admin routes. However, route parameters and query filters lacked explicit size/length constraints.
  - **Risk**: `MEDIUM`
  - **Remediation**: Maintain strict server-side authorization and enforce input bounds on all queries.

### 1.3 Human-in-the-Loop (HITL) Approval Service
- **Current State**: `ApprovalService` in `src/casefile/human/approval.py` and API route `decide_approval` enforce:
  1. Human role requirement (agents cannot decide).
  2. Separation of duties (requester cannot self-approve).
  3. Deadline / expiration enforcement (expired requests reject decisions).
  4. Terminal immutability (approved/rejected cannot be modified).
- **Finding SEC-04**: **Adversarial payload overrides in decision requests**. The API endpoint previously relied on the authenticated `principal` for `actor_id` and `role`, properly ignoring any spoofed actor in the body. However, rejection reasons and decision text were unbounded.
  - **Risk**: `LOW`
  - **Remediation**: Add explicit max length validation (e.g., 2,000 characters for `reason`).

### 1.4 Replay Engine
- **Current State**: Replay operates via `SafeReplayEngine` / `service.replay_workflow_safe` using offline deterministic simulation from checkpoints.
- **Finding SEC-05**: **Replay isolation from production state**. Original workflow state is immutable during replay; a new isolated replay run ID is generated. External tool execution is prohibited.
  - **Risk**: `LOW`
  - **Remediation**: Enforce operator-only role authorization (`OPERATOR`, `CLAIM_SUPERVISOR`, `ADMIN`) and rate limiting (30 requests/min).

### 1.5 Idempotency Framework
- **Current State**: In-memory `IdempotencyStore` hashes request payloads using SHA-256 and detects `IDEMPOTENCY_CONFLICT` (HTTP 409).
- **Finding SEC-06**: **Unbounded idempotency key length and memory retention**. Keys passed via `Idempotency-Key` header were not validated for maximum length or character set, allowing memory bloat from arbitrarily large header strings.
  - **Risk**: `MEDIUM`
  - **Remediation**: Validate `Idempotency-Key` header format (length 1–256 characters, printable ASCII), and reject malformed keys with HTTP 400.

### 1.6 Rate Limiting
- **Current State**: In-memory sliding-window `InMemoryRateLimiter`.
- **Finding SEC-07**: **Process-local rate limiting**. In-memory rate limiter is not horizontally distributed across multiple worker processes or nodes.
  - **Risk**: `MEDIUM`
  - **Remediation**: Document that the current rate limiter is process-local and sliding-window. Enforce rate limiting on claim creation, approvals, replay, and evaluation endpoints.

### 1.7 Environment & Production Configuration
- **Current State**: Configuration resides in `config/development.yaml` and `config/test.yaml`, loaded via `src/casefile/config.py`.
- **Finding SEC-08**: **Missing dedicated `config/production.yaml`**. Production configuration was not formally separated, and security configurations (CORS allowlist, allowed hosts, request size limits, CSP) were missing from `AppConfig`.
  - **Risk**: `HIGH`
  - **Remediation**: Create `config/production.yaml` and add `SecurityConfig` to `AppConfig` with fail-closed production defaults.

### 1.8 Database Configuration & Persistence
- **Current State**: Uses SQLite persistence via SQLAlchemy (`sqlite:///./casefile.db` or `:memory:`).
- **Finding SEC-09**: **File permissions & connection URL disclosure**. Database URL is configurable via `CASEFILE_DATABASE_URL`. SQLite is appropriate for local and evaluation runs; production environments require secure file permissions or external PostgreSQL connection strings without logging credentials.
  - **Risk**: `LOW`
  - **Remediation**: Document production database configuration, ensure credentials in URLs are masked in logs.

### 1.9 Telemetry & Observability Configuration
- **Current State**: OpenTelemetry SDK with OTLP/Jaeger exporter and Prometheus metrics.
- **Finding SEC-10**: **Silent failure resilience**. Telemetry failures are caught so external collector downtime never crashes core adjudication. Telemetry payload sanitization redacts PII and credentials (`src/casefile/observability/sanitize.py`).
  - **Risk**: `LOW`
  - **Remediation**: Document architecture: CASEFILE -> OTel SDK -> OTLP Collector -> Storage. Collector availability must not fail readiness checks unless strictly configured.

### 1.10 Frontend Configuration (Operations Console)
- **Current State**: Vite + React 19 application in `ui/`. Builds to static HTML/JS/CSS bundle in `ui/dist`.
- **Finding SEC-11**: **CORS wildcard configuration**. In `src/casefile/api/app.py`, `CORSMiddleware` was initialized with `allow_origins=["*"]` and `allow_credentials=True`.
  - **Risk**: `CRITICAL`
  - **Remediation**: Replace wildcard with an explicit origin allowlist loaded from configuration. Reject credentials with wildcard origins.

### 1.11 HTTP Security Headers
- **Current State**: Standard FastAPI default headers without explicit security policy headers.
- **Finding SEC-12**: **Missing defense-in-depth HTTP security headers**. Missing `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Content-Security-Policy`.
  - **Risk**: `HIGH`
  - **Remediation**: Add custom security headers middleware to inject standards-compliant headers across all API and static console responses.

### 1.12 Request Limits & Input Validation
- **Current State**: Pydantic models validate schema fields.
- **Finding SEC-13**: **Missing body size enforcement middleware**. While schemas validate model constraints, the ASGI server lacked a streaming body size limit to reject oversized payloads before buffering.
  - **Risk**: `MEDIUM`
  - **Remediation**: Add a RequestSizeLimiter middleware (default max 2 MB body) and validate string lengths on all inputs.

### 1.13 Docker & Containerization Status
- **Current State**: `docker-compose.yml` existed for development services (Redis, Jaeger). No production `Dockerfile` or `docker-compose.prod.yml` existed.
- **Host Finding SEC-14**: **Docker Desktop Engine unavailable on local host**. Docker CLI v29.6.1 is installed on host, but the Docker daemon (`dockerDesktopLinuxEngine`) is currently stopped and requires elevated permissions to start.
  - **Risk**: `MEDIUM` (Deployment verification dependency)
  - **Remediation**: Construct hardened multi-stage `Dockerfile` (non-root user `casefile`, unprivileged port, healthcheck) and `docker-compose.prod.yml`. Accurately document host daemon status per guidelines.

### 1.14 Dependency Tree & pip-audit
- **Current State**: `poetry.lock` tracks 121 virtual environment dependencies.
- **Finding SEC-15**: **pip-audit detected known CVEs in transitive dependencies**.
  - `aiohttp` 3.12.14: Multiple CVEs in HTTP parser/redirect handling.
  - `black` 24.10.0 (dev tool): CVE-2026-32274 cache collision.
  - `pyjwt` 2.14.0: CVE-2026-101918 recursive payload parsing.
  - `pytest` 8.4.2 (dev tool): CVE-2025-71176 `/tmp` predictable pattern on UNIX.
  - `langgraph` / `langgraph-checkpoint`: CVEs related to unvalidated checkpoint deserialization when using untrusted remote msgpack/JSON.
  - **Risk**: `HIGH` (Transitive dependencies)
  - **Remediation**: Document findings and mitigations. Do not arbitrarily break locked dependencies without testing. In CASEFILE, checkpoints are serialized/deserialized only via internal trusted SQLite tables without external deserialization sinks.

### 1.15 Secrets Management & `.gitignore`
- **Current State**: `.gitignore` ignores `.env`, `.env.local`, `*.db`, `*.sqlite`, `secrets/`, `__pycache__/`, `dist/`. `.env.example` provides template variables.
- **Finding SEC-16**: No secrets exist in source code, committed files, or documentation.
  - **Risk**: `LOW`
  - **Remediation**: Maintain automated secret scanning test in `tests/security/test_secrets.py`.

---

## 2. Summary Matrix

| Finding ID | Area | Severity | Status / Target |
|------------|------|----------|-----------------|
| SEC-01 | API Authentication (Role spoofing via headers) | `CRITICAL` | Fix in `src/casefile/api/auth.py` |
| SEC-11 | API CORS (`allow_origins=["*"]` with credentials) | `CRITICAL` | Fix in `src/casefile/api/app.py` |
| SEC-02 | Dev Tokens in Production | `HIGH` | Gated by `environment` mode |
| SEC-08 | Missing Production Configuration | `HIGH` | Create `config/production.yaml` |
| SEC-12 | Missing HTTP Security Headers | `HIGH` | Implement SecurityHeadersMiddleware |
| SEC-15 | Dependency CVEs (aiohttp, pyjwt, langgraph) | `HIGH` | Documented & verified mitigations |
| SEC-03 | Request/Query Parameter Validation Bounds | `MEDIUM` | Hardened in schemas |
| SEC-06 | Idempotency Key Validation & Length Limits | `MEDIUM` | Hardened in `idempotency.py` |
| SEC-07 | Process-Local Rate Limiting | `MEDIUM` | Documented limitation |
| SEC-13 | Missing Max Request Body Size Limit | `MEDIUM` | Implement RequestSizeLimitMiddleware |
| SEC-14 | Docker Daemon Inactive on Host | `MEDIUM` | Manifests created; report accurately |
| SEC-04 | Decision Reason Field Max Length | `LOW` | Schema validation |
| SEC-05 | Replay Isolation | `LOW` | Preserved and verified |
| SEC-09 | Database URL / Secret Masking | `LOW` | Verified in config & logging |
| SEC-10 | Telemetry Collector Fallback | `LOW` | Verified fail-safe |
| SEC-16 | Repository Secret Sanitation | `LOW` | Clean; verified via test suite |
