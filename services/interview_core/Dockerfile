# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# interview_core — LiveKit API + LiveKit worker (one image, two run modes).
#
# BUILD CONTEXT: repo root — required, not conventional. This file does
# `COPY shared /app/shared`, so a services/interview_core/ context cannot build
# it. Every caller passes the root: docker-compose.prod.yml:136-139,
# docker-compose.yml, render.yaml `dockerContext: .`, and the CI docker matrix.
# (The old header cited a railway.json that does not exist in this repo —
# CICD-6. Railway is not a deploy target; the supported one is the Oracle VM
# via docker-compose.prod.yml, with the HF Space running the root Dockerfile.)
#
# RUN MODES (same image, different CMD):
#   API:    uvicorn app.main:app --host 0.0.0.0 --port $PORT   (default CMD)
#   Worker: python -m app.worker.interview_worker start         (override CMD)
#
# livekit-agents + livekit-plugins-* are pinned in BOTH pyproject.toml and
# requirements.txt. requirements.txt is the ONLY install source — this image,
# the CI service matrix and the dev .venv all use it, and no job anywhere reads
# poetry.lock (DEP-2: two in-repo docs claim otherwise). pyproject is
# documentation; keep the two in sync anyway so it does not mislead:
#   livekit-agents==1.5.15
#   livekit-plugins-openai==1.5.15
#   livekit-plugins-sarvam==1.5.15
#   livekit-plugins-silero==1.5.15
#   livekit-plugins-simli==1.5.15
#   livekit-plugins-tavus==1.5.15
# ---------------------------------------------------------------------------

# ---- Stage 1: build native extensions ----
FROM python:3.12-slim AS builder

WORKDIR /build

# gcc/g++: asyncpg, cryptography, av (PyAV for audio), cffi
# portaudio: sounddevice (LiveKit VAD path)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
        libpq-dev \
        portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/interview_core/requirements.txt ./

RUN pip install --upgrade pip --no-cache-dir \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---- Stage 2: lean runtime image ----
FROM python:3.12-slim AS runtime

RUN useradd --create-home --no-log-init --uid 1001 appuser

WORKDIR /app

# libportaudio2: sounddevice (silero VAD audio I/O — links to portaudio at runtime)
# libgomp1:      fallback OpenMP for onnxruntime (silero VAD); wheel bundles it
#                but the system lib is a cheap safety net
# libpq5:        asyncpg native driver
# PyAV (av==17) and onnxruntime ship bundled native libs in their wheels;
# no additional system libav or libonnxruntime needed.
# Security updates from the base image, then the runtime libraries.
#
# `apt-get upgrade` here is deliberate. Every image in this repo shares
# python:3.12-slim, so ONE fixable base CVE turns all five CI legs red at once
# and stays red until someone edits five Dockerfiles — which is how
# CVE-2026-53615 (util-linux, HIGH, fixed in 2.41.5-0+deb13u1) took the whole
# pipeline down and, with it, the Space deploy that is gated on CI.
#
# The usual objection to `apt-get upgrade` is reproducibility, and it does not
# apply cleanly here: the FROM tag already floats, so two builds a week apart
# were never going to produce identical layers. What this buys is that a base
# CVE with an available fix gets picked up at build time instead of being
# argued into .trivyignore — which keeps that file meaning "accepted risk we
# cannot act on" rather than "backlog".
#
# Runtime stage only. Trivy scans the final image; the builder stage ships
# nothing but compiled wheels.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libffi8 \
        libpq5 \
        libportaudio2 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# shared/ from repo root
COPY --chown=appuser:appuser shared /app/shared

# Service code
COPY --chown=appuser:appuser services/interview_core /app

# shared/ at /app/shared/. PYTHONPATH=/app resolves `from shared.xxx import ...`.
# No editable install — poetry-core build backend is not needed at runtime.

USER appuser

ENV PORT=8001 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PYTHONUTF8=1

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health/live')"

# Default = API.
# Railway worker service overrides this to:
#   python -m app.worker.interview_worker start
# `exec` so uvicorn replaces the shell as PID 1 and actually RECEIVES SIGTERM
# (the Docker/compose stop signal). Without it the shell holds PID 1, uvicorn
# never sees the signal, in-flight requests are not drained, and the container is
# SIGKILLed at the stop grace period. --timeout-graceful-shutdown bounds the drain
# so it fits within the compose stop_grace_period.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 25"]
