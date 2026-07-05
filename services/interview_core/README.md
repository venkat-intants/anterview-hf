# interview_core

Real-time AI interview service: **LiveKit (WebRTC) transport + LangGraph brain + Sarvam voice pipeline + real-time avatar** (Tavus / Simli).

**Status:** Full real-time interview service (not a scaffold). It runs as **two processes** — the FastAPI **API** (sessions, room tokens, `/api/avatars`) and a separate **LiveKit worker** (the avatar + voice engine, one job subprocess per interview). The worker calls `cli.run_app(WorkerOptions(...))`, which owns its own process — it cannot live inside the uvicorn API.

## Prerequisites

- Python 3.12+
- The **in-project `.venv`** already provisioned (pip-managed LiveKit stack). Run the `.venv` python directly — do **NOT** `poetry install` here (the `poetry.lock` is out of sync with the LiveKit stack and a sync would uninstall working packages).
- `.env` filled in (from `.env.example`). DB (Neon / Prisma Postgres) and Redis (Upstash) are **cloud** — no local docker needed.

## Run

Both processes (see `dev-up.ps1` at the repo root, which launches the whole stack):

```powershell
cd services/interview_core

# API (:8001)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001

# LiveKit worker (no HTTP port) — start this BEFORE beginning an interview.
# PYTHONUTF8=1 stops the rich-console UnicodeEncodeError log spam on Windows.
$env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe -m app.worker.interview_worker dev
```

> Start the worker before beginning an interview — LiveKit auto-dispatch only assigns a worker to rooms created while it is running.

Open:
- http://localhost:8001/              → service info
- http://localhost:8001/docs          → OpenAPI Swagger UI
- http://localhost:8001/health/live   → liveness ping
- http://localhost:8001/health/deep   → checks dependencies (Postgres, Redis, S3, LLM, Sarvam)
- http://localhost:8001/api/avatars   → the 6 avatars for the picker

## Code layout

- `app/llm/`    — LLM adapters: `gemini` (default), `groq`, `anthropic` (env-swappable via `LLM_PROVIDER`)
- `app/speech/` — Sarvam STT (`saaras:v3`) + Sarvam TTS (`bulbul:v3`)
- `app/worker/` — the LiveKit worker (`interview_worker.py`)
