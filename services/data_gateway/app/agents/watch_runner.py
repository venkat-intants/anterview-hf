"""Nightly watcher sweep — gather, evaluate, notify.

The detection rules live in ``shared.agents.watchers`` (pure, unit-tested). This
module is the plumbing around them: the SQL that fills ``WatcherInput`` per
company, and the notification writer that turns findings into rows in the
existing ``notifications`` table, which already backs the header bell.

Three properties that matter operationally:

* **Deduplicated across runs.** Each finding carries a ``dedupe_key`` derived
  from what it is about, not when it fired. We keep a Redis marker per
  (user, key) so "7 applicants stalled" notifies once, not every night until
  someone acts. Nightly re-notification is how a notification bell becomes
  wallpaper.
* **Per-company isolation.** One company's failure is caught and logged; the
  sweep continues. A broken row in one tenant must not cost every other tenant
  their alerts.
* **No writes beyond notifications.** The watchers have no authority to act,
  matching the rest of the agent layer. They notice; a human decides.

Runs under the same APScheduler instance as the DPDP retention job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from shared.agents import (
    ErasureRequest,
    FunnelRow,
    QuestionStat,
    StalledApplicant,
    WatcherFinding,
    WatcherInput,
    run_watchers,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session_factory
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

# How long a delivered finding stays suppressed. Slightly over a week: long
# enough that a recurring condition does not nag daily, short enough that a
# genuinely ignored problem resurfaces before it is forgotten.
DEDUPE_TTL_SECONDS: int = 8 * 24 * 3600
_DEDUPE_PREFIX = "watcher:seen:"

# Bound per-company work so one enormous tenant cannot stall the sweep.
MAX_ROWS_PER_QUERY: int = 200

# notifications.kind is an opaque string the UI maps to an icon/tone.
NOTIFICATION_KIND: str = "system"

# Per-question pass rates.
#
# There is NO per-response table — an earlier version of this query assumed an
# ``exam_responses`` table and failed against the real schema. Answers live in
# two JSONB blobs on ``exam_attempts``:
#
#   graded_snapshot->'mcq'-><qid> = {"points": 1, "correct_index": 2}  answer key
#   answers        ->'mcq'->><qid> = 2                                 what they picked
#
# A question counts as SERVED when the graded snapshot carries a key for it, so
# an unanswered question still counts as an attempt (and not as correct) —
# which is exactly the signal "nobody can answer this one" needs.
#
# jsonb_exists() is used rather than the `?` operator: `?` collides with
# parameter binding in some driver/dialect combinations, and this SQL runs
# unattended every night.
QUESTION_STATS_SQL = text(
    """
    SELECT e.id AS exam_id, e.title, q.id AS question_id, q.position,
           COUNT(*) AS attempts,
           COUNT(*) FILTER (
               WHERE NULLIF(t.answers->'mcq'->>(q.id::text), '')::int
                   = (t.graded_snapshot->'mcq'->(q.id::text)->>'correct_index')::int
           ) AS correct
    FROM exam_questions q
    JOIN exams e ON e.id = q.exam_id AND e.company_id = q.company_id
    JOIN exam_attempts t
      ON t.exam_id = e.id AND t.company_id = e.company_id
     AND t.status = 'submitted' AND t.deleted_at IS NULL
     AND jsonb_exists(COALESCE(t.graded_snapshot->'mcq', '{}'::jsonb), q.id::text)
    WHERE e.company_id = CAST(:cid AS uuid)
      AND e.deleted_at IS NULL AND q.deleted_at IS NULL
    GROUP BY e.id, e.title, q.id, q.position
    LIMIT :limit
    """
)


async def gather_company_input(db: AsyncSession, company_id: str) -> WatcherInput:
    """Run the four gathering queries for one company."""
    stalled = (
        await db.execute(
            text(
                """
                SELECT a.id, a.full_name, a.status,
                       EXTRACT(DAY FROM (NOW() - a.updated_at))::int AS days
                FROM applicants a
                WHERE a.company_id = CAST(:cid AS uuid)
                  AND a.deleted_at IS NULL
                  -- Terminal stages are done, not stalled.
                  AND a.status NOT IN ('hired', 'rejected')
                ORDER BY a.updated_at ASC
                LIMIT :limit
                """
            ),
            {"cid": company_id, "limit": MAX_ROWS_PER_QUERY},
        )
    ).all()

    funnels = (
        await db.execute(
            text(
                """
                SELECT a.target_job_title AS title,
                       COUNT(*) AS applicants,
                       COUNT(sc.scorecard_id) AS interviewed
                FROM applicants a
                LEFT JOIN LATERAL (
                    SELECT i.session_id FROM interview_invites i
                    WHERE i.applicant_id = a.id AND i.company_id = a.company_id
                      AND i.deleted_at IS NULL AND i.session_id IS NOT NULL
                    ORDER BY i.created_at DESC LIMIT 1
                ) li ON TRUE
                LEFT JOIN scorecards sc ON sc.session_id = li.session_id
                WHERE a.company_id = CAST(:cid AS uuid) AND a.deleted_at IS NULL
                GROUP BY a.target_job_title
                LIMIT :limit
                """
            ),
            {"cid": company_id, "limit": MAX_ROWS_PER_QUERY},
        )
    ).all()

    questions = (
        await db.execute(QUESTION_STATS_SQL, {"cid": company_id, "limit": MAX_ROWS_PER_QUERY})
    ).all()

    return WatcherInput(
        company_id=company_id,
        stalled=[
            StalledApplicant(str(r.id), r.full_name, r.status, int(r.days or 0))
            for r in stalled
        ],
        funnels=[
            FunnelRow(
                # No stable job id on the denormalised applicant row, so the
                # title doubles as the grouping key. It is what the dedupe key
                # and the citation both need.
                job_id=r.title,
                job_title=r.title,
                applicants=int(r.applicants),
                interviewed=int(r.interviewed),
            )
            for r in funnels
        ],
        question_stats=[
            QuestionStat(
                exam_id=str(r.exam_id),
                exam_title=r.title,
                question_id=str(r.question_id),
                position=int(r.position),
                attempts=int(r.attempts),
                correct=int(r.correct),
            )
            for r in questions
        ],
    )


async def gather_erasure_deadlines(db: AsyncSession) -> list[ErasureRequest]:
    """Pending DPDP erasure requests, platform-wide.

    Not company-scoped: erasure requests are a platform-owner obligation under
    the DPDP Act, and the statutory deadline does not care whose tenant the
    subject belongs to.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT request_id,
                       EXTRACT(EPOCH FROM (scheduled_for - NOW())) / 3600.0 AS hours_left
                FROM erasure_requests
                WHERE status = 'pending'
                ORDER BY scheduled_for ASC
                LIMIT :limit
                """
            ),
            {"limit": MAX_ROWS_PER_QUERY},
        )
    ).all()
    return [
        ErasureRequest(str(r.request_id), float(r.hours_left or 0.0)) for r in rows
    ]


# Roles are a many-to-many via user_roles -> roles.name. There is NO
# users.role column: an earlier version of these queries assumed one and threw
# UndefinedColumnError against the real schema. Because the sweep catches
# per-company failures, that would not have crashed anything — it would have
# quietly delivered zero notifications forever, which is worse. Matches the
# join every other router uses (see admin_hr.py).
async def _recipients_for_company(db: AsyncSession, company_id: str) -> list[str]:
    """HR managers and super admins for a company."""
    rows = (
        await db.execute(
            text(
                """
                SELECT DISTINCT u.id
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE u.company_id = CAST(:cid AS uuid) AND u.deleted_at IS NULL
                  AND r.name IN ('hr_manager', 'super_admin')
                LIMIT 50
                """
            ),
            {"cid": company_id},
        )
    ).all()
    return [str(r.id) for r in rows]


async def _platform_owners(db: AsyncSession) -> list[str]:
    rows = (
        await db.execute(
            text(
                """
                SELECT DISTINCT u.id
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE r.name = 'platform_owner' AND u.deleted_at IS NULL
                LIMIT 20
                """
            )
        )
    ).all()
    return [str(r.id) for r in rows]


async def _already_notified(user_id: str, dedupe_key: str) -> bool:
    """Redis-backed suppression. Fails OPEN on a Redis error.

    A cache outage should cost a duplicate notification, never a missed one —
    the DPDP deadline alert in particular must not be swallowed because Redis
    was briefly unreachable.

    A pure read. Claiming the slot here (SET NX) would suppress the finding for
    the full TTL even when the INSERT that follows never commits — a DPDP
    deadline alert that was never delivered and can no longer fire.
    ``_mark_notified`` runs only after the write lands.
    """
    if not dedupe_key:
        return False
    try:
        redis = get_redis()
        return bool(await redis.exists(_dedupe_key(user_id, dedupe_key)))
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("watchers.dedupe_unavailable", error=type(exc).__name__)
        return False


async def _mark_notified(user_id: str, dedupe_key: str) -> None:
    """Suppress a finding for the TTL. Called only after the rows are committed.

    Best-effort: a Redis failure here costs a duplicate notification tomorrow,
    which is the side of the trade this module deliberately errs on.
    """
    if not dedupe_key:
        return
    try:
        redis = get_redis()
        await redis.set(_dedupe_key(user_id, dedupe_key), "1", ex=DEDUPE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("watchers.dedupe_unavailable", error=type(exc).__name__)


def _dedupe_key(user_id: str, dedupe_key: str) -> str:
    return f"{_DEDUPE_PREFIX}{user_id}:{dedupe_key}"


async def _safe_rollback(db: AsyncSession) -> None:
    """Clear an aborted transaction without letting the cleanup itself raise.

    ``run_watcher_sweep`` promises never to raise, and a rollback on a session
    whose connection has already gone is exactly the case where it would.
    """
    try:
        await db.rollback()
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("watchers.rollback_failed", error=type(exc).__name__)


async def deliver(
    db: AsyncSession, findings: list[WatcherFinding], recipients: list[str]
) -> int:
    """Write findings to the notifications table. Returns rows written.

    Write first, suppress second. The dedupe markers are only set once the
    commit has succeeded, so a failed insert costs a retry tomorrow rather than
    silently burning the finding's 8-day suppression window.
    """
    written = 0
    now = datetime.now(tz=UTC)
    # Ordered so the markers are set in the order the rows were written; the
    # membership test is what stops two findings sharing a dedupe_key from
    # notifying the same person twice within one sweep, which the old
    # claim-on-check SET NX used to handle as a side effect. An EMPTY
    # dedupe_key means "never suppress this", so it is excluded from that test.
    delivered: list[tuple[str, str]] = []

    for finding in findings:
        for user_id in recipients:
            if finding.dedupe_key and (user_id, finding.dedupe_key) in delivered:
                continue
            if await _already_notified(user_id, finding.dedupe_key):
                continue
            await db.execute(
                text(
                    """
                    INSERT INTO notifications
                        (id, user_id, kind, title, body, link, created_at)
                    VALUES
                        (:id, CAST(:uid AS uuid), :kind, :title, :body, :link, :ts)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": user_id,
                    "kind": NOTIFICATION_KIND,
                    # Severity is carried in the title because notifications
                    # has no severity column; adding one would be a migration
                    # for cosmetics.
                    "title": (
                        f"⚠ {finding.title}" if finding.severity == "critical" else finding.title
                    ),
                    "body": finding.body,
                    "link": finding.link,
                    "ts": now,
                },
            )
            written += 1
            delivered.append((user_id, finding.dedupe_key))

    if written:
        await db.commit()
        for user_id, dedupe_key in delivered:
            await _mark_notified(user_id, dedupe_key)
    return written


