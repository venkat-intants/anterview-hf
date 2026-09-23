"""Panel workload and calibration — PH4-O5, extended by PH5-E4. Read-only.

WORKLOAD is computed from what is booked and what is owed — interview sessions
and open scorecards — every time it is read, so it cannot drift. Over-allocation
is a FLAG for HR, not a refusal: HR knows who volunteered for a heavy week.

CALIBRATION compares how interviewers score the same candidates (see
``calibration_core``). It reads only the CURRENT scorecard per (round,
enrolment, interviewer) — the "last word" rule in
``app.metrics.definitions.CURRENT_SCORECARD_SQL`` (a submitted scorecard,
live or superseded only by a withdrawn correction) — the same rows a decision
reads, via the SAME SQL fragment ``hire_interviewer_score@1`` and the
``interviewed`` flag use. THE AGGREGATE REPORT (:func:`calibration`) names
interviewers only, never a candidate. The judgements DRILL-DOWN
(:func:`judgements`) is the one deliberate exception: it names candidates to
HR, because that is what a drill-down is for — capped at
:data:`JUDGEMENTS_LIMIT` rows, audited as ``panel.calibration.evidence_viewed``
WITHOUT any candidate id or name in the audit details, and refused (422)
whenever the cell it would drill into is already suppressed in the aggregate
report. Nothing in this module writes anything but its audit rows: no
scorecard, no status, no decision (O5 #10-#12).

PH5-E4 adds the governed spec (thresholds from
``app.metrics.definitions.calibration_spec()``, never a bare constant), the
per-criterion panel baseline (``app.calibration_core.criterion_baselines``),
labels joined through the FROZEN criterion (``round_criteria`` on
``round_id``) rather than a company-wide ``DISTINCT`` that could collide
across rounds, and the judgements drill-down itself (:func:`judgements`) —
see the security note above for what it is allowed to name and why.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.calibration_core import (
    ScoreRow,
    calibrate,
    criterion_baselines,
)
from app.interviewer_scorecards import RequestMeta, derived_state, list_assignable_interviewers
from app.metrics.definitions import CURRENT_SCORECARD_SQL, REGISTRY_HASH, calibration_spec
from app.models import AuditLog
from app.schedule_core import covered, merge

DEFAULT_MAX_PER_DAY = 4
DEFAULT_MAX_PER_WEEK = 15
MAX_SPAN = timedelta(days=92)
# Calibration looks back over scored interviews, and a quiet panel needs a long
# period to reach the minimum number of candidates at all. Read from the
# governed spec (PH5-E4), not a bare constant — kept as module constants
# because ``routers/interview_scheduling.py`` imports MAX_CALIBRATION_SPAN
# directly.
MAX_CALIBRATION_SPAN = timedelta(days=int(calibration_spec().thresholds["max_span_days"]))
# A calibration period shorter than this adds nothing but a way to isolate one
# candidate by time (security review M1).
MIN_CALIBRATION_SPAN = timedelta(days=int(calibration_spec().thresholds["min_span_days"]))
# Days are counted in the company's working timezone, not UTC: an interview at
# 09:00 IST belongs to that Indian working day.
WORK_TZ = "Asia/Kolkata"
# The drill-down is capped, newest first — the same "an unbounded list pushes
# the real question out" reasoning as app.metrics.compute.MEMBERS_LIMIT.
JUDGEMENTS_LIMIT = 200


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
# The "last word" rule (CURRENT_SCORECARD_SQL) substituted in below — the SAME
# fragment ``hire_interviewer_score@1`` and the ``interviewed`` flag use, so
# calibration and quality-of-hire agree on what "this interviewer's current
# scorecard" means (PH5-E4, closing O5 gap #10). `.replace(...)`, not an
# f-string: bandit's B608 heuristic flags both unconditionally, and this
# template is long; the value substituted is a module constant from the
# validated registry, never a request value.
_SCORES_SQL_TEMPLATE = """
SELECT s.id AS scorecard_id, s.interviewer_user_id, s.enrolment_id, s.round_id,
       ss.competency_id, ss.score, ss.not_assessed
  FROM interviewer_scorecards s
  LEFT JOIN interviewer_scorecards nxt ON nxt.id = s.superseded_by_id
  JOIN interviewer_scorecard_scores ss ON ss.scorecard_id = s.id
  JOIN enrolments e ON e.id = s.enrolment_id
 WHERE s.company_id = :c
   AND ({current_scorecard})
   AND s.submitted_at >= :from_ts AND s.submitted_at < :to_ts
   AND (CAST(:r AS uuid) IS NULL OR e.requisition_id = CAST(:r AS uuid))
   AND (CAST(:rd AS uuid) IS NULL OR s.round_id = CAST(:rd AS uuid))
   -- The PH4-A1 independence rule: a caller who still owes their own
   -- scorecard for a candidate and round sees nobody else's for it, here as
   -- everywhere (security review M1).
   AND NOT EXISTS (
       SELECT 1 FROM interviewer_scorecards mine
        WHERE mine.enrolment_id = s.enrolment_id AND mine.round_id = s.round_id
          AND mine.interviewer_user_id = :me
          AND mine.status IN ('assigned', 'in_progress') AND mine.superseded_at IS NULL)
 LIMIT 50000
