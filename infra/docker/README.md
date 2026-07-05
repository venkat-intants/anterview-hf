# Local Dev Stack (OPTIONAL / LEGACY — backing services only)

> **Heads-up — this is NOT the path the demo uses.**
> The current demo runs on **cloud managed services** (Neon Postgres, Upstash Redis,
> Cloudflare R2 / Backblaze) — there is **nothing to start locally** for a normal run.
>
> - To run the **whole app in Docker** against the cloud `.env` files, use the
>   **`docker-compose.yml` at the repo root** (it builds + runs the 5 app services).
> - This file (`infra/docker/docker-compose.yml`) only starts **local backing
>   services** (Postgres / Redis / MinIO / Mailpit) and is for fully-offline dev.
>   Do **not** run it alongside the root compose — they are different stacks.

The local backing stack below starts: Postgres + pgvector, Redis, MinIO (S3-compatible), and Mailpit (SMTP catcher).

---

## Prerequisites

- **Docker Desktop** for Windows (or Docker Engine on Mac/Linux)
- Ports `5432`, `6379`, `9000`, `9001`, `1025`, `8025` must be free on your machine

Check Docker is installed:
```powershell
docker --version
docker compose version
```

---

## Start

From the project root (`c:\Intants\ai_online_interview`):

```powershell
docker compose -f infra/docker/docker-compose.yml up -d
```

`-d` runs in background. First run takes 2–5 minutes (downloads images).

---

## Verify everything is healthy

```powershell
docker compose -f infra/docker/docker-compose.yml ps
```

You should see:
- `intants-postgres`  → `(healthy)`
- `intants-redis`     → `(healthy)`
- `intants-minio`     → `(healthy)`
- `intants-mailpit`   → `Up`
- `intants-minio-init` → `Exited (0)` (this one runs once and exits — correct behavior)

---

## Access URLs

| Service | URL | Credentials |
|---|---|---|
| Postgres | `localhost:5432` (DB clients) | `intants` / `intants_dev_pw` / db `intants_interview` |
| Redis | `localhost:6379` | none |
| MinIO API (S3) | `http://localhost:9000` | `minioadmin` / `minioadmin` |
| MinIO Console (browser) | `http://localhost:9001` | `minioadmin` / `minioadmin` |
| Mailpit Web UI | `http://localhost:8025` | none (any email sent locally appears here) |

---

## Common commands

```powershell
# View logs from all services (Ctrl+C to exit)
docker compose -f infra/docker/docker-compose.yml logs -f

# View logs from one service
docker compose -f infra/docker/docker-compose.yml logs -f postgres

# Restart one service
docker compose -f infra/docker/docker-compose.yml restart redis

# Stop everything (keeps data)
docker compose -f infra/docker/docker-compose.yml down

# WIPE EVERYTHING (deletes all data — useful for fresh start)
docker compose -f infra/docker/docker-compose.yml down -v

# Connect to Postgres shell
docker exec -it intants-postgres psql -U intants -d intants_interview

# Connect to Redis shell
docker exec -it intants-redis redis-cli
```

---

## What gets created automatically

On first run:
- Postgres database `intants_interview` with extensions: `vector`, `pg_trgm`, `pgcrypto`, `uuid-ossp`
- MinIO buckets: `intants-interview-audio`, `intants-interview-scorecards`
- Empty Redis instance
- Empty Mailpit inbox

---

## Production parallels

| Local (this stack) | Demo (Tier 1) | Production (Tier 2) |
|---|---|---|
| Postgres + pgvector | Neon | AWS RDS Mumbai |
| Redis | Upstash | AWS ElastiCache Mumbai |
| MinIO | Cloudflare R2 | AWS S3 Mumbai |
| Mailpit | Resend | AWS SES Mumbai |

The application code is identical — only `.env` values change.

---

## Troubleshooting

**"Port already allocated" error:**
Another service is using that port. Either stop the other service, or change the port mapping in `docker-compose.yml` (`"5433:5432"` to use 5433 on host).

**Postgres won't start / "database does not exist":**
Volume may be in a bad state. Wipe with `docker compose down -v` and re-run `docker compose up -d`.

**Slow startup:**
First run downloads ~1.5 GB of images. Subsequent runs are instant.

**Docker not found on Windows:**
Install Docker Desktop from https://www.docker.com/products/docker-desktop/
