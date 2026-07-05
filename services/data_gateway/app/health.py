"""Health-check endpoints for data_gateway.

Routes
------
GET /health/live  — liveness probe (no dependency checks, always 200)
GET /health/deep  — deep health: verifies Postgres and Redis are reachable.
                    Returns 200 when all checks pass, 503 when any check fails.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.sql import text

from app.database import get_session_factory
from app.redis_client import get_redis

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


async def _check_postgres() -> dict[str, Any]:
    """Execute ``SELECT 1`` via the shared async engine.

    Returns ``{"ok": True}`` on success, or
    ``{"ok": False, "error": "<ExcType>: <message>"}`` on any failure.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.scalar()
        return {"ok": row == 1}
    except Exception as exc:
        log.warning(
            "health.postgres.fail",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _check_redis() -> dict[str, Any]:
    """Call ``PING`` on the shared Redis singleton.

    Returns ``{"ok": True}`` on success, or
    ``{"ok": False, "error": "<ExcType>: <message>"}`` on any failure.
    """
    try:
        client = get_redis()
        pong = await client.ping()
        return {"ok": pong is True}
    except Exception as exc:
        log.warning(
            "health.redis.fail",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Liveness probe — confirms the process is running."""
    return {"status": "alive"}


@router.get("/deep")
async def deep_health() -> JSONResponse:
    """Deep health check — verifies Postgres and Redis are reachable.

    Response shape::

        {
            "status": "ok" | "degraded",
            "postgres": {"ok": true} | {"ok": false, "error": "..."},
            "redis":    {"ok": true} | {"ok": false, "error": "..."}
        }

    HTTP 200 when all dependencies are healthy, HTTP 503 otherwise.
    """
    postgres_status, redis_status = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
    )
    checks: dict[str, Any] = {
        "postgres": postgres_status,
        "redis": redis_status,
    }
    all_ok = all(c.get("ok") for c in checks.values())
    status_code = 200 if all_ok else 503
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", **checks},
        status_code=status_code,
    )
