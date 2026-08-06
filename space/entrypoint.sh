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

# ...but "runs without" only holds if the worker is not supervised without them.
# livekit-agents raises before its first connection attempt when any of the three
# LiveKit values is empty, so the worker would exit in under a second, exhaust
# startretries and trip supervisord's FATAL handler — turning an optional feature
# being off into a permanent boot loop for the whole Space. Read by
# [program:interview_worker] in space/supervisord.conf, which is why this is
# exported unconditionally: supervisord refuses to start if the name is unset.
if [ -n "${LIVEKIT_URL:-}" ] && [ -n "${LIVEKIT_API_KEY:-}" ] && [ -n "${LIVEKIT_API_SECRET:-}" ]; then
  export INTERVIEW_WORKER_AUTOSTART=true
else
  export INTERVIEW_WORKER_AUTOSTART=false
  echo "NOTICE: interview_worker stays stopped (LiveKit credentials incomplete)."
  echo "        Set all three LIVEKIT_* secrets and restart to enable interviews."
fi

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

# Email: HF blocks outbound SMTP ports (25/465/587) network-wide, so the only
# transport that can deliver from a Space is an HTTPS API. Set the
# RESEND_API_KEY Space secret and EMAIL_PROVIDER=resend to enable candidate
# emails; the smtp default leaves email queuing-but-undeliverable on Spaces.
export EMAIL_PROVIDER="${EMAIL_PROVIDER:-smtp}"

# Same-origin posture: frontend + APIs share one origin behind Caddy.
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-$PUBLIC_ORIGIN}"
export APP_BASE_URL="${APP_BASE_URL:-$PUBLIC_ORIGIN}"
export EXAM_LINK_BASE_URL="${EXAM_LINK_BASE_URL:-$PUBLIC_ORIGIN}"
export INTERVIEW_LINK_BASE_URL="${INTERVIEW_LINK_BASE_URL:-$PUBLIC_ORIGIN}"
export AUTH_COOKIE_SECURE="${AUTH_COOKIE_SECURE:-true}"
# Google SSO: Google must redirect back to the SPA callback route on this
# origin (also whitelist this exact URL in the Google Cloud OAuth client).
export GOOGLE_OAUTH_REDIRECT_URI="${GOOGLE_OAUTH_REDIRECT_URI:-$PUBLIC_ORIGIN/auth/google/callback}"
export AUTH_COOKIE_SAMESITE="${AUTH_COOKIE_SAMESITE:-lax}"
# TWO proxies append to X-Forwarded-For before a request reaches a service:
# HF's TLS-terminating edge, then our own Caddy (space/Caddyfile). The backends
# resolve the client by counting this many entries from the RIGHT, so 1 selected
# Caddy's peer — the HF edge — for EVERY request: one shared auth rate-limit
# bucket (any 6 logins/min locked out the whole platform) and one identical
# DPDP consent ip_hash for every data principal. Counting from the right also
# ignores attacker-prepended entries, which is why we do not let Caddy collapse
# the chain for us: its {client_ip} takes the LEFTMOST untrusted entry and is
# spoofable by a client that sends its own X-Forwarded-For.
# This 2 assumes Caddy trusts the HF edge and so preserves the chain (see the
# trusted_proxies note in space/Caddyfile). Verify on a live Space: the Caddy
# access log's request.remote_ip is the edge peer, and it must be inside
# private_ranges or listed in SPACE_EXTRA_TRUSTED_PROXIES.
export TRUSTED_PROXY_COUNT="${TRUSTED_PROXY_COUNT:-2}"   # HF edge + our Caddy

