# CASEFILE Threat Model (Phase 8)

**Document Version**: 1.0.0  
**Phase**: Phase 8 (Security + Deployment)  
**Target System**: CASEFILE Multi-Agent Insurance Claim Orchestration Platform  
**Date**: 2026-10-05  

---

## 1. System Overview & Architecture

CASEFILE orchestrates insurance claim intake, document extraction, evidence gathering, human oversight, and adjudication recommendation through a supervisor-worker multi-agent system. The platform consists of:

1. **REST API Gateway**: FastAPI ASGI service with authentication, RBAC, input validation, and observability middleware.
2. **Multi-Agent Orchestrator**: LangGraph/deterministic graph driving Extractor, Investigator, and Reviewer specialist agents.
3. **Tool Execution Engine**: Registry with agent-scoped authorization, rate limiting, and cached mock/external integrations.
4. **Human-in-the-Loop (HITL) Service**: Enforces separation of duties, role thresholds, and approval deadlines.
5. **Safe Replay Engine**: Checkpoint-based simulation isolating offline evaluations from production side effects.
6. **Persistence Layer**: Relational store (SQLite / PostgreSQL) for workflow runs, state transitions, approvals, and audit trails.
7. **Operations Console**: React / Vite SPA for operations, telemetry, and manual reviews.

---

## 2. Protected Assets

| Asset ID | Asset Name | Description & Sensitivity | Impact of Compromise |
|---|---|---|---|
| **A-1** | **Claim Data** | Claimant PII, incident date, damage narratives, requested amounts | Privacy breach, regulatory fines (GDPR/HIPAA/state insurance laws) |
| **A-2** | **Approval Decisions** | Human verdict, monetary payouts, reviewer notes | Fraudulent payouts, loss of reserves, regulatory sanctions |
| **A-3** | **Audit Trail** | Append-only security events, actor attribution, transition logs | Loss of non-repudiation, compliance failure, evidence tampering |
| **A-4** | **Workflow State & Checkpoints** | Execution graph snapshots, memory state, ledger entries | State corruption, replay attacks, duplicate claim processing |
| **A-5** | **Credentials & Secrets** | API keys, database credentials, JWT secrets, telemetry tokens | Total system compromise, lateral movement, unauthorized LLM usage |
| **A-6** | **Telemetry & Spans** | Distributed traces, metrics, system error logs | Data exfiltration via trace attributes or logs |

---

## 3. Threat Actors & Capabilities

| Actor ID | Actor Type | Capability & Motivation | Trust Level |
|---|---|---|---|
| **TA-1** | **Unauthenticated External Attacker** | Internet-based, automated vulnerability scanners, brute-force | Untrusted (Boundary: Public Internet) |
| **TA-2** | **Authenticated Viewer** | Internal read-only user attempting privilege escalation or data scraping | Low Trust (Authenticated, Read-Only) |
| **TA-3** | **Operator / Reviewer** | Authorized claims personnel attempting self-approval or unapproved replays | Medium Trust (Role-Scoped) |
| **TA-4** | **Malicious Insider / Admin** | Privileged administrator attempting to forge records or wipe audit logs | High Trust (Monitored & Audited) |
| **TA-5** | **Compromised LLM / Specialist Agent** | LLM hallucinating out-of-band actions or executing unauthorized tools | Untrusted Component |
| **TA-6** | **Malicious Document / Indirect Injection** | Claim submission containing prompt injection instructions in PDF/text | Untrusted Data |
| **TA-7** | **Compromised Tool / Data Source** | External vehicle history or fraud database returning poisoned data | Semi-Trusted External Source |

---

## 4. Trust Boundaries

```
[ Operations Console (Browser) ]
              │  (HTTPS / TLS)
══════════════╪═════════════════════════════════════════════════════════  Trust Boundary 1: Ingress (WAF / Gateway)
              ▼
[ FastAPI Gateway (Auth, RBAC, Middleware, Sanitization, Request Limits) ]
              │  (In-Process)
══════════════╪═════════════════════════════════════════════════════════  Trust Boundary 2: Domain Boundary
              ▼
[ Multi-Agent Orchestrator ] ──(Untrusted Prompt Boundary)──► [ LLM Providers ]
        │             │
        │             └──► [ Authorized Tool Registry ] ──► [ External Data Sources ]
        ▼
[ Persistence & State Store ] ──► [ Relational Database (Encrypted) ]
        │
        └──► [ Append-Only Audit Trail ]
```

