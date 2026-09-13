"""The company-wide hiring board — Group E, E3.

A company's super admin sees every open opening in one place, with the numbers
that say whether hiring is on course: who applied, who is still in play, hires
against the target, who is waiting on a person, and a health signal.

THE HEALTH SIGNAL IS COMPUTED, NOT ENTERED
It comes from the stage ledger, which records every move a candidate makes.
The question it answers is the one the spec asks: at the pace candidates have
actually been moving, will this opening fill by its closing date?

    pace       = candidates who reached a decision point (finished the workflow
                 or were hired) in the recent window, per day
    hire ratio = this opening's own hires ÷ (hires + rejections), once it has
                 at least MIN_DECISIONS of them; DEFAULT_HIRE_RATIO until then
    hires/day  = pace × hire ratio
    fill date  = today + (target − hired) ÷ hires/day

Why a pace of candidates and not of hires: hires are rare — an opening with a
target of three sees none for weeks — so a rate of hires alone reads zero, then
jumps, and says nothing useful until it is too late. Candidates reaching a
decision move every week. Scaling that by how many decisions become hires turns
it into a hiring pace without waiting for hires to happen. DEFAULT_HIRE_RATIO is
an assumption and is labelled as one on the board until the opening's own
decisions replace it.

THE BANDS
* not_published — no published workflow. A separate state, because nothing is
  moving anyone and no projection means anything; says so when the opening is
  also taking applications.
* at_risk — the projected fill date is after the closing date (including a
  pace of zero), or the closing date has passed short of target.
* watch — cannot honestly be projected yet (no target or closing date, open for
  under a week), fills with under a week to spare, or nobody has moved in two
  weeks.
* on_track — projected to fill before the closing date, or the target is met.

Pure (``hiring_health``) plus one company-scoped query (``hiring_board``).
Read-only: nothing here changes anything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WINDOW_DAYS = 30
MIN_DAYS_OPEN = 7
MIN_DECISIONS = 5
DEFAULT_HIRE_RATIO = 0.25
STALE_DAYS = 14
WATCH_MARGIN_DAYS = 7
BOARD_LIMIT = 200

BANDS = ("not_published", "at_risk", "watch", "on_track")
_BAND_ORDER = {b: i for i, b in enumerate(BANDS)}


@dataclass
class HealthInput:
    has_published_workflow: bool
    accepting_applications: bool
    target_hires: int | None
    hired: int
    rejected: int
    in_play: int
    closes_at: datetime | None
    created_at: datetime
    reached_decision: int
    last_movement_at: datetime | None
    now: datetime


def _day(d: datetime) -> str:
    return d.date().isoformat()


def hiring_health(h: HealthInput) -> dict[str, Any]:
    """The band, the reason in words, and the projection behind it. Pure."""

    def out(band: str, reason: str, fill: datetime | None = None,
            per_week: float | None = None, assumed: bool = False) -> dict[str, Any]:
        return {"band": band, "reason": reason,
                "projected_fill_date": _day(fill) if fill else None,
                "hires_per_week": round(per_week, 2) if per_week is not None else None,
                "assumed_hire_ratio": assumed}

    if not h.has_published_workflow:
        why = (
            "It is taking applications, but no workflow is published, so nobody who "
            "applies moves anywhere."
            if h.accepting_applications
            else "No workflow is published, so nobody is moving through a process."
        )
        return out("not_published", why)

    if h.target_hires and h.hired >= h.target_hires:
        return out("on_track", f"Target met: {h.hired} of {h.target_hires} hired.")
    if not h.target_hires or h.closes_at is None:
        return out("watch", "No target or no closing date, so there is nothing to project against.")

    remaining = h.target_hires - h.hired
    if h.closes_at <= h.now:
        return out("at_risk", f"Past its closing date ({_day(h.closes_at)}) with {h.hired} of "
                               f"{h.target_hires} hired.")

    days_open = (h.now - h.created_at).total_seconds() / 86_400
    if days_open < MIN_DAYS_OPEN:
        return out("watch", f"Open for under {MIN_DAYS_OPEN} days — too early to project a pace.")

    window = max(1.0, min(float(WINDOW_DAYS), days_open))
    decided = h.hired + h.rejected
    assumed = decided < MIN_DECISIONS
    ratio = DEFAULT_HIRE_RATIO if assumed else h.hired / decided
    per_day = (h.reached_decision / window) * ratio
    basis = (f"assuming {round(DEFAULT_HIRE_RATIO * 100)}% of decisions become hires until "
             f"there are {MIN_DECISIONS}" if assumed
             else f"{round(ratio * 100)}% of its decisions so far were hires")

    if per_day <= 0:
        return out("at_risk",
                   f"Nobody has reached a decision in the last {int(window)} days, so at this "
                   f"pace the remaining {remaining} hire{'' if remaining == 1 else 's'} will not "
                   f"happen by {_day(h.closes_at)}.", per_week=0.0, assumed=assumed)

    fill = h.now + timedelta(days=remaining / per_day)
    per_week = per_day * 7
    if fill > h.closes_at:
        return out("at_risk",
                   f"At the recent pace ({basis}) the target fills around {_day(fill)}, after "
                   f"the closing date ({_day(h.closes_at)}).", fill, per_week, assumed)
    if h.last_movement_at is None or (h.now - h.last_movement_at).days >= STALE_DAYS:
        return out("watch", f"No candidate has moved in {STALE_DAYS} days.", fill, per_week,
                   assumed)
    if fill > h.closes_at - timedelta(days=WATCH_MARGIN_DAYS):
        return out("watch",
                   f"On pace to fill around {_day(fill)}, with under a week to spare before "
                   f"{_day(h.closes_at)}.", fill, per_week, assumed)
    return out("on_track",
               f"At the recent pace ({basis}) the target fills around {_day(fill)}, before the "
               f"closing date ({_day(h.closes_at)}).", fill, per_week, assumed)


# Every open or paused opening in ONE company, with its counts and pace. The
# company is constrained on every table. "Awaiting a decision" is the shared
# database definition the decision queue uses.
_BOARD_SQL = """
SELECT r.id, r.title, r.location, r.status, r.target_hires, r.closes_at, r.created_at,
       r.public_apply_enabled,
       pub.version AS published_version,
       (SELECT max(w.version) FROM workflows w
         WHERE w.requisition_id = r.id AND w.company_id = r.company_id
           AND w.status = 'draft' AND w.deleted_at IS NULL) AS draft_version,
       count(e.id) AS applied,
       count(e.id) FILTER (WHERE e.status NOT IN ('hired', 'rejected')) AS in_play,
       count(e.id) FILTER (WHERE e.status = 'hired') AS hired,
       count(e.id) FILTER (WHERE e.status = 'rejected') AS rejected,
       count(e.id) FILTER (WHERE enrolment_awaits_human(e.status, e.current_round_id))
         AS awaiting_decision,
       (SELECT count(DISTINCT t.enrolment_id)
          FROM stage_transitions t
          JOIN enrolments x ON x.id = t.enrolment_id AND x.company_id = t.company_id
         WHERE x.requisition_id = r.id AND t.company_id = r.company_id
           AND x.deleted_at IS NULL
           AND t.to_status IN ('interviewed', 'hired')
           AND t.from_status IS DISTINCT FROM t.to_status
           AND t.occurred_at > NOW() - make_interval(days => :window)) AS reached_decision,
       (SELECT max(t.occurred_at)
          FROM stage_transitions t
          JOIN enrolments x ON x.id = t.enrolment_id AND x.company_id = t.company_id
         WHERE x.requisition_id = r.id AND t.company_id = r.company_id) AS last_movement_at
  FROM job_requisitions r
  LEFT JOIN LATERAL (
       SELECT w.version FROM workflows w
        WHERE w.requisition_id = r.id AND w.company_id = r.company_id
          AND w.status = 'published' AND w.deleted_at IS NULL
        LIMIT 1
  ) pub ON TRUE
  LEFT JOIN enrolments e
    ON e.requisition_id = r.id AND e.company_id = r.company_id AND e.deleted_at IS NULL
 WHERE r.company_id = :c AND r.deleted_at IS NULL AND r.status IN ('open', 'paused')
 GROUP BY r.id, pub.version
 ORDER BY r.created_at DESC
 LIMIT :lim
