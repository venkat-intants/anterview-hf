---
title: Intants AI Interview
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Voice-first AI interview platform (demo deployment)
---

# Intants AI Voice Interview Platform — Hugging Face Space

This repo is the **Hugging Face Spaces deployment** of the Intants platform: the
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

## Limits to expect on the free tier

- ~1–2 concurrent live interviews (2 vCPU); dashboards/exams scale further.
- Sleeps after ~48 h without visits; first visit rebuilds/wakes it.
- No custom domain on free Spaces; URL is `https://<owner>-<space>.hf.space`.
- Not a data-residency-compliant production home (Spaces run on US/EU infra) —
  fine for demos, not for the govt-bid posture.
