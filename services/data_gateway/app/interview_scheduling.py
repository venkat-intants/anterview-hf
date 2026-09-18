"""Interview scheduling and loops — PH4-A2.

When people meet, who is free, and the one itinerary a candidate receives.

WHAT HOLDS WHERE
- No double booking (an interviewer, or a candidate plus the loop's buffer) is
  an exclusion constraint (migration e6f8a0b2c4d6). The checks here run first so
  a person reads a sentence rather than a constraint name, and a race between
  two writers still ends with one refused — ``_conflict`` turns that refusal into
  the same sentence.
- A session's interviewers get their PH4-A1 scorecards through
  ``interviewer_scorecards.assign`` — eligibility, independence, an erased or
  decided candidate are refused there exactly as for an assignment made from the
  drawer. The scorecard is due a fixed grace after the interview ends.
- A candidate may BOOK an offered slot, once. There is no candidate route that
  moves or cancels a booked session (A2 #23): changing one is HR's, audited.
- Nothing here moves a candidate. No stage transition, no decision.

Every write is audited with facts, never prose (a cancel reason's length, not
its text). Callers commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import interviewer_scorecards as cards
from app.config import settings
from app.interviewer_scorecards import RequestMeta, ScorecardError
from app.mailer import candidate_language, enqueue_email
from app.models import AuditLog
from app.notifications_util import create_notification
from app.requisitions import TERMINAL_STATUSES
from app.schedule_core import (
    HORIZON,
    IcsEvent,
    ScheduleError,
    SlotQuery,
    aware,
    covered,
    ics,
    local_label,
    merge,
    slots,
    subtract,
    valid_timezone,
)

log = structlog.get_logger()

SCORECARD_GRACE = timedelta(days=2)
MAX_SESSIONS_PER_LOOP = 8
MAX_INTERVIEWERS_PER_SESSION = 6
REASON_MAX = 500
LOCATION_MAX = 500
LIVE_SESSION = "scheduled"


class SchedulingError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any],
    meta: RequestMeta,
    actor_type: str = "user",
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            event_ts=datetime.now(tz=UTC),
        )
    )


def _conflict(exc: IntegrityError) -> SchedulingError | None:
    """The sentence for an exclusion constraint that refused a write, or None."""
    msg = str(exc.orig)
    if "ex_session_interviewers_overlap" in msg:
        return SchedulingError(
            409, "One of the interviewers is already booked at that time. "
                 "Choose another time or another interviewer."
        )
    if "ex_interview_sessions_candidate_overlap" in msg:
        return SchedulingError(
            409, "The candidate already has an interview then, or within the gap this loop "
                 "leaves between sessions. Choose another time."
        )
    if "ex_interviewer_availability_overlap" in msg:
        return SchedulingError(409, "That overlaps a window already set. Adjust or remove it first.")
    return None


def _clean_text(value: str | None, *, limit: int, field: str) -> str | None:
    cleaned = (value or "").strip() or None
    if cleaned and len(cleaned) > limit:
        raise SchedulingError(422, f"The {field} is at most {limit} characters.")
    return cleaned


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
async def _assert_interviewer(db: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID) -> None:
    people = await cards.list_assignable_interviewers(db, company_id=company_id)
    if str(user_id) not in {p["user_id"] for p in people}:
        raise SchedulingError(
            404, "Only active interviewers and HR managers in your company have availability."
        )


async def list_availability(
    db: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID,
    start: datetime, end: datetime,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, starts_at, ends_at FROM interviewer_availability"
                " WHERE company_id = :c AND user_id = :u AND deleted_at IS NULL"
                "   AND ends_at > :s AND starts_at < :e ORDER BY starts_at LIMIT 500"
            ),
            {"c": company_id, "u": user_id, "s": start, "e": end},
        )
    ).mappings().all()
    return [{"id": str(r["id"]), "starts_at": r["starts_at"].isoformat(),
             "ends_at": r["ends_at"].isoformat()} for r in rows]


async def add_availability(
    db: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID,
    starts_at: datetime, ends_at: datetime, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    try:
        start, end = aware(starts_at, field="start"), aware(ends_at, field="end")
    except ScheduleError as exc:
        raise SchedulingError(422, str(exc)) from exc
    if end <= start:
        raise SchedulingError(422, "A window must end after it starts.")
    if end - start > timedelta(days=14):
        raise SchedulingError(422, "A single window is at most 14 days; add more than one.")
    if end <= datetime.now(tz=UTC):
        raise SchedulingError(422, "That window is already over.")
    await _assert_interviewer(db, company_id=company_id, user_id=user_id)
    window_id = uuid.uuid4()
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(
                "INSERT INTO interviewer_availability (id, company_id, user_id, starts_at,"
                " ends_at, created_by_user_id) VALUES (:i, :c, :u, :s, :e, :a)"
            ),
            {"i": window_id, "c": company_id, "u": user_id, "s": start, "e": end, "a": actor},
        )
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        raise (_conflict(exc) or exc) from exc
    _audit(db, actor_id=actor, action="availability.added", resource_type="user",
           resource_id=user_id, meta=meta,
           details={"company_id": str(company_id), "window_id": str(window_id),
                    "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                    "set_by_self": actor == user_id})
    return {"id": str(window_id), "starts_at": start.isoformat(), "ends_at": end.isoformat()}


async def remove_availability(
    db: AsyncSession, *, company_id: uuid.UUID, window_id: uuid.UUID, actor: uuid.UUID,
    meta: RequestMeta, only_user: uuid.UUID | None = None,
) -> None:
    """Remove a window. ``only_user`` scopes it to the caller's own (interviewer console)."""
    row = (
        await db.execute(
            text(
                "UPDATE interviewer_availability SET deleted_at = now(), updated_at = now()"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
                "   AND (CAST(:u AS uuid) IS NULL OR user_id = CAST(:u AS uuid))"
                " RETURNING user_id"
            ),
            {"i": window_id, "c": company_id, "u": only_user},
        )
    ).first()
    if row is None:
        raise SchedulingError(404, "Window not found.")
    _audit(db, actor_id=actor, action="availability.removed", resource_type="user",
           resource_id=row[0], meta=meta,
           details={"company_id": str(company_id), "window_id": str(window_id)})


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------
_ENROLMENT_SQL = """
SELECT e.id, e.status, e.workflow_id, e.applicant_id, e.requisition_id,
       a.full_name, a.email, a.user_id AS applicant_user_id,
       COALESCE(jr.title, e.target_job_title) AS job_title,
       ((a.full_name = '[redacted]' AND a.email IS NULL)
        OR EXISTS (SELECT 1 FROM erasure_requests er WHERE er.user_id = a.user_id))
         AS candidate_erased
  FROM enrolments e
  JOIN applicants a ON a.id = e.applicant_id
  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id
 WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL
 FOR SHARE OF e
"""


async def _live_enrolment(db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID) -> dict[str, Any]:
    e = (await db.execute(text(_ENROLMENT_SQL), {"e": enrolment_id, "c": company_id})).mappings().first()
    if e is None:
        raise SchedulingError(404, "Application not found.")
    if e["candidate_erased"]:
        raise SchedulingError(
            409, "This candidate's personal data has been erased or is being erased, so "
                 "nothing more can be scheduled for them."
        )
    if e["status"] in TERMINAL_STATUSES:
        raise SchedulingError(
            409, f"A final decision ({e['status']}) is already recorded for this application."
        )
    return dict(e)


