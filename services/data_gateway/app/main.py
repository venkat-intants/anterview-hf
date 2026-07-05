"""data_gateway — FastAPI application entry point.

Service lifecycle (managed by the ``lifespan`` async context manager):

  Startup:
    1. init_engine()        — create the async SQLAlchemy engine + session factory.
    2. init_redis()         — connect to Redis.
    3. get_auth_provider()  — instantiate the pluggable auth backend.
    4. AsyncIOScheduler     — start the daily DPDP §8(7) retention cron.
                              Runs at settings.retention_cron_hour UTC (default 03:00
                              UTC ≈ 08:30 IST).  Defaults to dry-run mode; set
                              RETENTION_DRY_RUN=false in production after confirming
                              expected delete counts via at least one dry-run cycle.

  Shutdown:
    1. scheduler.shutdown() — stop the retention cron (no-wait).
    2. dispose_engine()     — close the DB connection pool.
    3. close_redis()        — close the Redis connection.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from shared.auth.factory import get_auth_provider
from shared.observability.sentry import init_sentry

from app.config import settings
from app.database import dispose_engine, get_db_session, get_session_factory, init_engine
from app.dependencies import set_auth_provider
from app.health import router as health_router
from app.mailer import purge_old_email_events, start_email_worker, stop_email_worker
from app.redis_client import close_redis, get_redis, init_redis
from app.retention import purge_expired_sessions
from app.routers.admin_hr import router as admin_hr_router
from app.routers.auth import router as auth_router
from app.routers.consent import router as consent_router
from app.routers.exam_take import router as exam_take_router
from app.routers.hr_applicants import router as hr_applicants_router
from app.routers.hr_coding import router as hr_coding_router
from app.routers.hr_exams import router as hr_exams_router
from app.routers.hr_interviews import router as hr_interviews_router
from app.routers.hr_pipeline import router as hr_pipeline_router
from app.routers.hr_rounds import router as hr_rounds_router
from app.routers.interview_take import router as interview_take_router
from app.routers.jd import router as jd_router
from app.routers.jobs import router as jobs_router
from app.routers.notifications import router as notifications_router
from app.routers.profile import router as profile_router
from app.routers.resume import router as resume_router
from app.routers.sso_google import router as sso_google_router
from app.routers.sso_naipunyam import router as sso_naipunyam_router

# ---------------------------------------------------------------------------
# PII redaction processor (defense-in-depth — DPDP §8)
#
# Drops known PII field names from every log event dict before rendering.
# This catches cases where a developer accidentally logs a raw field.
# Placed just before JSONRenderer in the processor chain.
#
# Deny-list policy:
#   Identity PII   — email, password, phone, full_name
#   Voice / text   — transcript, answer, question, text_content
#   Document PII   — resume_text, jd_text (may contain candidate bio / job details)
#   Contact / geo  — address
# ---------------------------------------------------------------------------
_PII_FIELDS = frozenset({
    # identity
    "email",
    "password",
    "phone",
    "full_name",
    # voice / interview transcript content
    "transcript",
    "answer",
    "question",
    "text_content",
    # document PII (resume / job description free-text)
    "resume_text",
    "jd_text",
    # contact / geo
    "address",
})


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

# ---------------------------------------------------------------------------
# Prometheus metrics — /metrics endpoint (CROSS-CUTTING fix 5a)
#
# We use prometheus_client directly (no instrumentator) to avoid introducing
# a starlette version conflict.  Two core metrics are defined:
#   - http_requests_total  (Counter, labelled method/path/status)
#   - http_request_duration_seconds (Histogram, labelled method/path)
# Additional service-specific metrics can be added here.
# ---------------------------------------------------------------------------

_http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "path", "status_code"],
)

_http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

log = structlog.get_logger(__name__)


async def _run_retention_job() -> None:
    """APScheduler job wrapper: opens a fresh DB session, runs the purge, closes it.

    Errors are caught here to prevent the scheduler from dropping the job after
    one failure — a transient DB hiccup should not silence future nightly purges.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            await purge_expired_sessions(db=session, settings=settings)
    except Exception as exc:  # broad — transient DB errors must not kill the scheduler
        log.error(
            "retention.purge.error",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
    # Same cron tick: purge old delivered/failed email_events + expired auth tokens.
    try:
        async with factory() as session:
            deleted = await purge_old_email_events(db=session)
        log.info("email.retention.purged", rows=deleted)
    except Exception as exc:  # broad — never let email cleanup kill the scheduler
        log.error(
            "email.retention.error", exc_type=type(exc).__name__, exc_msg=str(exc)
        )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    # --- startup ---
    init_engine()
    init_redis()
    provider = get_auth_provider(
        settings=settings,
        db_session_factory=get_db_session,
        redis_client=get_redis(),
    )
    set_auth_provider(provider)

    # --- retention scheduler (DPDP §8(7)) ---
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_retention_job,
        CronTrigger(hour=settings.retention_cron_hour, minute=0, timezone="UTC"),
        id="retention_purge",
        name="DPDP §8(7) 90-day session purge",
        replace_existing=True,
    )
    scheduler.start()
    application.state.retention_scheduler = scheduler

    # --- transactional email outbox worker ---
    start_email_worker()

    # Determine next-run time for the startup log (may be None if no jobs yet).
    next_run_job = scheduler.get_job("retention_purge")
    next_run_iso = (
        next_run_job.next_run_time.isoformat()
        if next_run_job and next_run_job.next_run_time
        else "unknown"
    )

    log.info(
        "service.start",
        service=settings.service_name,
        env=settings.app_env,
        auth_provider=settings.auth_provider,
        port=settings.port,
    )
    log.info(
        "retention.scheduler.started",
        retention_days=settings.retention_days,
        dry_run=settings.retention_dry_run,
        cron_hour_utc=settings.retention_cron_hour,
        next_run_iso=next_run_iso,
    )

    yield  # application runs here

    # --- shutdown ---
    scheduler.shutdown(wait=False)
    await stop_email_worker()
    await dispose_engine()
    await close_redis()
    log.info("service.stop", service=settings.service_name)


