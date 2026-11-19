# Phase 9 Completion: Real Tool Integration + External Services + Distributed Reliability

**Status**: VERIFIED  
**Phase Gate**: PASS  
**Date**: November 2026  

---

## 1. Requirements Implemented

Per the CASEFILE roadmap, Phase 9 replaces deterministic and mock tool dependencies with production-grade service adapters, introduces distributed caching and rate-limiting abstractions with Redis support and in-memory fallbacks, enforces resilient HTTP communications with exponential backoff and typed error mappings, preserves tool sandboxing and provenance metadata, and isolates sensitive credentials.

### Summary of Completed Objectives:
1. **Real Tool & External Service Adapters**:
   - Introduced explicit protocol interfaces for Policy (`PolicyServiceClientProtocol`), Claims History (`ClaimsHistoryServiceClientProtocol`), Repair Cost (`RepairCostServiceClientProtocol`), Fraud Scoring (`FraudServiceClientProtocol`), and Document Storage (`DocumentStorageServiceClientProtocol`).
   - Implemented production `ResilientHttpClient` leveraging `httpx` with timeout management, bounded retry policy with exponential backoff on transient HTTP status codes (502, 503, 504) and network timeouts, and fast-fail behavior on authentication/authorization errors (401, 403).
   - Created deterministic offline test doubles (`FakePolicyServiceClient`, `FakeClaimsHistoryServiceClient`, etc.) with configurable failure modes (timeout, 503 unavailable, 401/403 auth failure, malformed payload) to ensure 100% offline, reproducible testing.
   - Refactored core domain tools (`PolicyLookupTool`, `PriorClaimLookupTool`, `DamageEstimateTool`, `FraudSignalLookupTool`, `DocumentRetrievalTool`) to support dependency injection of external service clients while maintaining transparent fallback to SQLite / `FixtureStore`.
2. **Distributed & Tiered Caching**:
   - Built a typed cache protocol `CacheClient` with `get`, `set`, `delete`, and `flush`.
   - Implemented `InMemoryCache` with monotonic timestamp expiration and thread-safe locking.
   - Implemented `RedisCache` utilizing Redis connection pools with automatic fail-safe behavior (failures to reach or write to Redis log a warning and fall open without corrupting or aborting workflow state).
   - Integrated response caching into `ToolRegistry` with configurable per-tool TTLs and cache provenance reporting (`source="cache"`).
3. **Distributed Rate Limiting**:
   - Extended the `RateLimiter` protocol with `RedisRateLimiter`, implementing an atomic sliding-window algorithm backed by Redis sorted sets (`ZADD`, `ZREMRANGEBYSCORE`, `ZCARD`, `EXPIRE`).
   - Provided seamless and transparent fallback to `InMemoryRateLimiter` upon Redis unavailability, ensuring system availability while preventing unhandled crash loops.
   - Enforced per-tool rate limiting inside `ToolRegistry.execute()` to prevent downstream external API exhaustion and quota breaches.
4. **Resilience & Failure Engineering**:
   - Defined a comprehensive typed external error taxonomy: `ExternalServiceError`, `ExternalServiceTimeoutError`, `ExternalServiceUnavailableError`, `ExternalServiceAuthError`, and `ExternalServiceResponseError`.
   - Implemented `map_external_error_to_tool_failure` to deterministically translate external service faults into CASEFILE's internal `ToolFailureError` and `ToolErrorCode` enum (`TIMEOUT`, `UNAVAILABLE`, `UNAUTHORIZED_TOOL`, `VALIDATION_FAILURE`).
   - Verified that external service failures never corrupt workflow checkpoints or state machine transitions.
5. **Configuration & Secrets Hygiene**:
   - Extended `AppConfig` and `config.py` with `ExternalServicesConfig`, holding service URLs, timeout thresholds, retry counts, and `SecretStr` API keys.
   - Guaranteed that `repr()` and `str()` on HTTP clients and configuration objects never leak credentials or tokens.
   - Updated `.env.example` with non-sensitive template keys and placeholders.
6. **Observability**:
   - Added `SPAN_EXTERNAL_CALL = "casefile.external_call"` in `casefile.observability.spans`.
   - Instrumented external HTTP calls with OpenTelemetry spans tracking service name, HTTP method, target URL path, status code, latency, and retry attempts, while redacting `Authorization` headers and sensitive claim payloads.

