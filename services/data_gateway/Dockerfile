# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# data_gateway — Auth, user management, DPDP consent, jobs, resume upload.
#
# BUILD CONTEXT: repo root (Railway sets "root": "/" in railway.json so the
# context includes both shared/ and services/data_gateway/).
#
# Multi-stage build. Non-root user (uid 1001). Binds to $PORT or 8002.
# ---------------------------------------------------------------------------

# ---- Stage 1: build / compile native extensions ----
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt path relative to repo-root build context
COPY services/data_gateway/requirements.txt ./

RUN pip install --upgrade pip --no-cache-dir \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---- Stage 2: lean runtime image ----
FROM python:3.12-slim AS runtime

RUN useradd --create-home --no-log-init --uid 1001 appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libffi8 \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Installed packages from builder
COPY --from=builder /install /usr/local

# shared/ package (from repo root build context)
COPY --chown=appuser:appuser shared /app/shared

# Service application code
COPY --chown=appuser:appuser services/data_gateway /app

# shared/ lives at /app/shared/. PYTHONPATH=/app makes `from shared.xxx import ...`
# resolve to /app/shared/. No editable install needed — poetry-core (the build
# backend declared in shared/pyproject.toml) is not in any service's requirements
# and is not needed at runtime.

USER appuser

# PYTHONPATH includes /app so `from app.xxx import ...` resolves without a package install.
ENV PORT=8002 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health/live')"

# API server. Can also be overridden to run migrations:
#   alembic -c alembic.ini upgrade head
# `exec` so uvicorn replaces the shell as PID 1 and actually RECEIVES SIGTERM
# (the Docker/compose stop signal). Without it the shell holds PID 1, uvicorn
# never sees the signal, in-flight requests are not drained, and the container is
# SIGKILLed at the stop grace period. --timeout-graceful-shutdown bounds the drain
# so it fits within the compose stop_grace_period.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 25"]
