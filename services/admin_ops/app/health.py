from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.sql import text

from app.config import settings
from app.database import get_session_factory
from app.redis_client import get_redis

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


async def _check_postgres() -> dict[str, Any]:
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.scalar()
        return {"ok": row == 1}
    except Exception as exc:
        log.warning("health.postgres.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _check_redis() -> dict[str, Any]:
    try:
        client = get_redis()
        pong = await client.ping()
        return {"ok": pong is True}
    except Exception as exc:
        log.warning("health.redis.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive", "service": settings.service_name}


@router.get("/deep")
async def deep_health() -> JSONResponse:
    postgres_status, redis_status = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
    )
    checks: dict[str, Any] = {"postgres": postgres_status, "redis": redis_status}
    all_ok = all(c.get("ok") for c in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", **checks},
        status_code=200 if all_ok else 503,
    )
