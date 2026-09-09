---
title: AntHire AI Interview
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: AntHire — voice-first AI interview platform (demo)
---

# AntHire — Voice-First AI Interview Platform (Hugging Face Space)

**AntHire** is the product. **Intants Private Limited** is the company that
builds it — so the copyright line, `support@intants.com` and "the Intants core"
(the platform-owner team) keep that name deliberately; everything that names the
*product* now reads AntHire.

This repo is the **Hugging Face Spaces deployment** of the platform: the
React frontend, all four FastAPI services, and the LiveKit interview worker run
inside **one free-tier Docker Space** (2 vCPU / 16 GB RAM). State lives in
external free-tier services (Neon Postgres, Upstash Redis, Cloudflare R2,
LiveKit Cloud), so the Space's ephemeral disk is fine.

> Demo-grade by design: free Spaces sleep after ~48 h of inactivity and can
> restart at any time. For production use the single-VM deploy
> (`docker-compose.prod.yml` + `docs/DEPLOY-ORACLE.md`).

## How it works

```
Browser ──HTTPS──▶ HF edge ──▶ :7860 Caddy ──▶ /            React SPA (webdist)
                                        ├──▶ /auth /hr /exam /jobs ...  data_gateway  :8002
                                        ├──▶ /api/scorecards*          feedback_billing :8003
                                        ├──▶ /api/* /ws/*              interview_core :8001
                                        ├──▶ /admin/overview* ...      admin_ops :8004
                                        └──▶ (worker: no port — connects out to LiveKit Cloud)
```

The frontend is built with **empty API base URLs**, so every call is
same-origin and `space/Caddyfile` fans it out — no CORS, first-party cookies.

> **Operator shortcuts:** `space.env` in this folder holds the real,
> consolidated values for every key below — copy-paste them into the Space
> settings. It is gitignored and must never be committed; the committable
> placeholder template is `space.env.example`. Full click-by-click
> instructions: `HF-Deployment-Guide.docx` (no secrets inside, safe to commit).

## Set up (one time)

1. **Create the Space**: New Space → SDK **Docker** → visibility your choice →
   then push this repo to it (or connect via the GitHub sync action below).
