"""What the per-opening dashboard shows beyond counts — Group E, E1.

The dashboard endpoint used to return a status histogram, the published
workflow's rounds and one median. That answers "how many?", not "what is
happening, and what needs me?". This module gathers the rest:

* **progress** — applications, who is moving through the workflow, who is
  waiting on a person, who is held, and hires against the target;
* **stage timing** — median days from application to the shortlist, to each
  round of the LIVE workflow, and to a hire, from the stage ledger (which
  records every move with its time) rather than from ``updated_at``;
* **scores** — the average resume match and the average of candidates' scored
  rounds, labelled as summaries: they rank and explain, they do not decide;
* **the held pool** — named, with why and for how long, so a hold is visibly a
  pause and never reads as a rejection (D-05);
* **needs attention** — the problems in THIS opening: held candidates, a
  decision backlog, assessment links about to expire or lapsed unused, scoring
  that is incomplete or gave up, no live workflow, an unpublished draft, a round
  almost nobody passes, a closing date approaching short of target;
* **activity** — what the automation did and when, told apart from what a
  person did, from the same ledger;
* **manual steps** — the things that wait for HR on purpose.

The rules that turn facts into "needs attention" items are pure functions over
a ``DashboardFacts`` value, so they are tested without a database. Every SQL
statement is a single literal (the SAST gate refuses assembled SQL) and is
scoped to the company and the requisition.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from shared.agents.watchers import WatcherInput, watch_round_stalls
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.watch_runner import gather_round_stalls
from app.publishing import public_gate_open

HELD_POOL_LIMIT = 25
ACTIVITY_LIMIT = 25
LINK_EXPIRY_WINDOW_HOURS = 48
LAPSED_LOOKBACK_DAYS = 14
DECISION_WAIT_WARN_DAYS = 3
DECISION_WAIT_CRITICAL_DAYS = 14
LOW_PASS_RATE = 20
LOW_PASS_MIN_ATTEMPTS = 5
CLOSING_SOON_DAYS = 7

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# One row: everything counted over this opening's enrolments. "Awaiting a
# person" is enrolment_awaits_human, the definition the decision queue uses.
_PROGRESS_SQL = """
SELECT count(*) AS applications,
       count(*) FILTER (WHERE e.status NOT IN ('hired', 'rejected')
                          AND e.current_round_id IS NOT NULL
                          AND NOT enrolment_awaits_human(e.status, e.current_round_id)
                       ) AS in_progress,
       count(*) FILTER (WHERE enrolment_awaits_human(e.status, e.current_round_id)) AS awaiting,
       count(*) FILTER (WHERE e.status = 'held') AS held,
       count(*) FILTER (WHERE e.status = 'hired'
         -- PH4-A3: a hire whose offer was declined, expired or withdrawn
         -- has not filled the role; the decision itself is unchanged.
         AND COALESCE(e.offer_outcome, '') NOT IN
             ('offer_declined', 'offer_expired', 'offer_withdrawn')) AS hired,
       count(*) FILTER (WHERE e.status = 'rejected') AS rejected,
       count(*) FILTER (WHERE e.status = 'new' AND e.current_round_id IS NULL) AS not_started,
       count(*) FILTER (WHERE e.status NOT IN ('hired', 'rejected', 'held')
                          AND wr.kind = 'human_review') AS awaiting_review,
       count(*) FILTER (WHERE e.status = 'interviewed' AND e.current_round_id IS NULL) AS finished,
       count(*) FILTER (WHERE e.status = 'new' AND e.current_round_id IS NULL
                          AND pub.thr IS NOT NULL AND e.ats_overall IS NOT NULL
                          AND e.ats_overall >= pub.thr * 10) AS ready_to_shortlist,
       max(pub.thr) AS shortlist_threshold,
       COALESCE(max(EXTRACT(EPOCH FROM (NOW() - enrolment_state_since(e.id, e.created_at)))
                    / 86400.0)
                FILTER (WHERE enrolment_awaits_human(e.status, e.current_round_id)),
                0) AS longest_wait_days,
       COALESCE(max(EXTRACT(EPOCH FROM (NOW() - COALESCE(e.held_at, e.updated_at))) / 86400.0)
                FILTER (WHERE e.status = 'held'), 0) AS longest_hold_days,
       avg(e.ats_overall) AS avg_ats,
       count(e.ats_overall) AS scored,
       count(*) FILTER (WHERE e.status NOT IN ('hired', 'rejected')
                          AND e.ats_overall IS NULL AND a.pending_enrichment) AS scoring_pending,
       count(*) FILTER (WHERE e.status NOT IN ('hired', 'rejected') AND e.ats_overall IS NULL
                          AND EXISTS (SELECT 1 FROM reconciliation_state rs
                                       WHERE rs.ref_id = e.id AND rs.gave_up_at IS NOT NULL)
                       ) AS scoring_failed,
       count(*) FILTER (WHERE e.status NOT IN ('hired', 'rejected')
                          AND e.workflow_id IS NULL) AS without_workflow,
       count(*) FILTER (WHERE e.status NOT IN ('hired', 'rejected')
                          AND e.workflow_id IS NOT NULL AND pub.id IS NOT NULL
                          AND e.workflow_id <> pub.id) AS on_older_version
  FROM enrolments e
  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL
  LEFT JOIN workflow_rounds wr ON wr.id = e.current_round_id
  LEFT JOIN LATERAL (
       SELECT w.id, w.shortlist_ats_threshold AS thr
         FROM workflows w
        WHERE w.requisition_id = e.requisition_id AND w.status = 'published'
          AND w.deleted_at IS NULL
        LIMIT 1
  ) pub ON TRUE
 WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
