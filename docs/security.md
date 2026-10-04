# CASEFILE Security Architecture & Policy (Phase 8)

**Document Version**: 1.0.0  
**Phase**: Phase 8 (Security + Deployment)  
**Date**: 2026-10-05  

---

## 1. Secrets Management Policy

1. **Zero Secret Ingestion in Source**:
   - No passwords, private keys, authentication tokens, or cloud secrets are checked into Git.
   - All secret configurations are loaded via environment variables (`CASEFILE_*`) or secure secret mounts.
   - [config.py](file:///E:/Projects/CASEFILE/src/casefile/config.py) automatically masks database URLs, API keys, and sensitive tokens in `model_dump()`, string representations, and error logs.
2. **Ignored Files**:
   - `.env`, `.env.local`, `.env.*.local`, `*.pem`, `*.key` are enforced in [.gitignore](file:///E:/Projects/CASEFILE/.gitignore).
   - [.env.example](file:///E:/Projects/CASEFILE/.env.example) is maintained with non-sensitive placeholder variables only.

---

## 2. Authentication & Authorization

### 2.1 Principal Model
Every authenticated request is mapped to a verified `Principal`:
- `principal_id`: Unique subject identifier.
- `role`: One of `VIEWER`, `OPERATOR`, `CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`.
- `auth_type`: `bearer_token`, `api_key`, `jwt`, `system_internal`.

### 2.2 Environment Isolation
- In `development` and `test`, `DevAuthProvider` allows developer ergonomics via static tokens (`dev-admin-token`, etc.).
- In `production`, `DevAuthProvider` raises `RuntimeError("DevAuthProvider is strictly forbidden in production mode")`.
- `ProductionAuthProvider` strictly rejects any unauthenticated access, rejects mock dev tokens, and prohibits client headers from dictating or elevating roles.

### 2.3 RBAC Matrix
| Role | Read Workflows | Submit Claims | Decide Approvals | Run Replay | Admin Operations |
|---|:---:|:---:|:---:|:---:|:---:|
| `VIEWER` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `OPERATOR` | ✅ | ✅ | ❌ | ✅ | ❌ |
| `CLAIM_REVIEWER` | ✅ | ❌ | ✅ | ❌ | ❌ |
| `SENIOR_REVIEWER` | ✅ | ❌ | ✅ (High-Value) | ❌ | ❌ |
| `CLAIM_SUPERVISOR`| ✅ | ✅ | ✅ | ✅ | ❌ |
| `ADMIN` | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 3. Human-in-the-Loop (HITL) Security

1. **Agent Exclusion**: Only authenticated human principals (`CLAIM_REVIEWER`, `SENIOR_REVIEWER`, `CLAIM_SUPERVISOR`, `ADMIN`) can submit verdicts. Autonomous agents cannot access the decision API.
2. **Separation of Duties**: The claimant/requester cannot approve their own claim (`SELF_APPROVAL_PROHIBITED`).
3. **State Immutability**: Once an approval item reaches `APPROVED` or `REJECTED`, subsequent attempts to decide it raise `APPROVAL_ALREADY_DECIDED` (HTTP 409).
4. **Deadline Enforcement**: Approvals past their expiration timestamp cannot be decided (`APPROVAL_EXPIRED`).

---

## 4. Replay Security & Simulation Isolation

Replay allows operators to test new logic or investigate historical claims safely:
1. **State Isolation**: A newly allocated UUID (`replay_id`) is created for each simulation. The original workflow run record, checkpoints, and approval entries are completely unmodified.
2. **No Side Effects**: Live external tools (payments, claimant notifications, insurer integrations) are blocked during replay runs.
3. **Access Control**: Replay is restricted to `OPERATOR`, `CLAIM_SUPERVISOR`, and `ADMIN`.

---

## 5. Network & HTTP Hardening

1. **Explicit CORS Allowlist**:
   - Production requires an explicit origin allowlist (e.g. `https://console.casefile.internal`).
   - Wildcard `allow_origins=["*"]` with `allow_credentials=True` is prohibited.
2. **Security Headers**:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
   - `Content-Security-Policy: default-src 'self'; ...`
3. **Request Body Bounds**:
   - `RequestSizeLimitMiddleware` terminates requests > 2MB with HTTP 413.
   - Pydantic models validate input strings, dates, and decimals before domain dispatch.

---

## 6. Dependency Audit & Accepted Risks

Run via `pip-audit` across 121 dependencies. 9 packages contain CVE advisories:

| Package | Advisory / CVE | Severity | Analysis & Accepted Risk Rationale |
|---|---|:---:|---|
| `aiohttp` | CVE-2024-52303, CVE-2024-52304 | Moderate | Used transitively by langchain providers; HTTP client calls in CASEFILE use trusted internal endpoints. |
| `black` | CVE-2024-21503 | Medium | Dev/linting tool only; not packaged or executed in the production container image. |
| `langchain-core` / `langchain-anthropic` | GHSA-pjh7-8977-w57c | Low/Mod | Transitive dependency for provider adapters. Prompts and tool parameters are quarantined and validated. |
| `langgraph` / `langgraph-checkpoint` | CVE-2024-28184 | Low | Checkpointing engine. Deserialization operates over internal SQLite database, not untrusted remote inputs. |
| `pyjwt` | CVE-2022-29217 | High | Key confusion advisory if using asymmetric algorithms without algorithm restrictions. Fixed in upstream versions. |
| `pytest` | CVE-2020-29651 | Low | Test framework only; not shipped to production runtime. |

All accepted risks are tracked for patch upgrades in scheduled release cycles.
