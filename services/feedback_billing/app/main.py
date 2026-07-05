"""feedback_billing — FastAPI application entry point (S5-001)."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from shared.observability.sentry import init_sentry

from app.config import settings
from app.database import dispose_engine, init_engine
from app.health import router as health_router
from app.redis_client import close_redis, init_redis
from app.routers.score import router as score_router
from app.routers.scorecard import router as scorecard_router
from app.routers.scorecard_list import router as scorecard_list_router

# ---------------------------------------------------------------------------
# PII redaction processor (defense-in-depth — DPDP §8)
#
# Drops known PII field names from every log event dict before rendering.
# This catches cases where a developer accidentally logs a raw PII field.
# Placed just before JSONRenderer in the processor chain — mirrors the same
# processor used in data_gateway and interview_core.
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


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    init_engine()
    init_redis()
    log.info("service.start", service=settings.service_name, env=settings.app_env)
    yield
    await dispose_engine()
    await close_redis()
    log.info("service.stop", service=settings.service_name)


app = FastAPI(
    title="Intants Feedback & Billing",
    description="End-of-session scoring, scorecard PDFs, billing pipeline",
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
app.include_router(score_router, prefix="/internal")
app.include_router(scorecard_router, prefix="/api")
app.include_router(scorecard_list_router, prefix="/api")

# ---------------------------------------------------------------------------
# Prometheus metrics — expose /metrics with default HTTP + latency histograms.
# The /metrics endpoint is unauthed (scrape access is controlled at the
# network/ingress level, not at the application level).
# instrument() must be called after all routers are registered so that all
# route labels are captured correctly, and expose() wires the /metrics route.
# ---------------------------------------------------------------------------
Instrumentator(
    should_group_status_codes=True,
    excluded_handlers=["/metrics", "/health/live"],
).instrument(app).expose(app, include_in_schema=False)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.service_name, "env": settings.app_env, "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
