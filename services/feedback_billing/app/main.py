"""feedback_billing — FastAPI application entry point (S5-001)."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from shared.http_observability import install_http_observability
from shared.metrics_auth import MetricsAuthError, check_metrics_auth
from shared.observability.pii import PII_FIELDS, redact_pii_processor
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
# Canonical set lives in shared/observability/pii.py so all four services
# redact the same fields. They previously did not: this service redacted four
# names while data_gateway redacted eleven, and the comment above claimed parity.
_PII_FIELDS = PII_FIELDS
_redact_pii_processor = redact_pii_processor


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

# ---------------------------------------------------------------------------
# HTTP metrics + generic 500 handler (XS-01 / XS-05 / XS-06).
#
# Installed AFTER add_middleware(CORSMiddleware) on purpose: Starlette inserts
# each new middleware at the OUTSIDE of the stack, so installing last is what
# makes the recorded latency the one the client experienced rather than what was
# left after CORS.
#
# This replaces prometheus-fastapi-instrumentator, which is why this service is
# named in XS-05: it was the only one of the four whose metrics came from a
# third-party package, so its label set (`handler`, grouped status codes) did
# not match the other three and no dashboard ported between them. Two of its
# collectors also carried the names the shared layer uses, so keeping both would
# have failed at import with a duplicate-timeseries error rather than quietly
# double-counting — see _metrics_for in shared/http_observability.py.
#
# `/health/live` is no longer excluded, though the instrumentator excluded it.
# The point of the shared layer is that the four services emit the same series,
# and probe volume is filterable at query time (`path!="/health/live"`) whereas a
# series that was never recorded cannot be recovered.
install_http_observability(app, service_name=settings.service_name)

app.include_router(health_router)
app.include_router(score_router, prefix="/internal")
app.include_router(scorecard_router, prefix="/api")
app.include_router(scorecard_list_router, prefix="/api")

# ---------------------------------------------------------------------------
# Prometheus scrape endpoint
# ---------------------------------------------------------------------------


@app.get(
    "/metrics",
    include_in_schema=False,
    summary="Prometheus metrics scrape endpoint",
)
async def metrics(authorization: str | None = Header(default=None)) -> Response:
    """Expose the collectors installed by shared/http_observability.py.

    Hand-written rather than generated, which is the shape the other three
    services already had. The gate used to be a route *dependency* because the
    route body belonged to prometheus-fastapi-instrumentator; with the shared
    layer there is no third-party handler to wrap, so the call is inline and the
    four services read alike.

    Worth protecting (M-6, CWE-497) because the exposition enumerates the route
    table — including which /internal and admin paths exist — alongside
    status-code distribution and traffic volume. The 401-vs-404 choice lives in
    shared/metrics_auth.py so all four services answer a prober identically.
    """
    try:
        check_metrics_auth(
            authorization=authorization,
            metrics_token=settings.metrics_token,
            app_env=settings.app_env,
        )
    except MetricsAuthError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from exc

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.service_name, "env": settings.app_env, "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
