# DEPLOY-SECURITY — Stage 2 Pre-Deploy Security Runbook

> Scope: Public demo deploy of the Intants AI Interview platform.
> Frontend on Vercel (`*.vercel.app`), backend on Railway (`*.railway.app`), **no custom domain**.
> White-hat / defensive only. This is a checklist for the founder to execute in provider dashboards.
> Author: security-auditor. Date: 2026-06-02.
>
> **Still applies to the current Oracle + Vercel deploy.** The backend now runs on
> an Oracle VM behind Caddy (`api.yourdomain.com`) instead of Railway, but the
> frontend (Vercel) and backend remain **different origins**, so the Section 2
> cross-origin cookie analysis (`AUTH_COOKIE_SAMESITE=none` + CSRF double-submit,
> as set in `docker-compose.prod.yml`) is unchanged and authoritative. Read
> "Railway origin" as "your Caddy API domain" throughout.

---

## 0. TL;DR — what blocks "go public"

1. **Rotate every API key that was ever pasted in chat / committed / shared** (Section 1). Treat all of them as burned.
2. **Decide cross-origin auth strategy — RECOMMENDED: Vercel rewrite proxy** so frontend + backend are same-origin `*.vercel.app` and cookies stay first-party (Section 2). This sidesteps the broken cross-site-cookie path entirely.
3. **Flip production env settings** per service (Section 3): `APP_ENV=production`, `DATABASE_SSL=require`, `AUTH_COOKIE_SECURE=true`, locked CORS, correct `TRUSTED_PROXY_COUNT`.
4. **Paste per-service env vars into Railway** (Section 4 matrix).
5. **Run the go/no-go checklist** (Section 5). Note DPDP/residency: **this demo tier is NOT India-resident and is NOT bid-compliant — demo only.**

---

## 1. Secret rotation checklist

Anything that was pasted into a chat, screen-shared, or put in a local `.env` that left the founder's machine must be considered **compromised and rotated before the URL is public**. Generate fresh values in each provider's dashboard, then paste into Railway → Service → Variables. Do **not** reuse the old values.

> The two app-generated secrets (`JWT_SECRET`, `CONSENT_IP_SALT`) are NOT provider keys — fresh values are generated for you at the bottom of this section.

| # | Credential | Env KEY NAME(s) | Where to rotate | Railway service(s) that need it |
|---|---|---|---|---|
| 1 | Tavus (avatar) | `TAVUS_API_KEY` (+ `TAVUS_REPLICA_ID`, `TAVUS_PERSONA_ID` are IDs, not secrets) | https://platform.tavus.io → API Keys → revoke + create | `interview_core`, **worker** |
| 2 | Sarvam (STT/TTS) | `SARVAM_API_KEY` | https://dashboard.sarvam.ai → API Keys → regenerate | `interview_core`, **worker** |
| 3 | Groq (LLM alt) | `GROQ_API_KEY` | https://console.groq.com → Keys → delete + create | `interview_core`, **worker** |
| 4 | Gemini (LLM primary) | `GEMINI_API_KEY` | https://aistudio.google.com → Get API key → delete + create | `interview_core`, `feedback_billing`, **worker** |
| 5 | OpenAI (Whisper STT + embeddings) | `OPENAI_API_KEY` | https://platform.openai.com/api-keys → revoke + create | `interview_core` (Whisper), `feedback_billing` (embeddings), **worker** |
| 6 | LiveKit (key + secret) | `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | https://cloud.livekit.io → Project → Settings → Keys → delete + create | `interview_core`, **worker** |
| 7 | Neon Postgres | `DATABASE_URL` | https://console.neon.tech → Project → Roles → Reset password (rotates the conn string) | all 5 (data_gateway, interview_core, feedback_billing, admin_ops, worker) |
| 8 | Upstash Redis | `REDIS_URL` | https://console.upstash.com → DB → Details → reset password / new token | data_gateway, interview_core, feedback_billing, admin_ops (worker uses interview_core's) |
| 9 | Cloudflare R2 | `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (+ `S3_ENDPOINT`) | https://dash.cloudflare.com → R2 → Manage API Tokens → roll | data_gateway (uploads), interview_core (audio), feedback_billing (scorecards), admin_ops |
| 10 | Resend (email) | `SMTP_PASSWORD` (the `re_...` key) | https://resend.com → API Keys → revoke + create | data_gateway, feedback_billing |
| 11 | Anthropic (LLM alt) | `ANTHROPIC_API_KEY` | https://console.anthropic.com → API Keys → revoke + create | only if `LLM_PROVIDER=anthropic` — interview_core, feedback_billing, worker |
| 12 | Razorpay | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | n/a — `FEATURE_BILLING=false` for demo; leave blank, do NOT set live keys | (none for demo) |

