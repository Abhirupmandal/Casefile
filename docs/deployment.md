# CASEFILE Deployment Guide (Phase 8)

**Document Version**: 1.0.0  
**Phase**: Phase 8 (Security + Deployment)  
**Date**: 2026-10-05  

---

## 1. Overview

This document specifies the reproducible containerized deployment for CASEFILE using Docker and Docker Compose. The architecture provides:
- Multi-stage build packaging both the FastAPI backend and React Operations Console static bundle.
- Non-root unprivileged runtime user (`casefile:casefile`, UID `10001`).
- Clean separation between development, test, and production configurations.
- Integrated OpenTelemetry collector and Redis dependencies.
- Health and readiness probes for zero-downtime rolling restarts.

---

## 2. Container Architecture

### 2.1 Multi-Stage Dockerfile
- **Stage 1 (Frontend Builder)**: Node.js 20 Alpine builds the Vite Operations Console (`ui/dist`).
- **Stage 2 (Python Runtime)**: Python 3.11 Slim installs pinned dependencies via Poetry/wheels. Static assets from Stage 1 are copied to `/app/static`.
- **Runtime User**: An explicit non-root user `casefile` (UID `10001`, GID `10001`) owns `/app`.

### 2.2 Docker Compose Topology
Defined in [docker-compose.prod.yml](file:///E:/Projects/CASEFILE/docker-compose.prod.yml):
1. `casefile-api`: Core ASGI application listening on port `8000`.
2. `redis`: Alpine Redis 7.4 providing distributed caching / rate-limiting backend.
3. `jaeger`: OpenTelemetry tracing collector and Jaeger UI on port `16686`.

---

## 3. Health & Readiness Probes

- **Liveness (`/health`)**: Checks immediate process responsiveness.
  ```json
  {"status": "healthy", "service": "casefile-api", "timestamp": "2026-10-05T03:00:00Z"}
  ```
- **Readiness (`/ready`)**: Verifies required dependencies:
  - Database connectivity (SQL `SELECT 1`).
  - Read-write storage access.
  - Collector availability is treated as non-blocking (telemetry failures do not fail readiness).

---

## 4. Environment Configuration

| Variable | Default (Dev) | Production Requirement | Description |
|---|---|---|---|
| `CASEFILE_ENV` | `development` | `production` | Switches authentication and security policies |
| `CASEFILE_DATABASE_URL` | `sqlite:///./casefile.db` | `postgresql+psycopg2://...` or volume SQLite | Database connection URL |
| `CASEFILE_LOG_LEVEL` | `INFO` | `INFO` / `WARN` | Structured logging level |
| `OTEL_EXPORTER_OTLP_ENDPOINT`| `http://localhost:4318` | `http://jaeger:4318` | OpenTelemetry OTLP endpoint |
| `OTEL_SERVICE_NAME` | `casefile` | `casefile-prod` | Distributed trace service name |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Explicit domain list | Comma-separated CORS allowlist |

---

## 5. Deployment Instructions

### 5.1 Local Production-Like Deployment
```bash
# 1. Ensure Docker daemon is running
docker compose -f docker-compose.prod.yml config

# 2. Build and run in background
docker compose -f docker-compose.prod.yml up --build -d

# 3. Verify health
curl -f http://localhost:8000/health
curl -f http://localhost:8000/ready
```

### 5.2 Graceful Shutdown
The application listens for `SIGTERM` / `SIGINT` signals:
- Stops accepting new connections.
- Allows in-flight claim workflows to reach state machine checkpoints.
- Disposes database connection pools (`engine.dispose()`).
- Flushes remaining OpenTelemetry trace spans.

---

## 6. Rollback & Disaster Recovery

1. **Database Migrations**: Schema changes must be backward-compatible with N-1 versions.
2. **Checkpoint State**: Workflow runs are checkpointed per state transition. If an instance crashes or is rolled back, pending runs can be resumed or replayed deterministically from the last recorded checkpoint.
