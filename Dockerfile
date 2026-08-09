# syntax=docker/dockerfile:1
# =============================================================================
# Hugging Face Spaces image — the ENTIRE Intants platform in one container.
#
# HF Docker Spaces build exactly one Dockerfile (this one, at repo root) and
# expose exactly one HTTP port (7860, declared as app_port in README.md).
# Inside, supervisord runs six processes:
#
#   caddy :7860        — serves the built React frontend + fans out API paths
#   data_gateway :8002 — auth / users / ATS / exams        (127.0.0.1 only)
#   interview_core :8001 — sessions / LiveKit room tokens   (127.0.0.1 only)
#   feedback_billing :8003 — scoring / scorecard PDFs       (127.0.0.1 only)
#   admin_ops :8004    — analytics / DPDP ops               (127.0.0.1 only)
#   interview_worker   — LiveKit avatar/voice engine (no port)
#
# Free Space hardware: 2 vCPU / 16 GB RAM — comfortably 1–2 concurrent live
# interviews. State lives in Neon/Upstash/R2 (set as Space secrets), so the
# ephemeral 50 GB disk resetting on restart is fine.
#
# Each service keeps its OWN virtualenv built from its OWN requirements.txt —
# the four freeze files pin different versions of shared packages (that is by
# design; see services/*/requirements.txt) and must not share one env.
# =============================================================================

# ---- Stage 1: frontend ------------------------------------------------------
FROM node:20-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
COPY web/scripts ./scripts
RUN npm ci
COPY web ./
# Deployment-specific, supplied with --build-arg. They are declared here rather
# than left to Vite's dotenv lookup because `web/.env` is now excluded by
# .dockerignore: it used to be copied into this stage and READ, so a
# developer's local values were baked into the production bundle. Neither is a
# secret (both ship in client JS by design), but both must come from the build
# invocation, not from whoever's laptop built the image.
ARG VITE_RPM_SUBDOMAIN=""
ARG VITE_SENTRY_DSN=""
# Same-origin API routing: empty base URLs make every client call relative
# (e.g. fetch("/auth/login")), and Caddy fans each path out to the right
# service. web/src/api/admin.ts uses ?? so the empty string is honoured.
ENV VITE_API_BASE_URL="" \
    VITE_INTERVIEW_API_URL="" \
    VITE_FEEDBACK_API_URL="" \
    VITE_ADMIN_API_URL="" \
    VITE_APP_NAME="Intants AI Interview" \
    VITE_APP_ENV="production" \
    VITE_USE_MOCK="false" \
    VITE_FEATURE_AVATAR="true" \
    VITE_FEATURE_VOICE_INTERRUPTION="true" \
    VITE_FEATURE_MULTILINGUAL="true" \
    VITE_RPM_SUBDOMAIN="${VITE_RPM_SUBDOMAIN}" \
    VITE_SENTRY_DSN="${VITE_SENTRY_DSN}"
# `npm run build` fires the prebuild hook (web/scripts/fetch-mediapipe.mjs),
# which vendors the FaceLandmarker model that proctoring loads from our own
# origin. That script defaults to LOUD-but-non-fatal and documents
# MEDIAPIPE_REQUIRED=1 as the switch "the container build does this" — and the
# container build did not (CICD-5): the flag was set nowhere in the repo, so the
# strict branch had never executed and a failed download shipped a green image
# whose proctoring was silently dead at runtime. The trade is deliberate: this
# build now depends on the pinned model URL being reachable, which is the right
# dependency for an artefact that cannot be repaired after it ships. The file is
# SHA-256-pinned, so a partial or substituted download fails here rather than
# poisoning a cache.
ENV MEDIAPIPE_REQUIRED="1"
RUN npm run build

# ---- Stage 2: python builder (four isolated venvs) --------------------------
FROM python:3.12-slim AS pybuild
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
        libpq-dev \
        portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/data_gateway/requirements.txt /tmp/req-dg.txt
COPY services/interview_core/requirements.txt /tmp/req-ic.txt
COPY services/feedback_billing/requirements.txt /tmp/req-fb.txt
COPY services/admin_ops/requirements.txt /tmp/req-ao.txt
RUN python -m venv /venvs/dg && /venvs/dg/bin/pip install --no-cache-dir -r /tmp/req-dg.txt
RUN python -m venv /venvs/ic && /venvs/ic/bin/pip install --no-cache-dir -r /tmp/req-ic.txt
RUN python -m venv /venvs/fb && /venvs/fb/bin/pip install --no-cache-dir -r /tmp/req-fb.txt
RUN python -m venv /venvs/ao && /venvs/ao/bin/pip install --no-cache-dir -r /tmp/req-ao.txt
# Supervisor gets its own tiny venv so it never collides with service pins.
RUN python -m venv /venvs/tools && /venvs/tools/bin/pip install --no-cache-dir "supervisor==4.3.0"

# ---- Stage 3: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libffi8 \
        libpq5 \
        libportaudio2 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Caddy static binary from the official image (no apt repo needed).
COPY --from=caddy:2-alpine /usr/bin/caddy /usr/local/bin/caddy

# HF Spaces convention: run as UID 1000.
RUN useradd -m -u 1000 appuser

COPY --from=pybuild --chown=appuser:appuser /venvs /venvs

WORKDIR /app
COPY --chown=appuser:appuser shared ./shared
COPY --chown=appuser:appuser services/data_gateway ./services/data_gateway
COPY --chown=appuser:appuser services/interview_core ./services/interview_core
COPY --chown=appuser:appuser services/feedback_billing ./services/feedback_billing
COPY --chown=appuser:appuser services/admin_ops ./services/admin_ops
COPY --from=webbuild --chown=appuser:appuser /web/dist ./webdist
COPY --chown=appuser:appuser space ./space

# Each service imports `shared` via pythonpath; make it importable from every
# venv with a .pth file (mirrors the dev-venv setup in dev-up.ps1).
RUN for v in dg ic fb ao; do \
        echo /app > /venvs/$v/lib/python3.12/site-packages/intants_shared.pth; \
    done \
    && chmod +x /app/space/entrypoint.sh \
    # Caddy + supervisord writable dirs for UID 1000
    && mkdir -p /home/appuser/.local/share/caddy /home/appuser/.config/caddy /var/log/intants \
    && chown -R appuser:appuser /home/appuser /var/log/intants

USER appuser
ENV HOME=/home/appuser \
    XDG_DATA_HOME=/home/appuser/.local/share \
    XDG_CONFIG_HOME=/home/appuser/.config \
    PYTHONUNBUFFERED=1

EXPOSE 7860
ENTRYPOINT ["/app/space/entrypoint.sh"]
