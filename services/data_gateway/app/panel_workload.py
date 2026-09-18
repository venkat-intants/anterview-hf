"""Panel workload and calibration — PH4-O5. Read-only.

WORKLOAD is computed from what is booked and what is owed — interview sessions
and open scorecards — every time it is read, so it cannot drift. Over-allocation
is a FLAG for HR, not a refusal: HR knows who volunteered for a heavy week.

CALIBRATION compares how interviewers score the same candidates (see
``calibration_core``). It reads only CURRENT submitted scorecards (submitted,
not superseded) — the same rows a decision reads — and names interviewers,
never candidates. Nothing in this module writes anything but its audit row:
no scorecard, no status, no decision (O5 #10–#12).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.calibration_core import (
    MEANINGFUL_DELTA,
    MIN_CANDIDATES,
    MIN_PAIRS,
    ScoreRow,
    calibrate,
)
from app.interviewer_scorecards import RequestMeta, derived_state, list_assignable_interviewers
from app.models import AuditLog
from app.schedule_core import covered, merge

DEFAULT_MAX_PER_DAY = 4
DEFAULT_MAX_PER_WEEK = 15
MAX_SPAN = timedelta(days=92)
# Calibration looks back over scored interviews, and a quiet panel needs a long
# period to reach the minimum number of candidates at all.
MAX_CALIBRATION_SPAN = timedelta(days=366)
# A calibration period shorter than this adds nothing but a way to isolate one
# candidate by time (security review M1).
MIN_CALIBRATION_SPAN = timedelta(days=7)
# Days are counted in the company's working timezone, not UTC: an interview at
# 09:00 IST belongs to that Indian working day.
WORK_TZ = "Asia/Kolkata"


class PanelError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _period(start: datetime, end: datetime,
            max_span: timedelta = MAX_SPAN) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise PanelError(422, "The period needs timezone offsets.")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        raise PanelError(422, "The period must end after it starts.")
    if end - start > max_span:
        raise PanelError(422, f"Choose a period of at most {max_span.days} days.")
    return start, end


_SESSIONS_SQL = """
SELECT si.interviewer_user_id AS user_id, s.id, s.starts_at, s.ends_at, s.loop_id,
       s.title, e.requisition_id
  FROM interview_session_interviewers si
  JOIN interview_sessions s ON s.id = si.session_id
  JOIN enrolments e ON e.id = s.enrolment_id
 WHERE si.company_id = :c AND si.live
   AND s.starts_at < :e AND s.ends_at > :s
 ORDER BY s.starts_at
"""

_OPEN_CARDS_SQL = """
SELECT interviewer_user_id AS user_id, status, due_at
  FROM interviewer_scorecards
 WHERE company_id = :c AND status IN ('assigned', 'in_progress') AND superseded_at IS NULL
"""


def _day(t: datetime) -> str:
    return t.astimezone(ZoneInfo(WORK_TZ)).date().isoformat()


def _week(t: datetime) -> str:
    y, w, _ = t.astimezone(ZoneInfo(WORK_TZ)).isocalendar()
    return f"{y}-W{w:02d}"


def workload_rows(
    people: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    open_cards: list[dict[str, Any]],
    windows: dict[str, list[tuple[datetime, datetime]]],
    capacity: dict[str, dict[str, int | None]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Pure: one row per interviewer, most loaded first. Tested without a database."""
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sessions:
        by_user[str(s["user_id"])].append(s)
    cards_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in open_cards:
        cards_by[str(c["user_id"])].append(c)

    rows = []
    for p in people:
        uid = p["user_id"]
        mine = sorted(by_user.get(uid, []), key=lambda s: s["starts_at"])
        cap = capacity.get(uid, {})
        per_day_max = cap.get("max_sessions_per_day") or DEFAULT_MAX_PER_DAY
        per_week_max = cap.get("max_sessions_per_week") or DEFAULT_MAX_PER_WEEK
        per_day: dict[str, int] = defaultdict(int)
        per_week: dict[str, int] = defaultdict(int)
        for s in mine:
            per_day[_day(s["starts_at"])] += 1
            per_week[_week(s["starts_at"])] += 1
        # Two live sessions of one person that overlap cannot exist (the
        # exclusion constraint); counted anyway, as a detector, never trusted.
        overlaps = sum(
            1 for a, b in zip(mine, mine[1:], strict=False) if b["starts_at"] < a["ends_at"]
        )
        ws = merge(windows.get(uid, []))
        outside = [s for s in mine if not covered(s["starts_at"], s["ends_at"], ws)]
        owed = cards_by.get(uid, [])
        overdue = sum(1 for c in owed if derived_state(c["status"], c["due_at"], now) == "late")
        over_days = sorted(d for d, n in per_day.items() if n > per_day_max)
        over_weeks = sorted(w for w, n in per_week.items() if n > per_week_max)
        rows.append({
            "user_id": uid, "name": p["full_name"], "role": p["role"],
            "sessions": len(mine),
            "hours": round(sum((s["ends_at"] - s["starts_at"]).total_seconds() for s in mine) / 3600, 2),
            "loops": len({str(s["loop_id"]) for s in mine}),
            "by_day": dict(sorted(per_day.items())),
            "open_scorecards": len(owed), "overdue_scorecards": overdue,
            "max_per_day": per_day_max, "max_per_week": per_week_max,
            "over_allocated_days": over_days, "over_allocated_weeks": over_weeks,
            "outside_availability": len(outside), "conflicts": overlaps,
            "flags": [f for f, on in (
                ("over_allocated", bool(over_days or over_weeks)),
                ("outside_availability", bool(outside)),
                ("overdue_scorecards", overdue > 0),
                ("conflict", overlaps > 0),
            ) if on],
        })
    return sorted(rows, key=lambda r: (-len(r["flags"]), -r["sessions"], r["name"].lower()))