export LLM_PROVIDER="${LLM_PROVIDER:-gemini}"
export GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
# Console copilots + assessment panel (data_gateway). GEMINI_API_KEY is
# already required above, so the agents run wherever the interviewer does.
# Set AGENTS_ENABLED=false to switch them off during an incident or a cost
# spike without rotating the key or redeploying.
export AGENTS_ENABLED="${AGENTS_ENABLED:-true}"
# Nightly proactive watchers (stalled candidates, funnel health, exam
# quality, DPDP deadlines). Hour is UTC; the Space runs UTC.
export WATCHERS_ENABLED="${WATCHERS_ENABLED:-true}"
export WATCHERS_CRON_HOUR="${WATCHERS_CRON_HOUR:-2}"
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
# 3. Supervisor's FATAL handler (see [eventlistener:fatal_abort]).
#    Generated here, next to the boot policy it enforces, because it only has
#    meaning inside this container's process tree.
# ---------------------------------------------------------------------------
cat > /tmp/fatal_abort.py <<'PYEOF'
"""Abort the container when supervisord gives up restarting a program.

autorestart=true stops applying once a program reaches FATAL (it has failed
startretries times in a row — a rejected Space secret is the usual cause).
supervisord itself stays up and caddy keeps answering :7860, so HF's port probe
reads the Space as healthy while the service behind those routes is gone for
good, with no self-heal. Ending supervisord converts that silent half-outage
into a container exit, which is the only restart mechanism a Space has.
"""
import os
import signal
import sys

# Programs whose FATAL degrades the product instead of ending it. Keep this
# tiny and deliberate: anything listed here can die permanently while the Space
# still reports healthy, which is exactly the silent half-outage this listener
# exists to prevent for everything else.
NON_FATAL_PROGRAMS = frozenset({"interview_worker"})


def main() -> None:
    while True:
        # Supervisor's eventlistener protocol: announce readiness, read the
        # header, then exactly `len` bytes of payload.
        sys.stdout.write("READY\n")
        sys.stdout.flush()
        header = sys.stdin.readline()
        if not header:
            return
        meta = dict(kv.split(":", 1) for kv in header.split())
        payload = sys.stdin.read(int(meta["len"]))
        fields = dict(kv.split(":", 1) for kv in payload.split() if ":" in kv)
        program = fields.get("processname", "")

        # interview_worker is the one program whose death must not take the
        # container with it. The boot policy below already starts the rest of
        # the app when LiveKit credentials are ABSENT; aborting when the worker
        # is BROKEN would make the degradation disproportionate — every login,
        # console and scorecard would go down because avatar interviews cannot
        # run. Log it loudly and keep serving.
        if program in NON_FATAL_PROGRAMS:
            sys.stderr.write(
                f"DEGRADED: {payload} - {program} gave up. The rest of the app "
                f"keeps serving; avatar interviews are UNAVAILABLE until this "
                f"container is restarted.\n"
            )
            sys.stderr.flush()
            sys.stdout.write("RESULT 2\nOK")
            sys.stdout.flush()
            continue

        # ASCII only: under a C/POSIX locale stderr can be ascii-encoded, and a
        # UnicodeEncodeError here would abort the abort.
        sys.stderr.write(
            f"FATAL: {payload} - supervisord gave up; aborting the container "
            f"so Hugging Face restarts it\n"
        )
        sys.stderr.flush()
        # Acknowledge before shutting down, or supervisord logs a protocol
        # error on the way out and buries the message above.
        sys.stdout.write("RESULT 2\nOK")
        sys.stdout.flush()
        # Our parent IS supervisord (it forks its listeners), which `exec` in
        # this script made PID 1. SIGTERM lets it drain live interviews within
        # stopwaitsecs instead of severing them.
        os.kill(os.getppid(), signal.SIGTERM)
        return


main()
PYEOF

# ---------------------------------------------------------------------------
# 4. Database migrations (data_gateway owns the schema).
# ---------------------------------------------------------------------------
echo "--- alembic upgrade head (data_gateway schema) ---"
cd /app/services/data_gateway
/venvs/dg/bin/alembic upgrade head
cd /app

# ---------------------------------------------------------------------------
# 5. Run everything under supervisord (nodaemon; container's PID 1 via exec).
# ---------------------------------------------------------------------------
echo "--- starting supervisord (caddy + 4 services + worker) ---"
exec /venvs/tools/bin/supervisord -c /app/space/supervisord.conf
