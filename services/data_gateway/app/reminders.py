"""Deadline reminders and results notification — A2 / A4.

Today a candidate receives exactly one email per stage — the invitation — and
then silence. Nothing warns them a link is about to expire, nothing follows up
when they miss it, and when their scorecard is generated nobody tells them. Most
of the drop-off in a hiring funnel is people who simply stopped hearing from us.

Three sweeps, all found by absence rather than by a queue
--------------------------------------------------------
1. ``_exam_reminders``       — an assignment whose link expires soon, unstarted
2. ``_interview_reminders``  — an invite whose appointment or link is near
3. ``_results_ready``        — a scorecard exists and the candidate was not told

Each is a plain SQL predicate over current state, so a sweep that does not run
(a sleeping Space, a crash) loses nothing: the next pass sees the same rows.

Idempotency
-----------
``email_events.dedupe_key`` already enforces one-send-per-key at the point of
enqueue, so the sweep can run as often as it likes without a candidate
receiving the same reminder twice. The key names the thing and the window —
``exam_reminder:<assignment_id>:24h`` — never the time it fired. In-app
notifications carry keys of their own (``notifications.dedupe_key``).

Why reminders carry no magic link
---------------------------------
Exam and interview links are single-use tokens stored **HMAC-hashed**; the raw
token is returned exactly once, at issue. It is therefore impossible to
reproduce a candidate's original link here, and the alternatives are both worse
than omitting it: minting a fresh token would silently break the link the
candidate already has in their inbox, and storing the raw token would turn a
database read into account access for every candidate at once.

So a reminder states the deadline and asks the candidate to use the link from
their invitation. A "resend my link" flow is the right way to close this gap and
is deliberately out of Group A scope — it needs an endpoint that can rotate a
token safely, which is a design decision, not a sweep.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.mailer import enqueue_email
from app.notifications_util import create_notification

log = structlog.get_logger(__name__)

# Reminder windows as non-overlapping bands: (label, nearer edge, farther edge).
# A deadline is due for a window when it falls in (now + nearer, now + farther].
#
# They used to be cumulative — "24h" meant anything inside the next day — so a
# link with forty minutes left matched both, and the candidate got two emails
# in the same minute, one of them saying the link "closes tomorrow". As bands,
# a deadline inside the hour is only ever in the 1h window. A candidate invited
# with less than a day's notice still gets whichever bands the deadline passes
# through, because each is evaluated independently rather than as a sequence.
_WINDOWS: tuple[tuple[str, timedelta, timedelta], ...] = (
    ("24h", timedelta(hours=1), timedelta(hours=24)),
    ("1h", timedelta(0), timedelta(hours=1)),
)

# A workflow can switch candidate reminders off ("Remind candidates" in the
# builder). Rows that did not come through a workflow have no enrolment or no
# workflow and keep the default, which is on. Both reminder queries and the
# expiry notice share this, so the switch means the same thing everywhere.
_REMINDERS_ON = "COALESCE(wf.reminders_enabled, true)"

# How far past a deadline the expiry sweep still looks. Generous relative to the
# hourly interval so a missed sweep (or a sleeping Space) still catches it on
# the next pass rather than the notice being lost for good.
_EXPIRY_LOOKBACK = timedelta(hours=48)

# Cap per sweep per category — keeps one pass bounded on a large tenant.
_BATCH = 200


@dataclass
class SweepResult:
    exam_reminders: int = 0
    interview_reminders: int = 0
    expiry_notices: int = 0
    results_emails: int = 0
    completions: int = 0

    def total(self) -> int:
        return (
            self.exam_reminders
            + self.interview_reminders
            + self.expiry_notices
            + self.completions
            + self.results_emails
        )


def _fmt(dt: datetime | None) -> str | None:
    """Deadline formatting for email copy — IST, since every candidate is in India.

    Rendered here rather than in the template because the templates take
    pre-formatted strings (see the existing exam_link builder), and because a
    reader should see one timezone rather than guessing at UTC.
    """
    if dt is None:
        return None
    ist = dt.astimezone(UTC) + timedelta(hours=5, minutes=30)
    return ist.strftime("%d %b %Y, %I:%M %p IST")


async def _lang_for_user(db: AsyncSession, user_id: uuid.UUID | None) -> str:
    """The candidate's language when we know it, English when we do not.

    An applicant row carries no language of its own — only a linked user account
    does. Defaulting to English is a real limitation of reminders to
    HR-uploaded applicants, not a claim that English is correct for them.
    """
    if user_id is None:
        return "en"
    lang = await db.scalar(
        text("SELECT preferred_language FROM users WHERE id = :uid AND deleted_at IS NULL"),
        {"uid": user_id},
    )
    return str(lang or "en")


# ---------------------------------------------------------------------------
# 1. Exam links about to expire
# ---------------------------------------------------------------------------
_EXAM_DUE_SQL = f"""
SELECT asg.id, asg.expires_at, asg.scheduled_at,
       a.id AS applicant_id, a.full_name, a.email, a.user_id,
       e.title AS exam_title, r.title AS round_title, asg.company_id
  FROM exam_assignments asg
  JOIN applicants a ON a.id = asg.applicant_id AND a.deleted_at IS NULL
  JOIN exams       e ON e.id = asg.exam_id
  LEFT JOIN exam_rounds r ON r.id = asg.round_id
  LEFT JOIN enrolments en ON en.id = asg.enrolment_id
  LEFT JOIN workflows  wf ON wf.id = en.workflow_id
 WHERE asg.status = 'invited'
   AND asg.deleted_at IS NULL
   AND asg.consumed_at IS NULL
   AND a.email IS NOT NULL
   AND {_REMINDERS_ON}
   AND asg.expires_at > :near
   AND asg.expires_at <= :horizon
 ORDER BY asg.expires_at
 LIMIT :lim