async def create_loop(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, title: str,
    candidate_timezone: str, buffer_minutes: int, self_schedule: bool,
    actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    e = await _live_enrolment(db, company_id=company_id, enrolment_id=enrolment_id)
    name = _clean_text(title, limit=200, field="title") or "Interviews"
    try:
        tz = valid_timezone(candidate_timezone)
    except ScheduleError as exc:
        raise SchedulingError(422, str(exc)) from exc
    if not 0 <= buffer_minutes <= 240:
        raise SchedulingError(422, "The gap between sessions is 0 to 240 minutes.")
    loop_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interview_loops (id, company_id, enrolment_id, applicant_id, title,"
            " candidate_timezone, buffer_minutes, self_schedule, created_by_user_id)"
            " VALUES (:i, :c, :e, :a, :t, :tz, :b, :ss, :u)"
        ),
        {"i": loop_id, "c": company_id, "e": enrolment_id, "a": e["applicant_id"], "t": name,
         "tz": tz, "b": buffer_minutes, "ss": self_schedule, "u": actor},
    )
    _audit(db, actor_id=actor, action="loop.created", resource_type="interview_loop",
           resource_id=loop_id, meta=meta,
           details={"company_id": str(company_id), "enrolment_id": str(enrolment_id),
                    "timezone": tz, "buffer_minutes": buffer_minutes,
                    "self_schedule": self_schedule})
    return {"loop_id": str(loop_id)}


_LOOP_SQL = """
SELECT l.*, e.status AS enrolment_status, e.workflow_id
  FROM interview_loops l
  JOIN enrolments e ON e.id = l.enrolment_id
 WHERE l.id = :l AND l.company_id = :c
 FOR UPDATE OF l
"""


async def _load_loop(db: AsyncSession, *, company_id: uuid.UUID, loop_id: uuid.UUID) -> dict[str, Any]:
    loop = (await db.execute(text(_LOOP_SQL), {"l": loop_id, "c": company_id})).mappings().first()
    if loop is None:
        raise SchedulingError(404, "Interview loop not found.")
    return dict(loop)


def _assert_open(loop: dict[str, Any]) -> None:
    if loop["status"] in ("cancelled", "completed"):
        raise SchedulingError(409, f"This loop is {loop['status']}; nothing more can change in it.")


_SESSIONS_SQL = """
SELECT s.id, s.loop_id, s.round_id, s.title, s.position, s.duration_minutes, s.starts_at,
       s.ends_at, s.blocked_until, s.location, s.status, s.booked_by, s.booked_at,
       s.cancelled_at, s.updated_at, wr.title AS round_title,
       COALESCE(json_agg(json_build_object(
           'user_id', si.interviewer_user_id, 'name', COALESCE(u.full_name, u.email),
           'scorecard_id', si.scorecard_id, 'scorecard_status', sc.status))
         FILTER (WHERE si.session_id IS NOT NULL), '[]') AS interviewers
  FROM interview_sessions s
  JOIN workflow_rounds wr ON wr.id = s.round_id
  LEFT JOIN interview_session_interviewers si ON si.session_id = s.id
  LEFT JOIN users u ON u.id = si.interviewer_user_id
  LEFT JOIN interviewer_scorecards sc ON sc.id = si.scorecard_id
 WHERE s.loop_id = :l
 GROUP BY s.id, wr.title
 ORDER BY s.position, s.starts_at NULLS LAST
"""


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _session_out(r: Any) -> dict[str, Any]:
    return {
        "id": str(r["id"]), "round_id": str(r["round_id"]), "round_title": r["round_title"],
        "title": r["title"], "position": r["position"],
        "duration_minutes": r["duration_minutes"], "starts_at": _iso(r["starts_at"]),
        "ends_at": _iso(r["ends_at"]), "location": r["location"], "status": r["status"],
        "booked_by": r["booked_by"], "interviewers": [
            {**i, "user_id": str(i["user_id"]),
             "scorecard_id": str(i["scorecard_id"]) if i.get("scorecard_id") else None}
            for i in (r["interviewers"] or [])
        ],
    }


async def _sessions(db: AsyncSession, loop_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text(_SESSIONS_SQL), {"l": loop_id})).mappings().all()
    return [_session_out(r) for r in rows]


def _loop_out(loop: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(loop["id"]), "enrolment_id": str(loop["enrolment_id"]),
        "title": loop["title"], "status": loop["status"],
        "candidate_timezone": loop["candidate_timezone"],
        "buffer_minutes": loop["buffer_minutes"], "self_schedule": loop["self_schedule"],
        "sent_at": _iso(loop["sent_at"]), "itinerary_version": loop["itinerary_version"],
        "cancelled_at": _iso(loop["cancelled_at"]), "sessions": sessions,
    }


async def get_loop(db: AsyncSession, *, company_id: uuid.UUID, loop_id: uuid.UUID) -> dict[str, Any]:
    loop = (
        await db.execute(
            text("SELECT * FROM interview_loops WHERE id = :l AND company_id = :c"),
            {"l": loop_id, "c": company_id},
        )
    ).mappings().first()
    if loop is None:
        raise SchedulingError(404, "Interview loop not found.")
    return _loop_out(dict(loop), await _sessions(db, loop_id))


