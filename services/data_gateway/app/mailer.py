"""Transactional email orchestration — enqueue, background delivery, notify().

This is the high-level API the rest of the app uses. Three pieces:

  1. ``enqueue_email`` — render a branded template now and stage a durable
     ``email_events`` row (status='queued') on the CALLER's DB session. The caller
     owns the commit, so the email is staged atomically with the action that
     triggered it (e.g. the invite row + its email commit together, or neither).
     Requests never do SMTP I/O on the hot path.

  2. The OUTBOX WORKER (``start_email_worker`` / ``stop_email_worker``) — a single
     asyncio task started in the app lifespan. It claims due rows
     (FOR UPDATE SKIP LOCKED — safe across replicas), hands each to the SMTP relay,
     and records the outcome: 'sent' (body nulled) or, on failure, re-queued with
     exponential backoff until ``max_attempts`` then 'failed'. Crash-safe: a row
     stuck in 'sending' past a visibility timeout is reclaimed.

  3. ``notify`` — fire an in-app notification AND an email from one call so a
     user's status is consistent in the app feed and their inbox (the spec's
     "synchronized with platform events" requirement).

``purge_old_email_events`` is invoked by the daily retention cron.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import Row, select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import email_templates
from app.config import settings
from app.database import get_session_factory
from app.email_util import deliver_smtp
from app.models import Company, EmailEvent
from app.notifications_util import create_notification

log = structlog.get_logger(__name__)

# A row stuck in 'sending' longer than this (worker crashed mid-send) is reclaimed.
_VISIBILITY_TIMEOUT_SECONDS = 120
# Retry backoff: base * 2^(attempt-1), capped. 30s, 1m, 2m, 4m, 8m, … (≤ 1h).
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600


def absolute_url(path_or_url: str | None) -> str | None:
    """Make a relative app path absolute against APP_BASE_URL (pass through URLs)."""
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return f"{settings.app_base_url.rstrip('/')}/{path_or_url.lstrip('/')}"


# ---------------------------------------------------------------------------
# Producer API
# ---------------------------------------------------------------------------
async def enqueue_email(
    db: AsyncSession,
    *,
    to: str | None,
    template: str,
    ctx: dict,
    lang: str = "en",
    to_user_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    related_kind: str | None = None,
    related_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> EmailEvent | None:
    """Render + stage one email on ``db`` (caller commits). Returns the row, or
    None when skipped (no/invalid recipient, or a dedupe_key already used).

    Never raises on a bad recipient — email is best-effort and must not fail the
    caller's request.
    """
    if not to or "@" not in to:
        log.warning("email.enqueue.skip_invalid_recipient", template=template)
        return None

    if dedupe_key is not None:
        existing = await db.scalar(
            select(EmailEvent.id).where(EmailEvent.dedupe_key == dedupe_key)
        )
        if existing is not None:
            log.info("email.enqueue.deduped", template=template, dedupe_key=dedupe_key)
            return None

    # Company-branding: when this email belongs to a tenant company, resolve the
    # company's display name and inject it as ``brand`` so the rendered subject,
    # body copy, and wordmark read as that company (e.g. "Google", "CDPR") rather
    # than the generic platform brand. The caller may pre-set ctx["brand"] to
    # override. Best-effort — a lookup miss just leaves the platform brand.
    render_ctx = ctx
    if company_id is not None and "brand" not in ctx:
        company_name = await db.scalar(
            select(Company.name).where(Company.id == company_id)
        )
        if company_name:
            render_ctx = {**ctx, "brand": company_name}

    rendered = email_templates.render(template, lang, render_ctx)
    now = datetime.now(tz=UTC)
    ev = EmailEvent(
        id=uuid.uuid4(),
        template=template,
        to_email=to,
        to_user_id=to_user_id,
        company_id=company_id,
        lang=lang,
        subject=rendered.subject,
        body_html=rendered.html,
        body_text=rendered.text,
        status="queued",
        attempts=0,
        max_attempts=settings.email_max_attempts,
        next_attempt_at=now,
        related_kind=related_kind,
        related_id=related_id,
        dedupe_key=dedupe_key,
        created_at=now,
        updated_at=now,
    )
    db.add(ev)
    log.info("email.enqueued", template=template, to_user_id=str(to_user_id) if to_user_id else None)
    return ev


async def notify(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    email: bool = False,
    to_email: str | None = None,
    lang: str | None = None,
    email_template: str = "generic",
    email_ctx: dict | None = None,
    company_id: uuid.UUID | None = None,
) -> None:
    """Create an in-app notification AND (optionally) enqueue the matching email.

    Keeps the user's status consistent across the app feed and their inbox. When
    ``email=True`` and ``to_email``/``lang`` aren't supplied, they're looked up
    from the target user. Caller owns the commit.
    """
    await create_notification(db, user_id=user_id, kind=kind, title=title, body=body, link=link)

    if not email or user_id is None:
        return

    if to_email is None or lang is None:
        row = (
            await db.execute(
                text(
                    "SELECT email, preferred_language FROM users "
                    "WHERE id = :uid AND deleted_at IS NULL"
                ),
                {"uid": user_id},
            )
        ).fetchone()
        if row is not None:
            to_email = to_email or row[0]
            lang = lang or row[1]
    lang = lang or "en"

    ctx = email_ctx or {
        "title": title,
        "body": body or "",
        "cta_label": "Open in app",
        "cta_url": absolute_url(link),
    }
    await enqueue_email(
        db,
        to=to_email,
        template=email_template,
        ctx=ctx,
        lang=lang,
        to_user_id=user_id,
        company_id=company_id,
        related_kind=kind,
    )


# ---------------------------------------------------------------------------
# Outbox worker
# ---------------------------------------------------------------------------
def _backoff_seconds(attempts: int) -> int:
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))


async def _claim_batch(db: AsyncSession) -> list[Row[Any]]:
    """Atomically claim up to email_batch_size due rows → set 'sending', bump
    attempts. Returns the rows' send payloads. FOR UPDATE SKIP LOCKED makes this
    safe to run concurrently across multiple worker instances."""
    ids = (
        await db.execute(
            text(
                "SELECT id FROM email_events "
                "WHERE attempts < max_attempts AND ("
                "  (status IN ('queued','failed') "
                "   AND (next_attempt_at IS NULL OR next_attempt_at <= now())) "
                "  OR (status = 'sending' "
                "   AND updated_at < now() - make_interval(secs => :vis))"
                ") ORDER BY created_at ASC LIMIT :lim "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"vis": _VISIBILITY_TIMEOUT_SECONDS, "lim": settings.email_batch_size},
        )
    ).scalars().all()
    if not ids:
        return []
    await db.execute(
        text(
            "UPDATE email_events SET status='sending', attempts=attempts+1, "
            "updated_at=now() WHERE id = ANY(:ids)"
        ),
        {"ids": list(ids)},
    )
    # LEFT JOIN companies so the sender DISPLAY NAME can be the tenant company
    # (company-branded invites). Platform emails (company_id NULL) get NULL here
    # and fall back to the platform brand in the relay.
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.to_email, e.subject, e.body_html, e.body_text, "
                "e.attempts, e.max_attempts, c.name AS from_name "
                "FROM email_events e LEFT JOIN companies c ON c.id = e.company_id "
                "WHERE e.id = ANY(:ids)"
            ),
            {"ids": list(ids)},
        )
    ).fetchall()
    await db.commit()
    return list(rows)


async def _record_sent(db: AsyncSession, row_id: uuid.UUID) -> None:
    # Null the body — it may carry a live magic link we must not retain post-send.
    await db.execute(
        text(
            "UPDATE email_events SET status='sent', sent_at=now(), updated_at=now(), "
            "last_error=NULL, body_html=NULL, body_text=NULL WHERE id=:id"
        ),
        {"id": row_id},
    )


async def _record_failure(
    db: AsyncSession, row_id: uuid.UUID, attempts: int, max_attempts: int, err: str
) -> None:
    terminal = attempts >= max_attempts
    await db.execute(
        text(
            "UPDATE email_events SET status=:st, last_error=:err, updated_at=now(), "
            "next_attempt_at = now() + make_interval(secs => :backoff) WHERE id=:id"
        ),
        {
            "st": "failed" if terminal else "queued",
            "err": err[:1000],
            "backoff": _backoff_seconds(attempts),
            "id": row_id,
        },
    )


async def _drain(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as db:
        batch = await _claim_batch(db)
    if not batch:
        return 0
    for row_id, to_email, subject, body_html, body_text, attempts, max_attempts, from_name in batch:
        async with factory() as db:
            try:
                await deliver_smtp(
                    to=to_email, subject=subject, html=body_html or "", text=body_text,
                    from_name=from_name,
                )
                await _record_sent(db, row_id)
                await db.commit()
                log.info("email.delivered", id=str(row_id), attempt=attempts)
            except Exception as exc:  # noqa: BLE001 — record + retry, never crash worker
                await _record_failure(
                    db, row_id, int(attempts), int(max_attempts), str(exc)
                )
                await db.commit()
                log.warning(
                    "email.delivery_failed",
                    id=str(row_id), attempt=attempts, max=max_attempts, error=str(exc),
                )
    return len(batch)


async def _worker_loop() -> None:
    factory = get_session_factory()
    log.info("email.worker.started", poll_seconds=settings.email_poll_interval_seconds)
    while True:
        try:
            await _drain(factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a transient DB hiccup must not kill the loop
            log.error("email.worker.error", error=str(exc))
        await asyncio.sleep(settings.email_poll_interval_seconds)


_worker_task: asyncio.Task | None = None


def start_email_worker() -> None:
    """Start the background outbox drain (no-op if disabled or already running)."""
    global _worker_task
    if not settings.email_outbox_enabled:
        log.info("email.worker.disabled")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop(), name="email-outbox-worker")


async def stop_email_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _worker_task
    _worker_task = None
    log.info("email.worker.stopped")


# ---------------------------------------------------------------------------
# Retention (called from the daily DPDP cron)
# ---------------------------------------------------------------------------
async def purge_old_email_events(db: AsyncSession) -> int:
    """Delete delivered/failed/cancelled rows older than EMAIL_RETENTION_DAYS, plus
    expired auth_tokens. Returns the email rows deleted. Queued/sending rows are
    kept regardless of age so an in-flight email is never dropped."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=settings.email_retention_days)
    result = await db.execute(
        text(
            "DELETE FROM email_events WHERE status IN ('sent','failed','cancelled') "
            "AND created_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    # Sweep consumed/expired single-use auth tokens (kept the table tidy + bounded).
    await db.execute(
        text(
            "DELETE FROM auth_tokens WHERE consumed_at IS NOT NULL OR expires_at < now()"
        )
    )
    await db.commit()
    # See retention.py: execute() is typed Result, DML returns CursorResult.
    return int(cast("CursorResult[Any]", result).rowcount or 0)