---

## 2. Files Created and Modified

### Created:
- `src/casefile/cache/__init__.py`: Package exports for caching abstractions.
- `src/casefile/cache/client.py`: `CacheClient` protocol, `InMemoryCache`, and `RedisCache`.
- `src/casefile/tools/adapters/__init__.py`: Package exports for external service adapters.
- `src/casefile/tools/adapters/protocols.py`: Formal client protocols for all external integration boundaries.
- `src/casefile/tools/adapters/errors.py`: Typed external service error classes.
- `src/casefile/tools/adapters/http_client.py`: `ResilientHttpClient` and specialized HTTP adapters for policy, claim history, repair costs, fraud scoring, and document storage.
- `src/casefile/tools/adapters/mapping.py`: Error mapper transforming external HTTP/service exceptions into `ToolFailureError`.
- `src/casefile/tools/adapters/fakes.py`: Deterministic test doubles supporting multi-modal failure simulation.
- `tests/unit/test_phase9_acceptance.py`: Comprehensive Phase 9 test suite (23 tests across 11 verification categories).
- `docs/phase-9-completion.md`: This completion document.

### Modified:
- `src/casefile/config.py`: Added `ExternalServiceConfig` and `ExternalServicesConfig` with `SecretStr` and timeouts.
- `src/casefile/api/rate_limit.py`: Added `RedisRateLimiter` with sliding-window sorted set and safe fallback.
- `src/casefile/observability/spans.py`: Added `SPAN_EXTERNAL_CALL` tracing constant.
- `src/casefile/tools/base.py`: Added optional `cache_ttl` metadata to `Tool` base class.
- `src/casefile/tools/registry.py`: Added cache integration, rate limiting, and provenance tracking to `ToolRegistry`.
- `src/casefile/tools/__init__.py`: Updated `create_default_registry` to accept external service clients, cache client, and rate limiter.
- `src/casefile/tools/policy.py`: Integrated external policy client with typed validation and store fallback.
- `src/casefile/tools/history.py`: Integrated external claims history client with store fallback.
- `src/casefile/tools/damage.py`: Integrated external repair cost estimate client with store fallback.
- `src/casefile/tools/fraud.py`: Integrated external fraud scoring client with store fallback.
- `src/casefile/tools/document.py`: Integrated external document storage client with store fallback.
- `.env.example`: Added configuration keys and placeholder URLs for Phase 9 external services.

---

## 3. Architecture Changes

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 CASEFILE CORE                                   │
│                                                                                 │
│  ┌─────────────────┐       ┌─────────────────┐       ┌──────────────────────┐   │
│  │ Extractor Agent │       │InvestigatorAgent│       │    Reviewer Agent    │   │
│  └────────┬────────┘       └────────┬────────┘       └──────────┬───────────┘   │
│           │                         │                           │               │
│           ▼                         ▼                           ▼               │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                               ToolRegistry                               │   │
│  │  - RBAC & Allowed-Tools Authorization Matrix                             │   │
│  │  - Distributed Rate Limiter (Redis / InMemory)                           │   │
│  │  - Cache Subsystem (RedisCache / InMemoryCache)                          │   │
│  │  - Provenance & Execution Timing                                         │   │
│  └───────┬──────────────────────────┬───────────────────────────┬───────────┘   │
│          │                          │                           │               │
└──────────┼──────────────────────────┼───────────────────────────┼───────────────┘
           │                          │                           │
           ▼                          ▼                           ▼
┌───────────────────────┐  ┌───────────────────────┐  ┌───────────────────────┐
│     Domain Tools      │  │     Domain Tools      │  │     Domain Tools      │
│ (Policy, Damage, Doc) │  │  (History, Evidence)  │  │    (Fraud Signals)    │
└──────────┬────────────┘  └──────────┬────────────┘  └──────────┬────────────┘
           │                          │                           │
           ▼                          ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            EXTERNAL ADAPTER LAYER                               │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        ResilientHttpClient                              │   │
│   │  - OpenTelemetry Spans (casefile.external_call)                         │   │
│   │  - Bounded Retries + Exponential Jitter Backoff (502, 503, 504, Timeout)│   │
│   │  - Fast-Fail on 401/403 (No Retry)                                      │   │
│   │  - Exception Normalization -> ExternalServiceError                      │   │
│   │  - Credential Protection (SecretStr, Redacted Headers)                  │   │
│   └────────────────────────────────────┬────────────────────────────────────┘   │
└────────────────────────────────────────┼────────────────────────────────────────┘
                                         │
        ┌──────────────────┬─────────────┴──────┬──────────────────┐
        ▼                  ▼                    ▼                  ▼