"""  # nosec B608 — see module policy: every value is bound, every fragment a module constant
_SCORES_SQL = _SCORES_SQL_TEMPLATE.replace("{current_scorecard}", CURRENT_SCORECARD_SQL)

# The SAME "current scorecard" rule, for the judgements drill-down's PANEL
# baseline (a LATERAL subquery keyed s2/nxt2, since s/nxt are already the
# outer scorecard). Pinned by a unit test against
# ``CURRENT_SCORECARD_SQL.replace("s.", "s2.").replace("nxt.", "nxt2.")`` so
# it cannot silently drift from the one true rule.
_PANEL_CURRENT_SCORECARD_SQL = (
    "s2.status = 'submitted' AND (s2.superseded_at IS NULL OR nxt2.status = 'withdrawn')"
)

_CRITERION_LABELS_SQL = """
SELECT rc.round_id, rc.competency_id, rc.competency_name,
       wr.title AS round_title, wr.kind AS round_kind,
       w.version AS workflow_version, w.requisition_id,
       req.title AS requisition_title
  FROM round_criteria rc
  JOIN workflow_rounds wr ON wr.id = rc.round_id
  JOIN workflows w ON w.id = wr.workflow_id
  LEFT JOIN job_requisitions req ON req.id = w.requisition_id AND req.company_id = w.company_id
 WHERE rc.company_id = :c AND rc.round_id = ANY(:round_ids)
"""

_JUDGEMENTS_SQL_TEMPLATE = """
SELECT s.id AS scorecard_id, s.round_id, s.enrolment_id, s.submitted_at,
       ss.competency_id, ss.score,
       e.applicant_id, e.requisition_id,
       ap.full_name AS candidate_name,
       req.title AS requisition_title,
       wr.title AS round_title,
       rc.competency_name,
       panel.panel_mean, panel.panel_size
  FROM interviewer_scorecards s
  LEFT JOIN interviewer_scorecards nxt ON nxt.id = s.superseded_by_id
  JOIN interviewer_scorecard_scores ss ON ss.scorecard_id = s.id AND NOT ss.not_assessed
  JOIN enrolments e ON e.id = s.enrolment_id
  JOIN applicants ap ON ap.id = e.applicant_id AND ap.company_id = e.company_id
  LEFT JOIN job_requisitions req ON req.id = e.requisition_id AND req.company_id = e.company_id
  JOIN workflow_rounds wr ON wr.id = s.round_id
  LEFT JOIN round_criteria rc ON rc.round_id = s.round_id AND rc.competency_id = ss.competency_id
  LEFT JOIN LATERAL (
      SELECT avg(ss2.score) FILTER (WHERE NOT ss2.not_assessed) AS panel_mean,
             count(*) FILTER (WHERE NOT ss2.not_assessed) AS panel_size
        FROM interviewer_scorecards s2
        LEFT JOIN interviewer_scorecards nxt2 ON nxt2.id = s2.superseded_by_id
        JOIN interviewer_scorecard_scores ss2
          ON ss2.scorecard_id = s2.id AND ss2.competency_id = ss.competency_id
       WHERE s2.enrolment_id = s.enrolment_id AND s2.round_id = s.round_id
         AND s2.company_id = s.company_id
         AND ({panel_current_scorecard})
  ) panel ON TRUE
 WHERE s.company_id = :c
   AND s.interviewer_user_id = :interviewer_id
   AND ({current_scorecard})
   AND s.submitted_at >= :from_ts AND s.submitted_at < :to_ts
   AND (CAST(:r AS uuid) IS NULL OR e.requisition_id = CAST(:r AS uuid))
   AND (CAST(:rd AS uuid) IS NULL OR s.round_id = CAST(:rd AS uuid))
   AND (CAST(:cid AS text) IS NULL OR ss.competency_id = CAST(:cid AS text))
   -- The PH4-A1 independence rule, same as the aggregate query.
   AND NOT EXISTS (
       SELECT 1 FROM interviewer_scorecards mine
        WHERE mine.enrolment_id = s.enrolment_id AND mine.round_id = s.round_id
          AND mine.interviewer_user_id = :me
          AND mine.status IN ('assigned', 'in_progress') AND mine.superseded_at IS NULL)
 ORDER BY s.submitted_at DESC
 LIMIT :lim
