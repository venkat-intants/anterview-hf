"""Cloud connectivity check for interview_core — Prisma Postgres + Upstash Redis.

Run from services/interview_core/:
    poetry run python scripts/verify_cloud_connectivity.py
"""

from __future__ import annotations

import asyncio
import os
import sys

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVICE_ROOT)


async def main() -> int:
    import redis.asyncio as aioredis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings

    print("=" * 60)
    print("interview_core cloud connectivity check")
    print("=" * 60)

    all_ok = True

    # --- Postgres ---
    try:
        eng = create_async_engine(
            settings.database_url,
            connect_args={"ssl": settings.database_ssl, "statement_cache_size": 0},
            poolclass=NullPool,
        )
        async with eng.connect() as conn:
            row = await conn.execute(text("SELECT version()"))
            ver = row.scalar()
            row2 = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            )
            tables = [r[0] for r in row2.fetchall()]
        await eng.dispose()
        print("\n[PASS] Postgres")
        print(f"       {ver[:60]}")
        print(f"       tables ({len(tables)}): {', '.join(tables)}")
    except Exception as exc:
        print(f"\n[FAIL] Postgres: {type(exc).__name__}: {exc}")
        all_ok = False

    # --- Redis ---
    try:
        rc = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore
        pong = await rc.ping()
        await rc.aclose()
        print(f"\n[PASS] Redis (Upstash TLS): PONG={pong}")
    except Exception as exc:
        print(f"\n[FAIL] Redis: {type(exc).__name__}: {exc}")
        all_ok = False

    print()
    if all_ok:
        print("All checks PASSED.")
        return 0
    print("One or more checks FAILED.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