"""

_WORKFLOW_STATE_SQL = """
SELECT (SELECT w.version FROM workflows w
         WHERE w.requisition_id = :r AND w.company_id = :c AND w.status = 'published'
           AND w.deleted_at IS NULL
         LIMIT 1) AS published_version,
       (SELECT max(w.version) FROM workflows w
         WHERE w.requisition_id = :r AND w.company_id = :c AND w.status = 'draft'
           AND w.deleted_at IS NULL) AS draft_version
"""

# First time each application reached the shortlist, a hire, and each round —
# measured from when they applied. GREATEST(0, …) absorbs backfilled ledger
# rows stamped fractionally before the enrolment row.
_TIMING_SQL = """
SELECT x.key, percentile_cont(0.5) WITHIN GROUP (ORDER BY x.days) AS median_days,
       count(*) AS n
  FROM (
        SELECT t.to_status AS key, e.id,
               GREATEST(0, EXTRACT(EPOCH FROM (min(t.occurred_at) - e.created_at)) / 86400.0)
                 AS days
          FROM enrolments e
          JOIN stage_transitions t
            ON t.enrolment_id = e.id AND t.to_status IN ('shortlisted', 'hired')
           AND t.from_status IS DISTINCT FROM t.to_status
         WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
         GROUP BY t.to_status, e.id, e.created_at
        UNION ALL
        SELECT CAST(t.to_round_id AS text) AS key, e.id,
               GREATEST(0, EXTRACT(EPOCH FROM (min(t.occurred_at) - e.created_at)) / 86400.0)
                 AS days
          FROM enrolments e
          JOIN stage_transitions t ON t.enrolment_id = e.id AND t.to_round_id IS NOT NULL
         WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
         GROUP BY t.to_round_id, e.id, e.created_at
       ) x
 GROUP BY x.key
"""

# The mean of each candidate's scored rounds, then the mean of those — so one
# candidate who sat four rounds does not count four times.
_COMPOSITE_SQL = """
SELECT avg(per.mean) AS avg_composite, count(*) AS assessed
  FROM (
        SELECT rr.enrolment_id, avg(rr.percent) AS mean
          FROM round_results rr
          JOIN enrolments e ON e.id = rr.enrolment_id
         WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
           AND rr.superseded_at IS NULL AND rr.percent IS NOT NULL
         GROUP BY rr.enrolment_id
       ) per