"""  # nosec B608 — see module policy
_JUDGEMENTS_SQL = (
    _JUDGEMENTS_SQL_TEMPLATE
    .replace("{current_scorecard}", CURRENT_SCORECARD_SQL)
    .replace("{panel_current_scorecard}", _PANEL_CURRENT_SCORECARD_SQL)
)
# The fragment concatenated below is module-constant text (the
# already-substituted _JUDGEMENTS_SQL, wrapped in a COUNT); no request value
# is interpolated, same policy as this module's other assembled SQL.
_JUDGEMENTS_COUNT_SQL = (
    "SELECT count(*) FROM (" + _JUDGEMENTS_SQL.replace(" LIMIT :lim", "") + ") j"  # nosec B608
)


async def _score_rows(
    db: AsyncSession, *, company_id: uuid.UUID, start: datetime, end: datetime,
    requisition_id: uuid.UUID | None, round_id: uuid.UUID | None, actor: uuid.UUID,
) -> Sequence[RowMapping]:
    return (
        await db.execute(
            text(_SCORES_SQL),
            {"c": company_id, "from_ts": start, "to_ts": end, "r": requisition_id,
             "rd": round_id, "me": actor},
        )
    ).mappings().all()


async def _criterion_labels(
    db: AsyncSession, *, company_id: uuid.UUID, round_ids: set[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Labels for every ``(round_id, competency_id)`` FROZEN criterion in
    ``round_ids`` — joined through ``round_criteria`` on ``round_id``, never
    the company-wide ``DISTINCT competency_id`` the O5 report used, which
    could collide two different rounds' anchors under one name (O5 gap #2)."""
    if not round_ids:
        return {}
    rows = (
        await db.execute(
            text(_CRITERION_LABELS_SQL),
            {"c": company_id, "round_ids": [uuid.UUID(r) for r in round_ids]},
        )
    ).mappings().all()
    return {
        (str(r["round_id"]), r["competency_id"]): {
            "competency_name": r["competency_name"], "round_title": r["round_title"],
            "round_kind": r["round_kind"], "workflow_version": r["workflow_version"],
            "requisition_id": str(r["requisition_id"]) if r["requisition_id"] else None,
            "requisition_title": r["requisition_title"],
        }
        for r in rows
    }