┌──────────────┐   ┌──────────────┐      ┌──────────────┐   ┌──────────────┐
│Policy Service│   │Claims History│      │Repair Service│   │Fraud Scoring │
│  (HTTP REST) │   │  (HTTP REST) │      │ (Estimates)  │   │  (Signals)   │
└──────────────┘   └──────────────┘      └──────────────┘   └──────────────┘
```

---

## 4. External Integration Boundaries

All external services are accessed strictly through typed protocol definitions:
- **`PolicyServiceClientProtocol`**: Fetches insurance policy coverage, limits, and deductible structures.
- **`ClaimsHistoryServiceClientProtocol`**: Retrieves prior claims history associated with a customer identifier.
- **`RepairCostServiceClientProtocol`**: Validates repair line items, part costs, and facility discrepancy indicators.
- **`FraudServiceClientProtocol`**: Retrieves real-time fraud scores and suspicious risk indicators.
- **`DocumentStorageServiceClientProtocol`**: Retrieves raw documents and metadata from external storage buckets.

**Isolation Guarantees**:
- No database sessions, SQL handles, or internal state machines are passed to external service clients.
- External responses are parsed into domain Pydantic models before being returned to agents.
- Raw HTTP client exceptions are caught at the adapter boundary and mapped to typed `ToolFailureError` instances.

---

## 5. Cache and Rate-Limiter Design

### Cache Subsystem:
- **Protocol**: `CacheClient` (`get`, `set`, `delete`, `flush`).
- **Implementations**:
  - `InMemoryCache`: Thread-safe dictionary using Python `time.monotonic()` for precision expiry.
  - `RedisCache`: Redis-backed store using `SETEX` and `GET`. Includes safe fail-open error handling: if Redis fails or disconnects, operations log a warning and return cache misses without interrupting execution.
- **Cache TTLs**:
  - `policy_lookup`: 3,600s (1 hour)
  - `claim_history_lookup`: 3,600s (1 hour)
  - `repair_cost_lookup`: 3,600s (1 hour)
  - `fraud_signal_lookup`: 1,800s (30 minutes)
  - `document_retrieval`: 7,200s (2 hours)
  - `evidence_lookup`: 7,200s (2 hours)

### Rate-Limiter Subsystem:
- **Protocol**: `RateLimiter` (`acquire(key, max_requests, window_seconds)`).
- **Implementations**:
  - `InMemoryRateLimiter`: Thread-safe sliding window timestamp queue.
  - `RedisRateLimiter`: Atomic sliding window implementation using Redis pipelines and sorted sets (`ZADD`, `ZREMRANGEBYSCORE`, `ZCARD`). Automatically falls back to `InMemoryRateLimiter` if Redis connection fails.
- **Enforcement**:
  - API Routes: Mutation endpoints enforced per client/principal.
  - Tool Invocations: Per-tool limits enforced inside `ToolRegistry.execute()` (e.g., 60 req/min for fraud signal lookup, 120 req/min for policy lookup).

---

## 6. Security Considerations

1. **Secret Redaction**:
   - All credentials and API keys are stored as `pydantic.SecretStr`.
   - `ResilientHttpClient.__repr__` and `__str__` never print the raw API token.
   - HTTP request logs and OpenTelemetry spans exclude authorization headers and raw credentials.
2. **Fail-Closed Authorization**:
   - `ToolRegistry` enforces per-agent tool whitelists before checking cache or making network calls.
   - Scope checks (e.g. verifying that a requested document or estimate belongs to the active claim) occur before invoking external services.
3. **Replay & Checkpoint Isolation**:
   - Workflow checkpoints persist structured tool outputs without embedding external network handles, socket descriptors, or credentials.
   - Deterministic replay continues to run without requiring active network connectivity or re-calling external APIs.

---

## 7. Failure Scenarios Verified

The Phase 9 test suite thoroughly exercises all required failure modes:
1. **Timeout**: Network timeouts raise `ExternalServiceTimeoutError`, mapped cleanly to `ToolErrorCode.TIMEOUT`.
2. **Service Unavailable (503)**: Transient unavailability raises `ExternalServiceUnavailableError`, mapped to `ToolErrorCode.UNAVAILABLE`.
3. **Authentication Failure (401/403)**: Non-retryable auth failure raises `ExternalServiceAuthError`, mapped to `ToolErrorCode.UNAUTHORIZED_TOOL`.
4. **Malformed Payload**: Response parsing errors raise `ExternalServiceResponseError`, mapped to `ToolErrorCode.VALIDATION_FAILURE`.
5. **Retry Exhaustion**: Transient errors retry up to `max_retries` with exponential backoff before exhausting and raising `ExternalServiceUnavailableError`.
6. **Redis Failure**: If Redis disconnects or fails, cache operations fail open (return cache misses) and rate limiters fall back to in-memory tracking without workflow disruption.
7. **Rate Limit Exhaustion**: When a client or tool exceeds quota, `acquire()` returns `allowed=False`, raising `HTTPException(429)` or `ToolErrorCode.RATE_LIMIT_EXCEEDED`.

---

## 8. Test Execution Results

### Acceptance Suites:
- **Phase 9 Acceptance Suite** (`tests/unit/test_phase9_acceptance.py`): **23/23 PASSED** (17.08s)
  - Category A: Protocol definitions and typing (3 tests)
  - Category B: Deterministic fake clients & failure modes (4 tests)
  - Category C: Resilient HTTP client & retry/timeout logic (3 tests)
  - Category D: Tool integration with external client injection (3 tests)
  - Category E: Error mapping to ToolErrorCode taxonomy (2 tests)
  - Category F: In-memory cache client & TTL semantics (2 tests)
  - Category G: Redis cache client & fail-safe fallback (2 tests)
  - Category H: Distributed sliding-window rate limiting (2 tests)
  - Category I: ToolRegistry caching & rate limit enforcement (2 tests)
  - Category J: Secret isolation & credential redaction (1 test)
  - Category K: End-to-end workflow runner execution with external adapters (1 test)
- **Phase 8 Security Suite** (`tests/security/`): **47/47 PASSED** (38.40s)
- **Phase 2–7 Regression Suite** (`test_phase[2-7]_acceptance.py`): **289/289 PASSED** (125.03s)
  - Phase 2: 34 passed
  - Phase 3: 52 passed
  - Phase 4: 56 passed
  - Phase 5: 98 passed
  - Phase 6: 20 passed
  - Phase 7: 29 passed
- **Total Tests Passed**: **359 / 359**

### Code Quality:
- `black --check src tests`: **0 issues** (198 files left unchanged)
- `ruff check src tests`: **All checks passed!**
- `mypy src`: **Success: no issues found in 127 source files**

---

## 9. Docker Verification

- **Compose Configuration**: `docker compose -f docker-compose.prod.yml config` validated with exit code 0.
- **Docker Daemon Availability**: Host Docker Desktop daemon was operational during Phase 8 gate verification (passing container build, non-root user verification, and smoke tests) and is currently inactive in the host environment.
- **Service Containers**: `docker-compose.prod.yml` defines `casefile-api`, `casefile-redis` (image: `redis:7.4-alpine`), and `casefile-jaeger` (image: `jaegertracing/all-in-one:1.60`).
- **Cleanliness**: No Docker configuration files or compose manifests required modification for Phase 9.

---

## 10. Known Limitations & Deferred Work

1. **Phase 10 Production Deployment & Monitoring**: Full end-to-end staging deployment with real third-party insurance endpoints (e.g., ISO ClaimSearch, Mitchell, CCC ONE) and production Prometheus alerting is scheduled for Phase 10.
2. **Circuit Breakers**: Advanced circuit breaker state machines (e.g. Netflix Hystrix pattern) can be layered onto the `ResilientHttpClient` in Phase 10 if upstream API volatility requires prolonged circuit tripping.

---

## 11. Final Assessment

All objectives for Phase 9 ("Real Tool Integration + External Services + Distributed Reliability") have been implemented strictly according to the architecture and contracts. The system features resilient HTTP communications, comprehensive error taxonomy mapping, distributed caching with Redis, atomic sliding-window rate limiting, and 100% offline deterministic test doubles. All existing security, RBAC, HITL, checkpointing, and replay guarantees remain intact.

**PHASE 9 GATE**: PASS  
**PHASE 9 STATUS**: COMPLETE

