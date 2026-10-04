# data_gateway

Auth (pluggable), user management, SSO, DPDP consent, jobs, and resume uploads.

**Status:** Full service. Local auth (register, login, refresh, logout, `/me`), Google SSO + Naipunyam SSO, DPDP consent ledger, jobs, and resume upload are all live. The DPDP retention cron (APScheduler) runs daily.

## Prerequisites

- Python 3.11+
- Poetry (`pip install poetry` or https://python-poetry.org/docs/#installation) — `data_gateway` uses **Poetry's own venv**.
- `.env` filled in (from `.env.example`). DB (Neon / Prisma Postgres) and Redis (Upstash) are **cloud** — no local docker needed.

## Install

```powershell
cd services/data_gateway
poetry install
```

## Run

```powershell
poetry run uvicorn app.main:app --reload --port 8002
```

Open:
- http://localhost:8002/              → service info
- http://localhost:8002/docs          → OpenAPI Swagger UI
- http://localhost:8002/health/live   → liveness ping
- http://localhost:8002/health/deep   → checks Postgres + Redis

## Endpoints (high level)

- **Auth (`/auth`)** — local register / login / refresh / logout / me
- **SSO** — Google OAuth + Naipunyam (govt SSO)
- **Consent** — DPDP consent ledger (IP hashed before storage)
- **Jobs** — job roles for interview selection
- **Resume / JD** — S3-compatible uploads (R2 demo / S3 Mumbai prod)

## DB Migrations

```powershell
# Apply all migrations
poetry run alembic upgrade head

# Roll back one migration -- NAME THE TARGET, do not use -1.
#
# `downgrade -1` fails with "Ambiguous walk" whenever the head is a merge
# revision, which it is: `f1b3d5a7c9e2` joins the PH3-B4b chain and main's
# question-bank chain. It fails CLOSED (nothing is applied, the database is
# untouched), so this is confusion rather than damage -- but it is the command
# somebody reaches for during an incident, so it should not be the one written
# down. An explicit target walks both parents correctly:
poetry run alembic downgrade <revision>

# Create a new migration (autogenerate)
poetry run alembic revision --autogenerate -m "describe change"
```

## Expected `/health/deep` response when everything works

```json
{
  "status": "healthy",
  "checks": {
    "postgres": {"ok": true},
    "redis":    {"ok": true}
  }
}
```