"""


async def _exam_reminders(db: AsyncSession, result: SweepResult) -> None:
    now = datetime.now(tz=UTC)
    for label, near, far in _WINDOWS:
        rows = (
            await db.execute(
                text(_EXAM_DUE_SQL),
                {"near": now + near, "horizon": now + far, "lim": _BATCH},
            )
        ).mappings().all()
        for r in rows:
            sent = await enqueue_email(
                db,
                to=r["email"],
                template="exam_reminder",
                lang=await _lang_for_user(db, r["user_id"]),
                ctx={
                    "name": r["full_name"],
                    "exam_title": r["round_title"] or r["exam_title"] or "",
                    "expires": _fmt(r["expires_at"]),
                    "when": _fmt(r["scheduled_at"]),
                    "window": label,
                },
                company_id=r["company_id"],
                related_kind="exam_assignment",
                related_id=r["id"],
                dedupe_key=f"exam_reminder:{r['id']}:{label}",
            )
            if sent is not None:
                result.exam_reminders += 1
    await db.commit()


# ---------------------------------------------------------------------------
# 2. Interviews about to start (or whose link is about to lapse)
# ---------------------------------------------------------------------------
_INTERVIEW_DUE_SQL = f"""
SELECT inv.id, inv.expires_at, inv.scheduled_at, inv.language, inv.company_id,
       a.full_name, a.email, a.user_id,
       j.title AS job_title
  FROM interview_invites inv
  JOIN applicants a ON a.id = inv.applicant_id AND a.deleted_at IS NULL
  LEFT JOIN jobs j ON j.id = inv.job_id
  LEFT JOIN enrolments en ON en.id = inv.enrolment_id
  LEFT JOIN workflows  wf ON wf.id = en.workflow_id
 WHERE inv.status = 'invited'
   AND inv.deleted_at IS NULL
   AND inv.consumed_at IS NULL
   AND a.email IS NOT NULL
   AND {_REMINDERS_ON}
   AND COALESCE(inv.scheduled_at, inv.expires_at) > :near
   AND COALESCE(inv.scheduled_at, inv.expires_at) <= :horizon
 ORDER BY COALESCE(inv.scheduled_at, inv.expires_at)
 LIMIT :lim
