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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from shared.auth.factory import get_auth_provider
from shared.http_observability import install_http_observability
from shared.metrics_auth import MetricsAuthError, check_metrics_auth
from shared.observability.pii import PII_FIELDS, redact_pii_processor
from shared.observability.sentry import init_sentry

from app.config import settings
from app.database import dispose_engine, get_db_session, get_session_factory, init_engine
from app.dependencies import set_auth_provider
from app.health import router as health_router
from app.mailer import purge_old_email_events, start_email_worker, stop_email_worker
from app.redis_client import close_redis, get_redis, init_redis
from app.retention import purge_expired_sessions
from app.routers.admin_hr import router as admin_hr_router
from app.routers.agent import router as agent_router
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
from app.routers.onboarding import router as onboarding_router
from app.routers.profile import router as profile_router
from app.routers.resume import router as resume_router
from app.routers.sso_google import router as sso_google_router
from app.routers.sso_naipunyam import router as sso_naipunyam_router
from app.s3_upload import StorageNotConfiguredError

# ---------------------------------------------------------------------------
# PII redaction processor (defense-in-depth — DPDP §8)
#
# Drops known PII field names from every log event dict before rendering.
# This catches cases where a developer accidentally logs a raw field.
# Placed just before JSONRenderer in the processor chain.
#
# Deny-list policy (canonical set: shared/observability/pii.py):
#   Identity PII   — email, password, phone, full_name, candidate_name/email
#   Voice / text   — transcript, answer, question, text_content, turn_text
#   Document PII   — resume_text, jd_text, target_jd_text
#   Contact / geo  — address
#   Credentials    — token, raw_token, access_token, refresh_token
#
# This service used to own the list. It is shared now because the other three
# had drifted to a four-field subset while their comments claimed parity — and
# the one missing "transcript" was interview_core.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Prometheus metrics + unhandled-exception handling
#
# Both used to be defined in this file — the Counter, the Histogram, a
# BaseHTTPMiddleware timer and a regex that collapsed UUID path segments. All
# four now come from ``shared/http_observability.py`` (installed below, after
# CORS), because this service's implementation was the ONLY one of the four and
# the other three therefore had neither HTTP metrics nor a 500 handler (XS-01,
# XS-05). The shared version also fixes what this one got wrong: an unmatched
# route was labelled with its RAW path, so an unauthenticated caller looping
# /aaa, /aab, … minted a new time series per request (XS-06, CWE-770).
#
# Nothing here may re-declare http_requests_total / http_request_duration_seconds
# — prometheus_client refuses a duplicate name on the same registry, and the
# shared module raises with that instruction if it happens.
# ---------------------------------------------------------------------------

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
    except StorageNotConfiguredError as exc:
        # Named separately from the catch-all below so the log says "this
        # deployment cannot delete scorecard objects" — an operator fix, one env
        # var away — rather than burying it in a generic purge error. Nothing was
        # deleted and nothing committed, so the next nightly run retries the same
        # set; the purge is stalled, not partially applied.
        log.error(
            "retention.purge.storage_refusal",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
            detail=(
                "The DPDP §8(7) purge collected scorecard object keys but object "
                "storage is not configured. Set S3_ENDPOINT and S3_ACCESS_KEY_ID. "
                "No rows were deleted."
            ),
        )
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
    # --- nightly proactive watchers (agent layer) ---
    # Same scheduler instance as retention: a second AsyncIOScheduler would mean
    # a second thread pool for one job a day.
    if settings.watchers_enabled:
        from app.agents.watch_runner import run_watcher_sweep

        scheduler.add_job(
            run_watcher_sweep,
            CronTrigger(hour=settings.watchers_cron_hour, minute=30, timezone="UTC"),
            id="agent_watchers",
            name="Pipeline watchers -> notifications",
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    # XS-10: this list is the UNION across the four services and is deliberately
    # the widest of the four — the other three allow only
    # GET/POST/PUT/DELETE/OPTIONS and only Authorization/Content-Type. Nothing at
    # HEAD depends on the difference, but a PATCH route or a custom header added
    # to another service would fail its preflight there and pass here, which is
    # the confusing direction to fail. Keep this list as the reference; the
    # convergence has to happen in the other three ``main.py`` files, which this
    # service does not own. Do not narrow it to "match" them.
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

# Installed LAST so it is the OUTERMOST middleware: Starlette pushes each new
# middleware onto the outside of the stack, so the latency this records is what
# the client experienced rather than what was left after CORS. Registers both the
# HTTP metrics and the generic unhandled-exception handler (XS-01/XS-05/XS-06) —
# see shared/http_observability.py.
install_http_observability(app, service_name=settings.service_name)

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
app.include_router(agent_router)
app.include_router(consent_router)
app.include_router(jobs_router)
app.include_router(notifications_router)
app.include_router(onboarding_router)
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
async def metrics(authorization: str | None = Header(default=None)) -> Response:
    """Expose Prometheus metrics for scraping by a collector (e.g. VictoriaMetrics,
    Prometheus server, or Railway's built-in metrics plugin).

    Returns text/plain in the standard Prometheus exposition format.
    The endpoint is excluded from OpenAPI docs (include_in_schema=False) since
    it is an ops endpoint, not part of the service's REST API.

    A user JWT is deliberately NOT reused as the credential: a scrape job holds a
    static secret, not a short-lived session token. The policy — and the
    401-vs-404 choice — lives in shared/metrics_auth.py so all four services
    refuse identically; this handler only translates it into HTTP. Note the edge
    proxy already gates /metrics in the Caddy topologies, but render.yaml gives
    each backend its own public hostname with no proxy in front, and a control
    one deploy target lacks is not a control.
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

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