app = FastAPI(
    title="Intants Data Gateway",
    description="Auth (pluggable), user management, Naipunyam SSO bridge",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Prometheus scrape middleware — records request count + latency per route.
# Placed BEFORE CORSMiddleware so it captures all requests including OPTIONS.
# The /metrics endpoint itself is excluded from its own counters to avoid
# inflating noise in the scrape-cycle data.
# ---------------------------------------------------------------------------
@app.middleware("http")
async def _prometheus_middleware(request: Request, call_next: Any) -> Response:
    path = request.url.path
    method = request.method
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration = time.perf_counter() - start
    # Normalise high-cardinality UUIDs in paths to avoid metric explosion.
    # Simple heuristic: replace UUID-like path segments with {id}.
    normalised = re.sub(
        r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "/{id}",
        path,
    )
    if normalised != "/metrics":
        _http_requests_total.labels(
            method=method,
            path=normalised,
            status_code=str(response.status_code),
        ).inc()
        _http_request_duration_seconds.labels(method=method, path=normalised).observe(duration)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # "Cookie" is a forbidden CORS header name (browsers always send it, never
    # include it in preflight allow-lists — doing so is spec-invalid and ignored).
    # X-CSRF-Token is a custom request header set by JS for the double-submit
    # CSRF pattern on /auth/refresh — it MUST appear here so the preflight passes.
    # X-Exam-Token: the applicant's magic-link token, sent by the public exam
    # take page (no login) on /exam calls.
    allow_headers=[
        "Authorization", "Content-Type", "X-CSRF-Token", "X-Exam-Token", "X-Interview-Token"
    ],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(admin_hr_router)
app.include_router(hr_applicants_router)
app.include_router(hr_exams_router)
app.include_router(hr_coding_router)
app.include_router(hr_rounds_router)
app.include_router(exam_take_router)
app.include_router(hr_interviews_router)
app.include_router(interview_take_router)
app.include_router(hr_pipeline_router)
app.include_router(consent_router)
app.include_router(jobs_router)
app.include_router(notifications_router)
app.include_router(profile_router)
app.include_router(resume_router)
app.include_router(jd_router)
app.include_router(sso_naipunyam_router)
app.include_router(sso_google_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "env": settings.app_env,
        "version": "0.1.0",
    }


@app.get(
    "/metrics",
    include_in_schema=False,  # not part of the public API contract
    summary="Prometheus metrics scrape endpoint",
)
async def metrics() -> Response:
    """Expose Prometheus metrics for scraping by a collector (e.g. VictoriaMetrics,
    Prometheus server, or Railway's built-in metrics plugin).

    Returns text/plain in the standard Prometheus exposition format.
    The endpoint is excluded from OpenAPI docs (include_in_schema=False) since
    it is an ops endpoint, not part of the service's REST API.
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