async def workload(
    db: AsyncSession, *, company_id: uuid.UUID, start: datetime, end: datetime,
) -> dict[str, Any]:
    start, end = _period(start, end)
    people = await list_assignable_interviewers(db, company_id=company_id)
    sessions = [dict(r) for r in (
        await db.execute(text(_SESSIONS_SQL), {"c": company_id, "s": start, "e": end})
    ).mappings().all()]
    open_cards = [dict(r) for r in (
        await db.execute(text(_OPEN_CARDS_SQL), {"c": company_id})
    ).mappings().all()]
    windows: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for r in (
        await db.execute(
            text(
                "SELECT user_id, starts_at, ends_at FROM interviewer_availability"
                " WHERE company_id = :c AND deleted_at IS NULL AND ends_at > :s AND starts_at < :e"
            ),
            {"c": company_id, "s": start - timedelta(days=15), "e": end},
        )
    ).mappings().all():
        windows[str(r["user_id"])].append((r["starts_at"], r["ends_at"]))
    capacity = {
        str(r["user_id"]): {"max_sessions_per_day": r["max_sessions_per_day"],
                            "max_sessions_per_week": r["max_sessions_per_week"]}
        for r in (
            await db.execute(
                text("SELECT user_id, max_sessions_per_day, max_sessions_per_week"
                     "  FROM interviewer_capacity WHERE company_id = :c"),
                {"c": company_id},
            )
        ).mappings().all()
    }
    rows = workload_rows(people, sessions, open_cards, windows, capacity, now=datetime.now(tz=UTC))
    return {"start": start.isoformat(), "end": end.isoformat(), "timezone": WORK_TZ,
            "defaults": {"max_per_day": DEFAULT_MAX_PER_DAY, "max_per_week": DEFAULT_MAX_PER_WEEK},
            "interviewers": rows}