---

## 5. Threat Analysis (STRIDE)

### 5.1 Spoofing
- **T-S1**: **Header-Based Principal Spoofing**. An attacker sends `X-Principal-Role: ADMIN` to bypass authentication.
  - *Mitigation*: In `production` environment, `DevAuthProvider` is explicitly forbidden. `ProductionAuthProvider` strictly rejects header-based role assignments and dev tokens.
  - *Residual Risk*: Low.

- **T-S2**: **Approval Actor Spoofing**. A reviewer passes someone else's ID in the JSON body of an approval decision.
  - *Mitigation*: The decision API completely ignores body-supplied actor IDs and extracts actor identity directly from the cryptographically verified `Principal` object.
  - *Residual Risk*: Negligible.

### 5.2 Tampering
- **T-T1**: **Original Workflow Mutation via Replay**. An operator triggers a replay simulation that overwrites completed workflow state.
  - *Mitigation*: Replay is strictly isolated using `SafeReplayEngine`. A distinct simulation run ID is generated; original database records are treated as read-only. Live tools and payment webhooks are barred during replay.
  - *Residual Risk*: Negligible.

- **T-T2**: **Audit Log Injection & Newline Manipulation**. An attacker inputs newline characters `\r\n` into claim descriptions or claimant names to forge audit log entries.
  - *Mitigation*: `sanitize_log_message` strips all CR and LF characters and masks sensitive tokens before logging.
  - *Residual Risk*: Low.

### 5.3 Repudiation
- **T-R1**: **Denial of Claim Decisions**. A reviewer denies approving an illegitimate claim payout.
  - *Mitigation*: All approval decisions record actor ID, timestamp, verdict, reason, and role in an append-only database record accompanied by an audit event.
  - *Residual Risk*: Low.

### 5.4 Information Disclosure
- **T-I1**: **PII / Secret Leakage via Observability**. Claimant details or API keys leak into OpenTelemetry spans, console logs, or HTTP headers.
  - *Mitigation*: `sanitize_attributes` and `ObservabilityMiddleware` drop `Authorization`, cookies, prompts, and passwords. Credit card numbers, SSNs, and email patterns are regex-masked.
  - *Residual Risk*: Low.

- **T-I2**: **Database Credentials Exposure in Configuration or Logs**. Database connection string containing password printed during startup.
  - *Mitigation*: `mask_secret` regex masks database passwords (`postgresql://user:***@host:port/db`) in configuration models and logging.
  - *Residual Risk*: Low.

### 5.5 Denial of Service (DoS)
- **T-D1**: **Oversized Request Payloads**. Attacker floods API with massive JSON bodies or multipart files.
  - *Mitigation*: `RequestSizeLimitMiddleware` terminates any request exceeding 2MB immediately with HTTP 413 before parsing. String fields have explicit Pydantic bounds (description ≤ 10,000 chars, reason ≤ 2,000 chars).
  - *Residual Risk*: Low.

- **T-D2**: **Endpoint Flooding / Resource Starvation**. Rapidly submitting claims or triggering compute-heavy replay simulations.
  - *Mitigation*: Sliding-window rate limiter enforces per-principal and per-IP throttles (60 req/min for claims, 30 req/min for replay).
  - *Residual Risk*: Medium (in-memory rate limiter is not distributed across multiple horizontal instances).

### 5.6 Elevation of Privilege
- **T-E1**: **Reviewer Self-Approval**. A claims reviewer submits a claim and approves it themselves.
  - *Mitigation*: Domain engine verifies `claimant_id != actor_id` and raises `SELF_APPROVAL_PROHIBITED` (HTTP 409/403).
  - *Residual Risk*: Negligible.

- **T-E2**: **Agent Tool Authorization Escape**. An agent executes unauthorized tools (e.g. Extractor calling fraud lookup).
  - *Mitigation*: Dual enforcement: agent-level allowed tools whitelist + `ToolRegistry` authorization matrix verification before tool dispatch.
  - *Residual Risk*: Negligible.

---

## 6. Residual Risk Summary

1. **Horizontal Distributed Rate Limiting**: The current sliding-window rate limiter is in-memory. In a multi-replica deployment, an external Redis-backed rate limiter is recommended for unified cluster quota enforcement.
2. **Third-Party Dependency Vulnerabilities**: Known advisories in transitive packages (e.g., `aiohttp`, `black`, `pyjwt`) require ongoing maintenance and version pinning as upstream patches stabilize.