**Why DB / Redis / R2 are in scope:** their full credentials live inside `DATABASE_URL` / `REDIS_URL` / the S3 key pair. If any `.env` containing these ever left the founder's machine (chat paste, screenshot, shared zip), they are equivalent to exposed secrets and must be rolled. If you are certain they were never shared, you may skip 7-10 — but rolling them is cheap insurance for a first public deploy.

### App-generated secrets (NOT provider keys) — paste these into Railway

These are generated locally and must be **identical across all services** that share them. `data_gateway` ISSUES the JWT; `interview_core`, `feedback_billing`, `admin_ops`, and the **worker** VALIDATE it — they must use the **same** `JWT_SECRET` or every cross-service call 401s.

```
# Generate locally — NEVER commit the result:  python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=<paste-your-freshly-generated-64-hex-value>
```
Set `JWT_SECRET` to your generated value on **ALL FIVE** Railway services (data_gateway, interview_core, feedback_billing, admin_ops, worker). Byte-for-byte identical.

```
# Generate locally — NEVER commit the result:  python -c "import secrets; print(secrets.token_hex(32))"
CONSENT_IP_SALT=<paste-your-freshly-generated-64-hex-value>
```
Set `CONSENT_IP_SALT` on **`data_gateway` only** (it is the only service that writes the DPDP consent ledger and sha256-hashes client IPs). No other service reads it.

> Rotation policy going forward: rotate provider keys quarterly and on any suspected exposure. Rotating `JWT_SECRET` invalidates all live access+refresh tokens (everyone re-logs in) — fine for a demo, schedule for production.

---

## 2. Cross-origin auth design (CRITICAL)

### The problem
The app uses a two-cookie scheme set by `data_gateway` on login/register/refresh:
- `refresh_token` — `httpOnly` (XSS-safe, browser-only).
- `csrf_token` — `httpOnly=false` so JS reads it and echoes it as the `X-CSRF-Token` header (double-submit CSRF defense on `/auth/refresh`).

Source of truth: `services/data_gateway/app/routers/auth.py` (`_set_auth_cookies`, `refresh`), `services/data_gateway/app/config.py` (cookie settings), and `web/src/api/client.ts` (`attemptRefresh` reads `document.cookie['csrf_token']`).