async def calibration(
    db: AsyncSession, *, company_id: uuid.UUID, start: datetime, end: datetime,
    requisition_id: uuid.UUID | None, round_id: uuid.UUID | None, actor: uuid.UUID,
    meta: RequestMeta,
) -> dict[str, Any]:
    spec = calibration_spec()
    start, end = _period(start, end, MAX_CALIBRATION_SPAN)
    if end - start < MIN_CALIBRATION_SPAN:
        raise PanelError(422, "Choose a period of at least 7 days.")
    rows = await _score_rows(
        db, company_id=company_id, start=start, end=end, requisition_id=requisition_id,
        round_id=round_id, actor=actor,
    )
    score_rows = [
        ScoreRow(str(r["scorecard_id"]), str(r["interviewer_user_id"]), str(r["enrolment_id"]),
                 str(r["round_id"]), r["competency_id"],
                 None if r["not_assessed"] else r["score"])
        for r in rows
    ]
    report = calibrate(score_rows, spec)
    baselines = criterion_baselines(score_rows, spec)
    names = {p["user_id"]: p["full_name"]
             for p in await list_assignable_interviewers(db, company_id=company_id)}
    labels = await _criterion_labels(
        db, company_id=company_id, round_ids={str(r["round_id"]) for r in rows},
    )

    competencies: dict[str, str] = {}
    if round_id is not None:
        rid = str(round_id)
        competencies = {
            comp: label["competency_name"]
            for (r, comp), label in labels.items() if r == rid
        }

    criteria_out = [
        {
            "criterion_key": b.criterion_key, "round_id": b.round_id,
            "competency_id": b.competency_id,
            "competency_name": (
                labels.get((b.round_id, b.competency_id), {}).get("competency_name")
                or b.competency_id
            ),
            "round_title": labels.get((b.round_id, b.competency_id), {}).get("round_title"),
            "round_kind": labels.get((b.round_id, b.competency_id), {}).get("round_kind"),
            "workflow_version": labels.get((b.round_id, b.competency_id), {}).get(
                "workflow_version"
            ),
            "requisition_id": labels.get((b.round_id, b.competency_id), {}).get(
                "requisition_id"
            ),
            "requisition_title": labels.get((b.round_id, b.competency_id), {}).get(
                "requisition_title"
            ),
            "suppressed": b.suppressed,
            "candidates": b.candidates, "interviewers": b.interviewers,
            # Withheld with the other figures below the floor, on the O5
            # precedent: a count over one candidate says how many criteria
            # they were scored on. not_assessed is the SAME shape of count
            # over the SAME too-small population as scores, so it is withheld
            # alongside it (security review, PH5-E4 sign-off) — only
            # candidates/interviewers (population sizes, not scores) survive
            # suppression.
            "scores": None if b.suppressed else b.scores,
            "not_assessed": None if b.suppressed else b.not_assessed,
            "distribution": (
                {str(k): v for k, v in sorted(b.distribution.items())}
                if b.distribution is not None else None
            ),
            "mean": b.mean, "shared_judgements": b.shared_judgements,
            "disagreement": b.disagreement, "signal": b.signal,
        }
        for b in baselines
    ]

    db.add(AuditLog(
        actor_id=actor, actor_type="user", action="panel.calibration.viewed",
        resource_type="company", resource_id=company_id,
        details={
            "start": start.isoformat(), "end": end.isoformat(),
            "spec": {"name": spec.name, "version": spec.version},
            "registry_hash": REGISTRY_HASH, "basis": "scorecard_submitted",
            "filters": {"requisition_id": str(requisition_id) if requisition_id else None,
                        "round_id": str(round_id) if round_id else None},
            "interviewers": len(report), "criteria": len(criteria_out),
        },
        ip_address=meta.ip_address, user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))
    return {
        "spec": {"name": spec.name, "version": spec.version},
        "registry_hash": REGISTRY_HASH,
        "cohort": {"basis": "scorecard_submitted", "from": start.isoformat(), "to": end.isoformat()},
        "filters": {"requisition_id": str(requisition_id) if requisition_id else None,
                    "round_id": str(round_id) if round_id else None},
        "start": start.isoformat(), "end": end.isoformat(),
        "rules": dict(spec.thresholds),
        "competencies": competencies,
        "criteria": criteria_out,
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
             # by_competency mixes different rounds' anchors under one
             # competency id (O5 gap #1); shown only when a round_id filter
             # makes it unambiguous — calibrate() still always computes it.
             "by_competency": (c.by_competency if round_id is not None else {}),
             "pairs": c.pairs, "mean_delta": c.mean_delta,
             "same_direction_share": c.same_direction_share, "flag": c.flag,
             "by_criterion": [
                 {"criterion_key": g.criterion_key, "shared_judgements": g.shared_judgements,
                  "candidates": g.candidates, "gap": g.gap, "signal": g.signal}
                 for g in c.by_criterion
             ]}
            for c in report
        ],
    }


