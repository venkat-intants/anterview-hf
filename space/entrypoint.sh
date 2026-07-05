#!/bin/bash
# =============================================================================
# HF Space entrypoint — env defaults, fail-fast secret checks, DB migrations,
# then supervisord (which runs caddy + 4 APIs + the LiveKit worker).
# =============================================================================
set -euo pipefail

echo "=== Intants HF Space boot ==="

# ---------------------------------------------------------------------------
# 1. Hard-required secrets (set under Space Settings → Variables and secrets).
#    Fail fast with a readable message instead of five crash-looping services.
# ---------------------------------------------------------------------------
REQUIRED=(DATABASE_URL REDIS_URL JWT_SECRET CONSENT_IP_SALT EXAM_LINK_SECRET INTERVIEW_LINK_SECRET)
MISSING=()
for v in "${REQUIRED[@]}"; do
  [ -n "${!v:-}" ] || MISSING+=("$v")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "FATAL: missing required Space secrets: ${MISSING[*]}"
  echo "Add them in the Space Settings -> Variables and secrets, then restart."
  echo 'Generate strong values with: python -c "import secrets; print(secrets.token_hex(32))"'
  exit 1
fi

# Soft-required: the live interview needs these; the rest of the app runs without.
for v in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET SARVAM_API_KEY GEMINI_API_KEY; do
  [ -n "${!v:-}" ] || echo "WARNING: $v is not set — live interviews will not work until it is."
done

# ---------------------------------------------------------------------------
# 2. Defaults (every one overridable via Space variables).
#    SPACE_HOST is injected by Hugging Face (e.g. user-name-space.hf.space).
# ---------------------------------------------------------------------------
PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-https://${SPACE_HOST:-localhost:7860}}"

export APP_ENV="${APP_ENV:-production}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export DATABASE_SSL="${DATABASE_SSL:-require}"
export DATABASE_POOL_SIZE="${DATABASE_POOL_SIZE:-3}"   # 5 procs share Neon's cap
export AUTH_PROVIDER="${AUTH_PROVIDER:-local}"
export JWT_ALGORITHM="${JWT_ALGORITHM:-HS256}"
export JWT_ISSUER="${JWT_ISSUER:-intants-data-gateway}"
export JWT_AUDIENCE="${JWT_AUDIENCE:-intants-services}"
export JWT_EXPIRY_HOURS="${JWT_EXPIRY_HOURS:-24}"

# Same-origin posture: frontend + APIs share one origin behind Caddy.
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-$PUBLIC_ORIGIN}"
export APP_BASE_URL="${APP_BASE_URL:-$PUBLIC_ORIGIN}"
export EXAM_LINK_BASE_URL="${EXAM_LINK_BASE_URL:-$PUBLIC_ORIGIN}"
export INTERVIEW_LINK_BASE_URL="${INTERVIEW_LINK_BASE_URL:-$PUBLIC_ORIGIN}"
export AUTH_COOKIE_SECURE="${AUTH_COOKIE_SECURE:-true}"
export AUTH_COOKIE_SAMESITE="${AUTH_COOKIE_SAMESITE:-lax}"
export TRUSTED_PROXY_COUNT="${TRUSTED_PROXY_COUNT:-1}"   # HF edge proxy

export LLM_PROVIDER="${LLM_PROVIDER:-gemini}"
export GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
export GEMINI_MAX_TOKENS="${GEMINI_MAX_TOKENS:-2048}"
export SPEECH_STT_PROVIDER="${SPEECH_STT_PROVIDER:-sarvam}"
export SPEECH_TTS_PROVIDER="${SPEECH_TTS_PROVIDER:-sarvam}"
export SARVAM_STT_MODEL="${SARVAM_STT_MODEL:-saaras:v3}"
export SARVAM_TTS_MODEL="${SARVAM_TTS_MODEL:-bulbul:v3}"
# none = voice-only interview (no paid avatar quota needed); tavus|simli to enable.
export AVATAR_PROVIDER="${AVATAR_PROVIDER:-none}"

export S3_REGION="${S3_REGION:-auto}"
export S3_USE_SSL="${S3_USE_SSL:-true}"
export S3_BUCKET_DG="${S3_BUCKET_DG:-intants-uploads}"
export S3_BUCKET_AUDIO="${S3_BUCKET_AUDIO:-intants-interview-audio}"
export S3_BUCKET_SCORECARDS="${S3_BUCKET_SCORECARDS:-intants-interview-scorecards}"
# feedback_billing + admin_ops use different setting names for the same things.
export S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-${S3_ENDPOINT:-}}"
export S3_SCORECARD_BUCKET="${S3_SCORECARD_BUCKET:-$S3_BUCKET_SCORECARDS}"

export EXECUTION_PROVIDER="${EXECUTION_PROVIDER:-jdoodle}"
export FEATURE_AVATAR_ENABLED="${FEATURE_AVATAR_ENABLED:-true}"
export FEATURE_VOICE_INTERRUPTION="${FEATURE_VOICE_INTERRUPTION:-true}"
export FEATURE_MULTILINGUAL="${FEATURE_MULTILINGUAL:-true}"

# Cross-service URLs — everything is loopback inside the one container.
export DATA_GATEWAY_URL="http://127.0.0.1:8002"
export INTERVIEW_CORE_URL="http://127.0.0.1:8001"
export FEEDBACK_BILLING_URL="http://127.0.0.1:8003"

# Worker sizing for the free Space (2 vCPU / 16 GB).
export WORKER_MAX_CONCURRENT_JOBS="${WORKER_MAX_CONCURRENT_JOBS:-2}"
export WORKER_DRAIN_TIMEOUT_SECONDS="${WORKER_DRAIN_TIMEOUT_SECONDS:-120}"

export SENTRY_DSN="${SENTRY_DSN:-}"
export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-}"

# ---------------------------------------------------------------------------
# 3. Database migrations (data_gateway owns the schema).
# ---------------------------------------------------------------------------
echo "--- alembic upgrade head (data_gateway schema) ---"
cd /app/services/data_gateway
/venvs/dg/bin/alembic upgrade head
cd /app

# ---------------------------------------------------------------------------
# 4. Run everything under supervisord (nodaemon; container's PID 1 via exec).
# ---------------------------------------------------------------------------
echo "--- starting supervisord (caddy + 4 services + worker) ---"
exec /venvs/tools/bin/supervisord -c /app/space/supervisord.conf
