# CASEFILE REST API Specification (Phase 7)

## Overview

The CASEFILE REST API provides an observable, operator-facing application boundary for the Multi-Agent Insurance Claim Orchestration Platform. It exposes claim submission, state machine inspection, agent/tool execution visibility, durable checkpoint inspection, safe deterministic simulation replay, human-in-the-loop approval decisioning, and evaluation benchmark results.

All business endpoints are versioned under the `/api/v1` namespace. Health and readiness probes are exposed as unversioned root probes for orchestration infrastructure.

---

## API Contract Table

| Method | Endpoint | Authorized Roles | Purpose | Idempotent | Rate Limit |
|---|---|---|---|---|---|
| `GET` | `/health` | Public | Application liveness probe | N/A | None |
| `GET` | `/ready` | Public | Dependency readiness probe (DB, storage) | N/A | None |
| `POST` | `/api/v1/claims` | `OPERATOR`, `ADMIN` | Submit and execute claim workflow | Yes (`Idempotency-Key`) | 60/min |
| `GET` | `/api/v1/workflows` | `VIEWER` and above | List workflow summaries with state filter | N/A | None |
| `GET` | `/api/v1/workflows/{id}` | `VIEWER` and above | Retrieve sanitized operational status | N/A | None |
| `GET` | `/api/v1/workflows/{id}/history` | `VIEWER` and above | Append-only state transition audit trail | N/A | None |
| `GET` | `/api/v1/workflows/{id}/agents` | `OPERATOR`, `ADMIN` | Specialist agent execution telemetry | N/A | None |
| `GET` | `/api/v1/workflows/{id}/tools` | `OPERATOR`, `ADMIN` | Sanitized external tool invocation log | N/A | None |
| `GET` | `/api/v1/workflows/{id}/checkpoints`| `OPERATOR`, `ADMIN` | Durable checkpoint metadata and hashes | N/A | None |
| `POST` | `/api/v1/workflows/{id}/replay` | `OPERATOR`, `ADMIN` | Trigger safe deterministic simulation replay | Yes (`Idempotency-Key`) | 10/min |
| `GET` | `/api/v1/approvals` | `CLAIM_REVIEWER` and above | Human-in-the-loop approval queue | N/A | None |
| `GET` | `/api/v1/approvals/{id}` | `CLAIM_REVIEWER` and above | Retrieve specific approval item details | N/A | None |
| `POST` | `/api/v1/approvals/{id}/decision` | `CLAIM_REVIEWER` and above | Submit authoritative adjudication decision | Yes (`Idempotency-Key`) | 30/min |
| `GET` | `/api/v1/evaluations` | `VIEWER` and above | Phase 6 benchmark results & 22 metrics | N/A | None |
| `GET` | `/api/v1/evaluations/cases` | `VIEWER` and above | List all 30 evaluation scenarios | N/A | None |
| `GET` | `/api/v1/evaluations/cases/{id}`| `VIEWER` and above | Specific evaluation scenario details | N/A | None |
| `GET` | `/api/v1/audit` | `ADMIN` | Chronological immutable system audit events | N/A | None |
| `GET` | `/api/v1/metrics` | `VIEWER` and above | Real-time console operational KPIs | N/A | None |

---

## Idempotency & Concurrency Control

Mutation endpoints (`POST /api/v1/claims`, `POST /api/v1/approvals/{id}/decision`, `POST /api/v1/workflows/{id}/replay`) support the standard `Idempotency-Key` header:
- **Identical Key + Identical Payload**: Returns the cached response immediately without re-executing the operation.
- **Identical Key + Conflicting Payload**: Returns structured `HTTP 409 Conflict` (`IDEMPOTENCY_CONFLICT`).

---

## Rate Limiting Abstraction

The API implements a sliding-window rate limiter (`RateLimiter` protocol / `InMemoryRateLimiter`).
- Keyed by client IP or authenticated `principal_id` + HTTP method + route path.
- When exhausted, returns `HTTP 429 Too Many Requests` with a standard `Retry-After` header.
- Rejection occurs before domain service invocation and never corrupts state machine or storage records.

---

## Uniform Error Response Format

All API errors return a strict JSON payload without stack traces or leaked secrets:

```json
{
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Principal with role 'VIEWER' is not authorized for this operation",
    "request_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "correlation_id": "c1f2b3a4-d5e6-4f7a-8b9c-0d1e2f3a4b5c",
    "details": {}
  }
}
```
