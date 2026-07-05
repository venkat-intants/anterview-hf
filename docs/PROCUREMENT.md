# Procurement Checklist

We adopted a **two-tier strategy** (decided 2026-05-27):

- **Tier 1 — Demo stack:** Fast, free/cheap, managed services. Ships a live demo URL in 10–14 days. Use for: demo, first 50–100 paying customers, investor pitches, college sales.
- **Tier 2 — Production stack:** AWS Mumbai + custom avatar. Required for DPDP compliance + L1 govt bid economics. Migrate when revenue or compliance demands it.

Same codebase, same `.env` interface — just different values.

---

## TIER 1 — Demo Stack (sign up THIS WEEK)

| # | Service | URL | Time | Cost | Used by |
|---|---|---|---|---|---|
| 🔴 1 | **Anthropic API** (Claude Sonnet 4.6) | https://console.anthropic.com/ | 10 min | $5 prepaid (set $50/mo cap) | `interview_core`, `feedback_billing` |
| 🔴 2 | **Bhashini ULCA** (STT/TTS for HI/TE/EN) | https://bhashini.gov.in/ulca/user/register | Same day | **Free** (Govt) | `interview_core` |
| 🔴 3 | **HeyGen** (interactive avatar) | https://app.heygen.com/ → Subscriptions → API | 30 min | Pay-as-you-go (~₹15/min) | `interview_core` |
| 🟡 4 | **Neon** (managed Postgres + pgvector) | https://neon.tech | 10 min | Free tier (0.5 GB) | all 4 services |
| 🟡 5 | **Upstash** (serverless Redis) | https://upstash.com | 10 min | Free tier (10k cmds/day) | all 4 services |
| 🟡 6 | **Cloudflare R2** (S3-compatible storage) | https://dash.cloudflare.com → R2 | 15 min | Free tier (10 GB) | `interview_core`, `feedback_billing`, `admin_ops` |
| 🟡 7 | **Resend** (transactional email) | https://resend.com | 10 min | Free tier (100/day) | `data_gateway`, `feedback_billing` |
| 🟡 8 | **OpenAI** (text embeddings only) | https://platform.openai.com | 10 min | $50/mo cap | `feedback_billing` |
| 🟡 9 | **Google OAuth** | https://console.cloud.google.com | 30 min | Free | `data_gateway` |
| 🟢 10 | **Vercel** (frontend hosting) | https://vercel.com | 10 min | Free tier (hobby) | `web` |
| 🟢 11 | **Railway** (backend hosting) | https://railway.app | 15 min | $5/mo per service | all 4 services |
| 🟢 12 | **Sentry** (error tracking) | https://sentry.io/signup | 5 min | Free tier (5k/mo) | all services |
| 🟢 13 | **Domain name** | godaddy / namecheap | 10 min | ~₹1,000/yr | production DNS |

**Total monthly cost for demo: ~$20–40 (₹1,800–3,500)** plus per-session Claude + HeyGen usage.

**Total sign-up time if you batch: ~3–4 hours of admin work.**

---

## TIER 2 — Production Stack (migrate later, post-revenue or pre-govt-bid)

| Component | Service | Why migrate |
|---|---|---|
| LLM | AWS Bedrock Mumbai (Claude Sonnet 4.6) | DPDP — India data residency |
| Database | AWS RDS Mumbai (Postgres 16 + pgvector) | DPDP residency + production scale |
| Cache | AWS ElastiCache Mumbai (Redis 7) | DPDP residency |
| Storage | AWS S3 Mumbai (SSE-KMS) | DPDP residency + lifecycle policies |
| Email | AWS SES Mumbai | Volume + deliverability |
| Hosting | AWS EKS Mumbai (Multi-AZ) | Scale, redundancy, compliance |
| Avatar | Custom Three.js + Ready Player Me + Rhubarb-Lipsync | Per-session cost drops from ₹150 → ₹5 |
| Payments | Razorpay | Indian businesses + govt invoicing |
| WAF/CDN | Cloudflare (already in demo, just promote) | DDoS + edge caching |

Estimated migration effort: **5–10 engineering days** when triggered.

---

## Per-Service `.env` Files (where credentials go)

| Service | File | Port | Demo values needed |
|---|---|---|---|
| `interview_core` | `services/interview_core/.env.example` | 8001 | Anthropic, Bhashini, HeyGen, R2, JWT_SECRET |
| `data_gateway` | `services/data_gateway/.env.example` | 8002 | Neon URL, Upstash URL, JWT_SECRET, Resend (optional), Google OAuth (optional) |
| `feedback_billing` | `services/feedback_billing/.env.example` | 8003 | Anthropic, OpenAI, R2, JWT_SECRET, Resend |
| `admin_ops` | `services/admin_ops/.env.example` | 8004 | Neon URL, Upstash URL, R2, JWT_SECRET |
| `web` | `web/.env.example` | 5173 | Backend URLs, HeyGen Avatar ID (not API key — that stays server-side) |

Setup per service:

```bash
cd services/<service-name>
cp .env.example .env
# fill in the demo-tier values from above
```

---

## Shared Secrets (Must Match Across Services)

These values MUST be **identical** in every backend service's `.env`, or auth breaks:

- `JWT_SECRET` — generate once via `openssl rand -hex 32`, paste into all 4 backend services
- `JWT_ALGORITHM` — keep at `HS256` everywhere