"""


def interview_reminder_key(invite_id: uuid.UUID | str, window: str) -> str:
    return f"interview_reminder:{invite_id}:{window}"


async def rearm_interview_reminders(db: AsyncSession, invite_id: uuid.UUID) -> None:
    """Let a rescheduled interview be reminded about its new slot.

    The reminder keys name the invite and the window, so once the 24h and 1h
    reminders had gone out for the original slot, the new slot matched keys
    that were already spent and the candidate heard nothing about the time
    they actually have to turn up.

    The sent rows are kept — they are the delivery log — and only their keys
    are retired, suffixed with their own id so each stays unique. The key
    format itself is unchanged, which is what keeps this safe to deploy: a
    format that embedded the slot time would have re-sent every reminder
    already delivered for an invite still inside its window. Caller commits.
    """
    await db.execute(
        text(
            "UPDATE email_events"
            "   SET dedupe_key = dedupe_key || ':superseded:' || id::text"
            " WHERE dedupe_key IN (:k24, :k1)"
        ),
        {
            "k24": interview_reminder_key(invite_id, "24h"),
            "k1": interview_reminder_key(invite_id, "1h"),
        },
    )


async def _interview_reminders(db: AsyncSession, result: SweepResult) -> None:
    now = datetime.now(tz=UTC)
    for label, near, far in _WINDOWS:
        rows = (
            await db.execute(
                text(_INTERVIEW_DUE_SQL),
                {"near": now + near, "horizon": now + far, "lim": _BATCH},
            )
        ).mappings().all()
        for r in rows:
            sent = await enqueue_email(
                db,
                to=r["email"],
                template="interview_reminder",
                # The invite records the language the interview will be
                # conducted in, so the reminder matches the interview itself.
                lang=str(r["language"] or "en"),
                ctx={
                    "name": r["full_name"],
                    "job_title": r["job_title"] or "",
                    "when": _fmt(r["scheduled_at"]),
                    "expires": _fmt(r["expires_at"]),
                    "window": label,
                },
                company_id=r["company_id"],
                related_kind="interview_invite",
                related_id=r["id"],
                dedupe_key=interview_reminder_key(r["id"], label),
            )
            if sent is not None:
                result.interview_reminders += 1
    await db.commit()


# ---------------------------------------------------------------------------
# 3. Links that lapsed unused — tell the candidate, and tell the owner
# ---------------------------------------------------------------------------
# Each lapsed link carries two independent obligations, and a row stays in the
# result only while one of them is still owed:
#
#   * the candidate email — owed when there is a usable address and the
#     workflow has reminders on, and nothing has been staged under its key;
#   * the owner notification — owed when there is an owner and they have not
#     been told under either key.
#
# They used to be one obligation. The owner was told only if the candidate
# email was staged, so a candidate with no address on file lapsed in silence on
# both sides — and, having no key, came back on every sweep and took one of the
# batch slots. The owner check accepts the email key as proof as well as its
# own, because before notifications had keys the two were always staged in the
# same transaction; without that, every lapse still inside the lookback on the
# day this shipped would have been announced to HR a second time.
#
# The owner is whoever created the link, falling back to the workflow's owner.
# Links a workflow issues automatically carry no creator, and those lapses
# reached nobody.
#
# `a.email LIKE '%@%'` mirrors enqueue_email's own validity check, so a row the
# mailer would refuse is not counted as owing an email it can never send.
_LAPSED_SQL = f"""
SELECT * FROM (
  SELECT 'exam' AS kind, asg.id, asg.expires_at, asg.company_id,
         COALESCE(asg.created_by_user_id, wf.created_by_user_id) AS owner_user_id,
         a.full_name, a.email, a.user_id, COALESCE(r.title, e.title) AS what,
         (a.email LIKE '%@%' AND {_REMINDERS_ON}) AS mail_candidate
    FROM exam_assignments asg
    JOIN applicants a ON a.id = asg.applicant_id AND a.deleted_at IS NULL
    JOIN exams       e ON e.id = asg.exam_id
    LEFT JOIN exam_rounds r ON r.id = asg.round_id
    LEFT JOIN enrolments en ON en.id = asg.enrolment_id
    LEFT JOIN workflows  wf ON wf.id = en.workflow_id
   WHERE asg.status = 'invited' AND asg.deleted_at IS NULL AND asg.consumed_at IS NULL
     AND asg.expires_at <= :now AND asg.expires_at > :floor
  UNION ALL
  SELECT 'interview' AS kind, inv.id, inv.expires_at, inv.company_id,
         COALESCE(inv.created_by_user_id, wf.created_by_user_id) AS owner_user_id,
         a.full_name, a.email, a.user_id, j.title AS what,
         (a.email LIKE '%@%' AND {_REMINDERS_ON}) AS mail_candidate
    FROM interview_invites inv
    JOIN applicants a ON a.id = inv.applicant_id AND a.deleted_at IS NULL
    LEFT JOIN jobs j ON j.id = inv.job_id
    LEFT JOIN enrolments en ON en.id = inv.enrolment_id
    LEFT JOIN workflows  wf ON wf.id = en.workflow_id
   WHERE inv.status = 'invited' AND inv.deleted_at IS NULL AND inv.consumed_at IS NULL
     AND inv.expires_at <= :now AND inv.expires_at > :floor
) lapsed
 WHERE (
         mail_candidate
         AND NOT EXISTS (SELECT 1 FROM email_events ee
                          WHERE ee.dedupe_key = 'expiry_notice:' || lapsed.kind || ':' || lapsed.id::text)
       )
    OR (
         owner_user_id IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM notifications n
                          WHERE n.dedupe_key = 'link_expired:' || lapsed.kind || ':' || lapsed.id::text)
         AND NOT EXISTS (SELECT 1 FROM email_events ee
                          WHERE ee.dedupe_key = 'expiry_notice:' || lapsed.kind || ':' || lapsed.id::text)
       )
 ORDER BY expires_at
 LIMIT :lim
