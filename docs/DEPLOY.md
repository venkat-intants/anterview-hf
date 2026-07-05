# Intants AI Interview Platform — Stage 2 Demo Deploy Runbook

**Target:** Railway (5 backend services) + Vercel (frontend)
**URLs:** free `*.railway.app` / `*.vercel.app` (no custom domain)
**Status:** Demo tier only. NOT bid-compliant (no India residency; data in US/EU Railway/Vercel DCs).

---

## Architecture Overview

```
Browser (*.vercel.app)
  |
  |-- /gateway/*   --[Vercel rewrite]--> data-gateway.railway.app      :HTTPS
  |-- /interview/* --[Vercel rewrite]--> interview-core-api.railway.app :HTTPS
  |-- /feedback/*  --[Vercel rewrite]--> feedback-billing.railway.app   :HTTPS
  |-- SPA routes   --[Vercel]--> index.html
```

Five Railway services (one Railway project, separate services):

| Service name              | Image source                       | HTTP? | Health           |
|---------------------------|------------------------------------|-------|------------------|
| `data-gateway`            | services/data_gateway/Dockerfile   | yes   | /health/live     |
| `interview-core-api`      | services/interview_core/Dockerfile | yes   | /health/live     |
| `interview-core-worker`   | services/interview_core/Dockerfile | NO    | none (background)|
| `feedback-billing`        | services/feedback_billing/Dockerfile | yes | /health/live     |
| `admin-ops`               | services/admin_ops/Dockerfile      | yes   | /health/live     |

All images use **repo root as Docker build context** — the Dockerfiles reference
`services/<name>/` paths and `shared/` relative to the repo root.

---

## Prerequisites

- Railway account + Railway CLI (`railway login`)
- Vercel account + Vercel CLI (`vercel login`)
- All env-var values from `services/<name>/.env` files
- Neon Postgres URL (with pgvector extension enabled)
- Upstash Redis URL
- Cloudflare R2 bucket + keys (or any S3-compatible store)
- LiveKit Cloud project (URL, API key, API secret)
- Groq API key (worker LLM)
- Sarvam API key (STT + TTS)
- Gemini API key (feedback scoring)
- Tavus API key + persona ID (if AVATAR_PROVIDER=tavus)
- Simli API key + face ID (if AVATAR_PROVIDER=simli)
- One shared JWT_SECRET (same value across ALL services)

---

## Step 1 — Provision Railway project

```
railway init --name intants-demo
```

Create 5 services inside the project. Each service points to the same GitHub
repo but uses a different Dockerfile via the Railway dashboard or `railway.json`.

Railway reads `railway.json` at the repo root to discover service definitions,
build paths, and start commands. Import the project and Railway will create
services from the JSON.

**Set build root to `/` (repo root) for every service** — the Dockerfiles use
`services/<name>/` relative paths from the repo root.

---

## Step 2 — Set environment variables

Set these per-service in Railway dashboard > Variables. Values come from the
real `.env` files — never committed to git.

### Shared variables (set identically on ALL 5 services)

```
DATABASE_URL          postgresql+asyncpg://... (Neon pooled URL)
DATABASE_SSL          require
REDIS_URL             rediss://...  (Upstash TLS URL)
JWT_SECRET            <64-char random hex — SAME on all services>
JWT_ALGORITHM         HS256
JWT_ISSUER            intants-data-gateway
JWT_AUDIENCE          intants-services
APP_ENV               production
LOG_LEVEL             INFO
SENTRY_DSN            <optional — see Observability stub below>
```

### data-gateway specific

```
CONSENT_IP_SALT           <random 32-char hex>
AUTH_PROVIDER             local
AUTH_COOKIE_SECURE        true
AUTH_COOKIE_SAMESITE      none
CORS_ALLOWED_ORIGINS      https://<your-vercel-app>.vercel.app
TRUSTED_PROXY_COUNT       1
RETENTION_DRY_RUN         true
S3_ENDPOINT               https://<account>.r2.cloudflarestorage.com
S3_BUCKET_NAME            intants-uploads
S3_ACCESS_KEY_ID          <R2 key>
S3_SECRET_ACCESS_KEY      <R2 secret>
S3_REGION                 auto
GOOGLE_OAUTH_CLIENT_ID    <optional — for Google SSO>
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI https://<your-vercel-app>.vercel.app/gateway/sso/google/callback
```

### interview-core-api specific