---

## Step-by-Step Sign-up (Demo Tier)

### 1. Anthropic API
1. https://console.anthropic.com/ → sign up + verify
2. Settings → Billing → add $5 credit, set $50/mo cap
3. API Keys → create → copy `sk-ant-…`
4. Paste into `services/interview_core/.env` and `services/feedback_billing/.env` as `ANTHROPIC_API_KEY`

### 2. Bhashini ULCA
1. https://bhashini.gov.in/ulca/user/register → register, verify email
2. Login → Create Application → copy `USER_ID` and `API_KEY`
3. Browse Pipelines → pick ASR + TTS pipeline IDs for `en`, `hi`, `te`
4. Paste all into `services/interview_core/.env`

### 3. HeyGen (Interactive Avatar)
1. https://app.heygen.com/ → sign up
2. Settings → Subscriptions → choose API plan
3. API Access → generate key → copy
4. Avatar Library → pick an avatar (or upload custom) → copy `avatar_id`
5. Paste `HEYGEN_API_KEY` + `HEYGEN_AVATAR_ID` into `services/interview_core/.env`
6. Paste `HEYGEN_AVATAR_ID` into `web/.env` as `VITE_HEYGEN_AVATAR_ID` (just for display)

### 4. Neon (Postgres)
1. https://neon.tech → sign up with GitHub
2. Create project "intants-interview" → choose region closest to your demo target (e.g., `aws-ap-southeast-1` for India proximity)
3. Connection details → copy the connection string (starts with `postgresql://`)
4. Convert prefix: `postgresql://` → `postgresql+asyncpg://`
5. Paste as `DATABASE_URL` in all 4 backend services' `.env`
6. Enable `pgvector` extension: SQL Editor → `CREATE EXTENSION IF NOT EXISTS vector;`

### 5. Upstash (Redis)
1. https://upstash.com → sign up with GitHub
2. Create database "intants-cache" → region = closest to your backend
3. Copy the `rediss://` URL (TLS)
4. Paste as `REDIS_URL` in all 4 services' `.env` — change the DB number per service (`/0` `/1` `/2` `/3`)

### 6. Cloudflare R2
1. https://dash.cloudflare.com → sign up
2. R2 → Create bucket "intants-interview-audio" + "intants-interview-scorecards"
3. Manage R2 API Tokens → create token with Read+Write for those buckets
4. Copy: Account ID, Access Key ID, Secret Access Key
5. Paste into `interview_core`, `feedback_billing`, `admin_ops` `.env` as:
   - `S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com`
   - `S3_ACCESS_KEY_ID=<key>`
   - `S3_SECRET_ACCESS_KEY=<secret>`
   - `S3_REGION=auto`
   - `S3_USE_SSL=true`

### 7. Resend (Email)
1. https://resend.com → sign up
2. Verify a domain (or use their test domain for demo)
3. API Keys → create → copy `re_xxx`
4. Paste into `data_gateway` and `feedback_billing` `.env`:
   - `SMTP_HOST=smtp.resend.com`
   - `SMTP_PORT=465`
   - `SMTP_USER=resend`
   - `SMTP_PASSWORD=re_xxx`
   - `SMTP_USE_TLS=true`

### 8. OpenAI
1. https://platform.openai.com/signup
2. Add payment, set $50/mo cap
3. https://platform.openai.com/api-keys → create
4. Paste into `feedback_billing/.env` as `OPENAI_API_KEY`

### 9. Google OAuth
1. https://console.cloud.google.com → new project "Intants Interview"
2. APIs & Services → Credentials → Create OAuth 2.0 Client (Web app)
3. Authorized redirect URI: `http://localhost:8002/auth/google/callback` + your deployed URL
4. Copy Client ID + Secret → paste into `data_gateway/.env`

### 10. Vercel (frontend deploy)
1. https://vercel.com → sign up with GitHub
2. Import the repo (or `web/` folder) → auto-deploys on push
3. Add env vars from `web/.env.example` in Vercel project settings

### 11. Railway (backend deploy)
1. https://railway.app → sign up with GitHub
2. New Project → Deploy from repo
3. Add 4 services (one per backend) → each maps to a different folder
4. Add env vars per service in Railway dashboard
5. $5/month per service

### 12. Sentry
1. https://sentry.io/signup
2. Create 5 projects (one per service + web)
3. Copy each DSN → paste into the matching `.env` as `SENTRY_DSN` / `VITE_SENTRY_DSN`

---

## Local-Dev-Only Services (no sign-up needed)

These run via `docker-compose` in `infra/docker/`:
- PostgreSQL 16 + pgvector (local Postgres for offline development)
- Redis 7 (local cache)
- MinIO (S3-compatible — local storage)
- Mailpit (catches outbound email locally)

Defaults in `.env.example` files work out-of-the-box with the docker-compose stack.

---

## Security Reminder

- `.env` files **NEVER** committed (in `.gitignore`, matches any depth)
- `.env.example` files **ARE** committed (templates only, no real values)
- Real credentials live ONLY on dev machines + in deploy-platform secret stores (Railway env vars, Vercel env vars, K8s Secrets in prod)
- Rotate all API keys + `JWT_SECRET` quarterly
- HeyGen API key stays **server-side only** — never exposed to frontend