With **frontend on `*.vercel.app` and backend on `*.railway.app`** (two different registrable domains), this breaks:
- The frontend JS cannot read a cookie that Railway set on the `*.railway.app` domain — `document.cookie` only exposes cookies for the page's own origin. So `readCookie('csrf_token')` returns `null` and `attemptRefresh()` short-circuits to "not logged in" → refresh never works.
- Cross-site cookies require `SameSite=None; Secure`, and `*.railway.app` is on the **Public Suffix List**, so a host-scoped cookie is fine but is still **third-party** relative to the Vercel page. Modern browsers (Safari ITP, Chrome's third-party-cookie phase-out, Firefox ETP) block or partition third-party cookies, so the `refresh_token` cookie may silently not be sent on cross-site requests.

Net: session refresh is fragile-to-broken on the free cross-domain setup.

### RECOMMENDED FIX — Vercel rewrite proxy (same-origin)

Make the frontend proxy `/api/*` to Railway so the **browser only ever talks to `*.vercel.app`**. Cookies become first-party, no `SameSite=None` needed, no third-party-cookie issues, and CSRF double-submit works because JS and the cookie share one origin.

Create `web/vercel.json`:
```json
{
  "rewrites": [
    { "source": "/api/dg/:path*",    "destination": "https://<data-gateway>.up.railway.app/:path*" },
    { "source": "/api/iv/:path*",     "destination": "https://<interview-core>.up.railway.app/:path*" },
    { "source": "/api/fb/:path*",     "destination": "https://<feedback-billing>.up.railway.app/:path*" },
    { "source": "/api/ad/:path*",     "destination": "https://<admin-ops>.up.railway.app/:path*" }
  ]
}
```

Then set the frontend `VITE_*` URLs to **same-origin relative-ish prefixes** (full origin is `https://<app>.vercel.app`):
```
VITE_API_BASE_URL=https://<app>.vercel.app/api/dg
VITE_INTERVIEW_API_URL=https://<app>.vercel.app/api/iv
VITE_FEEDBACK_API_URL=https://<app>.vercel.app/api/fb
VITE_ADMIN_API_URL=https://<app>.vercel.app/api/ad
VITE_USE_MOCK=false
VITE_APP_ENV=production
```

Cookie settings with the proxy (cookies are now first-party `*.vercel.app`):
| Setting | data_gateway value | Reason |
|---|---|---|
| `AUTH_COOKIE_SECURE` | `true` | HTTPS only (also enforced by the prod gate in config.py) |
| `AUTH_COOKIE_SAMESITE` | `lax` | First-party now; `lax` is enough and stronger than `none` |
| `AUTH_COOKIE_DOMAIN` | *(leave blank)* | Browser scopes to the request host = the Vercel app host |
| `CORS_ALLOWED_ORIGINS` | `https://<app>.vercel.app` | Even with the proxy, requests arrive with the Vercel Origin |
| `TRUSTED_PROXY_COUNT` | `2` | Vercel edge proxy hop **+** Railway's own proxy hop. See note below. |

Frontend (`VITE_*`): the proxy prefixes above; the LiveKit `wss://` URL is returned by the room-token API and is unaffected — LiveKit media does not go through the Vercel proxy (it's a separate WebRTC connection direct to LiveKit Cloud, which is correct).

> One caveat to verify: Vercel rewrites do forward cookies, but confirm `Set-Cookie` from Railway survives the rewrite in your Vercel project (it does for standard rewrites; test login → refresh end-to-end before going public). If a `Set-Cookie` issue appears, fall back to Alternative B below for the auth service only.

**TRUSTED_PROXY_COUNT with the proxy:** the client IP recorded in the DPDP consent ledger comes from `X-Forwarded-For`. With the Vercel rewrite, the chain reaching `data_gateway` is `client, vercel-edge, railway-proxy`. The config logic trusts the rightmost N hops and takes the entry to their left as the client. That means **`TRUSTED_PROXY_COUNT=2`** for `data_gateway` to correctly recover the real client IP. **Verify empirically** after deploy by recording one consent and confirming the stored hash corresponds to your real IP, because Railway's and Vercel's exact header behavior must be confirmed, not assumed (config.py warns: setting this too high lets a client spoof its IP). If you cannot confirm 2 hops, set `TRUSTED_PROXY_COUNT=1` and accept slightly less precise IP attribution — never set it higher than the proven hop count.

### Alternative A — `SameSite=None; Secure` cross-site cookies (NOT recommended)
Set `AUTH_COOKIE_SAMESITE=none`, `AUTH_COOKIE_SECURE=true`, `AUTH_COOKIE_DOMAIN` blank, and `CORS_ALLOWED_ORIGINS=https://<app>.vercel.app`. The config validator already enforces `none ⇒ secure`. Problem: this is exactly the third-party-cookie path browsers are killing. Safari blocks it today; Chrome is phasing it out. Brittle for a demo you want to "just work." **Avoid.**

### Alternative B — body-token fallback (last resort, weaker)
The refresh endpoint already accepts the refresh token in the JSON body and skips CSRF for body-supplied tokens (`auth.py` `refresh`). You could store the refresh token in JS and POST it in the body. **This negates the `httpOnly` XSS protection** (token becomes readable/stealable by any XSS). Only use if both proxy and SameSite=None fail. Treat as a temporary demo crutch, never for the bid.

**Verdict: use the Vercel rewrite proxy (recommended). No blocker found.**

---

## 3. Production env settings that MUST change from dev

Apply per service in Railway. Defaults below assume the **recommended proxy** design from Section 2.

### All API services (data_gateway, interview_core, feedback_billing, admin_ops) + worker
- `APP_ENV=production`
  - In `interview_core` this also hard-disables any `?token=` query-param auth path (JWT-in-URL leaks into access logs). NOTE: the live transport is already a Bearer-gated `POST /api/rooms/{id}/token` endpoint (`services/interview_core/app/routers/rooms.py`) — the old WS query-param handler is gone — but keep `APP_ENV=production` so the gate stays closed.
  - `app_env` is normalized (lowercased/stripped) in interview_core so `Production`/`PROD` still trips the gate.
- `DATABASE_SSL=require` — Neon is a TLS/pooled endpoint; without this asyncpg connects without SSL. Required on all services that hold `DATABASE_URL`.
- `LOG_LEVEL=INFO` (not `DEBUG`) — avoids verbose logs that risk leaking request detail.

### data_gateway (issues tokens + sets cookies)
- `AUTH_COOKIE_SECURE=true` (config startup-gate **requires** this when `APP_ENV=production`; service won't boot otherwise).
- `AUTH_COOKIE_SAMESITE=lax` (proxy design) — or `none` only if you took Alternative A.
- `AUTH_COOKIE_DOMAIN=` (blank).
- `CORS_ALLOWED_ORIGINS=https://<app>.vercel.app` (exact deployed origin, no trailing slash, no wildcard — the validator rejects `*`).
- `TRUSTED_PROXY_COUNT=2` (proxy: Vercel edge + Railway) — verify empirically (Section 2).
- `CONSENT_IP_SALT=<the generated value>` (Section 1).

### interview_core, feedback_billing, admin_ops
- `CORS_ALLOWED_ORIGINS=https://<app>.vercel.app`.
- `TRUSTED_PROXY_COUNT=2` where the field exists (interview_core has it; default is 1) — same Vercel+Railway reasoning. Verify.
- `JWT_SECRET` identical to data_gateway (Section 1).
- **`feedback_billing` has an insecure default `JWT_SECRET="change-me-to-random-64-char-hex"`** in `config.py` (it does NOT fail-fast like the others). You MUST explicitly set the real `JWT_SECRET` here or token validation silently uses the placeholder and breaks/weakens auth. Flagged in Section 5.

### worker (interview_core agent worker — separate Railway service)
- Same `APP_ENV=production`, `JWT_SECRET`, `DATABASE_URL`/`DATABASE_SSL`, all LLM/speech/avatar/LiveKit keys (it runs the live media pipeline — see `services/interview_core/app/worker/interview_worker.py`).

---

## 4. Per-service env-var matrix (KEY NAMES the founder pastes into Railway)

Five Railway services: **data_gateway, interview_core, feedback_billing, admin_ops, worker**. (The worker shares interview_core's codebase/config but runs as its own process; give it the same vars interview_core needs for LLM/speech/avatar/LiveKit/DB.)

Legend: ✅ required · ⬜ optional/feature-gated · — not used

| Env KEY | data_gateway | interview_core | feedback_billing | admin_ops | worker |
|---|---|---|---|---|---|
| `SERVICE_NAME` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `APP_ENV=production` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `LOG_LEVEL=INFO` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `HOST` / `PORT` | ✅ | ✅ | ✅ | ✅ | ⬜ |
| `DATABASE_URL` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `DATABASE_SSL=require` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `REDIS_URL` | ✅ | ✅ | ✅ | ✅ | ⬜ |
| `JWT_SECRET` (identical) | ✅ issues | ✅ validates | ✅ validates | ✅ validates | ✅ validates |
| `JWT_ALGORITHM` / `JWT_ISSUER` / `JWT_AUDIENCE` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `JWT_EXPIRY_HOURS` / `JWT_REFRESH_EXPIRY_DAYS` / `PASSWORD_HASH_ROUNDS` | ✅ | — | — | — | — |
| `AUTH_PROVIDER=local` | ✅ | — | — | — | — |
| `AUTH_COOKIE_SECURE=true` | ✅ | — | — | — | — |
| `AUTH_COOKIE_SAMESITE` | ✅ | — | — | — | — |
| `AUTH_COOKIE_DOMAIN` (blank) | ✅ | — | — | — | — |
| `CONSENT_IP_SALT` | ✅ | — | — | — | — |
| `RETENTION_DAYS` / `RETENTION_DRY_RUN` / `RETENTION_CRON_HOUR` | ✅ | — | — | — | — |
| `RATE_LIMIT_LOGIN_PER_MINUTE` / `RATE_LIMIT_API_PER_MINUTE` | ⬜ (see §5 — not yet enforced) | — | — | — | — |
| `CORS_ALLOWED_ORIGINS` | ✅ | ✅ | ✅ | ✅ | — |
| `TRUSTED_PROXY_COUNT` | ✅ | ✅ | — | — | — |
| `SMTP_HOST/PORT/USER/PASSWORD/USE_TLS` + `EMAIL_FROM` | ✅ (Resend) | — | ✅ (Resend) | — | — |
| `LLM_PROVIDER` | — | ✅ | ✅ | — | ✅ |
| `GEMINI_API_KEY` / `GEMINI_MODEL` / `GEMINI_MAX_TOKENS` / `GEMINI_API_BASE_URL` | — | ✅ | ✅ | — | ✅ |
| `GROQ_API_KEY` / `GROQ_MODEL` | — | ⬜ | — | — | ⬜ |
| `ANTHROPIC_API_KEY` + model vars | — | ⬜ | ⬜ | — | ⬜ |
| `SPEECH_STT_PROVIDER` / `SPEECH_TTS_PROVIDER` | — | ✅ | — | — | ✅ |
| `SARVAM_API_KEY` / `SARVAM_STT_MODEL` / `SARVAM_TTS_MODEL` | — | ✅ | — | — | ✅ |
| `OPENAI_API_KEY` | — | ⬜ (Whisper) | ✅ (embeddings) | — | ⬜ |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | — | — | ✅ | — | — |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | — | ✅ | — | — | ✅ |
| `AVATAR_PROVIDER` | — | ✅ | — | — | ✅ |
| `TAVUS_API_KEY` / `TAVUS_REPLICA_ID` / `TAVUS_PERSONA_ID` / `TAVUS_API_URL` | — | ✅ (if tavus) | — | — | ✅ (if tavus) |
| `SIMLI_API_KEY` / `SIMLI_FACE_ID` | — | ⬜ (if simli) | — | — | ⬜ (if simli) |
| `S3_ENDPOINT` / `S3_REGION` / `S3_BUCKET_NAME` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_USE_SSL=true` | ✅ uploads | ✅ audio | ✅ scorecards | ✅ | ⬜ |
| `FEEDBACK_BILLING_URL` | — | ✅ (https Railway URL of feedback_billing) | — | — | ✅ |
| `FEATURE_BILLING=false` + `RAZORPAY_*` (blank) | — | — | ✅ | — | — |
| `SENTRY_DSN` | ⬜ (see §5 DPDP) | ⬜ | ⬜ | ⬜ | ⬜ |

> Notes: `feedback_billing` S3 var names differ — its config uses `S3_ENDPOINT_URL` and `S3_SCORECARD_BUCKET` (confirm against `services/feedback_billing/app/config.py` when pasting). `FEEDBACK_BILLING_URL` on interview_core/worker must be the **internal or public HTTPS Railway URL** of the feedback_billing service (used for fire-and-forget scoring), not localhost.

---

## 5. Pre-public GO / NO-GO checklist

### Secrets not in repo (VERIFIED)
- [x] `.gitignore` ignores `.env`, `.env.local`, `*.pem`, `*.key`, `secrets/`, `credentials.json`.
- [x] `git ls-files | grep '\.env$'` returns **nothing** — only `*.env.example` templates are tracked. No real `.env` is committed.
- [ ] Before going public, run a secret scan (`gitleaks detect`) on the full history as belt-and-suspenders; if any key was ever committed in an old commit it lives in history and MUST be rotated (Section 1 already assumes burned).
- [ ] Confirm `web/.env` (frontend) contains **no secrets** — only `VITE_*` public values (it does today; client.ts confirms all `VITE_*` are URLs/flags). Anything in `VITE_*` ships to the browser.

### Config correctness
- [ ] `feedback_billing` `JWT_SECRET` explicitly set (it has an insecure DEFAULT placeholder and will NOT fail-fast — see §3). HIGH priority.
- [ ] `JWT_SECRET` byte-identical across all 5 services (test: log in on frontend, then load a scorecard from feedback_billing — a mismatch 401s).
- [ ] `DATABASE_SSL=require` on all 5 (Neon).
- [ ] `APP_ENV=production` on all 5 (data_gateway cookie gate will refuse to boot if `AUTH_COOKIE_SECURE` is not also true — that's intended).
- [ ] `CORS_ALLOWED_ORIGINS` = exact `https://<app>.vercel.app` on all 4 APIs (no `*`, no trailing slash).
- [ ] `RETENTION_DRY_RUN` — keep `true` for the demo unless you have run a dry-run cycle and confirmed delete counts. The DPDP retention cron (`services/data_gateway/app/retention.py`) purges completed sessions older than `RETENTION_DAYS`; live deletes only after dry-run validation.

### Auth / cross-origin
- [ ] Vercel rewrite proxy live (`web/vercel.json`) and `VITE_*` point at the same-origin `/api/*` prefixes (Section 2).
- [ ] End-to-end test BEFORE public: register → login → reload page → token refresh works (proves `csrf_token` is readable and `refresh_token` is sent first-party).
- [ ] `TRUSTED_PROXY_COUNT` verified against real client IP (Section 2), not assumed.

### Admin user seeding
- [ ] Seed the first admin AFTER deploy: register a normal account via the UI, then run `services/admin_ops/scripts/grant_admin.py <email>` against the production DB (it is marked "NOT for production / local convenience" — acceptable as a one-off manual bootstrap, but do it over a trusted connection and do not leave it in any automated path).
- [ ] Verify admin-only endpoints (admin_ops analytics, erasure) reject non-admin tokens — admin role check lives in `services/admin_ops/app/admin_auth.py`.

### Authn/authz coverage (OWASP A01 / A07)
- [x] Room-token endpoint enforces auth + ownership + DPDP consent server-side (`rooms.py`) — good, can't bypass via curl.
- [x] CSRF double-submit on `/auth/refresh` (auth.py).
- [x] Refresh token delivered only via httpOnly cookie; access token Bearer-only; refresh token NOT in JSON body.
- [ ] **A07 — login rate limiting is NOT enforced.** `RATE_LIMIT_LOGIN_PER_MINUTE` exists in config but no limiter (slowapi/Redis) is wired to `/auth/login` or `/auth/register`. For a public demo this allows credential brute-force / registration spam. MITIGATION before public: enable Railway/Cloudflare-level rate limiting on `/auth/*`, OR keep the demo gated (invite-only accounts, low traffic), OR accept the risk as a documented demo limitation. Track for production fix.

### Known deferred hardening (documented in code, acceptable for demo, NOT for bid)
- Refresh-token reuse/lineage detection deferred (`auth.py` SECURITY TODO (a)) — a stolen, already-rotated refresh token is not family-revoked.
- User→refresh-keys reverse index deferred (`auth.py` SECURITY TODO (b)) — "log out all devices" and atomic DPDP erasure of live sessions rely on a SCAN today.
- No WAF / no per-IP global rate limit at the edge — add Cloudflare in front (free) before any sustained public exposure.

### DPDP Act 2023 / India data residency
- **Consent ledger:** PASS — `POST /consent` writes `dpdp_consent_ledger`; IPs are sha256(`CONSENT_IP_SALT`)-hashed, never raw (`config.py` §S3-011, `consent.py`). Room-token endpoint blocks interviews without active consent.
- **Right to erasure:** PASS (functional) — endpoint exists at `services/admin_ops/app/routers/erasure.py` with backing audit tables (alembic `erasure_audit_tables`). Verify it runs end-to-end against the prod DB and that the consent ledger row is preserved as the immutable audit trail (it must NOT be deleted).
- **Data residency:** **FAIL for production / bid — EXPECTED for demo.** This demo tier sends PII outside India:
  - Neon Postgres → typically `ap-southeast-1` (Singapore) per the env comments, NOT Mumbai.
  - Cloudflare R2 (`S3_REGION=auto`), Upstash, Gemini, Groq, OpenAI, **Tavus (US-hosted)**, LiveKit Cloud — all process candidate audio/text/PII outside India.
  - Sentry (if `SENTRY_DSN` set) is a third-party SaaS that can capture PII in error payloads — **leave `SENTRY_DSN` blank for the public demo** unless a DPO-signed DPA + PII scrubbing is in place.
  - **This is the documented two-tier strategy: the DEMO is explicitly demo-only and NOT APSSDC/Naipunyam bid-compliant.** Production residency (AWS Mumbai, Bhashini, self-hosted LiveKit, custom avatar) is Tier-2. Do not present this demo URL as bid-compliant or use it with real candidate PII at scale.
- **Privacy notice at consent point:** confirm the consent modal text is surfaced before recording (frontend `web/src/api/consent.ts` + modal) and discloses the demo's foreign processors (Gemini/Groq/Tavus/etc.) per the consent-modal wording decision.

### Vulnerable components (OWASP A06)
- [ ] Run `pip-audit` per service venv and `npm audit` in `web/` before go-live; patch any HIGH/CRITICAL. (Could not run a full CVE scan here; schedule it as the final gate.)

---

## Verdict for public demo deploy

**CONDITIONAL GO** — approved as a **demo-only, non-bid, low-traffic** deployment once ALL of the following are true:
1. All Section 1 secrets rotated; `JWT_SECRET` identical across 5 services; `feedback_billing` `JWT_SECRET` explicitly set.
2. Section 2 Vercel proxy live and login→refresh verified end-to-end.
3. Section 3 production env flags set on every service (`APP_ENV=production`, `DATABASE_SSL=require`, secure cookies, locked CORS, verified `TRUSTED_PROXY_COUNT`).
4. Login `/auth/*` rate limiting addressed at the edge (Cloudflare/Railway) OR demo kept invite-only.
5. `pip-audit` / `npm audit` show no unpatched HIGH/CRITICAL.

**BLOCKED for:** any use as a bid-compliant or production system, or any handling of real candidate PII at scale — data residency is FAIL by design for the demo tier. Migrate to Tier-2 (AWS Mumbai) before that.