"""

_LINKS_SQL = """
SELECT
  (SELECT count(*) FROM exam_assignments x JOIN enrolments e ON e.id = x.enrolment_id
    WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
      AND e.status NOT IN ('hired', 'rejected')
      AND x.status IN ('invited', 'started') AND x.consumed_at IS NULL
      AND x.expires_at > NOW() AND x.expires_at <= NOW() + make_interval(hours => :h))
  +
  (SELECT count(*) FROM interview_invites i JOIN enrolments e ON e.id = i.enrolment_id
    WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
      AND e.status NOT IN ('hired', 'rejected')
      AND i.status = 'invited' AND i.consumed_at IS NULL
      AND i.expires_at > NOW() AND i.expires_at <= NOW() + make_interval(hours => :h))
  AS expiring,
  (SELECT count(*) FROM exam_assignments x JOIN enrolments e ON e.id = x.enrolment_id
    WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
      AND e.status NOT IN ('hired', 'rejected') AND x.consumed_at IS NULL
      AND (x.status = 'expired' OR (x.status = 'invited' AND x.expires_at <= NOW()))
      AND x.expires_at > NOW() - make_interval(days => :lb))
  +
  (SELECT count(*) FROM interview_invites i JOIN enrolments e ON e.id = i.enrolment_id
    WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
      AND e.status NOT IN ('hired', 'rejected') AND i.consumed_at IS NULL
      AND (i.status = 'expired' OR (i.status = 'invited' AND i.expires_at <= NOW()))
      AND i.expires_at > NOW() - make_interval(days => :lb))
  AS lapsed
"""

_HELD_SQL = """
SELECT e.id, e.applicant_id, a.full_name, e.held_reason, e.ats_overall,
       wr.title AS round_title,
       EXTRACT(EPOCH FROM (NOW() - COALESCE(e.held_at, e.updated_at))) / 86400.0 AS held_days
  FROM enrolments e
  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL
  LEFT JOIN workflow_rounds wr ON wr.id = e.current_round_id
 WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL
   AND e.status = 'held'
 ORDER BY e.held_at NULLS LAST, e.created_at
 LIMIT :lim
"""

_ACTIVITY_SQL = """
SELECT t.occurred_at, t.automated, t.from_status, t.to_status, t.reason,
       fr.title AS from_round, tr.title AS to_round,
       a.full_name AS candidate, u.full_name AS actor, e.id AS enrolment_id
  FROM stage_transitions t
  JOIN enrolments e ON e.id = t.enrolment_id
  JOIN applicants a ON a.id = e.applicant_id
  LEFT JOIN workflow_rounds fr ON fr.id = t.from_round_id
  LEFT JOIN workflow_rounds tr ON tr.id = t.to_round_id
  LEFT JOIN users u ON u.id = t.actor_user_id
 WHERE e.requisition_id = :r AND t.company_id = :c AND e.company_id = :c
 ORDER BY t.occurred_at DESC, t.id DESC
 LIMIT :lim
"""

_ACTIVITY_SUMMARY_SQL = """
SELECT count(*) FILTER (WHERE t.automated) AS automated,
       count(*) FILTER (WHERE NOT t.automated) AS manual,
       max(t.occurred_at) FILTER (WHERE t.automated) AS last_automated_at
  FROM stage_transitions t
  JOIN enrolments e ON e.id = t.enrolment_id
 WHERE e.requisition_id = :r AND t.company_id = :c
   AND t.occurred_at > NOW() - interval '7 days'