"""


async def _expiry_notices(db: AsyncSession, result: SweepResult) -> None:
    """Notify on a lapsed link.

    Deliberately two audiences with different messages. The candidate is told
    neutrally that the window closed and that the employer has been informed —
    never that they have been rejected, because they have not been: under D-05
    only a person ends a candidacy. The owner gets an in-app notification so the
    lapse appears as work rather than as silence.

    The two are independent: neither waits on the other, and each has its own
    key. See _LAPSED_SQL for why that matters.
    """
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(_LAPSED_SQL),
            {"now": now, "floor": now - _EXPIRY_LOOKBACK, "lim": _BATCH},
        )
    ).mappings().all()

    for r in rows:
        if r["mail_candidate"]:
            sent = await enqueue_email(
                db,
                to=r["email"],
                template="link_expired",
                lang=await _lang_for_user(db, r["user_id"]),
                ctx={
                    "name": r["full_name"],
                    "what": r["what"] or "",
                    "kind": r["kind"],
                    "expired": _fmt(r["expires_at"]),
                },
                company_id=r["company_id"],
                related_kind=f"{r['kind']}_expiry",
                related_id=r["id"],
                dedupe_key=f"expiry_notice:{r['kind']}:{r['id']}",
            )
            if sent is not None:
                result.expiry_notices += 1

        await create_notification(
            db,
            user_id=r["owner_user_id"],
            kind="link_expired",
            title=f"{r['full_name']} did not use their {r['kind']} link",
            body=(
                f"The link for {r['what'] or r['kind']} expired on "
                f"{_fmt(r['expires_at'])} without being opened."
            ),
            link="/hr/applicants",
            dedupe_key=f"link_expired:{r['kind']}:{r['id']}",
        )
    await db.commit()


# ---------------------------------------------------------------------------
# 4. Results ready — the email nobody was sending
# ---------------------------------------------------------------------------
# Found by absence: a scorecard exists for the invite's session, and no email
# with this scorecard's dedupe key has ever been staged. That means it also
# picks up every historical scorecard on first run, which is why the floor
# below exists — a deploy should not email six months of past candidates.
_RESULTS_SQL = """
SELECT sc.scorecard_id, sc.created_at, inv.company_id, inv.language,
       a.full_name, a.email, a.user_id, j.title AS job_title
  FROM scorecards sc
  JOIN interview_invites inv ON inv.session_id = sc.session_id AND inv.deleted_at IS NULL
  JOIN applicants a ON a.id = inv.applicant_id AND a.deleted_at IS NULL
  LEFT JOIN jobs j ON j.id = inv.job_id
 WHERE a.email IS NOT NULL
   AND sc.created_at > :floor
   AND NOT EXISTS (
         SELECT 1 FROM email_events ee
          WHERE ee.dedupe_key = 'results_ready:' || sc.scorecard_id::text
       )
 ORDER BY sc.created_at
 LIMIT :lim