async def run_watcher_sweep() -> dict[str, int]:
    """Run every watcher for every company. Never raises.

    Scheduled nightly. Returns a small summary for the log and for the manual
    trigger endpoint.
    """
    if not settings.watchers_enabled:
        log.info("watchers.sweep.disabled")
        return {"companies": 0, "findings": 0, "notifications": 0}

    totals = {"companies": 0, "findings": 0, "notifications": 0}
    factory = get_session_factory()

    async with factory() as db:
        companies = (
            await db.execute(
                text("SELECT id FROM companies WHERE deleted_at IS NULL LIMIT 500")
            )
        ).all()

        # Platform-wide DPDP obligations go to the platform owners once, not to
        # every company's HR team.
        try:
            erasures = await gather_erasure_deadlines(db)
            if erasures:
                dpdp_findings = run_watchers(
                    WatcherInput(company_id="platform", erasure_requests=erasures)
                )
                owners = await _platform_owners(db)
                totals["findings"] += len(dpdp_findings)
                totals["notifications"] += await deliver(db, dpdp_findings, owners)
        except Exception as exc:  # noqa: BLE001
            await _safe_rollback(db)
            log.warning("watchers.dpdp_sweep_failed", error=type(exc).__name__)

        for company in companies:
            company_id = str(company.id)
            try:
                data = await gather_company_input(db, company_id)
                findings = run_watchers(data)
                if not findings:
                    continue
                recipients = await _recipients_for_company(db, company_id)
                if not recipients:
                    # A company with nobody to tell is not an error — a tenant
                    # can exist before its HR managers are created.
                    continue
                totals["findings"] += len(findings)
                totals["notifications"] += await deliver(db, findings, recipients)
            except Exception as exc:  # noqa: BLE001 — one tenant must not
                # cost every other tenant their alerts.
                #
                # The rollback is what makes that true rather than aspirational:
                # all 500 companies share one session, and a SQL-level failure
                # leaves the transaction aborted, so without it every remaining
                # tenant's queries fail too and the sweep quietly delivers
                # nothing platform-wide.
                await _safe_rollback(db)
                log.warning(
                    "watchers.company_failed",
                    company_id=company_id,
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
            finally:
                totals["companies"] += 1

    log.info("watchers.sweep.complete", **totals)
    return totals