"""


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------
@dataclass
class DashboardFacts:
    """What the attention rules read. Built from the queries, or by a test."""

    requisition_id: str
    status: str
    accepting_public: bool
    has_published_workflow: bool
    now: datetime
    published_version: int | None = None
    draft_version: int | None = None
    awaiting: int = 0
    held: int = 0
    longest_wait_days: float = 0.0
    longest_hold_days: float = 0.0
    expiring_links: int = 0
    lapsed_links: int = 0
    scoring_pending: int = 0
    scoring_failed: int = 0
    without_workflow: int = 0
    target_hires: int | None = None
    hired: int = 0
    closes_at: datetime | None = None
    # (round title, pass rate %, attempts) for rounds almost nobody clears.
    low_pass_rounds: list[tuple[str, int, int]] = field(default_factory=list)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def attention_items(f: DashboardFacts) -> list[dict[str, Any]]:
    """The problems in this opening, worst first. Pure."""
    rid = f.requisition_id
    decisions = f"/hr/requisitions/{rid}/decisions"
    workflow = f"/hr/requisitions/{rid}/workflow"
    items: list[dict[str, Any]] = []

    def add(key: str, severity: str, title: str, body: str, link: str | None) -> None:
        items.append({"key": key, "severity": severity, "title": title, "body": body,
                      "link": link})

    live = f.status != "closed"

    if live and not f.has_published_workflow and (f.accepting_public or f.without_workflow):
        who = (
            f"{_plural(f.without_workflow, 'candidate')} in this opening "
            f"{'has' if f.without_workflow == 1 else 'have'} nothing to move through. "
            if f.without_workflow
            else ""
        )
        add("no_workflow", "warning", "No live workflow",
            f"{who}Nobody advances until a workflow is published; publishing attaches "
            "everyone who is waiting."
            + (" The public link will not accept applications until then."
               if f.accepting_public else ""), workflow)

    if f.held:
        add("held", "warning" if f.longest_hold_days >= DECISION_WAIT_WARN_DAYS else "info",
            f"{_plural(f.held, 'candidate')} held for your decision",
            "They scored below a round threshold and were held, not rejected. The longest "
            f"has waited {f.longest_hold_days:.0f} day{'' if round(f.longest_hold_days) == 1 else 's'}.",
            decisions)

    waiting_not_held = f.awaiting - f.held
    if waiting_not_held > 0 and f.longest_wait_days >= DECISION_WAIT_WARN_DAYS:
        add("decision_backlog",
            "critical" if f.longest_wait_days >= DECISION_WAIT_CRITICAL_DAYS else "warning",
            f"{_plural(waiting_not_held, 'candidate')} waiting on a decision",
            "They finished every round or reached a review round. The longest has waited "
            f"{f.longest_wait_days:.0f} days, and nothing moves them without a person.",
            decisions)

    if f.expiring_links:
        add("links_expiring", "warning",
            f"{_plural(f.expiring_links, 'assessment link')} expire within "
            f"{LINK_EXPIRY_WINDOW_HOURS} hours",
            "These candidates have not started yet. A lapsed link does not reject anyone, "
            "but it does stall them.", None)

    if f.lapsed_links:
        add("links_lapsed", "info",
            f"{_plural(f.lapsed_links, 'link')} lapsed unused in the last "
            f"{LAPSED_LOOKBACK_DAYS} days",
            "Those candidates are still in the opening. Re-send the assessment or decide "
            "on them.", None)

    if f.scoring_failed:
        add("scoring_failed", "warning",
            f"{_plural(f.scoring_failed, 'application')} could not be scored",
            "Scoring gave up after repeated attempts — usually an unreadable CV. Open them "
            "to check the file.", "/hr/applicants")

    if f.scoring_pending:
        add("scoring_pending", "info",
            f"{_plural(f.scoring_pending, 'CV')} still being read",
            "Their resume match fills in shortly. An empty score means not read yet, not "
            "zero.", None)

    for title, rate, attempts in f.low_pass_rounds:
        add(f"low_pass:{title}", "warning", f"{title}: {rate}% pass rate",
            f"Only {rate}% of {attempts} candidates cleared it. A round almost nobody passes "
            "is usually the threshold or the questions, not the candidates.", workflow)

    if (live and f.draft_version and f.published_version
            and f.draft_version > f.published_version):
        add("draft_pending", "info", f"Version {f.draft_version} is still a draft",
            f"Candidates keep running version {f.published_version} until you publish it.",
            workflow)

    if live and f.closes_at is not None:
        days_left = (f.closes_at - f.now).total_seconds() / 86400
        short = f.target_hires is not None and f.hired < f.target_hires
        if days_left < 0:
            add("past_close", "warning", "Past its closing date and still open",
                f"It closed on {f.closes_at.date().isoformat()}. Close it, or move the date.",
                None)
        elif days_left <= CLOSING_SOON_DAYS and short:
            add("closing_short", "warning",
                f"Closes in {max(0, round(days_left))} days with {f.hired} of "
                f"{f.target_hires} hired",
                "At the current pace the target may not be met by the closing date.",
                decisions)

    items.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 9))
    return items


def manual_steps(p: dict[str, Any], requisition_id: str) -> list[dict[str, Any]]:
    """What waits for HR on purpose — the human gates, with counts. Pure.

    Not problems: these are the steps D-05 keeps for a person. Listed so it is
    obvious nothing is stuck for want of automation.
    """
    decisions = f"/hr/requisitions/{requisition_id}/decisions"
    ready = int(p.get("ready_to_shortlist") or 0)
    threshold = p.get("shortlist_threshold")
    steps = [
        ("ready_to_shortlist", ready,
         f"Scored at or above the {threshold}/10 bar — confirm the shortlist to start round one",
         "/hr/applicants"),
        ("to_screen", int(p.get("not_started") or 0) - ready,
         "New applicants to screen and shortlist", "/hr/applicants"),
        ("awaiting_review", int(p.get("awaiting_review") or 0),
         "On a human review round — record the review", decisions),
        ("held", int(p.get("held") or 0),
         "Held below a round threshold — let them continue, or decide", decisions),
        ("finished", int(p.get("finished") or 0),
         "Finished every round — record the final decision", decisions),
    ]
    return [
        {"key": key, "count": count, "label": label, "link": link}
        for key, count, label, link in steps
        if count > 0
    ]


def stage_timing(
    rows: list[dict[str, Any]], rounds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Median days from application to each stage, in the order they happen. Pure.

    Only rounds of the live workflow are listed, in their order: the funnel
    adapts to the published workflow, and so does this.
    """
    by_key = {str(r["key"]): r for r in rows}

    def entry(key: str, label: str) -> dict[str, Any]:
        row = by_key.get(key)
        median = row["median_days"] if row else None
        return {
            "key": key,
            "label": label,
            "median_days": round(float(median), 1) if median is not None else None,
            "count": int(row["n"]) if row else 0,
        }

    out = [entry("shortlisted", "Shortlisted")]
    out += [entry(str(r["id"]), f"Reached {r['title']}") for r in rounds]
    out.append(entry("hired", "Hired"))
    return out


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------
def _f(v: Any) -> float | None:
    return round(float(v), 1) if v is not None else None