async def judgements(
    db: AsyncSession, *, company_id: uuid.UUID, interviewer_id: uuid.UUID, start: datetime,
    end: datetime, requisition_id: uuid.UUID | None, round_id: uuid.UUID | None,
    criterion_key: str | None, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """The drill-down behind one interviewer's calibration row, or one
    interviewer x criterion cell — criterion 13.

    Refuses (422) when the interviewer's row, or the requested criterion
    cell, would be suppressed in the aggregate report — the drill-down can
    never show what the summary itself withheld. 404 when the interviewer is
    not ``list_assignable_interviewers`` for this company (tenant isolation).
    Applies the SAME independence ``NOT EXISTS`` as the aggregate query.
    Capped at :data:`JUDGEMENTS_LIMIT` rows, newest first. Audited as
    ``panel.calibration.evidence_viewed`` with no candidate ids or names.
    """
    spec = calibration_spec()
    start, end = _period(start, end, MAX_CALIBRATION_SPAN)
    if end - start < MIN_CALIBRATION_SPAN:
        raise PanelError(422, "Choose a period of at least 7 days.")

    people = await list_assignable_interviewers(db, company_id=company_id)
    match = next((p for p in people if p["user_id"] == str(interviewer_id)), None)
    if match is None:
        raise PanelError(404, "Interviewer not found.")

    crit_round_id: uuid.UUID | None = None
    crit_competency_id: str | None = None
    if criterion_key is not None:
        try:
            round_part, comp_part = criterion_key.split(":", 1)
            crit_round_id = uuid.UUID(round_part)
        except ValueError as exc:
            raise PanelError(422, "criterion_key must be '<round_id>:<competency_id>'.") from exc
        crit_competency_id = comp_part

    rows = await _score_rows(
        db, company_id=company_id, start=start, end=end, requisition_id=requisition_id,
        round_id=round_id, actor=actor,
    )
    score_rows = [
        ScoreRow(str(r["scorecard_id"]), str(r["interviewer_user_id"]), str(r["enrolment_id"]),
                 str(r["round_id"]), r["competency_id"],
                 None if r["not_assessed"] else r["score"])
        for r in rows
    ]
    report = {c.interviewer_id: c for c in calibrate(score_rows, spec)}
    mine = report.get(str(interviewer_id))
    if mine is None or mine.suppressed:
        raise PanelError(422, "Too few candidates to show.")
    if criterion_key is not None and not any(g.criterion_key == criterion_key for g in mine.by_criterion):
        raise PanelError(422, "Too few candidates to show.")

    effective_round_id = crit_round_id or round_id
    params = {
        "c": company_id, "interviewer_id": interviewer_id, "from_ts": start, "to_ts": end,
        "r": requisition_id, "rd": effective_round_id, "cid": crit_competency_id, "me": actor,
    }
    total = int((await db.execute(text(_JUDGEMENTS_COUNT_SQL), params)).scalar_one())
    detail_rows = (
        await db.execute(text(_JUDGEMENTS_SQL), {**params, "lim": JUDGEMENTS_LIMIT})
    ).mappings().all()

    db.add(AuditLog(
        actor_id=actor, actor_type="user", action="panel.calibration.evidence_viewed",
        resource_type="user", resource_id=interviewer_id,
        details={
            "company_id": str(company_id), "start": start.isoformat(), "end": end.isoformat(),
            "requisition_id": str(requisition_id) if requisition_id else None,
            "round_id": str(round_id) if round_id else None,
            "criterion_key": criterion_key, "rows": total,
        },
        ip_address=meta.ip_address, user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))
    return {
        "spec": {"name": spec.name, "version": spec.version},
        "cohort": {"basis": "scorecard_submitted", "from": start.isoformat(), "to": end.isoformat()},
        "filters": {"requisition_id": str(requisition_id) if requisition_id else None,
                    "round_id": str(round_id) if round_id else None,
                    "criterion_key": criterion_key},
        "interviewer": {"user_id": str(interviewer_id), "name": match["full_name"]},
        "total": total, "truncated": total > len(detail_rows),
        "rows": [
            {
                "enrolment_id": str(r["enrolment_id"]), "applicant_id": str(r["applicant_id"]),
                "candidate_name": r["candidate_name"],
                "requisition_title": r["requisition_title"],
                "round_id": str(r["round_id"]), "round_title": r["round_title"],
                "criterion_key": f"{r['round_id']}:{r['competency_id']}",
                "competency_name": r["competency_name"] or r["competency_id"],
                "score": r["score"],
                "panel_mean": (
                    round(float(r["panel_mean"]), 3) if r["panel_mean"] is not None else None
                ),
                "panel_size": int(r["panel_size"] or 0),
                "gap": (
                    round(float(r["score"]) - float(r["panel_mean"]), 3)
                    if r["panel_mean"] is not None else None
                ),
                "scorecard_id": str(r["scorecard_id"]),
                "submitted_at": r["submitted_at"].isoformat(),
                "evidence_href": f"/hr/enrolments/{r['enrolment_id']}/evidence",
            }
            for r in detail_rows
        ],
    }