2. **Add secrets** (Space → Settings → Variables and secrets). Required — the
   container refuses to boot without these six:

   | Secret | Example / how to get |
   |---|---|
   | `DATABASE_URL` | Neon **pooled** URL, asyncpg form: `postgresql+asyncpg://USER:PASS@...-pooler...neon.tech/db` (no `?sslmode=`) |
   | `REDIS_URL` | Upstash: `rediss://default:PASS@HOST:6379` |
   | `JWT_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `CONSENT_IP_SALT` | same generator — must differ from JWT_SECRET |
   | `EXAM_LINK_SECRET` | same generator |
   | `INTERVIEW_LINK_SECRET` | same generator |

   Needed for **live interviews** (warned if missing, app still boots):
   `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `SARVAM_API_KEY`,
   `GEMINI_API_KEY`. For file/scorecard storage: `S3_ENDPOINT`,
   `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (Cloudflare R2). Optional:
   `SMTP_HOST/PORT/USER/PASSWORD` + `EMAIL_FROM` (Resend), `SENTRY_DSN`,
   `PLATFORM_OWNER_PASSWORD` (initial platform-owner login),
   `JDOODLE_CLIENT_ID`/`JDOODLE_CLIENT_SECRET` (coding exams),
   `OPENAI_API_KEY` (JD/NOS embeddings during scoring).

   Useful variables (defaults in `space/entrypoint.sh`): `AVATAR_PROVIDER`
   (`none` = voice-only default; `tavus`/`simli` need their keys),
   `WORKER_MAX_CONCURRENT_JOBS` (default 2 — sized for 2 vCPU).
3. **Restart the Space.** Boot order: secret check → alembic migrations →
   supervisord starts caddy + 4 services + worker.

## Deploying from GitHub

Push this repo to GitHub, then add a repo secret `HF_TOKEN` (Hugging Face →
Settings → Access Tokens → **write**) and set `HF_SPACE` (e.g. `you/intants`)
— `.github/workflows/sync-to-space.yml` force-pushes `main` to the Space on
every push.

## Files that make this a Space

| File | Purpose |
|---|---|
| `Dockerfile` | one image: web build → 4 isolated venvs (one per service, matching each `requirements.txt` exactly) → runtime with Caddy + supervisord |
| `space/entrypoint.sh` | env defaults, fail-fast secret check, migrations |
| `space/supervisord.conf` | runs the six processes, drain-aware worker stop |
| `space/Caddyfile` | same-origin routing contract (mirror of root `Caddyfile`) |
| `web/src/api/admin.ts` | one-line `??` patch so empty base URL = same origin |

Everything else is the unmodified platform source. Project documentation:
[README-project.md](README-project.md).

## AntHire Phase 2.5 — change checklist

Everything below landed **after** the Phase 2 sign-off. Each shipped item names
the PR that carried it so the claim can be checked against the diff; each open
item names where it is tracked. Nothing here is a plan — ticked means merged to
`main`, unticked means open.

### Shipped

**Candidate ↔ HR bridge** (PR #13)
- [x] Public per-company careers board — `GET /careers/{slug}` (unauthenticated)
- [x] Signed-in cross-tenant feed — `GET /users/me/open-roles`; a candidate no
      longer needs somebody to send them a link
- [x] Account activation from a guest application + a candidate applications
      page. **No score or threshold is ever returned to the person it describes.**
- [x] HR side: pipeline board, attention panel, decision queue, requisition
      dashboard, screening-question authoring

**Live provider wiring — four integrations that failed silently** (PRs #13, #14)
- [x] Every emailed candidate link opened a dead port: `APP_BASE_URL` /
      `EXAM_LINK_BASE_URL` / `INTERVIEW_LINK_BASE_URL` did not follow the dev
      server to `:5174` when CORS did. Now asserted — a link base must open an
      origin CORS allows.
- [x] Sarvam retired `bulbul:v2`, so the interviewer could not speak; the config
      default lagged `sarvam_tts.py`, and the default is what wins at runtime.
- [x] Google retired `gemini-2.5-flash` (HTTP 404 for new users) across all three
      services → `gemini-flash-lite-latest`, deliberately a floating alias since a
      hard pin is what expired. Fixed again in the deploy targets that override a
      pydantic default: `space/entrypoint.sh`, `render.yaml`, both `.env.example`.
- [x] `why-match` returned 502 on every call — the prompt asked for a bare
      sentence while `call_llm_json` demanded an object. Tests now derive their
      fixture from the prompt, so the two halves cannot drift apart again.
- [x] JDoodle credentials wired, so coding rounds execute; `admin_ops` pins
      `tzdata` (it is the service that calls `ZoneInfo`)

**DPDP erasure coverage** (PR #13)
- [x] 11 tables were in neither `ERASED_TABLES` nor `EXCLUDED_TABLES`; all now
      declared with reasons
- [x] `application_answers` is deleted — candidate-authored prose keeps
      identifying them after `applicants` is anonymised
- [x] `enrolments.scored_resume_s3_key` collected, so excluding that row no
      longer orphans a resume in the bucket
- [x] `.local-cvs/` gitignored — it holds real candidate CVs

**Phase 2 / UI-UX specification gaps** (PR #15)
- [x] **AC-12** — closing a requisition while people are mid-process now refuses
      with 409 carrying the count and the decision-queue path;
      `acknowledge_unresolved` closes anyway and the audit entry records
      `closed_with_unresolved`. The console renders the refusal as a prompt, not
      an error toast — a 409 here is a question.
- [x] **§7 / §6.5** — `GET /hr/applicants/{id}/round-results` (superseded retakes
      excluded) plus a drawer rendering per-criterion scores and their evidence
      *separately* from the frozen axes. `passed` renders as "advanced / held for
      your decision", never pass/fail.
- [x] **E5** — the 25-file bulk cap existed because of in-request scoring that
      Group E already moved to the reconciler. Now a request-size bound: 500
      files or 250 MB, 413 rather than 400. A 200-CV graduate intake goes through.
- [x] **A4** — `applicants.upload_batch_id` (partial index, pending rows only)
      gives the reconciler a handle, and it emits one notification when a batch's
      last row finishes. Not a batches table: a batch has no state beyond its rows.
- [x] **E3** — `delivery_risk()` projects the observed hire rate to the closing
      date. Arithmetic, not a model. Returns `None` — rendered as nothing, never
      as "fine" — when the question cannot honestly be asked. Only `at_risk` and
      `off_track` are badged.
- [x] Job board `min_salary` filter (roles that publish no salary are kept —
      absence is not a mismatch) and `sort`, offered only alongside a query
- [x] `ApiError` carries the response body's structured `detail`; an object
      detail used to render as `[object Object]`

**CI and test safety** (PRs #14, #16)
- [x] The integration suite was writing into the live Neon database the Space
      uses — 571 test rows against 23 real ones, deleted by hand 2026-09-07.
      Collection now aborts unless `DATABASE_URL` is a loopback host;
      `ALLOW_REMOTE_TEST_DB=1` overrides, and shows up in shell history.
- [x] `pypdf` → 6.17.0 (CVE-2026-84309 / -84310 / -84311); the
      `postcss-selector-parser` advisory (GHSA-w9m9-85wc-3x92) cleared inside the
      6.x line, `package.json` untouched
- [x] mypy: `.rowcount` cast to `CursorResult`, `Seniority` literal cast in
      `workflow_tools.py`; 11 new bandit B608 re-read against the source (every
      caller-supplied value is a bound parameter) and baselined
- [x] Green CI restored on `main` — `sync-to-space` runs on `workflow_run` and is
      skipped while CI is red, so `main` and the deployed Space had drifted

Suites at the close of Phase 2.5: 954 `data_gateway` + 555 `interview_core` +
202 `feedback_billing` + 153 `admin_ops` + 773 frontend, with mypy, ruff and
eslint clean and the bandit baseline matching 22/22.

### Remaining — open going into Phase 3

Accepted risks (owner and firing trigger in [`docs/ACCEPTED-RISKS.md`](docs/ACCEPTED-RISKS.md)):
- [ ] **AR-1** — the demo tier is not India-resident. Fires on a
      residency-asserting bid, or on Bedrock Mumbai approval.
- [ ] **AR-2** — one symmetric HS256 secret signs and verifies for every service
- [ ] **AR-3** — candidate-authored code executes on JDoodle, a third party
- [ ] **AR-4** — no production avatar gate, and the Tier-2 avatar is not built.
      `AVATAR_PROVIDER=custom` is not a recognised value: the worker logs
      `unknown avatar_provider=…; falling back to simli` — a WARNING, not a
      startup refusal, so a deploy that sets it still runs on a US-hosted avatar.

Platform work:
- [ ] Bhashini ULCA speech — approval pending; env-swappable via `SPEECH_*_PROVIDER`
- [ ] mypy strict per service. The root `mypy.ini` CI uses is deliberately
      non-strict and its header explains why; the per-service
      `[tool.mypy] strict = true` blocks are never loaded.
- [ ] Tier-2 AWS Mumbai migration — Bedrock, RDS, ElastiCache, S3 (SSE-KMS), SES,
      EKS, Helm + ArgoCD (roadmap Sprints 8–9)
- [ ] Custom avatar — Three.js + Ready Player Me + Rhubarb-Lipsync, replacing the
      hosted vendor (Sprint 10). Hard gate before any government bid.
- [ ] DPDP consent-ledger hardening, penetration test, 20-lakh-user capacity
      proof (Sprint 11)
- [x] The **AntHire** rename is applied across the product surface — UI copy,
      email and PDF headers, the four service titles, `CLAUDE.md` and `docs/`.
      Left on the company name deliberately: `Intants Private Limited`,
      `support@intants.com`, and the JWT issuer / `intants:` storage keys
      (renaming those two signs every session out and drops saved preferences).

---

## Limits to expect on the free tier

- ~1–2 concurrent live interviews (2 vCPU); dashboards/exams scale further.
- Sleeps after ~48 h without visits; first visit rebuilds/wakes it.
- No custom domain on free Spaces; URL is `https://<owner>-<space>.hf.space`.
- Not a data-residency-compliant production home (Spaces run on US/EU infra) —
  fine for demos, not for the govt-bid posture.