async def set_capacity(
    db: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID, per_day: int | None,
    per_week: int | None, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    if per_day is not None and not 1 <= per_day <= 24:
        raise PanelError(422, "A daily limit is 1 to 24 sessions.")
    if per_week is not None and not 1 <= per_week <= 100:
        raise PanelError(422, "A weekly limit is 1 to 100 sessions.")
    people = await list_assignable_interviewers(db, company_id=company_id)
    if str(user_id) not in {p["user_id"] for p in people}:
        raise PanelError(404, "Interviewer not found.")
    await db.execute(
        text(
            "INSERT INTO interviewer_capacity (user_id, company_id, max_sessions_per_day,"
            " max_sessions_per_week, updated_by_user_id, updated_at)"
            " VALUES (:u, :c, :d, :w, :a, now())"
            " ON CONFLICT (user_id) DO UPDATE SET max_sessions_per_day = :d,"
            " max_sessions_per_week = :w, updated_by_user_id = :a, updated_at = now()"
            " WHERE interviewer_capacity.company_id = :c"
        ),
        {"u": user_id, "c": company_id, "d": per_day, "w": per_week, "a": actor},
    )
    db.add(AuditLog(actor_id=actor, actor_type="user", action="panel.capacity.updated",
                    resource_type="user", resource_id=user_id,
                    details={"company_id": str(company_id), "max_per_day": per_day,
                             "max_per_week": per_week},
                    ip_address=meta.ip_address, user_agent=meta.user_agent,
                    event_ts=datetime.now(tz=UTC)))
    return {"user_id": str(user_id), "max_sessions_per_day": per_day,
            "max_sessions_per_week": per_week}


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
_SCORES_SQL = """
SELECT sc.id AS scorecard_id, sc.interviewer_user_id, sc.enrolment_id, sc.round_id,
       ss.competency_id, ss.score, ss.not_assessed
  FROM interviewer_scorecards sc
  JOIN interviewer_scorecard_scores ss ON ss.scorecard_id = sc.id
  JOIN enrolments e ON e.id = sc.enrolment_id
 WHERE sc.company_id = :c
   AND sc.status = 'submitted' AND sc.superseded_at IS NULL
   AND sc.submitted_at >= :s AND sc.submitted_at < :e
   AND (CAST(:r AS uuid) IS NULL OR e.requisition_id = CAST(:r AS uuid))
   AND (CAST(:rd AS uuid) IS NULL OR sc.round_id = CAST(:rd AS uuid))
   -- The PH4-A1 independence rule: a caller who still owes their own
   -- scorecard for a candidate and round sees nobody else's for it, here as
   -- everywhere (security review M1).
   AND NOT EXISTS (
       SELECT 1 FROM interviewer_scorecards mine
        WHERE mine.enrolment_id = sc.enrolment_id AND mine.round_id = sc.round_id
          AND mine.interviewer_user_id = :me
          AND mine.status IN ('assigned', 'in_progress') AND mine.superseded_at IS NULL)
 LIMIT 50000
"""


async def calibration(
    db: AsyncSession, *, company_id: uuid.UUID, start: datetime, end: datetime,
    requisition_id: uuid.UUID | None, round_id: uuid.UUID | None, actor: uuid.UUID,
    meta: RequestMeta,
) -> dict[str, Any]:
    start, end = _period(start, end, MAX_CALIBRATION_SPAN)
    if end - start < MIN_CALIBRATION_SPAN:
        raise PanelError(422, "Choose a period of at least 7 days.")
    rows = (
        await db.execute(
            text(_SCORES_SQL),
            {"c": company_id, "s": start, "e": end, "r": requisition_id, "rd": round_id,
             "me": actor},
        )
    ).mappings().all()
    report = calibrate(
        ScoreRow(str(r["scorecard_id"]), str(r["interviewer_user_id"]), str(r["enrolment_id"]),
                 str(r["round_id"]), r["competency_id"],
                 None if r["not_assessed"] else r["score"])
        for r in rows
    )
    names = {p["user_id"]: p["full_name"]
             for p in await list_assignable_interviewers(db, company_id=company_id)}
    competency_names = {
        r[0]: r[1] for r in (
            await db.execute(
                text("SELECT DISTINCT competency_id, competency_name FROM round_criteria"
                     " WHERE company_id = :c AND competency_id = ANY(:ids)"),
                {"c": company_id, "ids": sorted({r["competency_id"] for r in rows}) or [""]},
            )
        ).all()
    }
    db.add(AuditLog(actor_id=actor, actor_type="user", action="panel.calibration.viewed",
                    resource_type="company", resource_id=company_id,
                    details={"start": start.isoformat(), "end": end.isoformat(),
                             "requisition_id": str(requisition_id) if requisition_id else None,
                             "round_id": str(round_id) if round_id else None,
                             "interviewers": len(report)},
                    ip_address=meta.ip_address, user_agent=meta.user_agent,
                    event_ts=datetime.now(tz=UTC)))
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "rules": {"min_pairs": MIN_PAIRS, "meaningful_delta": MEANINGFUL_DELTA,
                  "min_candidates": MIN_CANDIDATES, "scale": "1-5"},
        "competencies": competency_names,
        "interviewers": [
            {"user_id": c.interviewer_id,
             "name": names.get(c.interviewer_id, "Former interviewer"),
             "scorecards": c.scorecards, "candidates": c.candidates,
             # Withheld with the figures: a count over one candidate says how
             # many criteria they were scored on (code review).
             "suppressed": c.suppressed, "scores": None if c.suppressed else c.scores,
             "mean": c.mean,
             "not_assessed_rate": c.not_assessed_rate,
             "distribution": ({str(k): v for k, v in sorted(c.distribution.items())}
                              if c.distribution is not None else None),
             "by_competency": c.by_competency, "pairs": c.pairs, "mean_delta": c.mean_delta,
             "flag": c.flag}
            for c in report
        ],
    }