async def gather_dashboard(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    req: dict[str, Any],
    rounds: list[Any],
) -> dict[str, Any]:
    """Everything the dashboard shows beyond the funnel. Read-only."""
    params = {"r": requisition_id, "c": company_id}
    p = dict((await db.execute(text(_PROGRESS_SQL), params)).mappings().first() or {})
    wf = dict((await db.execute(text(_WORKFLOW_STATE_SQL), params)).mappings().first() or {})
    timing_rows = [dict(r) for r in (await db.execute(text(_TIMING_SQL), params)).mappings()]
    comp = dict((await db.execute(text(_COMPOSITE_SQL), params)).mappings().first() or {})
    links = dict(
        (await db.execute(
            text(_LINKS_SQL),
            {**params, "h": LINK_EXPIRY_WINDOW_HOURS, "lb": LAPSED_LOOKBACK_DAYS},
        )).mappings().first() or {}
    )
    held_rows = (
        await db.execute(text(_HELD_SQL), {**params, "lim": HELD_POOL_LIMIT})
    ).mappings().all()
    activity_rows = (
        await db.execute(text(_ACTIVITY_SQL), {**params, "lim": ACTIVITY_LIMIT})
    ).mappings().all()
    summary = dict(
        (await db.execute(text(_ACTIVITY_SUMMARY_SQL), params)).mappings().first() or {}
    )

    round_dicts = [dict(r) for r in rounds]
    low_pass = [
        (str(r["title"]), round(int(r["passed"]) / int(r["attempted"]) * 100),
         int(r["attempted"]))
        for r in round_dicts
        if int(r["attempted"] or 0) >= LOW_PASS_MIN_ATTEMPTS
        and int(r["passed"] or 0) / int(r["attempted"]) * 100 < LOW_PASS_RATE
    ]

    closes_at = req.get("closes_at")
    now = datetime.now(tz=UTC)
    facts = DashboardFacts(
        requisition_id=str(requisition_id),
        status=str(req.get("status")),
        # The shared gate, not the flag (PH3-B0). Reading the column alone told
        # HR an opening was accepting applications when its status was paused or
        # its closing date had passed — while the public surfaces, which applied
        # the whole predicate, had already stopped showing it.
        accepting_public=public_gate_open(
            status=req.get("status"),
            public_apply_enabled=req.get("public_apply_enabled"),
            approval_status=req.get("approval_status"),
            closes_at=closes_at if isinstance(closes_at, datetime) else None,
            now=now,
        ),
        has_published_workflow=bool(round_dicts),
        now=now,
        published_version=wf.get("published_version"),
        draft_version=wf.get("draft_version"),
        awaiting=int(p.get("awaiting") or 0),
        held=int(p.get("held") or 0),
        longest_wait_days=float(p.get("longest_wait_days") or 0),
        longest_hold_days=float(p.get("longest_hold_days") or 0),
        expiring_links=int(links.get("expiring") or 0),
        lapsed_links=int(links.get("lapsed") or 0),
        scoring_pending=int(p.get("scoring_pending") or 0),
        scoring_failed=int(p.get("scoring_failed") or 0),
        without_workflow=int(p.get("without_workflow") or 0),
        target_hires=req.get("target_hires"),
        hired=int(p.get("hired") or 0),
        closes_at=closes_at if isinstance(closes_at, datetime) else None,
        low_pass_rounds=low_pass,
    )

    # Stalled rounds, from the rule the nightly watcher sends (E6), so the
    # dashboard and the notification say the same thing about the same round.
    stalls = await gather_round_stalls(db, str(company_id), str(requisition_id))
    attention = attention_items(facts) + [
        {"key": f"round_stall:{f.dedupe_key}", "severity": f.severity, "title": f.title,
         "body": f.body, "link": None}
        for f in watch_round_stalls(WatcherInput(company_id=str(company_id), round_stalls=stalls))
    ]
    attention.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 9))

    return {
        "progress": {
            "applications": int(p.get("applications") or 0),
            "in_progress": int(p.get("in_progress") or 0),
            "awaiting_decision": facts.awaiting,
            "held": facts.held,
            "hired": facts.hired,
            "rejected": int(p.get("rejected") or 0),
            "not_started": int(p.get("not_started") or 0),
            "on_older_version": int(p.get("on_older_version") or 0),
            "target_hires": facts.target_hires,
        },
        "workflow_state": {
            "published_version": facts.published_version,
            "draft_version": facts.draft_version,
        },
        "stage_timing": stage_timing(timing_rows, round_dicts),
        "scores": {
            "avg_ats": _f(p.get("avg_ats")),
            "scored_applications": int(p.get("scored") or 0),
            "avg_composite": _f(comp.get("avg_composite")),
            "assessed_candidates": int(comp.get("assessed") or 0),
        },
        "held_pool": [
            {
                "enrolment_id": str(r["id"]),
                "applicant_id": str(r["applicant_id"]),
                "full_name": r["full_name"],
                "held_reason": r["held_reason"],
                "round_title": r["round_title"],
                "ats_overall": r["ats_overall"],
                "held_days": _f(r["held_days"]),
            }
            for r in held_rows
        ],
        "attention": attention,
        "manual_steps": manual_steps(p, str(requisition_id)),
        "activity": [
            {
                "occurred_at": r["occurred_at"].isoformat(),
                "automated": bool(r["automated"]),
                # A person is named; the system is not a person.
                "actor": None if r["automated"] else r["actor"],
                "candidate": r["candidate"],
                "enrolment_id": str(r["enrolment_id"]),
                "from_status": r["from_status"],
                "to_status": r["to_status"],
                "from_round": r["from_round"],
                "to_round": r["to_round"],
                "reason": r["reason"],
            }
            for r in activity_rows
        ],
        "activity_summary": {
            "automated_7d": int(summary.get("automated") or 0),
            "manual_7d": int(summary.get("manual") or 0),
            "last_automated_at": (
                summary["last_automated_at"].isoformat()
                if summary.get("last_automated_at") is not None
                else None
            ),
        },
    }
