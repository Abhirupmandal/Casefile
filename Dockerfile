# ==============================================================================
# Stage 1: Frontend Build (Operations Console)
# ==============================================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /app/ui

# Install dependencies deterministically
COPY ui/package*.json ./
RUN npm ci

# Build static assets
COPY ui/ ./
RUN npm run build

# ==============================================================================
# Stage 2: Hardened Python Application Runtime
# ==============================================================================
FROM python:3.11-slim AS runtime

# Security: Install minimal OS dependencies and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user and group
RUN groupadd -g 10001 casefile && \
    useradd -u 10001 -g casefile -s /bin/bash -m casefile

# Working directory
WORKDIR /app

# Install poetry with pinned installer
ENV POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# Copy dependency manifests first for caching
COPY pyproject.toml poetry.lock* ./

# Install production dependencies only (no dev packages)
RUN poetry install --without dev --no-root --no-interaction --no-ansi

# Copy project source and configuration
COPY src/ ./src/
COPY config/ ./config/
COPY README.md ./

# Install application package
RUN poetry install --without dev --no-interaction --no-ansi

# Copy built frontend assets from builder stage
COPY --from=frontend-builder /app/ui/dist ./ui/dist

# Create persistent data directory with non-root ownership
RUN mkdir -p /app/data && chown -R casefile:casefile /app

# Switch to non-root user
USER casefile

# Expose production ASGI port
EXPOSE 8000

# Container liveness health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production ASGI server entrypoint
CMD ["uvicorn", "casefile.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