"""


async def hiring_board(db: AsyncSession, *, company_id: uuid.UUID) -> dict[str, Any]:
    """Every open or paused opening in the company, worst health first."""
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(_BOARD_SQL), {"c": company_id, "window": WINDOW_DAYS, "lim": BOARD_LIMIT}
        )
    ).mappings().all()

    openings: list[dict[str, Any]] = []
    for r in rows:
        published = r["published_version"] is not None
        health = hiring_health(HealthInput(
            has_published_workflow=published,
            accepting_applications=bool(r["public_apply_enabled"]) and r["status"] == "open",
            target_hires=r["target_hires"],
            hired=int(r["hired"] or 0),
            rejected=int(r["rejected"] or 0),
            in_play=int(r["in_play"] or 0),
            closes_at=r["closes_at"],
            created_at=r["created_at"],
            reached_decision=int(r["reached_decision"] or 0),
            last_movement_at=r["last_movement_at"],
            now=now,
        ))
        openings.append({
            "requisition_id": str(r["id"]),
            "title": r["title"],
            "location": r["location"],
            "status": r["status"],
            "workflow_state": "published" if published
            else ("draft" if r["draft_version"] is not None else "none"),
            "published_version": r["published_version"],
            "draft_version": r["draft_version"],
            "accepting_applications": bool(r["public_apply_enabled"]),
            "applied": int(r["applied"] or 0),
            "in_play": int(r["in_play"] or 0),
            "target_hires": r["target_hires"],
            "hired": int(r["hired"] or 0),
            "awaiting_decision": int(r["awaiting_decision"] or 0),
            "closes_at": r["closes_at"].isoformat() if r["closes_at"] else None,
            "health": health,
        })

    openings.sort(key=lambda o: (_BAND_ORDER[o["health"]["band"]], o["title"].lower()))
    summary = {band: 0 for band in BANDS}
    for o in openings:
        summary[o["health"]["band"]] += 1
    return {"generated_at": now.isoformat(), "summary": summary, "openings": openings}
