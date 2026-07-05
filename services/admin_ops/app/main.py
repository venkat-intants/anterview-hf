"""admin_ops — FastAPI application entry point (S5-002)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager, suppress
from typing import Any

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from shared.observability.sentry import init_sentry

from app.admin_auth import verify_admin_role
from app.config import settings
from app.database import dispose_engine, get_session_factory, init_engine
from app.health import router as health_router
from app.redis_client import close_redis, init_redis

# ---------------------------------------------------------------------------
# PII redaction processor (defense-in-depth — DPDP §8)
#
# Drops known PII field names from every log event dict before rendering.
# This catches cases where a developer accidentally logs a raw field.
# Placed just before JSONRenderer in the processor chain.
#
# Mirrors the same processor in data_gateway/app/main.py and
# interview_core/app/main.py — MUST stay in sync.
# ---------------------------------------------------------------------------
_PII_FIELDS = frozenset({"email", "password", "phone", "full_name"})


def _redact_pii_processor(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Remove PII fields from structlog event dict before rendering."""
    for field in _PII_FIELDS:
        event_dict.pop(field, None)
    return event_dict


structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        _redact_pii_processor,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
)

# Optional Sentry error tracking — no-op unless SENTRY_DSN is set (DPDP-safe scrub).
init_sentry(
    settings.sentry_dsn, environment=settings.app_env, service_name=settings.service_name
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics — custom counters / histograms exposed on GET /metrics
#
# We do NOT use prometheus-fastapi-instrumentator because its latest version
# requires starlette>=1.0 which conflicts with FastAPI 0.115.x (starlette<0.47).
# Instead we define a minimal set of business-relevant metrics here and expose
# them via a plain GET /metrics endpoint using prometheus_client.generate_latest().
# HTTP request counts/latency can be added via middleware if needed later.
# ---------------------------------------------------------------------------
_registry = CollectorRegistry(auto_describe=True)

ERASURE_REQUESTS_COMPLETED = Counter(
    "admin_ops_erasure_requests_completed_total",
    "Total DPDP erasure requests fully executed by the executor",
    registry=_registry,
)

ERASURE_EXECUTOR_ERRORS = Counter(
    "admin_ops_erasure_executor_errors_total",
    "Total errors encountered during the erasure executor poll cycle",
    registry=_registry,
)

ERASURE_EXECUTOR_POLL_DURATION = Histogram(
    "admin_ops_erasure_executor_poll_duration_seconds",
    "Wall-clock duration of each erasure executor poll cycle",
    registry=_registry,
)

# All routes under /admin automatically require admin role.
from fastapi import APIRouter  # noqa: E402

from app.routers.analytics import router as analytics_router  # noqa: E402
from app.routers.erasure import router as erasure_router  # noqa: E402
from app.routers.system import router as system_router  # noqa: E402

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_role)])


@admin_router.get("/status")
async def admin_status() -> dict[str, str]:
    """Admin liveness probe — confirms the admin guard is active."""
    return {"status": "ok", "service": settings.service_name}


# ---------------------------------------------------------------------------
# Instrumented erasure poll — wraps run_erasure_poll and updates metrics
# ---------------------------------------------------------------------------


async def _instrumented_erasure_task() -> None:
    """Wrapper around erasure_executor_task that records Prometheus metrics."""
    import uuid as _uuid_mod

    from app.erasure_executor import run_erasure_poll  # local import to avoid circular
    actor = _uuid_mod.UUID("00000000-0000-0000-0000-000000000001")
    log.info(
        "erasure.executor.started",
        poll_interval_seconds=settings.erasure_poll_interval_seconds,
        system_actor_id=str(actor),
    )
    while True:
        try:
            with ERASURE_EXECUTOR_POLL_DURATION.time():
                completed = await run_erasure_poll(
                    session_factory=get_session_factory(),
                    system_actor_id=actor,
                    settings=settings,
                )
            if completed:
                ERASURE_REQUESTS_COMPLETED.inc(completed)
                log.info(
                    "erasure.executor.cycle_complete",
                    completed=completed,
                )
        except asyncio.CancelledError:
            log.info("erasure.executor.cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 — polling errors must not kill the task
            ERASURE_EXECUTOR_ERRORS.inc()
            log.error(
                "erasure.executor.poll_error",
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
        await asyncio.sleep(settings.erasure_poll_interval_seconds)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    init_engine()
    init_redis()
    log.info("service.start", service=settings.service_name, env=settings.app_env)

    # Start the DPDP erasure executor as a background task.
    # It runs every settings.erasure_poll_interval_seconds (default 300 s).
    erasure_task = asyncio.create_task(
        _instrumented_erasure_task(),
        name="dpdp_erasure_executor",
    )
    application.state.erasure_task = erasure_task
    log.info(
        "erasure.executor.task_created",
        poll_interval_seconds=settings.erasure_poll_interval_seconds,
    )

    yield

    # Cancel the background task gracefully on shutdown.
    erasure_task.cancel()
    with suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(erasure_task), timeout=5.0)

    await dispose_engine()
    await close_redis()
    log.info("service.stop", service=settings.service_name)


app = FastAPI(
    title="Intants Admin & Ops",
    description="Admin dashboard APIs, analytics, DPDP erasure",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(erasure_router)
app.include_router(analytics_router)
app.include_router(system_router)


@app.get(
    "/metrics",
    tags=["observability"],
    summary="Prometheus metrics endpoint",
    description=(
        "Exposes Prometheus-format metrics: erasure executor counters, "
        "poll duration histogram, and any future business metrics. "
        "Not protected by admin JWT — bind this port behind an internal "
        "firewall rule in production (not internet-facing)."
    ),
    response_class=Response,
    include_in_schema=True,
)
async def metrics() -> Response:
    """GET /metrics — Prometheus text exposition format."""
    data = generate_latest(_registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.service_name, "env": settings.app_env, "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
