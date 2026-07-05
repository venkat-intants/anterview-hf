"""System health aggregator — backs the AdminOverview status board.

Pings the peer microservices' /health/live, checks admin_ops's own Postgres +
Redis, and returns a single operational/degraded/down rollup. Admin-only.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.admin_auth import verify_admin_role
from app.config import settings
from app.health import _check_postgres, _check_redis

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["system"], dependencies=[Depends(verify_admin_role)])


class ServiceHealth(BaseModel):
    name: str
    kind: str  # service | datastore
    status: str  # operational | degraded | down
    latency_ms: int | None = None
    detail: str | None = None


class SystemHealth(BaseModel):
    overall: str  # operational | degraded
    services: list[ServiceHealth]
    checked_at: str


_PEERS: list[tuple[str, str]] = [
    ("interview_core", settings.interview_core_url),
    ("data_gateway", settings.data_gateway_url),
    ("feedback_billing", settings.feedback_billing_url),
]


async def _ping(client: httpx.AsyncClient, name: str, base: str) -> ServiceHealth:
    url = f"{base.rstrip('/')}/health/live"
    start = time.perf_counter()
    try:
        resp = await client.get(url, timeout=3.0)
        latency = int((time.perf_counter() - start) * 1000)
        ok = resp.status_code == 200
        return ServiceHealth(
            name=name,
            kind="service",
            status="operational" if ok else "degraded",
            latency_ms=latency,
            detail=None if ok else f"HTTP {resp.status_code}",
        )
    except Exception as exc:  # noqa: BLE001 — any failure = down, never raise
        return ServiceHealth(
            name=name, kind="service", status="down", latency_ms=None, detail=type(exc).__name__
        )


@router.get("/system/health", response_model=SystemHealth)
async def system_health() -> SystemHealth:
    async with httpx.AsyncClient() as client:
        peer_results = await asyncio.gather(*[_ping(client, n, b) for n, b in _PEERS])

    pg, rd = await asyncio.gather(_check_postgres(), _check_redis())

    services: list[ServiceHealth] = [
        # admin_ops answered this request, so it is operational by definition.
        ServiceHealth(name="admin_ops", kind="service", status="operational", latency_ms=0),
        *peer_results,
        ServiceHealth(
            name="postgres",
            kind="datastore",
            status="operational" if pg.get("ok") else "down",
            detail=None if pg.get("ok") else str(pg.get("error")),
        ),
        ServiceHealth(
            name="redis",
            kind="datastore",
            status="operational" if rd.get("ok") else "down",
            detail=None if rd.get("ok") else str(rd.get("error")),
        ),
    ]
    overall = "operational" if all(s.status == "operational" for s in services) else "degraded"
    return SystemHealth(
        overall=overall, services=services, checked_at=datetime.now(tz=UTC).isoformat()
    )
