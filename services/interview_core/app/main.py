from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
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

app = FastAPI(
    title="Intants Interview Core",
    description="Voice interview WebSocket + LangGraph orchestrator + voice pipeline",
    version="0.1.0",
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
        "Prometheus instance every 15 s."
    ),
    response_class=Response,
    tags=["observability"],
    include_in_schema=True,
)
async def metrics() -> Response:
    """Return prometheus_client default metrics in text exposition format."""
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.on_event("startup")
async def startup() -> None:
    init_engine()
    init_redis()
    # Store the LLM adapter on app.state so all WebSocket sessions can read it
    # via websocket.app.state.llm_adapter — no module-level singleton (S4-013).
    app.state.llm_adapter = build_default_adapter()
    log.info(
        "service.start",
        service=settings.service_name,
        env=settings.app_env,
        llm_provider=settings.llm_provider,
        stt_provider=settings.speech_stt_provider,
        tts_provider=settings.speech_tts_provider,
        avatar_provider=settings.avatar_provider,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    await dispose_engine()
    await close_redis()
    log.info("service.stop", service=settings.service_name)
