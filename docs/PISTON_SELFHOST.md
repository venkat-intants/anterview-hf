# Self-hosting Piston for the coding round

The coding round runs candidate code in a sandbox via **Piston**. The free
**public** Piston API (`emkc.org`) became **whitelist-only on 2026‑02‑15**, so it
now returns `HTTP 401 "Public Piston API is now whitelist only…"` for everyone.

The fix is to run your **own** Piston — free, no card, no whitelist, and it's also
the path to **India data residency** for production (self-host in Mumbai). The
execution client is swappable, so this is a config change, not a code change.

---

## One-time setup (local dev, Windows)

### 1. Install Docker
Install **Docker Desktop** → https://www.docker.com/products/docker-desktop/ —
then **launch it** and wait until it says "Engine running".

### 2. Start Piston + install the languages
From the repo root:

```powershell
.\scripts\piston-up.ps1
```

This runs the official Piston container on `localhost:2000` (privileged — Piston
sandboxes each run with isolate) and installs every language the coding round
supports (Python, JavaScript, TypeScript, Java, C++, C, Go, C#, Ruby, Rust). The
first run downloads each runtime, so it takes a few minutes.

> `--privileged` is required. On Docker Desktop (WSL2 backend) it works out of the box.

### 3. Point the app at it (already wired)
`services/data_gateway/.env` is already set to:

```
EXECUTION_PROVIDER=piston
PISTON_API_URL=http://localhost:2000/api/v2
```

### 4. Restart data_gateway
So it reads the new `PISTON_API_URL`:

```powershell
# stop the old data_gateway window, then from the repo root:
cd services\data_gateway
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002
```

### 5. Verify
- `curl http://localhost:2000/api/v2/runtimes` lists installed languages.
- In the app, open a coding exam → **Run samples** → you should now get
  pass/fail results instead of the 401.

---

## Managing the container

```powershell
docker ps                    # is piston_api running?
docker logs piston_api       # logs
docker stop piston_api       # stop (start again with scripts\piston-up.ps1)
docker rm -f piston_api      # remove entirely
```

Re-running `scripts\piston-up.ps1` is safe — it starts the existing container and
skips already-installed languages.

---

## Production (Tier-2, India residency)

Run the same Piston image as its **own service** on **AWS Mumbai** (never inside
`data_gateway`), then set `PISTON_API_URL` to that internal URL. Untrusted code
then executes in-region, in an isolated service you scale independently — and the
≤₹12/session cost stays on your own compute.

> **Known v1 limitation:** grading is **synchronous** (it runs every hidden test
> in one request), so a coding exam should keep test counts modest
> (`CODE_MAX_TEST_CASES`, default 20). Moving grading to a background task is the
> Tier-2 improvement (flagged to `cto-architect`).
