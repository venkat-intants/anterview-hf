from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from shared.metrics_auth import MetricsAuthError, check_metrics_auth
from shared.observability.pii import PII_FIELDS, redact_pii_processor
from shared.observability.sentry import init_sentry

from app.config import settings
from app.database import dispose_engine, init_engine
from app.health import router as health_router
from app.llm import build_default_adapter
from app.redis_client import close_redis, init_redis
from app.routers.api import router as api_router
from app.routers.avatars import router as avatars_router
from app.routers.integrity import router as integrity_router
from app.routers.rooms import router as rooms_router
from app.routers.sessions import router as sessions_router

# ---------------------------------------------------------------------------
# PII redaction processor (defense-in-depth — DPDP §8)
#
# Drops known PII field names from every log event dict before rendering.
# Placed just before JSONRenderer in the processor chain.
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
    """Service lifecycle. Replaces the deprecated ``@app.on_event`` hooks.

    Starlette deprecated ``on_event`` in favour of this context manager and will
    drop it at the next major; the other three services had already migrated, so
    this one was the last place a Starlette bump could have broken. Same ordering
    as the two handlers it replaces — engine before Redis on the way up, engine
    disposed before Redis is closed on the way down — because the DB teardown may
    still emit a log line and must not race a half-torn-down pool.

    Behaviour is otherwise unchanged, including the S4-013 rule that the LLM
    adapter lives on ``app.state`` rather than in a module-level singleton so
    every WebSocket session reads it via ``websocket.app.state.llm_adapter``.
    """
    # --- startup ---
    init_engine()
    init_redis()
    # Store the LLM adapter on app.state so all WebSocket sessions can read it
    # via websocket.app.state.llm_adapter — no module-level singleton (S4-013).
    application.state.llm_adapter = build_default_adapter()
    log.info(
        "service.start",
        service=settings.service_name,
        env=settings.app_env,
        llm_provider=settings.llm_provider,
        stt_provider=settings.speech_stt_provider,
        tts_provider=settings.speech_tts_provider,
        avatar_provider=settings.avatar_provider,
    )

    yield  # application runs here

    # --- shutdown ---
    await dispose_engine()
    await close_redis()
    log.info("service.stop", service=settings.service_name)


app = FastAPI(
    title="Intants Interview Core",
    description="Voice interview WebSocket + LangGraph orchestrator + voice pipeline",
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
app.include_router(api_router)
app.include_router(avatars_router)
app.include_router(sessions_router)
app.include_router(rooms_router)
app.include_router(integrity_router)
# NOTE: WS turn-loop + avatar routers removed 2026-05-31 — the real-time
# interview transport (LiveKit/Pipecat) + avatar layer are being rebuilt
# from scratch. The brain (graph/), voice (speech/), LLM and data layers
# are retained. See CLAUDE.md § "Avatar vendor decision".


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "env": settings.app_env,
        "version": "0.1.0",
    }


@app.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "Exposes default process metrics (CPU, memory, open FDs, GC) "
        "in Prometheus text format. Scraped by the deploy-cluster's "
        "Prometheus instance every 15 s. Requires "
        "'Authorization: Bearer <METRICS_TOKEN>' when METRICS_TOKEN is set, "
        "and is refused outright in production when it is not."
    ),
    response_class=Response,
    tags=["observability"],
    include_in_schema=True,
)
async def metrics(authorization: str | None = Header(default=None)) -> Response:
    """Return prometheus_client default metrics in text exposition format.

    The service JWT is deliberately NOT reused here: a Prometheus scrape job
    holds a static credential, not a short-lived user token. The policy (and the
    401-vs-404 choice) lives in shared/metrics_auth.py so all four services
    refuse identically; this handler only translates it into HTTP.
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

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
