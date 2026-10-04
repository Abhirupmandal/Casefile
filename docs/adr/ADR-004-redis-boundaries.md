# ADR-004: Redis for Caching and Rate Limiting

> **Amendment (2026-09-22)**: Per [ADR-011](ADR-011-sqlite-local-persistence.md),
> every "PostgreSQL" below now reads "SQLite". Redis remains ephemeral
> coordination/cache only — it is not the system of record.

**Status**: Accepted (amended by ADR-011)
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `architecture.md`, `tool-architecture.md`, `budget-control.md`

## Context

CASEFILE has requirements for:
- **Tool response caching**: Reduce duplicate external API calls within workflows
- **Rate limiting**: Enforce per-tool and per-workflow API call limits
- **Distributed locking**: Coordinate concurrent workflow executions
- **Session state**: Temporary data that doesn't require PostgreSQL durability
- **Performance**: Low-latency access for budget checks and cache lookups

Requirements:
- **Ephemeral data**: Data can be lost without workflow failure (cache misses are acceptable)
- **Sub-millisecond latency**: Budget checks must not add perceptible overhead
- **Atomic operations**: Rate limit counters must be thread-safe
- **TTL support**: Cached responses expire after configurable duration

**Important**: Redis is NOT the system of record. PostgreSQL remains the source of truth for workflow state.

## Decision

We will use **Redis 7+** for caching, rate limiting, and distributed coordination.

### Use Cases

#### 1. Tool Response Caching
```python
# Cache key: tool_name:hash(parameters)
# TTL: 1 hour (configurable per tool)
cache_key = f"tool_response:{tool_name}:{param_hash}"
cached_response = redis.get(cache_key)
if cached_response:
    return json.loads(cached_response)
```

#### 2. Rate Limiting
```python
# Per-tool rate limit: 100 calls/minute
rate_limit_key = f"rate_limit:{tool_name}:{minute_window}"
call_count = redis.incr(rate_limit_key)
redis.expire(rate_limit_key, 60)
if call_count > 100:
    raise RateLimitExceeded()
```

#### 3. Distributed Locking
```python
# Ensure single writer for budget updates
lock_key = f"lock:budget:{workflow_id}"
with redis_lock(lock_key, timeout=5):
    update_budget_state(workflow_id)
```

#### 4. Budget Pre-Flight Cache
```python
# Cache recent budget state to avoid PostgreSQL query on every agent call
budget_cache_key = f"budget:{workflow_id}"
budget_state = redis.get(budget_cache_key)
if budget_state is None:
    budget_state = load_from_postgres(workflow_id)
    redis.setex(budget_cache_key, 30, budget_state)  # 30s TTL
```

## Consequences

### Positive
- **Performance**: Sub-millisecond cache lookups reduce workflow latency
- **Cost reduction**: Cached tool responses avoid redundant external API calls
- **Rate limit enforcement**: Atomic counters prevent API abuse
- **Reduced PostgreSQL load**: Cache frequent reads (budget state, tool responses)
- **Horizontal scaling**: Redis Cluster supports sharding for high throughput

### Negative
- **Cache invalidation**: Stale cache entries if tool data changes externally
- **Additional infrastructure**: Redis instance to deploy, monitor, maintain
- **Cache miss handling**: Application must handle cache misses gracefully
- **Consistency complexity**: Cache and database can diverge temporarily

### Mitigations
- **TTL-based expiration**: All cached data has expiration (default: 1 hour)
- **Cache-aside pattern**: Always validate critical data against PostgreSQL
- **Graceful degradation**: Workflow continues if Redis unavailable (cache misses)
- **Monitoring**: Track cache hit rates, eviction rates, memory usage
- **Redis Sentinel**: High availability deployment for production

## Alternatives Considered

### 1. No caching (always call external APIs)
- **Pros**: Simplest architecture, always fresh data
- **Rejected**: Wasteful (duplicate calls), slow (external API latency), expensive (API costs)

### 2. In-memory Python cache (functools.lru_cache)
- **Pros**: No external dependencies, simple
- **Rejected**: Not shared across workflow instances, lost on process restart

### 3. Memcached
- **Pros**: Mature caching solution, simple protocol
- **Rejected**: No atomic operations for rate limiting, no distributed locking, no persistence option

### 4. PostgreSQL for caching
- **Pros**: Single database, simpler infrastructure
- **Rejected**: Too slow for sub-millisecond cache lookups, overloads system of record

## Boundaries

### Redis is NOT used for:
- **Workflow state**: Always stored in PostgreSQL
- **Checkpoints**: PostgreSQL is the durable checkpoint store
- **Event log**: Immutable audit trail lives in PostgreSQL
- **Critical business data**: Anything that cannot tolerate loss

### Redis IS used for:
- **Tool response cache**: Ephemeral, can be regenerated
- **Rate limit counters**: Transient state, resets periodically
- **Budget state cache**: Read-through cache backed by PostgreSQL
- **Distributed locks**: Coordination primitive, not business data

## Configuration

```yaml
redis:
  host: redis.casefile.internal
  port: 6379
  db: 0
  max_connections: 50
  socket_timeout: 5s

cache:
  tool_response_ttl: 3600  # 1 hour
  budget_state_ttl: 30     # 30 seconds

rate_limits:
  policy_lookup: 100/minute
  claim_history_lookup: 50/minute
  repair_cost_lookup: 100/minute
  fraud_signal_lookup: 50/minute
  document_retrieval: 20/minute
```

## References

- Redis 7 documentation: https://redis.io/docs/
- CASEFILE architecture: `docs/architecture.md`
- Tool architecture: `docs/tool-architecture.md`
