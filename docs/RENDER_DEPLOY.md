# Deploying Intants to Render

This deploys the full platform — 4 FastAPI APIs, the LiveKit worker, and the
React/Vite frontend — from the [`render.yaml`](../render.yaml) Blueprint at the
repo root. The database (Neon), cache (Upstash), storage (R2), LiveKit, Gemini,
and Sarvam stay on your **existing demo accounts**; Render only runs the compute.

---

## 0. Before you start

You need:

- A **Render** account (https://render.com) connected to this GitHub repo.
- Your existing demo credentials handy (the same values from each
  `services/<name>/.env`): Neon URL, Upstash URL, R2 keys, Gemini key, Sarvam
  key, LiveKit key/secret, and your JWT/consent/magic-link secrets.
- A **payment method on Render** — the `interview-worker` is a background worker,
  which Render does not offer on the free plan (~$7/mo `starter`). Everything
  else can be free. If you only want to demo the UI/auth/dashboards for $0, you
  can comment the worker out of `render.yaml` (the live interview won't run).

> **Reality check (free plan):** free web services sleep after 15 min idle and
> cold-start in ~30–60s, and have 512 MB RAM. `interview-core` is a heavy image
> (onnxruntime + PyAV + livekit-agents) and may OOM at 512 MB — if it crashes on
> boot, bump it to `starter` in the dashboard. See the cost note in `render.yaml`.

---

## 1. Generate the secrets (once)

The Blueprint keeps secrets out of git (`sync: false`). Generate the random ones:

```bash
python -c "import secrets; print('JWT_SECRET         =', secrets.token_hex(32))"
python -c "import secrets; print('CONSENT_IP_SALT    =', secrets.token_hex(32))"
python -c "import secrets; print('EXAM_LINK_SECRET   =', secrets.token_hex(32))"
python -c "import secrets; print('INTERVIEW_LINK_SECRET =', secrets.token_hex(32))"
```

Keep these four — `JWT_SECRET` **must be identical** across all services (the
Blueprint guarantees that by storing it once in the `intants-shared` group), and
the three link secrets must each be **distinct** from `JWT_SECRET`.

Make sure your **Neon** connection string is the **pooled** one
(`...-pooler.<region>.aws.neon.tech`) in asyncpg form, with **no** `?sslmode=`:

```
postgresql+asyncpg://USER:PASSWORD@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/intants
```

(TLS is supplied separately via `DATABASE_SSL=require`, already set in the Blueprint.)

---

## 2. Create the Blueprint

1. Push this branch (with `render.yaml`) to GitHub.
2. Render Dashboard → **New → Blueprint** → pick this repo → Render reads
   `render.yaml` and lists the 6 services + the `intants-shared` env group.
3. It will prompt for every `sync: false` value. You can fill them now or click
   **Apply** and fill them after (services will fail to boot until the required
   ones are set — that's expected).

### Fill the `intants-shared` group (applies to all backends)

| Key | Value |
|---|---|
| `DATABASE_URL` | Neon **pooled** asyncpg URL (above) |
| `JWT_SECRET` | from step 1 |
| `GEMINI_API_KEY` | Google AI Studio key |
| `S3_ENDPOINT` | `https://<r2-account-id>.r2.cloudflarestorage.com` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | R2 keys |
| `CORS_ALLOWED_ORIGINS` | leave blank for now — set in step 4 |
| `SENTRY_DSN` | optional |

### Per-service secrets

- **All backends:** `REDIS_URL` — your Upstash `rediss://default:PASS@HOST:6379`.
- **data_gateway:** `CONSENT_IP_SALT`, `EXAM_LINK_SECRET`, `INTERVIEW_LINK_SECRET`,
  `SMTP_PASSWORD` (Resend `re_…` key). Leave `EXAM_LINK_BASE_URL` /
  `INTERVIEW_LINK_BASE_URL` blank for now (step 4).
- **interview-core + interview-worker:** `SARVAM_API_KEY`, `LIVEKIT_URL`,
  `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. Avatar defaults to `none` (voice-only,
  no Tavus cost) — to enable an avatar set `AVATAR_PROVIDER=tavus` and fill
  `TAVUS_*` (or `simli` + `SIMLI_*`).
- **feedback-billing:** `OPENAI_API_KEY` (embeddings), `SMTP_PASSWORD`.

Then **Apply** — Render builds and deploys. The first build of `interview-core`
is large (~10–20 min); the others are quick.

---

## 3. Note the public URLs

After the first deploy, each web service has a public URL:

```
https://intants-data-gateway.onrender.com
https://intants-interview-core.onrender.com
https://intants-feedback-billing.onrender.com
https://intants-admin-ops.onrender.com
https://intants-web.onrender.com
```

If a name was taken globally Render appends a suffix — **copy the exact URLs**
from each service's page. (Inter-service backend calls use Render's private
network by service name and are already wired in `render.yaml` — you don't touch
those.)

---

## 4. Close the loop: frontend URLs ↔ CORS

There's a deliberate two-pass step because the frontend bakes the API URLs at
build time and the backends must trust the frontend origin:

1. On **`intants-web`** → Environment, set the four `VITE_*` vars to the **exact
   public URLs** from step 3:
   - `VITE_API_BASE_URL` → data-gateway URL
   - `VITE_INTERVIEW_API_URL` → interview-core URL
   - `VITE_FEEDBACK_API_URL` → feedback-billing URL
   - `VITE_ADMIN_API_URL` → admin-ops URL
   Then **Manual Deploy → Clear build cache & deploy** (Vite must rebuild to
   inline them).
2. In the **`intants-shared`** env group, set `CORS_ALLOWED_ORIGINS` to the
   frontend URL (e.g. `https://intants-web.onrender.com`, **no trailing slash**).
3. On **`intants-data-gateway`**, set `EXAM_LINK_BASE_URL` and
   `INTERVIEW_LINK_BASE_URL` to the same frontend URL.
4. Saving the group/vars triggers a redeploy of the affected services. Once
   green, open the frontend URL.

> Multiple allowed origins (e.g. a custom domain too) → comma-separate them in
> `CORS_ALLOWED_ORIGINS`.

---

## 5. Smoke test

```bash
curl https://intants-data-gateway.onrender.com/health/live
curl https://intants-interview-core.onrender.com/health/live
curl https://intants-feedback-billing.onrender.com/health/live
curl https://intants-admin-ops.onrender.com/health/live
```

(First hit on a free service is slow — it's waking from sleep.) Then in the
browser: register/login, start an interview. The `interview-worker` log should
show it picking up the LiveKit room. If the avatar/voice doesn't start, check
that the worker is **running** (not free-tier asleep — workers don't sleep, but
confirm it deployed) and that `LIVEKIT_*` match the interview-core values.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `interview-core` crashes on boot, "out of memory" | Bump its plan to `starter`/`standard`. 512 MB is tight for that image. |
| Browser console: CORS error | `CORS_ALLOWED_ORIGINS` must equal the frontend origin exactly, no trailing slash, scheme included. |
| Frontend calls `localhost:800x` | `VITE_*` weren't set before the build — set them and **clear build cache & redeploy** the static site. |
| Interview never starts, no worker activity | Worker not deployed (it can't be free) or `LIVEKIT_*` mismatch between core API and worker. |
| `asyncpg ... sslmode` error | Remove `?sslmode=require` from `DATABASE_URL`; keep `DATABASE_SSL=require`. |
| Worker → feedback_billing call fails intermittently | On free plans a slept service won't wake from private traffic. Either keep `feedback-billing` on `starter`, or set `FEEDBACK_BILLING_URL` to its public `https://…onrender.com` URL on the worker + data-gateway. |
| DB "too many connections" | Use the Neon **pooled** endpoint and keep `DATABASE_POOL_SIZE` low (3). |

---

## What this does NOT change

- No new database/cache/storage is provisioned — you reuse Neon/Upstash/R2. The
  schema is whatever your demo Neon already has (migrations are not re-run here).
- Production (Tier-2 AWS Mumbai) is unchanged — this is a Render demo target, the
  same env-swappable code.