```
LIVEKIT_URL               wss://your-project.livekit.cloud
LIVEKIT_API_KEY           <key>
LIVEKIT_API_SECRET        <secret>
AVATAR_PROVIDER           tavus     # or simli or none
TAVUS_API_KEY             <key>
TAVUS_REPLICA_ID          <replica>
TAVUS_PERSONA_ID          <echo-mode persona>
SIMLI_API_KEY             <key>
SIMLI_FACE_ID             <face>
GROQ_API_KEY              <key>
GROQ_MODEL                llama-3.3-70b-versatile
SARVAM_API_KEY            <key>
SARVAM_STT_MODEL          saaras:v3
LLM_PROVIDER              groq
FEEDBACK_BILLING_URL      https://<feedback-billing-railway-url>
CORS_ALLOWED_ORIGINS      https://<your-vercel-app>.vercel.app
TRUSTED_PROXY_COUNT       1
S3_ENDPOINT               https://<account>.r2.cloudflarestorage.com
S3_BUCKET_NAME            intants-interview-audio
S3_ACCESS_KEY_ID          <R2 key>
S3_SECRET_ACCESS_KEY      <R2 secret>
S3_REGION                 auto
```

### interview-core-worker specific

```
LIVEKIT_URL               wss://your-project.livekit.cloud
LIVEKIT_API_KEY           <key>
LIVEKIT_API_SECRET        <secret>
AVATAR_PROVIDER           tavus     # or simli or none
TAVUS_API_KEY             <key>
TAVUS_REPLICA_ID          <replica>
TAVUS_PERSONA_ID          <echo-mode persona>
SIMLI_API_KEY             <key>
SIMLI_FACE_ID             <face>
GROQ_API_KEY              <key>
GROQ_MODEL                llama-3.3-70b-versatile
SARVAM_API_KEY            <key>
SARVAM_STT_MODEL          saaras:v3
FEEDBACK_BILLING_URL      https://<feedback-billing-railway-url>
```

The worker has NO PORT and NO public URL. It connects outbound to LiveKit.
Set "Never Sleep" / "Always Running" in Railway — workers must not scale to zero.
Start command (override from Dockerfile default):
```
python -m app.worker.interview_worker start
```

### feedback-billing specific

```
GEMINI_API_KEY            <key>
GEMINI_MODEL              gemini-2.5-flash
S3_ENDPOINT_URL           https://<account>.r2.cloudflarestorage.com
S3_SCORECARD_BUCKET       intants-interview-scorecards
S3_ACCESS_KEY_ID          <R2 key>
S3_SECRET_ACCESS_KEY      <R2 secret>
S3_REGION                 auto
CORS_ALLOWED_ORIGINS      https://<your-vercel-app>.vercel.app
```

### admin-ops specific

```
CORS_ALLOWED_ORIGINS      https://<your-vercel-app>.vercel.app
```

---

## Step 3 — DB Migrations (MUST run before first API deploy)

Alembic migrations are owned by `data-gateway`. Run them as a **Railway one-off
job** using the same `data-gateway` image with the start command overridden.

In Railway dashboard, on the `data-gateway` service:
1. Click "Deploy" > "Create one-off job"  
   OR use the CLI:
   ```
   railway run --service data-gateway -- \
     alembic -c alembic.ini upgrade head
   ```
2. Watch the logs until you see `INFO  [alembic.runtime.migration] Running upgrade`.
3. Confirm completion: `INFO  [alembic.runtime.migration] Running upgrade ... -> <rev>, <desc>`.

The alembic migrations live in `services/data_gateway/alembic/versions/`.
As of 2026-06-02 there are 11 migration files covering:
- Initial auth tables
- Interview data model
- DPDP consent partial unique index
- Erasure audit tables
- Scorecards table
- Erasure request security fixes
- Interview context fields
- Resume + JD columns
- Jobs created_by_user_id
- Resumes table
- Session presenter_id

**Deploy order is critical.** Migrations MUST complete before any API service
starts. Railway deploy order:
```
1. Run migration job (data-gateway image, alembic upgrade head)
2. data-gateway API
3. feedback-billing
4. admin-ops
5. interview-core-api
6. interview-core-worker   ← LAST; must connect AFTER LiveKit is ready
```

---

## Step 4 — Deploy Vercel frontend

The frontend uses three env vars that must point to the **Vercel proxy paths**
(not Railway URLs directly), so the httpOnly cookie stays same-origin:

```
VITE_API_BASE_URL       (empty string — data_gateway calls go via /gateway/ Vercel rewrite)
VITE_INTERVIEW_API_URL  (empty string — interview_core calls go via /interview/ rewrite)
VITE_FEEDBACK_API_URL   (empty string — feedback calls go via /feedback/ rewrite)
VITE_LIVEKIT_URL        wss://your-project.livekit.cloud
VITE_USE_MOCK           false
VITE_SENTRY_DSN         <optional>
```

IMPORTANT: The Vercel rewrites in `web/vercel.json` contain placeholder URLs
that MUST be replaced with real Railway service URLs before deploying:
- `PLACEHOLDER_DATA_GATEWAY_URL` → actual Railway data-gateway public URL
- `PLACEHOLDER_INTERVIEW_CORE_URL` → actual Railway interview-core-api public URL
- `PLACEHOLDER_FEEDBACK_BILLING_URL` → actual Railway feedback-billing public URL

Update `web/vercel.json` rewrites with actual URLs, then:

```
cd web
vercel --prod
```

Set env vars in Vercel dashboard > Settings > Environment Variables.