"""


async def _results_ready(db: AsyncSession, result: SweepResult) -> None:
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(_RESULTS_SQL),
            {"floor": now - timedelta(days=7), "lim": _BATCH},
        )
    ).mappings().all()

    for r in rows:
        sent = await enqueue_email(
            db,
            to=r["email"],
            template="results_ready",
            lang=str(r["language"] or "en"),
            ctx={
                "name": r["full_name"],
                "job_title": r["job_title"] or "",
                # No score in the email. The scorecard is the place a candidate
                # reads their result, in context and with the improvement notes;
                # a bare number in an inbox is the worst possible framing of it.
                "cta_url": None,
            },
            company_id=r["company_id"],
            related_kind="scorecard",
            related_id=r["scorecard_id"],
            dedupe_key=f"results_ready:{r['scorecard_id']}",
        )
        if sent is not None:
            result.results_emails += 1

        # In-app as well as by email, and NOT conditional on the email having
        # been staged. `a.user_id` was already selected here and never used, so
        # a candidate whose mail bounced, was filtered, or went to a local sink
        # had no way to learn their scorecard existed — the same failure the
        # interview invitation had, at the other end of the journey.
        #
        # The dedupe_key above guards the email; this is guarded by running only
        # for rows the email query found, which are rows nobody has been told
        # about yet — once staged, the NOT EXISTS excludes the row for good.
        #
        # Boundary worth stating: _RESULTS_SQL requires `a.email IS NOT NULL`,
        # so an applicant with no address on file is still told nothing. It is
        # close to unreachable — public applications require an address, and
        # create_notification is a no-op without a user_id anyway.
        #
        # Its own key as well, so two sweeps that both read the row before
        # either staged the email cannot both put it in the candidate's bell.
        await create_notification(
            db,
            user_id=r["user_id"],
            kind="results_ready",
            title="Your interview results are ready",
            body=(
                (f"{r['job_title']} · " if r["job_title"] else "")
                + "Open your applications to read the full scorecard."
            ),
            link="/applications",
            dedupe_key=f"results_ready:{r['scorecard_id']}",
        )
    await db.commit()


# ---------------------------------------------------------------------------
# 5. Interview completed — telling HR without waiting to be asked
# ---------------------------------------------------------------------------
# The invite's consumed -> completed transition, and the notification that goes
# with it, used to happen inside GET /hr/interviews. That made a read path a
# writer, and meant the event had no announcer: HR learned that a candidate had
# finished only if HR happened to open that page. Nobody opens the page, nobody
# is told, and the invite sits at 'consumed' indefinitely.
#
# Here it is found the same way everything else in this file is — by absence. A
# scorecard exists for the invite's session and the invite has not been closed
# out yet. The status flip is the primary idempotency guard: it is a one-way
# transition on a single row, so the row stops matching the moment it is
# announced — provided only the sweep whose UPDATE actually flipped the row
# announces it. The notification's dedupe key is the backstop for that.
_COMPLETED_SQL = """
SELECT inv.id, inv.company_id, inv.created_by_user_id, a.full_name, j.title AS job_title
  FROM interview_invites inv
  JOIN scorecards sc ON sc.session_id = inv.session_id
  JOIN applicants a  ON a.id = inv.applicant_id AND a.deleted_at IS NULL
  LEFT JOIN jobs j   ON j.id = inv.job_id
 WHERE inv.deleted_at IS NULL
   AND inv.status = 'consumed'
 ORDER BY sc.created_at
 LIMIT :lim