async def loops_for_enrolment(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT * FROM interview_loops WHERE enrolment_id = :e AND company_id = :c"
                " ORDER BY created_at DESC LIMIT 50"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    return [_loop_out(dict(r), await _sessions(db, r["id"])) for r in rows]


async def _refresh_loop_status(db: AsyncSession, loop_id: uuid.UUID) -> str:
    """Derive the loop's status from its sessions; a cancelled loop stays so.

    One rule, shared with close_for_decision: once no session is pending
    (scheduled or awaiting a slot), the loop is COMPLETED if any session was
    held — completed, or the candidate did not show — and CANCELLED if none
    was. Until then it is scheduled once sent, draft before.
    """
    row = (
        await db.execute(
            text(
                "SELECT l.status, l.sent_at, count(s.id) AS total,"
                "       count(*) FILTER (WHERE s.status IN ('scheduled', 'awaiting_slot'))"
                "         AS pending,"
                "       count(*) FILTER (WHERE s.status IN ('completed', 'no_show')) AS held"
                "  FROM interview_loops l LEFT JOIN interview_sessions s ON s.loop_id = l.id"
                " WHERE l.id = :l GROUP BY l.id"
            ),
            {"l": loop_id},
        )
    ).mappings().first()
    if row is None or row["status"] == "cancelled":
        return row["status"] if row else "cancelled"
    if row["total"] and not row["pending"]:
        new = "completed" if row["held"] else "cancelled"
    elif row["sent_at"] is not None:
        new = "scheduled"
    else:
        new = "draft"
    if new != row["status"]:
        await db.execute(
            text(
                "UPDATE interview_loops SET status = :s, updated_at = now(),"
                " cancelled_at = CASE WHEN :s = 'cancelled' THEN now() END WHERE id = :l"
            ),
            {"s": new, "l": loop_id},
        )
    return new


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
async def _availability_windows(
    db: AsyncSession, company_id: uuid.UUID, user_ids: list[uuid.UUID], start: datetime,
    end: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    rows = (
        await db.execute(
            text(
                "SELECT user_id, starts_at, ends_at FROM interviewer_availability"
                " WHERE company_id = :c AND user_id = ANY(:u) AND deleted_at IS NULL"
                "   AND ends_at > :s AND starts_at < :e"
            ),
            {"c": company_id, "u": user_ids, "s": start, "e": end},
        )
    ).mappings().all()
    out: dict[str, list[tuple[datetime, datetime]]] = {str(u): [] for u in user_ids}
    for r in rows:
        out[str(r["user_id"])].append((r["starts_at"], r["ends_at"]))
    return out


async def _bookings(
    db: AsyncSession, company_id: uuid.UUID, user_ids: list[uuid.UUID], start: datetime,
    end: datetime, *, except_session: uuid.UUID | None = None,
) -> dict[str, list[tuple[datetime, datetime]]]:
    rows = (
        await db.execute(
            text(
                "SELECT interviewer_user_id, starts_at, ends_at FROM interview_session_interviewers"
                " WHERE company_id = :c AND interviewer_user_id = ANY(:u) AND live"
                "   AND ends_at > :s AND starts_at < :e"
                "   AND (CAST(:x AS uuid) IS NULL OR session_id <> CAST(:x AS uuid))"
            ),
            {"c": company_id, "u": user_ids, "s": start, "e": end, "x": except_session},
        )
    ).mappings().all()
    out: dict[str, list[tuple[datetime, datetime]]] = {str(u): [] for u in user_ids}
    for r in rows:
        out[str(r["interviewer_user_id"])].append((r["starts_at"], r["ends_at"]))
    return out


async def _candidate_busy(
    db: AsyncSession, applicant_id: uuid.UUID, start: datetime, end: datetime,
    *, except_session: uuid.UUID | None = None,
) -> list[tuple[datetime, datetime]]:
    rows = (
        await db.execute(
            text(
                "SELECT starts_at, blocked_until FROM interview_sessions"
                " WHERE applicant_id = :a AND status = 'scheduled'"
                "   AND blocked_until > :s AND starts_at < :e"
                "   AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"
            ),
            {"a": applicant_id, "s": start, "e": end, "x": except_session},
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


def _times(starts_at: datetime, duration: int, buffer: int) -> tuple[datetime, datetime, datetime]:
    start = aware(starts_at, field="start time")
    if start.second or start.microsecond:
        start = start.replace(second=0, microsecond=0)
    end = start + timedelta(minutes=duration)
    return start, end, end + timedelta(minutes=buffer)


async def _outside_availability(
    db: AsyncSession, company_id: uuid.UUID, interviewer_ids: list[uuid.UUID], start: datetime,
    end: datetime,
) -> list[str]:
    """Interviewers (ids) for whom [start, end) is not inside a window they set."""
    windows = await _availability_windows(
        db, company_id, interviewer_ids, start - timedelta(days=15), end
    )
    return [uid for uid, ws in windows.items() if not covered(start, end, ws)]


async def add_session(
    db: AsyncSession, *, company_id: uuid.UUID, loop_id: uuid.UUID, round_id: uuid.UUID,
    title: str | None, duration_minutes: int, interviewer_ids: list[uuid.UUID],
    starts_at: datetime | None, location: str | None, allow_outside_availability: bool,
    actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    loop = await _load_loop(db, company_id=company_id, loop_id=loop_id)
    _assert_open(loop)
    e = await _live_enrolment(db, company_id=company_id, enrolment_id=loop["enrolment_id"])
    ids = list(dict.fromkeys(interviewer_ids))
    if not ids:
        raise SchedulingError(422, "Choose at least one interviewer.")
    if len(ids) > MAX_INTERVIEWERS_PER_SESSION:
        raise SchedulingError(422, f"A session has at most {MAX_INTERVIEWERS_PER_SESSION} interviewers.")
    if not 15 <= duration_minutes <= 480:
        raise SchedulingError(422, "A session lasts 15 minutes to 8 hours.")
    # Eligibility FIRST: nothing about a person is looked up — not even whether
    # they are free — until they are known to be this company's interviewer
    # (security review L1: availability was probed before this check).
    eligible = {
        p["user_id"] for p in await cards.list_assignable_interviewers(db, company_id=company_id)
    }
    if any(str(i) not in eligible for i in ids):
        raise SchedulingError(
            422, "Only active interviewers and HR managers in your company can be scheduled."
        )
    count = await db.scalar(
        text("SELECT count(*) FROM interview_sessions WHERE loop_id = :l AND status <> 'cancelled'"),
        {"l": loop_id},
    )
    if int(count or 0) >= MAX_SESSIONS_PER_LOOP:
        raise SchedulingError(422, f"A loop has at most {MAX_SESSIONS_PER_LOOP} sessions.")
    place = _clean_text(location, limit=LOCATION_MAX, field="location")
    round_title = await db.scalar(
        text("SELECT title FROM workflow_rounds WHERE id = :r AND company_id = :c AND deleted_at IS NULL"),
        {"r": round_id, "c": company_id},
    )
    if round_title is None:
        raise SchedulingError(404, "Round not found.")
    name = _clean_text(title, limit=200, field="title") or str(round_title)

    if starts_at is None:
        if not loop["self_schedule"]:
            raise SchedulingError(
                422, "Give this session a time, or turn on candidate self-scheduling for the loop."
            )
        start = end = blocked = None
        status = "awaiting_slot"
    else:
        try:
            start, end, blocked = _times(starts_at, duration_minutes, loop["buffer_minutes"])
        except ScheduleError as exc:
            raise SchedulingError(422, str(exc)) from exc
        if start <= datetime.now(tz=UTC):
            raise SchedulingError(422, "The session must start in the future.")
        if not allow_outside_availability:
            outside = await _outside_availability(db, company_id, ids, start, end)
            if outside:
                raise SchedulingError(
                    409, f"{len(outside)} of the interviewers has not marked that time as "
                         "available. Choose a time inside their availability, or schedule it anyway."
                )
        status = LIVE_SESSION

    # Scorecards first: A1's rules (eligible, independent, not the candidate,
    # not decided, not erased) refuse here before anything is booked.
    # Taken BEFORE the scorecards are assigned and written as the session's
    # created_at: a scorecard this call creates is then never older than the
    # session, and one HR assigned by hand beforehand always is — which is how a
    # cancellation tells the two apart (_RELEASABLE_SQL). One clock, not two.
    created = datetime.now(tz=UTC)
    due = (end or datetime.now(tz=UTC) + HORIZON) + SCORECARD_GRACE
    try:
        await cards.assign(
            db, company_id=company_id, enrolment_id=loop["enrolment_id"], round_id=round_id,
            interviewer_user_ids=ids, assigned_by=actor, due_at=due, meta=meta, notify=False,
        )
    except ScorecardError as exc:
        raise SchedulingError(exc.status_code, exc.detail) from exc
    live_cards = {
        str(r[0]): r[1]
        for r in (
            await db.execute(
                text(
                    "SELECT interviewer_user_id, id FROM interviewer_scorecards"
                    " WHERE enrolment_id = :e AND round_id = :r AND status <> 'withdrawn'"
                    "   AND superseded_at IS NULL AND interviewer_user_id = ANY(:u)"
                ),
                {"e": loop["enrolment_id"], "r": round_id, "u": ids},
            )
        ).all()
    }

    session_id = uuid.uuid4()
    position = int(count or 0)
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(
                "INSERT INTO interview_sessions (id, company_id, loop_id, enrolment_id,"
                " applicant_id, round_id, title, position, duration_minutes, starts_at, ends_at,"
                " blocked_until, location, status, booked_by, booked_at, created_at,"
                " updated_at)"
                " VALUES (:i, :c, :l, :e, :a, :r, :t, :p, :d, :s, :en, :b, :loc, :st,"
                " CASE WHEN :st = 'scheduled' THEN 'hr' END,"
                " CASE WHEN :st = 'scheduled' THEN now() END, :created, :created)"
            ),
            {"i": session_id, "c": company_id, "l": loop_id, "e": loop["enrolment_id"],
             "a": e["applicant_id"], "r": round_id, "t": name, "p": position,
             "d": duration_minutes, "s": start, "en": end, "b": blocked, "loc": place,
             "st": status, "created": created},
        )
        for uid in ids:
            await db.execute(
                text(
                    "INSERT INTO interview_session_interviewers (session_id, company_id,"
                    " interviewer_user_id, scorecard_id) VALUES (:s, :c, :u, :sc)"
                ),
                {"s": session_id, "c": company_id, "u": uid, "sc": live_cards.get(str(uid))},
            )
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        raise (_conflict(exc) or exc) from exc

    _audit(db, actor_id=actor, action="session.created", resource_type="interview_session",
           resource_id=session_id, meta=meta,
           details={"company_id": str(company_id), "loop_id": str(loop_id),
                    "round_id": str(round_id), "interviewer_count": len(ids),
                    "starts_at": _iso(start), "duration_minutes": duration_minutes,
                    "outside_availability": bool(allow_outside_availability and start)})
    if loop["sent_at"] is not None and start is not None:
        await _notify_interviewers(db, company_id=company_id, loop=loop, session_id=session_id,
                                   user_ids=ids, what="scheduled")
    await _refresh_loop_status(db, loop_id)
    return {"session_id": str(session_id), "status": status}


async def _load_session(
    db: AsyncSession, *, company_id: uuid.UUID, session_id: uuid.UUID
) -> dict[str, Any]:
    s = (
        await db.execute(
            text(
                "SELECT s.*, l.buffer_minutes, l.status AS loop_status, l.sent_at,"
                "       l.candidate_timezone, l.self_schedule"
                "  FROM interview_sessions s JOIN interview_loops l ON l.id = s.loop_id"
                " WHERE s.id = :s AND s.company_id = :c FOR UPDATE OF s, l"
            ),
            {"s": session_id, "c": company_id},
        )
    ).mappings().first()
    if s is None:
        raise SchedulingError(404, "Session not found.")
    return dict(s)


async def reschedule_session(
    db: AsyncSession, *, company_id: uuid.UUID, session_id: uuid.UUID,
    starts_at: datetime, duration_minutes: int | None, location: str | None,
    allow_outside_availability: bool, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """HR moves a session (A2 #24). The candidate cannot (A2 #23)."""
    s = await _load_session(db, company_id=company_id, session_id=session_id)
    if s["loop_status"] in ("cancelled", "completed"):
        raise SchedulingError(409, f"This loop is {s['loop_status']}.")
    if s["status"] not in ("scheduled", "awaiting_slot"):
        raise SchedulingError(409, f"A {s['status'].replace('_', ' ')} session cannot be moved.")
    await _live_enrolment(db, company_id=company_id, enrolment_id=s["enrolment_id"])
    duration = duration_minutes or s["duration_minutes"]
    if not 15 <= duration <= 480:
        raise SchedulingError(422, "A session lasts 15 minutes to 8 hours.")
    try:
        start, end, blocked = _times(starts_at, duration, s["buffer_minutes"])
    except ScheduleError as exc:
        raise SchedulingError(422, str(exc)) from exc
    if start <= datetime.now(tz=UTC):
        raise SchedulingError(422, "The session must start in the future.")
    place = (_clean_text(location, limit=LOCATION_MAX, field="location")
             if location is not None else s["location"])
    ids = [
        r[0] for r in (
            await db.execute(
                text("SELECT interviewer_user_id FROM interview_session_interviewers WHERE session_id = :s"),
                {"s": session_id},
            )
        ).all()
    ]
    if not allow_outside_availability:
        outside = await _outside_availability(db, company_id, ids, start, end)
        if outside:
            raise SchedulingError(
                409, f"{len(outside)} of the interviewers has not marked that time as available. "
                     "Choose a time inside their availability, or schedule it anyway."
            )
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(
                "UPDATE interview_sessions SET starts_at = :s, ends_at = :e, blocked_until = :b,"
                " duration_minutes = :d, location = :loc, status = 'scheduled',"
                " booked_by = COALESCE(booked_by, 'hr'), booked_at = COALESCE(booked_at, now()),"
                " updated_at = now() WHERE id = :i"
            ),
            {"s": start, "e": end, "b": blocked, "d": duration, "loc": place, "i": session_id},
        )
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        raise (_conflict(exc) or exc) from exc
    await _move_scorecard_due(db, session_id=session_id, due=end + SCORECARD_GRACE)
    _audit(db, actor_id=actor, action="session.rescheduled", resource_type="interview_session",
           resource_id=session_id, meta=meta,
           details={"company_id": str(company_id), "from": _iso(s["starts_at"]),
                    "to": start.isoformat(), "duration_minutes": duration,
                    "outside_availability": allow_outside_availability})
    if s["sent_at"] is not None:
        await _bump_itinerary(db, [s["loop_id"]])
        loop = await _load_loop(db, company_id=company_id, loop_id=s["loop_id"])
        await _tell_candidate_session(db, loop=loop, session_id=session_id, cancelled=False)
        await _notify_interviewers(db, company_id=company_id, loop=loop, session_id=session_id,
                                   user_ids=ids, what="moved")
    await _refresh_loop_status(db, s["loop_id"])
    return {"session_id": str(session_id), "starts_at": start.isoformat()}


async def _bump_itinerary(db: AsyncSession, loop_ids: list[uuid.UUID]) -> None:
    """A change to a schedule the candidate already has raises its calendar
    SEQUENCE. Without it, a calendar app that already holds the event keeps the
    old time or ignores the cancellation (RFC 5545 3.8.7.4). Loops not yet sent
    have no event anywhere, so they are left alone."""
    if loop_ids:
        await db.execute(
            text("UPDATE interview_loops SET itinerary_version = itinerary_version + 1,"
                 " updated_at = now() WHERE id = ANY(:l) AND sent_at IS NOT NULL"),
            {"l": loop_ids},
        )


async def _move_scorecard_due(db: AsyncSession, *, session_id: uuid.UUID, due: datetime) -> None:
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET due_at = :d, updated_at = now()"
            " WHERE id IN (SELECT scorecard_id FROM interview_session_interviewers"
            "               WHERE session_id = :s AND scorecard_id IS NOT NULL)"
            "   AND status IN ('assigned', 'in_progress')"
        ),
        {"d": due, "s": session_id},
    )


# Which of a cancelled interview's scorecards to hand back: those scheduling
# itself created (no older than the first session they were linked to — one HR
# assigned by hand before scheduling is left alone), that no other session of
# the same candidate and round still needs (scheduled, awaiting a slot, or
# already held), and that the interviewer has not started. A begun one holds
# their words, and whether it stands is HR's call from the drawer.
_RELEASABLE_SQL = """
SELECT DISTINCT si.scorecard_id
  FROM interview_session_interviewers si
  JOIN interviewer_scorecards sc ON sc.id = si.scorecard_id
 WHERE si.session_id = ANY(:s) AND sc.company_id = :c
   AND sc.status = 'assigned' AND sc.superseded_at IS NULL AND sc.corrects_id IS NULL
   AND sc.created_at >= (SELECT min(fs.created_at)
                           FROM interview_session_interviewers f
                           JOIN interview_sessions fs ON fs.id = f.session_id
                          WHERE f.scorecard_id = si.scorecard_id)
   AND NOT EXISTS (
         SELECT 1 FROM interview_session_interviewers o
           JOIN interview_sessions os ON os.id = o.session_id
          WHERE o.scorecard_id = si.scorecard_id AND os.id <> ALL(:s)
            AND os.status IN ('scheduled', 'awaiting_slot', 'completed', 'no_show'))
"""


async def _release_scorecards(
    db: AsyncSession, *, company_id: uuid.UUID, session_ids: list[uuid.UUID],
    actor: uuid.UUID, meta: RequestMeta,
) -> int:
    """Withdraw the scorecards only these (now cancelled) sessions were holding
    open, so none turns "late" for an interview that is not going to happen.

    Through A1's own ``withdraw``, one at a time: each keeps its audit row and
    its notifications, and A1's refusals stand — an HR manager on the panel is
    not withdrawn by their own hand, and is left for a colleague, as before.
    Returns how many were withdrawn. Caller commits.
    """
    if not session_ids:
        return 0
    ids = [r[0] for r in (await db.execute(
        text(_RELEASABLE_SQL), {"s": session_ids, "c": company_id})).all()]
    released = 0
    for card_id in ids:
        try:
            async with db.begin_nested():
                await cards.withdraw(db, company_id=company_id, scorecard_id=card_id,
                                     actor=actor, reason="The interview was cancelled.",
                                     meta=meta)
            released += 1
        except ScorecardError as exc:
            log.info("scheduling.scorecard_kept", scorecard_id=str(card_id),
                     reason=exc.detail[:80])
    return released


async def set_session_outcome(
    db: AsyncSession, *, company_id: uuid.UUID, session_id: uuid.UUID, outcome: str,
    reason: str | None, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """cancelled | completed | no_show. HR only; audited; changes no candidate status."""
    if outcome not in ("cancelled", "completed", "no_show"):
        raise SchedulingError(422, "Unknown outcome.")
    s = await _load_session(db, company_id=company_id, session_id=session_id)
    if s["status"] in ("cancelled", "completed", "no_show"):
        raise SchedulingError(409, f"This session is already {s['status'].replace('_', ' ')}.")
    if outcome != "cancelled" and (s["starts_at"] is None or s["starts_at"] > datetime.now(tz=UTC)):
        raise SchedulingError(409, "A session can be marked done or missed only once it has started.")
    why = _clean_text(reason, limit=REASON_MAX, field="reason") if outcome == "cancelled" else None
    await db.execute(
        text(
            "UPDATE interview_sessions SET status = :o, updated_at = now(),"
            " cancelled_at = CASE WHEN :o = 'cancelled' THEN now() END,"
            " cancel_reason = :r WHERE id = :i"
        ),
        {"o": outcome, "r": why, "i": session_id},
    )
    released = 0
    if outcome == "cancelled":
        released = await _release_scorecards(db, company_id=company_id,
                                             session_ids=[session_id], actor=actor, meta=meta)
    _audit(db, actor_id=actor, action=f"session.{outcome}", resource_type="interview_session",
           resource_id=session_id, meta=meta,
           details={"company_id": str(company_id), "has_reason": bool(why),
                    "reason_chars": len(why or ""), "scorecards_released": released})
    if outcome == "cancelled" and s["sent_at"] is not None and s["starts_at"] is not None:
        await _bump_itinerary(db, [s["loop_id"]])
        loop = await _load_loop(db, company_id=company_id, loop_id=s["loop_id"])
        await _tell_candidate_session(db, loop=loop, session_id=session_id, cancelled=True)
        ids = [r[0] for r in (await db.execute(
            text("SELECT interviewer_user_id FROM interview_session_interviewers WHERE session_id = :s"),
            {"s": session_id})).all()]
        await _notify_interviewers(db, company_id=company_id, loop=loop, session_id=session_id,
                                   user_ids=ids, what="cancelled")
    status = await _refresh_loop_status(db, s["loop_id"])
    return {"session_id": str(session_id), "status": outcome, "loop_status": status}


async def cancel_loop(
    db: AsyncSession, *, company_id: uuid.UUID, loop_id: uuid.UUID, reason: str | None,
    actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    loop = await _load_loop(db, company_id=company_id, loop_id=loop_id)
    _assert_open(loop)
    why = _clean_text(reason, limit=REASON_MAX, field="reason")
    pending = [
        r[0] for r in (await db.execute(
            text("SELECT id FROM interview_sessions WHERE loop_id = :l"
                 " AND status IN ('scheduled', 'awaiting_slot')"),
            {"l": loop_id})).all()
    ]
    await db.execute(
        text(
            "UPDATE interview_sessions SET status = 'cancelled', cancelled_at = now(),"
            " updated_at = now() WHERE loop_id = :l AND status IN ('scheduled', 'awaiting_slot')"
        ),
        {"l": loop_id},
    )
    await db.execute(
        text(
            "UPDATE interview_loops SET status = 'cancelled', cancelled_at = now(),"
            " cancel_reason = :r, cancelled_by_user_id = :u, updated_at = now() WHERE id = :l"
        ),
        {"r": why, "u": actor, "l": loop_id},
    )
    released = await _release_scorecards(db, company_id=company_id, session_ids=pending,
                                         actor=actor, meta=meta)
    _audit(db, actor_id=actor, action="loop.cancelled", resource_type="interview_loop",
           resource_id=loop_id, meta=meta,
           details={"company_id": str(company_id), "sessions_cancelled": len(pending),
                    "has_reason": bool(why), "reason_chars": len(why or ""),
                    "scorecards_released": released})
    if loop["sent_at"] is not None:
        await _bump_itinerary(db, [loop_id])
        loop = await _load_loop(db, company_id=company_id, loop_id=loop_id)
        await _tell_candidate_loop(db, loop={**loop, "status": "cancelled"}, kind="cancelled")
    return {"loop_id": str(loop_id), "status": "cancelled", "sessions_cancelled": len(pending)}


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------
async def free_slots(
    db: AsyncSession, *, company_id: uuid.UUID, session_id: uuid.UUID,
    now: datetime | None = None,
) -> list[str]:
    """Start times at which this session could be booked right now."""
    now = now or datetime.now(tz=UTC)
    s = (
        await db.execute(
            text(
                "SELECT s.id, s.applicant_id, s.duration_minutes, l.buffer_minutes"
                "  FROM interview_sessions s JOIN interview_loops l ON l.id = s.loop_id"
                " WHERE s.id = :s AND s.company_id = :c"
            ),
            {"s": session_id, "c": company_id},
        )
    ).mappings().first()
    if s is None:
        raise SchedulingError(404, "Session not found.")
    ids = [
        r[0] for r in (await db.execute(
            text("SELECT interviewer_user_id FROM interview_session_interviewers WHERE session_id = :s"),
            {"s": session_id})).all()
    ]
    if not ids:
        return []
    horizon_end = now + HORIZON
    windows = await _availability_windows(db, company_id, ids, now, horizon_end)
    booked = await _bookings(db, company_id, ids, now, horizon_end, except_session=session_id)
    free = [subtract(merge(windows[str(u)]), booked[str(u)]) for u in ids]
    busy = await _candidate_busy(db, s["applicant_id"], now, horizon_end + timedelta(days=1),
                                 except_session=session_id)
    found = slots(
        SlotQuery(duration_minutes=s["duration_minutes"], buffer_minutes=s["buffer_minutes"], now=now),
        interviewer_free=free, candidate_busy=busy,
    )
    return [t.isoformat() for t in found]


# ---------------------------------------------------------------------------
# Sending, and what the candidate reads
# ---------------------------------------------------------------------------
async def _candidate_contact(db: AsyncSession, loop: dict[str, Any]) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT a.id, a.full_name, a.email, a.user_id,"
                "       COALESCE(jr.title, e.target_job_title) AS job_title"
                "  FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                "  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id"
                " WHERE e.id = :e"
            ),
            {"e": loop["enrolment_id"]},
        )
    ).mappings().first()
    return dict(row) if row else {}


def _itinerary_items(sessions: list[dict[str, Any]], tz: str) -> list[dict[str, Any]]:
    out = []
    for s in sessions:
        if s["status"] != "scheduled" or not s["starts_at"]:
            continue
        out.append({"title": s["title"], "duration_minutes": s["duration_minutes"],
                    "when": local_label(datetime.fromisoformat(s["starts_at"]), tz),
                    "location": s["location"]})
    return out


async def _tell_candidate_loop(db: AsyncSession, *, loop: dict[str, Any], kind: str) -> None:
    """kind: itinerary | slot_request | cancelled."""
    who = await _candidate_contact(db, loop)
    if not who:
        return
    lang = await candidate_language(db, who.get("id"))
    link = f"{settings.app_base_url.rstrip('/')}/applications"
    version = int(loop.get("itinerary_version") or 0)
    sessions = await _sessions(db, loop["id"])
    if kind == "itinerary":
        template, ctx = "interview_itinerary", {
            "name": who.get("full_name"), "job_title": who.get("job_title"),
            "timezone": loop["candidate_timezone"], "updated": version > 1,
            "sessions": _itinerary_items(sessions, loop["candidate_timezone"]), "cta_url": link,
        }
        title = "Your interview schedule"
    elif kind == "slot_request":
        template, ctx = "interview_slot_request", {
            "name": who.get("full_name"), "job_title": who.get("job_title"),
            "count": sum(1 for s in sessions if s["status"] == "awaiting_slot"), "cta_url": link,
        }
        title = "Choose your interview times"
    else:
        template, ctx = "interview_session_update", {
            "name": who.get("full_name"), "job_title": who.get("job_title"), "cancelled": True,
            "session": {"title": loop["title"]}, "cta_url": link,
        }
        title = "Interviews cancelled"
    await create_notification(
        db, user_id=who.get("user_id"), kind="interview_schedule", title=title,
        body=f"{who.get('job_title') or ''}", link="/applications",
        dedupe_key=f"loop:{loop['id']}:{kind}:{version}",
    )
    await enqueue_email(
        db, to=who.get("email"), template=template, lang=lang, ctx=ctx,
        to_user_id=who.get("user_id"), company_id=loop["company_id"],
        related_kind="interview_loop", related_id=loop["id"],
        dedupe_key=f"loop:{loop['id']}:{kind}:{version}",
    )


async def _tell_candidate_session(
    db: AsyncSession, *, loop: dict[str, Any], session_id: uuid.UUID, cancelled: bool
) -> None:
    who = await _candidate_contact(db, loop)
    if not who:
        return
    s = next((x for x in await _sessions(db, loop["id"]) if x["id"] == str(session_id)), None)
    if s is None:
        return
    lang = await candidate_language(db, who.get("id"))
    item = {"title": s["title"], "duration_minutes": s["duration_minutes"],
            "location": s["location"],
            "when": local_label(datetime.fromisoformat(s["starts_at"]), loop["candidate_timezone"])
            if s["starts_at"] else ""}
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
    await create_notification(
        db, user_id=who.get("user_id"), kind="interview_schedule",
        title="Interview cancelled" if cancelled else "Interview time changed",
        body=f"{s['title']} · {item['when']}" if not cancelled else s["title"],
        link="/applications", dedupe_key=f"session:{session_id}:{stamp}",
    )
    await enqueue_email(
        db, to=who.get("email"), template="interview_session_update", lang=lang,
        ctx={"name": who.get("full_name"), "job_title": who.get("job_title"),
             "cancelled": cancelled, "session": item,
             "cta_url": f"{settings.app_base_url.rstrip('/')}/applications"},
        to_user_id=who.get("user_id"), company_id=loop["company_id"],
        related_kind="interview_session", related_id=session_id,
        dedupe_key=f"session:{session_id}:{stamp}",
    )


async def _notify_interviewers(
    db: AsyncSession, *, company_id: uuid.UUID, loop: dict[str, Any], session_id: uuid.UUID,
    user_ids: list[uuid.UUID], what: str,
) -> None:
    s = next((x for x in await _sessions(db, loop["id"]) if x["id"] == str(session_id)), None)
    if s is None:
        return
    who = await _candidate_contact(db, loop)
    when = (local_label(datetime.fromisoformat(s["starts_at"]), loop["candidate_timezone"])
            if s["starts_at"] else "time to be chosen by the candidate")
    verb = {"scheduled": "Interview scheduled", "moved": "Interview moved",
            "cancelled": "Interview cancelled"}[what]
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
    for uid in user_ids:
        await create_notification(
            db, user_id=uid, kind="interview_scheduled",
            title=f"{verb}: {who.get('full_name') or 'candidate'}",
            body=f"{s['title']} · {when}", link="/interviewer",
            dedupe_key=f"iv-session:{session_id}:{uid}:{what}:{stamp}",
        )


async def send_loop(
    db: AsyncSession, *, company_id: uuid.UUID, loop_id: uuid.UUID, actor: uuid.UUID,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Give the candidate the loop: the itinerary, or the request to pick times."""
    loop = await _load_loop(db, company_id=company_id, loop_id=loop_id)
    _assert_open(loop)
    await _live_enrolment(db, company_id=company_id, enrolment_id=loop["enrolment_id"])
    sessions = [s for s in await _sessions(db, loop_id) if s["status"] != "cancelled"]
    if not sessions:
        raise SchedulingError(422, "Add at least one session before sending the loop.")
    awaiting = [s for s in sessions if s["status"] == "awaiting_slot"]
    if awaiting and not loop["self_schedule"]:
        raise SchedulingError(422, "Every session needs a time before the loop is sent.")
    version = int(loop["itinerary_version"] or 0) + 1
    await db.execute(
        text(
            "UPDATE interview_loops SET sent_at = COALESCE(sent_at, now()),"
            " itinerary_version = :v, updated_at = now() WHERE id = :l"
        ),
        {"v": version, "l": loop_id},
    )
    loop = {**loop, "itinerary_version": version}
    kind = "slot_request" if awaiting else "itinerary"
    await _tell_candidate_loop(db, loop=loop, kind=kind)
    for s in sessions:
        if s["status"] == "scheduled":
            await _notify_interviewers(
                db, company_id=company_id, loop=loop, session_id=uuid.UUID(s["id"]),
                user_ids=[uuid.UUID(i["user_id"]) for i in s["interviewers"]], what="scheduled",
            )
    _audit(db, actor_id=actor, action="loop.sent", resource_type="interview_loop",
           resource_id=loop_id, meta=meta,
           details={"company_id": str(company_id), "version": version, "kind": kind,
                    "sessions": len(sessions), "awaiting_slot": len(awaiting)})
    status = await _refresh_loop_status(db, loop_id)
    return {"loop_id": str(loop_id), "status": status, "sent": kind, "version": version}


# ---------------------------------------------------------------------------
# The candidate's side
# ---------------------------------------------------------------------------
_CANDIDATE_LOOPS_SQL = """
SELECT l.*, COALESCE(jr.title, e.target_job_title) AS job_title
  FROM interview_loops l
  JOIN enrolments e ON e.id = l.enrolment_id AND e.deleted_at IS NULL
  JOIN applicants a ON a.id = l.applicant_id
  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id
 WHERE a.user_id = :u AND l.sent_at IS NOT NULL
 ORDER BY l.created_at DESC
 LIMIT 50
"""


def _candidate_session(s: dict[str, Any]) -> dict[str, Any]:
    """What a candidate sees of a session: no scorecards, no interviewer ids."""
    return {"id": s["id"], "title": s["title"], "duration_minutes": s["duration_minutes"],
            "starts_at": s["starts_at"], "ends_at": s["ends_at"], "location": s["location"],
            "status": s["status"],
            "interviewers": [i["name"] for i in s["interviewers"]]}


async def candidate_loops(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text(_CANDIDATE_LOOPS_SQL), {"u": user_id})).mappings().all()
    out = []
    for r in rows:
        sessions = await _sessions(db, r["id"])
        out.append({
            "id": str(r["id"]), "title": r["title"], "job_title": r["job_title"],
            "status": r["status"], "timezone": r["candidate_timezone"],
            "self_schedule": r["self_schedule"],
            "sessions": [_candidate_session(s) for s in sessions if s["status"] != "cancelled"
                         or r["status"] == "cancelled"],
        })
    return out


async def _candidate_loop(db: AsyncSession, *, user_id: uuid.UUID, loop_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT l.* FROM interview_loops l JOIN applicants a ON a.id = l.applicant_id"
                " WHERE l.id = :l AND a.user_id = :u AND l.sent_at IS NOT NULL FOR UPDATE OF l"
            ),
            {"l": loop_id, "u": user_id},
        )
    ).mappings().first()
    if row is None:
        # Somebody else's loop reads exactly like a missing one.
        raise SchedulingError(404, "Interview schedule not found.")
    return dict(row)


async def _still_open(db: AsyncSession, loop: dict[str, Any]) -> bool:
    """Whether the application behind a loop can still be scheduled: not decided,
    not erased. Asked on the candidate's side, where the reason is not given —
    the candidate reads the same neutral sentence either way (security review L2)."""
    row = (
        await db.execute(
            text(
                "SELECT e.status,"
                "       ((a.full_name = '[redacted]' AND a.email IS NULL)"
                "        OR EXISTS (SELECT 1 FROM erasure_requests er"
                "                    WHERE er.user_id = a.user_id)) AS candidate_erased"
                "  FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.id = :e AND e.deleted_at IS NULL"
            ),
            {"e": loop["enrolment_id"]},
        )
    ).mappings().first()
    if row is None:
        return False
    return not row["candidate_erased"] and row["status"] not in TERMINAL_STATUSES


async def candidate_slots(
    db: AsyncSession, *, user_id: uuid.UUID, loop_id: uuid.UUID, session_id: uuid.UUID
) -> list[str]:
    loop = await _candidate_loop(db, user_id=user_id, loop_id=loop_id)
    if not await _still_open(db, loop):
        return []
    s = await db.scalar(
        text("SELECT status FROM interview_sessions WHERE id = :s AND loop_id = :l"),
        {"s": session_id, "l": loop_id},
    )
    if s is None:
        raise SchedulingError(404, "Session not found.")
    if s != "awaiting_slot" or loop["status"] == "cancelled":
        return []
    return await free_slots(db, company_id=loop["company_id"], session_id=session_id)


async def candidate_book(
    db: AsyncSession, *, user_id: uuid.UUID, loop_id: uuid.UUID, session_id: uuid.UUID,
    starts_at: datetime, timezone: str | None, meta: RequestMeta,
) -> dict[str, Any]:
    """Book one offered slot, once. There is no second call that moves it."""
    loop = await _candidate_loop(db, user_id=user_id, loop_id=loop_id)
    if (loop["status"] == "cancelled" or not loop["self_schedule"]
            or not await _still_open(db, loop)):
        raise SchedulingError(409, "This schedule is not open for choosing times.")
    s = await _load_session(db, company_id=loop["company_id"], session_id=session_id)
    if s["loop_id"] != loop["id"]:
        raise SchedulingError(404, "Session not found.")
    if s["status"] != "awaiting_slot":
        # A23: a booked time is changed by the hiring team, not the candidate.
        raise SchedulingError(
            409, "This interview already has a time. To change it, contact the hiring team."
        )
    try:
        want = aware(starts_at, field="time")
    except ScheduleError as exc:
        raise SchedulingError(422, str(exc)) from exc
    offered = await free_slots(db, company_id=loop["company_id"], session_id=session_id)
    if want.isoformat() not in offered:
        raise SchedulingError(409, "That time is no longer available. Please pick another.")
    start, end, blocked = _times(want, s["duration_minutes"], s["buffer_minutes"])
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(
                "UPDATE interview_sessions SET starts_at = :s, ends_at = :e, blocked_until = :b,"
                " status = 'scheduled', booked_by = 'candidate', booked_at = now(),"
                " updated_at = now() WHERE id = :i AND status = 'awaiting_slot'"
            ),
            {"s": start, "e": end, "b": blocked, "i": session_id},
        )
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        # Two candidates (or a candidate and HR) raced for it; one lost.
        raise SchedulingError(409, "That time was just taken. Please pick another.") from exc
    if timezone:
        try:
            tz = valid_timezone(timezone)
            await db.execute(
                text("UPDATE interview_loops SET candidate_timezone = :t, updated_at = now() WHERE id = :l"),
                {"t": tz, "l": loop_id},
            )
            loop["candidate_timezone"] = tz
        except ScheduleError:
            pass  # a browser zone we do not know changes nothing; the loop's stands
    await _move_scorecard_due(db, session_id=session_id, due=end + SCORECARD_GRACE)
    _audit(db, actor_id=user_id, action="session.booked_by_candidate",
           resource_type="interview_session", resource_id=session_id, meta=meta,
           details={"company_id": str(loop["company_id"]), "loop_id": str(loop_id),
                    "starts_at": start.isoformat()})
    ids = [r[0] for r in (await db.execute(
        text("SELECT interviewer_user_id FROM interview_session_interviewers WHERE session_id = :s"),
        {"s": session_id})).all()]
    await _notify_interviewers(db, company_id=loop["company_id"], loop=loop,
                               session_id=session_id, user_ids=ids, what="scheduled")
    remaining = await db.scalar(
        text("SELECT count(*) FROM interview_sessions WHERE loop_id = :l AND status = 'awaiting_slot'"),
        {"l": loop_id},
    )
    if not remaining:
        version = int(loop["itinerary_version"] or 0) + 1
        await db.execute(
            text("UPDATE interview_loops SET itinerary_version = :v, updated_at = now() WHERE id = :l"),
            {"v": version, "l": loop_id},
        )
        await _tell_candidate_loop(db, loop={**loop, "itinerary_version": version}, kind="itinerary")
    await _refresh_loop_status(db, loop_id)
    return {"session_id": str(session_id), "starts_at": start.isoformat(),
            "all_booked": not remaining}


# ---------------------------------------------------------------------------
# Calendar files
# ---------------------------------------------------------------------------
def _events(loop: dict[str, Any], sessions: list[dict[str, Any]], job_title: str) -> list[IcsEvent]:
    host = settings.app_base_url.split("//")[-1].split("/")[0] or "anthire"
    out = []
    for s in sessions:
        if not s["starts_at"] or s["status"] not in ("scheduled", "cancelled"):
            continue
        out.append(IcsEvent(
            uid=f"{s['id']}@{host}", sequence=int(loop.get("itinerary_version") or 0),
            starts_at=datetime.fromisoformat(s["starts_at"]),
            ends_at=datetime.fromisoformat(s["ends_at"]),
            summary=f"{s['title']} — {job_title}".strip(" —"),
            description=f"Interview for {job_title}." if job_title else "Interview.",
            location=s["location"] or "", cancelled=s["status"] == "cancelled",
        ))
    return out


async def candidate_ics(db: AsyncSession, *, user_id: uuid.UUID, loop_id: uuid.UUID) -> str:
    loop = await _candidate_loop(db, user_id=user_id, loop_id=loop_id)
    who = await _candidate_contact(db, loop)
    sessions = await _sessions(db, loop_id)
    return ics(_events(loop, sessions, who.get("job_title") or ""), now=datetime.now(tz=UTC))


async def hr_ics(db: AsyncSession, *, company_id: uuid.UUID, loop_id: uuid.UUID) -> str:
    loop = await _load_loop(db, company_id=company_id, loop_id=loop_id)
    who = await _candidate_contact(db, loop)
    sessions = await _sessions(db, loop_id)
    return ics(_events(loop, sessions, who.get("job_title") or ""), now=datetime.now(tz=UTC))


# ---------------------------------------------------------------------------
# The interviewer's side
# ---------------------------------------------------------------------------
async def sessions_for_interviewer(
    db: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID, start: datetime, end: datetime,
) -> list[dict[str, Any]]:
    """Only sessions this person sits on (O5 #6), with the candidate's name and
    their own scorecard — nothing about the rest of the panel's scoring."""
    rows = (
        await db.execute(
            text(
                "SELECT s.id, s.title, s.starts_at, s.ends_at, s.duration_minutes, s.location,"
                "       s.status, a.full_name, COALESCE(jr.title, e.target_job_title) AS job_title,"
                "       si.scorecard_id"
                "  FROM interview_session_interviewers si"
                "  JOIN interview_sessions s ON s.id = si.session_id"
                "  JOIN enrolments e ON e.id = s.enrolment_id"
                "  JOIN applicants a ON a.id = s.applicant_id"
                "  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id"
                " WHERE si.interviewer_user_id = :u AND si.company_id = :c"
                "   AND s.status IN ('scheduled', 'completed', 'no_show')"
                "   AND s.starts_at >= :s AND s.starts_at < :e"
                " ORDER BY s.starts_at LIMIT 200"
            ),
            {"u": user_id, "c": company_id, "s": start, "e": end},
        )
    ).mappings().all()
    return [{"id": str(r["id"]), "title": r["title"], "starts_at": _iso(r["starts_at"]),
             "ends_at": _iso(r["ends_at"]), "duration_minutes": r["duration_minutes"],
             "location": r["location"], "status": r["status"], "candidate": r["full_name"],
             "job_title": r["job_title"],
             "scorecard_id": str(r["scorecard_id"]) if r["scorecard_id"] else None}
            for r in rows]


# ---------------------------------------------------------------------------
# A final decision closes the schedule
# ---------------------------------------------------------------------------
async def close_for_decision(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID,
    actor: uuid.UUID | None, decision: str,
) -> int:
    """Cancel an application's interviews not yet held, and close its open loops,
    when a final decision is recorded. Returns how many sessions were cancelled.

    Nobody should turn up to interview a candidate the company has already hired
    or rejected, and an interviewer's calendar should not stay blocked by it.
    The candidate is not emailed about each cancellation — the decision email is
    the news; the interviewers are told, so they do not turn up. Called by
    final_decision.record_final_decision. Caller commits.
    """
    now = datetime.now(tz=UTC)
    cancelled = (
        await db.execute(
            text(
                "UPDATE interview_sessions SET status = 'cancelled',"
                " cancelled_at = CAST(:n AS timestamptz), updated_at = CAST(:n AS timestamptz)"
                " WHERE enrolment_id = :e AND company_id = :c"
                "   AND (status = 'awaiting_slot' OR (status = 'scheduled' AND starts_at > :n))"
                " RETURNING id, title, starts_at, loop_id"
            ),
            {"e": enrolment_id, "c": company_id, "n": now},
        )
    ).mappings().all()
    closed = (
        await db.execute(
            text(
                "UPDATE interview_loops l SET updated_at = CAST(:n AS timestamptz),"
                " status = CASE WHEN EXISTS (SELECT 1 FROM interview_sessions s"
                "                             WHERE s.loop_id = l.id"
                "                               AND s.status IN ('completed', 'no_show'))"
                "               THEN 'completed' ELSE 'cancelled' END,"
                " cancelled_at = CASE WHEN EXISTS (SELECT 1 FROM interview_sessions s"
                "                                   WHERE s.loop_id = l.id"
                "                                     AND s.status IN ('completed', 'no_show'))"
                "                     THEN NULL ELSE CAST(:n AS timestamptz) END,"
                " cancelled_by_user_id = :a"
                " WHERE l.enrolment_id = :e AND l.company_id = :c"
                "   AND l.status IN ('draft', 'scheduled')"
                "   AND NOT EXISTS (SELECT 1 FROM interview_sessions s WHERE s.loop_id = l.id"
                "                    AND s.status IN ('scheduled', 'awaiting_slot'))"
                " RETURNING l.id"
            ),
            {"e": enrolment_id, "c": company_id, "n": now, "a": actor},
        )
    ).all()
    for s in cancelled:
        if s["starts_at"] is None:
            continue
        panel = (
            await db.execute(
                text("SELECT interviewer_user_id FROM interview_session_interviewers"
                     " WHERE session_id = :s"),
                {"s": s["id"]},
            )
        ).all()
        for (uid,) in panel:
            await create_notification(
                db, user_id=uid, kind="interview_scheduled",
                title="Interview cancelled: a decision has been made",
                body=f"{s['title']} · no longer needed", link="/interviewer",
                dedupe_key=f"iv-session:{s['id']}:{uid}:decision",
            )
    await _bump_itinerary(db, list({s["loop_id"] for s in cancelled}))
    # A decided candidate's untouched scorecards for interviews that will not
    # now happen are handed back too — when a person made the decision.
    released = 0
    if actor is not None:
        released = await _release_scorecards(
            db, company_id=company_id, session_ids=[s["id"] for s in cancelled],
            actor=actor, meta=RequestMeta())
    if cancelled or closed:
        _audit(db, actor_id=actor, action="loop.closed_on_decision",
               resource_type="enrolment", resource_id=enrolment_id, meta=RequestMeta(),
               details={"company_id": str(company_id), "decision": decision,
                        "sessions_cancelled": len(cancelled), "loops_closed": len(closed),
                        "scorecards_released": released})
    return len(cancelled)