Build command: `npm run build`
Output directory: `dist`
Root directory: `web`

### Why the Vercel proxy approach

The httpOnly `refresh_token` cookie is set by data-gateway. If the browser
calls Railway directly (`*.railway.app`) while the page is on `*.vercel.app`,
that is a **cross-site** request. Browsers block `SameSite=None` cookies
without `Secure=true`, and some browsers block them entirely.

The Vercel rewrite makes all API calls appear same-origin to the browser:
the browser posts to `https://your-app.vercel.app/gateway/auth/login`,
Vercel proxies to Railway, sets the response cookie on `*.vercel.app`,
and the cookie flows back on subsequent same-origin requests.

On Railway set:
```
AUTH_COOKIE_SAMESITE=none
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_DOMAIN=         (blank = Vercel domain scopes it)
```

---

## Step 5 — Seed admin user

After all services are healthy, seed the first admin user using the
`admin_ops` grant_admin script via Railway one-off job:

```
railway run --service admin-ops -- \
  python scripts/grant_admin.py support@intants.com
```

To list all users and roles:
```
railway run --service admin-ops -- \
  python scripts/grant_admin.py --list
```

The script is idempotent — safe to run multiple times.

---

## Step 6 — Health check verification

After all services are deployed, verify each API is up:

```
curl https://<data-gateway-url>.railway.app/health/live
# Expected: {"status":"alive"}

curl https://<interview-core-api-url>.railway.app/health/live
# Expected: {"status":"alive"}

curl https://<feedback-billing-url>.railway.app/health/live
# Expected: {"status":"alive"}

curl https://<admin-ops-url>.railway.app/health/live
# Expected: {"status":"alive"}
```

Deep health (checks Postgres, Redis, S3, LLM):
```
curl https://<data-gateway-url>.railway.app/health/deep
```

Worker has no HTTP endpoint — verify it is running in Railway logs.
You should see lines like:
```
INFO interview-worker — starting worker, ws_url=wss://...
```

---

## Step 7 — Smoke test checklist

Run through the full candidate journey:

1. **Login** — go to `https://<vercel-app>.vercel.app/login`, register or log in.
   Verify the JWT is stored (check DevTools > Application > Local Storage or the
   `Authorization` header on the next request).

2. **Consent** — confirm the DPDP consent modal appears on first login.
   Accept and verify a consent record is created
   (`GET /consent/me` via the `/gateway/` proxy returns a record).

3. **Interview setup** — navigate to /interview, pick a job role, pick an avatar,
   select language (EN/HI/TE).

4. **Interview** — start the interview. Confirm:
   - LiveKit room is created (check interview-core-api logs)
   - Worker picks up the room (check interview-core-worker logs)
   - Avatar appears and speaks the greeting (Q1)
   - Candidate audio is processed (Sarvam STT)
   - 10 questions cycle to completion
   - Closing message is spoken

5. **Score** — after the interview ends, verify feedback-billing receives the
   transcript (check logs for `interview-worker.score.ok`).

6. **Results** — navigate to /results or /history, verify the scorecard appears
   with composite score, dimension breakdowns, and the PDF download link.

7. **Admin** — log in as the seeded admin user, navigate to /admin, verify the
   analytics dashboard loads (session count, scores, user list).

---

## Observability stub

### Sentry

Sentry DSN is wired but optional. Set `SENTRY_DSN` in all services to enable.
Each service accepts the env var via its `Settings.sentry_dsn` field.

**TODO (follow-up):** The actual `sentry_sdk.init()` call and exception
middleware are not yet wired in `app/main.py` of any service. To fully wire:
1. Add `sentry-sdk[fastapi]` to each service's requirements.txt (or pip install
   into the relevant .venv and re-freeze).
2. In each service's `app/main.py`, add before the `FastAPI()` call:
   ```python
   import sentry_sdk
   if settings.sentry_dsn:
       sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
   ```
3. This is a 15-minute task per service; flagged as a post-first-deploy item.

### Logs

All services use `structlog` with JSON output. Railway captures stdout/stderr
and displays them in the Railway logs panel. No additional log aggregator
is wired for the demo tier (Loki is Tier-2 EKS).

---

## Demo limitations (NOT bid-compliant)

- **No India data residency** — Railway and Vercel are US/EU DCs.
  Production (Tier-2) uses AWS EKS Mumbai.
- **Avatar providers (Tavus/Simli) are US-hosted** — no India path.
  Production uses Three.js + Ready Player Me (Tier-2).
- **No custom domain** — `*.railway.app` / `*.vercel.app`.
- **No WAF / DDoS protection** — Cloudflare is Tier-2.
- **Scale-to-zero risk** — Railway free tier may sleep idle services.
  Set "Never Sleep" on all services before demos.
- **Tavus 3-minute session cap** — Tavus free tier limits sessions to 3 minutes.
  Upgrade or switch to `AVATAR_PROVIDER=simli` or `none` for longer sessions.