"""


async def _interview_completed(db: AsyncSession, result: SweepResult) -> None:
    rows = (
        await db.execute(text(_COMPLETED_SQL), {"lim": _BATCH})
    ).mappings().all()

    for r in rows:
        flipped = await db.execute(
            text(
                "UPDATE interview_invites SET status = 'completed', updated_at = now() "
                "WHERE id = :id AND status = 'consumed'"
            ),
            {"id": r["id"]},
        )
        # The guard only guards if its answer is read. Two sweeps can both
        # select the row while it is still 'consumed'; the second one's UPDATE
        # then matches nothing, and announcing anyway is a duplicate. The
        # notification's own key backs this up at the database.
        if flipped.rowcount != 1:
            continue
        # In-app only. There is no interview_completed email template and never
        # has been — a comment in hr_interviews.py claimed one had been "moved"
        # to the redemption path, but git history shows the claim arrived with
        # the initial import and the template was never written. HR lives in the
        # console; the candidate is the one who needs mail, and gets it from
        # _results_ready above.
        await create_notification(
            db,
            user_id=r["created_by_user_id"],
            kind="interview_completed",
            title="Interview completed",
            body=(
                f"{r['full_name']} finished their interview"
                + (f" · {r['job_title']}" if r["job_title"] else "")
                + " — scorecard ready"
            ),
            link="/hr/interviews",
            dedupe_key=f"interview_completed:{r['id']}",
        )
        result.completions += 1
    await db.commit()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
async def run_once(factory: async_sessionmaker[AsyncSession]) -> SweepResult:
    """One full sweep. Each stage is independent — one failing does not stop the rest."""
    result = SweepResult()
    async with factory() as db:
        for name, fn in (
            ("exam", _exam_reminders),
            ("interview", _interview_reminders),
            ("expiry", _expiry_notices),
            ("results", _results_ready),
            ("completed", _interview_completed),
        ):
            try:
                await fn(db, result)
            except Exception as exc:  # noqa: BLE001 — one stage must not sink the sweep
                await db.rollback()
                log.error(
                    "reminders.stage_failed",
                    stage=name,
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )
    if result.total():
        log.info("reminders.sweep", **asdict(result))
    return result


_task: asyncio.Task[None] | None = None


async def _loop(factory: async_sessionmaker[AsyncSession]) -> None:
    interval = max(300, settings.reminders_interval_seconds)
    await asyncio.sleep(min(60, interval))
    while True:
        try:
            await run_once(factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("reminders.sweep_failed", exc_type=type(exc).__name__, exc_msg=str(exc))
        await asyncio.sleep(interval)


def start(factory: async_sessionmaker[AsyncSession]) -> None:
    global _task
    if not settings.reminders_enabled:
        log.info("reminders.disabled")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(factory), name="reminder-sweep")
    log.info("reminders.started", interval_s=settings.reminders_interval_seconds)


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 — shutdown must not raise
        pass
    _task = None
    log.info("reminders.stopped")
