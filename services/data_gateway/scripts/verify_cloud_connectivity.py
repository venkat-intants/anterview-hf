"""Cloud connectivity verification script — Prisma Postgres + Upstash Redis.

Run from services/data_gateway/:
    poetry run python scripts/verify_cloud_connectivity.py

Checks (without starting uvicorn):
  1. Prisma Postgres: connects with SSL + statement_cache_size=0, runs SELECT 1
     and lists all tables created by alembic migrations.
  2. Upstash Redis: connects with TLS (rediss://), runs PING.

Exit code 0 = all checks passed.
Exit code 1 = one or more checks failed (details printed to stdout).
"""

from __future__ import annotations

import asyncio
import os
import sys

# ---------------------------------------------------------------------------
# Resolve service root so we can import app.config regardless of cwd.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVICE_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _SERVICE_ROOT)


async def check_postgres() -> tuple[bool, str]:
    """Connect to Prisma Postgres and run a schema sanity check."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
    except ImportError as exc:
        return False, f"sqlalchemy not installed: {exc}"

    # Read from .env via app.config so this script always uses the same
    # settings as the running service.
    try:
        from app.config import settings
    except Exception as exc:
        return False, f"Failed to load app.config.settings: {exc}"

    url = settings.database_url
    ssl_val = settings.database_ssl

    if not ssl_val:
        return False, (
            "DATABASE_SSL is not set — expected 'require' for Prisma endpoint. "
            "Check .env."
        )

    connect_args = {
        "ssl": ssl_val,
        "statement_cache_size": 0,
    }

    try:
        engine = create_async_engine(
            url,
            connect_args=connect_args,
            poolclass=NullPool,
        )
        async with engine.connect() as conn:
            # Basic connectivity
            row = await conn.execute(text("SELECT 1 AS alive"))
            alive = row.scalar()
            if alive != 1:
                return False, f"SELECT 1 returned unexpected value: {alive!r}"

            # PG version
            row2 = await conn.execute(text("SELECT version()"))
            pg_ver = row2.scalar()

            # List tables (public schema)
            row3 = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' ORDER BY tablename"
                )
            )
            tables = [r[0] for r in row3.fetchall()]

            # Check pgvector extension
            row4 = await conn.execute(
                text(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )
            )
            vec_row = row4.fetchone()
            vec_ver = vec_row[0] if vec_row else "NOT INSTALLED"

        await engine.dispose()

        detail = (
            f"OK — {pg_ver[:60]}\n"
            f"  pgvector: {vec_ver}\n"
            f"  tables ({len(tables)}): {', '.join(tables) if tables else '(none — run alembic upgrade head)'}"
        )
        return True, detail

    except Exception as exc:
        return False, f"Postgres connection error: {type(exc).__name__}: {exc}"


async def check_redis() -> tuple[bool, str]:
    """Connect to Upstash Redis over TLS and run PING."""
    try:
        import redis.asyncio as aioredis
    except ImportError as exc:
        return False, f"redis-py not installed: {exc}"

    try:
        from app.config import settings
    except Exception as exc:
        return False, f"Failed to load app.config.settings: {exc}"

    url = settings.redis_url

    if not url.startswith("rediss://"):
        return False, (
            f"REDIS_URL does not use rediss:// scheme (got: {url[:40]}...). "
            "Upstash requires TLS. Check .env."
        )

    try:
        client = aioredis.from_url(url, decode_responses=True)  # type: ignore[no-untyped-call]
        pong = await client.ping()
        await client.aclose()
        if pong:
            return True, f"OK — PONG received from {url.split('@')[-1]}"
        return False, "PING returned falsy value"
    except Exception as exc:
        return False, f"Redis connection error: {type(exc).__name__}: {exc}"


async def main() -> int:
    print("=" * 60)
    print("Intants cloud connectivity check")
    print("=" * 60)

    pg_ok, pg_msg = await check_postgres()
    redis_ok, redis_msg = await check_redis()

    print(f"\n[{'PASS' if pg_ok else 'FAIL'}] Prisma Postgres")
    for line in pg_msg.splitlines():
        print(f"       {line}")

    print(f"\n[{'PASS' if redis_ok else 'FAIL'}] Upstash Redis")
    for line in redis_msg.splitlines():
        print(f"       {line}")

    print()
    if pg_ok and redis_ok:
        print("All checks PASSED.")
        return 0
    else:
        print("One or more checks FAILED — review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
