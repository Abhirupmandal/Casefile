# ADR-011: Replace PostgreSQL with SQLite for Local Persistence

**Status**: Accepted
**Date**: 2026-09-22
**Deciders**: Architecture Team
**Related Documents**: `persistence.md`, `architecture.md`, `checkpointing.md`, `ADR-003` (superseded), `ADR-004` (amended), `ADR-006` (amended), `ADR-007` (amended)

## Context

ADR-003 selected PostgreSQL 15+ as the system of record. In practice this
forces every developer machine to provision a database server (Docker or
host-installed PostgreSQL), manage credentials (`DB_PASSWORD`), coordinate
host ports (`DB_PORT`), and keep dev/test databases (`casefile_dev`,
`casefile_test`) in sync with configuration. A host-local PostgreSQL holding
port 5432 blocked the entire Phase 1 verification gate, proving the
operational cost is paid on every machine, not just in production.

CASEFILE Phase 1 needs durable local state (claims, workflow/run state,
audit records, evaluation records) plus fast ephemeral coordination
(checkpoints-in-flight, locks, caches). No Phase 1 workload requires a
networked multi-writer database server.

## Decision

- **Primary persistence: SQLite.** Durable CASEFILE state (claims,
  workflow/run state, audit records, evaluation records where appropriate)
  lives in a local SQLite file. Default `sqlite:///./casefile.db`
  (development) and `sqlite:///./casefile_test.db` (test), overridable via
  the `CASEFILE_DATABASE_URL` environment variable. No server, host, port,
  or password. Health checks open the database and run `SELECT 1`.
- **Coordination / ephemeral state: Redis (unchanged role).** Checkpoint
  coordination where appropriate, ephemeral state, locks, caching, temporary
  coordination. Redis is explicitly NOT the permanent database.
- **Unchanged**: LangGraph orchestration, FastAPI API, Pydantic v2
  contracts, OpenTelemetry observability.
- PostgreSQL (`postgres` Docker service, `psycopg2-binary`, `asyncpg`,
  `DB_PASSWORD`/`DB_HOST`/`DB_PORT`) is removed from code, configuration,
  Docker, dependencies, and tests. ADR-003 is marked superseded (preserved
  as history); ADR-004/006/007 carry amendment notes.

## Alternatives Considered

### 1. Keep PostgreSQL, move it off port 5432 (rejected)
Worked technically (host port 5433 mapped to container 5432) but keeps the
server requirement, credential management, and volume lifecycle on every
developer machine. Treats the symptom, not the cause.

### 2. Require host-installed PostgreSQL (rejected)
Contradicts the goal of zero-install local reproducibility and reintroduces
per-machine credential/port drift.

### 3. Redis as primary store (rejected)
Same rejection as ADR-003 §Alternatives: not designed for durable
system-of-record use; complex queries difficult; snapshot persistence
weaker than a real database file.

### 4. DuckDB / other embedded OLAP (rejected)
Optimized for analytics, weaker story for transactional workflow state and
SQLAlchemy/Alembic migration tooling already selected for CASEFILE.

## Tradeoffs

SQLite is selected for:
- Zero external database installation (Python stdlib `sqlite3`)
- Easy local reproducibility (`poetry install` + `docker compose up -d redis`)
- Simple development (database is a file; `*.db` is gitignored)
- Deterministic test environments (isolated `casefile_test.db`)

Redis is retained for:
- Fast ephemeral coordination (locks, rate limiting, caches)
- Checkpoint/cache capabilities alongside durable SQLite rows
- Distributed-style coordination semantics for later phases

## Consequences

### Positive
- `poetry run casefile healthcheck --env test` passes with only Docker Redis
  running; no database server to install, secure, or port-manage
- Single configuration knob (`CASEFILE_DATABASE_URL`) replaces
  `DB_PASSWORD`/`DB_HOST`/`DB_PORT` and per-environment database names
- Smaller dependency surface (no `psycopg2-binary`, `asyncpg`)
- SQLAlchemy/Alembic investment preserved for future schema evolution

### Negative / Limitations
- **SQLite is not a production-scale multi-writer database.** Single-writer
  semantics and file locking do not match PostgreSQL-level concurrency for a
  large deployed insurance workload. This is an explicit, accepted
  limitation for local development and test.
- No network access, no `LISTEN`/`NOTIFY`, no JSONB/GIN indexing; JSON is
  stored as `JSON`/text.
- File backups replace WAL archiving / point-in-time recovery.

### Migration impact
- `DatabaseConfig` now exposes `url` (+ derived `path`) instead of
  host/port/name/user/password/pool fields
- Health service key renamed `postgresql` → `sqlite`
- `docker-compose.yml` provides Redis + Jaeger only; `docker-compose.test.yml`
  deleted
- Tests assert SQLite URLs and cover missing/corrupt database handling

## Future production migration path

If a deployed workload outgrows SQLite: reintroduce a server database
(PostgreSQL or managed equivalent) behind the same `DatabaseConfig.url`
contract and SQLAlchemy/Alembic migrations, add a new ADR superseding this
one, and gate the cutover on the evaluation suite (ADR-010). Application
code must keep database access behind SQLAlchemy so the dialect switch stays
mechanical.

## References

- Supersedes: `ADR-003-postgresql-persistence.md`
- CASEFILE persistence design: `docs/persistence.md`
- Health implementation: `src/casefile/health.py`
- Configuration: `src/casefile/config.py`, `config/development.yaml`, `config/test.yaml`
