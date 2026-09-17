"""Human interview scorecards — PH4-A1.

A ``human_review`` round used to record one thing: any HR manager's pass or
hold, with a note. This module adds what a structured interview needs — named
interviewers, one independent scorecard each, scored against the round's FROZEN
criteria — while leaving the progression decision exactly where it was.

WHAT A SCORECARD IS NOT
It does not advance, hold or reject anyone. Submitting every scorecard for a
candidate changes nothing about their status; it notifies the people who make
that call. ``post_round_review`` (pass/hold) and ``record_final_decision``
(hire/reject) are still the only writers of an outcome, and both are human-only.
That is the same rule ``PanelVerdict.decision_authority`` encodes for the AI:
evidence is gathered by many, decided by a person.

INDEPENDENCE
Interviewers never see each other's scorecards — there is no interviewer-facing
read of anybody else's. An HR manager can ALSO be assigned as an interviewer,
and HR can read submitted scorecards; so an HR reader who still owes their own
scorecard for a round sees the others' CONTENT hidden until they submit. Without
that, "interviewers submit independently" holds for interviewers and quietly
fails for the HR managers who interview.

WHERE THE GUARANTEES LIVE
The database enforces the ones that must never bend (migration d2f4a6c8e0b1):
scores only against frozen criteria, one live scorecard per interviewer, and a
submitted scorecard that cannot change except by supersession or DPDP
redaction. This module enforces the rest and explains all of them, so a refusal
reaches a person as a sentence rather than as a constraint name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.mailer import enqueue_email
from app.models import AuditLog
from app.notifications_util import create_notification
from app.requisitions import TERMINAL_STATUSES
from app.workflows import load_criteria

log = structlog.get_logger(__name__)

#: Company staff who may be assigned to interview. HR managers interview too.
INTERVIEWER_ROLES: tuple[str, ...] = ("interviewer", "hr_manager")

#: Round kinds a scorecard can hang off. PH4-A2's interview sessions extend this.
SCORABLE_ROUND_KINDS: frozenset[str] = frozenset({"human_review"})

EDITABLE: frozenset[str] = frozenset({"assigned", "in_progress"})
SCORE_MIN, SCORE_MAX = 1, 5
MAX_TEXT = 4000
MAX_INTERVIEWERS_PER_CALL = 10
CORRECTION_REASON_MIN, CORRECTION_REASON_MAX = 10, 1000


class ScorecardError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RequestMeta:
    """Who is asking, for the audit row. Both optional: a background caller has neither."""

    ip_address: str | None = None
    user_agent: str | None = None


def derived_state(status: str, due_at: datetime | None, now: datetime | None = None) -> str:
    """The state a person sees. ``late`` is derived, never stored.

    Stored, it would need a sweep to set it and would be wrong between sweeps;
    derived, it is correct at the instant it is read. A scorecard submitted after
    its due date is ``submitted`` — lateness is recorded on the audit row, not
    held against the evidence.
    """
    now = now or datetime.now(tz=UTC)
    if status in EDITABLE and due_at is not None and due_at < now:
        return "late"
    return status


def _audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    scorecard_id: uuid.UUID,
    details: dict[str, Any],
    meta: RequestMeta,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            actor_type="user",
            action=action,
            resource_type="interviewer_scorecard",
            resource_id=scorecard_id,
            details=details,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            event_ts=datetime.now(tz=UTC),
        )
    )


# ---------------------------------------------------------------------------
# Who can interview
# ---------------------------------------------------------------------------
async def list_assignable_interviewers(
    db: AsyncSession, *, company_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT ON (u.id) u.id, u.full_name, u.email, r.name AS role"
                "  FROM users u"
                "  JOIN user_roles ur ON ur.user_id = u.id"
                "  JOIN roles r ON r.id = ur.role_id AND r.name = ANY(:roles)"
                " WHERE u.company_id = :c AND u.deleted_at IS NULL AND u.is_active"
                # interviewer before hr_manager, so a person holding both is
                # labelled by the role they were created for.
                " ORDER BY u.id, (r.name = 'interviewer') DESC"
            ),
            {"c": company_id, "roles": list(INTERVIEWER_ROLES)},
        )
    ).mappings().all()
    people = [
        {"user_id": str(r["id"]), "full_name": r["full_name"] or r["email"],
         "email": r["email"], "role": r["role"]}
        for r in rows
    ]
    return sorted(people, key=lambda p: p["full_name"].lower())


# ---------------------------------------------------------------------------
# Assignment (HR)
# ---------------------------------------------------------------------------
async def assign(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    round_id: uuid.UUID,
    interviewer_user_ids: list[uuid.UUID],
    assigned_by: uuid.UUID,
    due_at: datetime | None,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Give each named interviewer an independent scorecard. Caller commits.

    Idempotent per interviewer: someone who already holds a live scorecard for
    this candidate and round is reported as ``already_assigned`` rather than
    refused, so re-sending the same panel is harmless.
    """
    ids = list(dict.fromkeys(interviewer_user_ids))
    if not ids:
        raise ScorecardError(422, "Choose at least one interviewer.")
    if len(ids) > MAX_INTERVIEWERS_PER_CALL:
        raise ScorecardError(
            422, f"Assign at most {MAX_INTERVIEWERS_PER_CALL} interviewers at once."
        )

    enrolment = (
        await db.execute(
            text(
                "SELECT e.id, e.workflow_id, e.status, e.requisition_id, a.full_name,"
                "       COALESCE(jr.title, e.target_job_title) AS job_title"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                "  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if enrolment is None:
        raise ScorecardError(404, "Application not found.")
    if enrolment["status"] in TERMINAL_STATUSES:
        raise ScorecardError(
            409, f"A final decision ({enrolment['status']}) is already recorded for this "
                 "application, so there is nothing left to interview for."
        )

    round_ = (
        await db.execute(
            text(
                "SELECT id, workflow_id, kind, title, deadline_days FROM workflow_rounds"
                " WHERE id = :r AND company_id = :c AND deleted_at IS NULL"
            ),
            {"r": round_id, "c": company_id},
        )
    ).mappings().first()
    if round_ is None:
        raise ScorecardError(404, "Round not found.")
    if enrolment["workflow_id"] is None or round_["workflow_id"] != enrolment["workflow_id"]:
        raise ScorecardError(
            409, f"'{round_['title']}' is not part of the workflow this candidate is on."
        )
    if round_["kind"] not in SCORABLE_ROUND_KINDS:
        raise ScorecardError(
            409, f"'{round_['title']}' is scored by the system, not by interviewers."
        )
    criteria = (await load_criteria(db, [round_id])).get(str(round_id), [])
    if not criteria:
        raise ScorecardError(
            409, f"'{round_['title']}' has no evaluation criteria to score against. "
                 "Add criteria to the round before assigning interviewers."
        )

    now = datetime.now(tz=UTC)
    if due_at is None:
        due_at = now + timedelta(days=int(round_["deadline_days"] or 7))
    elif due_at.tzinfo is None:
        raise ScorecardError(422, "The due date must include a timezone.")
    elif due_at <= now:
        raise ScorecardError(422, "The due date must be in the future.")

    eligible = {p["user_id"]: p for p in await list_assignable_interviewers(db, company_id=company_id)}
    unknown = [str(i) for i in ids if str(i) not in eligible]
    if unknown:
        raise ScorecardError(
            422, "Only active interviewers and HR managers in your company can be "
                 f"assigned ({len(unknown)} not eligible)."
        )

    created: list[dict[str, Any]] = []
    already: list[str] = []
    for interviewer_id in ids:
        card_id = uuid.uuid4()
        savepoint = await db.begin_nested()
        try:
            await db.execute(
                text(
                    "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
                    " interviewer_user_id, assigned_by_user_id, status, due_at, created_at,"
                    " updated_at) VALUES (:id, :c, :e, :r, :iv, :by, 'assigned', :due, :n, :n)"
                ),
                {"id": card_id, "c": company_id, "e": enrolment_id, "r": round_id,
                 "iv": interviewer_id, "by": assigned_by, "due": due_at, "n": now},
            )
            await savepoint.commit()
        except IntegrityError as exc:
            await savepoint.rollback()
            if "uq_interviewer_scorecards_live" not in str(exc.orig):
                raise
            already.append(str(interviewer_id))
            continue

        _audit(
            db, actor_id=assigned_by, action="scorecard.assigned", scorecard_id=card_id,
            details={"company_id": str(company_id), "enrolment_id": str(enrolment_id),
                     "round_id": str(round_id), "interviewer_user_id": str(interviewer_id),
                     "due_at": due_at.isoformat()},
            meta=meta,
        )
        person = eligible[str(interviewer_id)]
        link = f"/interviewer/scorecards/{card_id}"
        await create_notification(
            db, user_id=interviewer_id, kind="interview_assigned",
            title=f"You're interviewing {enrolment['full_name']}",
            body=f"{round_['title']} · {enrolment['job_title']} · due {due_at:%d %b %Y}",
            link=link,
        )
        await enqueue_email(
            db, to=person["email"], template="generic", lang="en",
            ctx={
                "name": person["full_name"],
                "title": f"You're interviewing {enrolment['full_name']}",
                "body": (
                    f"You have been assigned to {round_['title']} for the "
                    f"{enrolment['job_title']} opening. Your scorecard is due by "
                    f"{due_at:%d %b %Y}. The interview kit and scorecard are ready "
                    "when you sign in."
                ),
                "cta_label": "Open the scorecard",
                "cta_url": f"{settings.app_base_url.rstrip('/')}{link}",
            },
            to_user_id=interviewer_id, company_id=company_id,
            related_kind="interview_assigned", related_id=card_id,
        )
        created.append({"scorecard_id": str(card_id), "interviewer_user_id": str(interviewer_id)})

    log.info(
        "scorecard.assigned", enrolment_id=str(enrolment_id), round_id=str(round_id),
        created=len(created), already_assigned=len(already),
    )
    return {"created": created, "already_assigned": already, "due_at": due_at.isoformat()}


async def withdraw(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    scorecard_id: uuid.UUID,
    actor: uuid.UUID,
    reason: str | None,
    meta: RequestMeta,
) -> None:
    """Take an assignment back before it is submitted. Caller commits.

    A submitted scorecard is evidence and cannot be withdrawn — the database
    refuses it as well; this says so in words first.
    """
    row = (
        await db.execute(
            text(
                "SELECT id, status, superseded_at, interviewer_user_id, enrolment_id"
                "  FROM interviewer_scorecards"
                " WHERE id = :s AND company_id = :c FOR UPDATE"
            ),
            {"s": scorecard_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise ScorecardError(404, "Scorecard not found.")
    if row["status"] not in EDITABLE or row["superseded_at"] is not None:
        raise ScorecardError(
            409, "Only an assignment that has not been submitted can be withdrawn."
        )
    why = (reason or "").strip()[:1000] or None
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET status = 'withdrawn', withdrawn_at = now(),"
            " withdrawn_reason = :why, updated_at = now() WHERE id = :s"
        ),
        {"s": scorecard_id, "why": why},
    )
    _audit(
        db, actor_id=actor, action="scorecard.withdrawn", scorecard_id=scorecard_id,
        details={"company_id": str(company_id), "enrolment_id": str(row["enrolment_id"]),
                 "interviewer_user_id": str(row["interviewer_user_id"]), "reason": why},
        meta=meta,
    )
    await create_notification(
        db, user_id=row["interviewer_user_id"], kind="interview_unassigned",
        title="An interview assignment was withdrawn",
        body=why, link="/interviewer",
    )


# ---------------------------------------------------------------------------
# The interviewer's side
# ---------------------------------------------------------------------------
_OWNED_SQL = """
SELECT s.id, s.company_id, s.enrolment_id, s.round_id, s.interviewer_user_id,
       s.assigned_by_user_id, s.status, s.due_at, s.summary, s.started_at,
       s.submitted_at, s.corrects_id, s.correction_reason, s.superseded_at,
       s.superseded_by_id, s.redacted_at, s.created_at,
       e.status AS enrolment_status, e.requisition_id,
       a.full_name AS candidate_name,
       COALESCE(jr.title, e.target_job_title) AS job_title,
       jr.owner_user_id AS requisition_owner,
       wr.title AS round_title, wr.kind AS round_kind
  FROM interviewer_scorecards s
  JOIN enrolments e ON e.id = s.enrolment_id AND e.deleted_at IS NULL
  JOIN applicants a ON a.id = e.applicant_id
  JOIN workflow_rounds wr ON wr.id = s.round_id
  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id
 WHERE s.id = :s AND s.company_id = :c AND s.interviewer_user_id = :iv
"""


async def _load_owned(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
    for_update: bool = False,
) -> dict[str, Any]:
    """The caller's own scorecard, or 404.

    404 rather than 403 for someone else's: an interviewer probing ids should not
    learn which ones exist. Ownership is in the WHERE clause, so there is no
    code path that loads a scorecard and then forgets to check whose it is.
    """
    sql = _OWNED_SQL + (" FOR UPDATE OF s" if for_update else "")
    row = (
        await db.execute(
            text(sql), {"s": scorecard_id, "c": company_id, "iv": interviewer_user_id}
        )
    ).mappings().first()
    if row is None:
        raise ScorecardError(404, "Scorecard not found.")
    return dict(row)


async def list_for_interviewer(
    db: AsyncSession, *, interviewer_user_id: uuid.UUID, company_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT s.id, s.status, s.due_at, s.submitted_at, s.created_at, s.corrects_id,"
                "       a.full_name AS candidate_name,"
                "       COALESCE(jr.title, e.target_job_title) AS job_title,"
                "       wr.title AS round_title"
                "  FROM interviewer_scorecards s"
                "  JOIN enrolments e ON e.id = s.enrolment_id AND e.deleted_at IS NULL"
                "  JOIN applicants a ON a.id = e.applicant_id"
                "  JOIN workflow_rounds wr ON wr.id = s.round_id"
                "  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id"
                " WHERE s.interviewer_user_id = :iv AND s.company_id = :c"
                "   AND s.superseded_at IS NULL AND s.status <> 'withdrawn'"
            ),
            {"iv": interviewer_user_id, "c": company_id},
        )
    ).mappings().all()
    now = datetime.now(tz=UTC)
    out = [
        {
            "scorecard_id": str(r["id"]),
            "state": derived_state(r["status"], r["due_at"], now),
            "status": r["status"],
            "due_at": r["due_at"].isoformat() if r["due_at"] else None,
            "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
            "candidate_name": r["candidate_name"],
            "job_title": r["job_title"],
            "round_title": r["round_title"],
            "is_correction": r["corrects_id"] is not None,
        }
        for r in rows
    ]
    # What needs doing first: late, then due soonest, then everything submitted.
    order = {"late": 0, "assigned": 1, "in_progress": 1, "submitted": 2}
    return sorted(
        out, key=lambda c: (order.get(c["state"], 3), c["due_at"] or "9999")
    )


async def _scores_for(db: AsyncSession, scorecard_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT competency_id, score, not_assessed, evidence"
                "  FROM interviewer_scorecard_scores WHERE scorecard_id = :s"
            ),
            {"s": scorecard_id},
        )
    ).mappings().all()
    return {
        r["competency_id"]: {
            "score": r["score"], "not_assessed": r["not_assessed"], "evidence": r["evidence"],
        }
        for r in rows
    }


async def get_for_interviewer(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
) -> dict[str, Any]:
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id,
    )
    criteria = (await load_criteria(db, [card["round_id"]])).get(str(card["round_id"]), [])
    scores = await _scores_for(db, scorecard_id)
    decided = card["enrolment_status"] in TERMINAL_STATUSES
    live = card["superseded_at"] is None
    return {
        "scorecard_id": str(card["id"]),
        "state": derived_state(card["status"], card["due_at"]),
        "status": card["status"],
        "candidate_name": card["candidate_name"],
        "job_title": card["job_title"],
        "round_title": card["round_title"],
        "due_at": card["due_at"].isoformat() if card["due_at"] else None,
        "submitted_at": card["submitted_at"].isoformat() if card["submitted_at"] else None,
        "summary": card["summary"],
        "correction_reason": card["correction_reason"],
        "is_correction": card["corrects_id"] is not None,
        "superseded": not live,
        "can_edit": card["status"] in EDITABLE and live and not decided,
        "can_correct": card["status"] == "submitted" and live and not decided,
        "criteria": [
            {
                "competency_id": c["competency_id"],
                "competency_name": c["competency_name"],
                "weight": c["weight"],
                "anchors": c.get("anchors"),
                **scores.get(c["competency_id"], {
                    "score": None, "not_assessed": False, "evidence": None,
                }),
            }
            for c in criteria
        ],
    }


def _validate_payload(
    criteria_ids: set[str], scores: list[dict[str, Any]], summary: str | None
) -> None:
    seen: set[str] = set()
    for item in scores:
        cid = str(item.get("competency_id") or "")
        if cid not in criteria_ids:
            # Named in words before the foreign key refuses it by constraint name.
            raise ScorecardError(
                422, "A score refers to a criterion this round does not assess."
            )
        if cid in seen:
            raise ScorecardError(422, "Each criterion can be scored once.")
        seen.add(cid)
        score = item.get("score")
        not_assessed = bool(item.get("not_assessed"))
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, int):
                raise ScorecardError(422, "Scores are whole numbers from 1 to 5.")
            if not SCORE_MIN <= score <= SCORE_MAX:
                raise ScorecardError(422, "Scores are whole numbers from 1 to 5.")
            if not_assessed:
                raise ScorecardError(
                    422, "A criterion is either scored or marked not assessed, not both."
                )
        evidence = item.get("evidence")
        if evidence is not None and len(str(evidence)) > MAX_TEXT:
            raise ScorecardError(422, f"Evidence is limited to {MAX_TEXT} characters.")
    if summary is not None and len(summary) > MAX_TEXT:
        raise ScorecardError(422, f"The summary is limited to {MAX_TEXT} characters.")


async def save_draft(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
    scores: list[dict[str, Any]],
    summary: str | None,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Store work in progress. Caller commits."""
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id, for_update=True,
    )
    if card["superseded_at"] is not None:
        raise ScorecardError(409, "This scorecard was corrected; open the latest version.")
    if card["status"] not in EDITABLE:
        raise ScorecardError(
            409, "A submitted scorecard cannot be edited. Open a correction if something "
                 "needs to change — the original stays on record."
        )
    if card["enrolment_status"] in TERMINAL_STATUSES:
        raise ScorecardError(
            409, "A final decision has been recorded for this candidate, so the scorecard "
                 "is locked."
        )

    criteria = (await load_criteria(db, [card["round_id"]])).get(str(card["round_id"]), [])
    _validate_payload({c["competency_id"] for c in criteria}, scores, summary)

    for item in scores:
        evidence = item.get("evidence")
        evidence = str(evidence).strip() or None if evidence is not None else None
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
                " competency_id, score, not_assessed, evidence, updated_at)"
                " VALUES (:s, :c, :r, :cid, :score, :na, :ev, now())"
                " ON CONFLICT (scorecard_id, competency_id) DO UPDATE SET"
                "   score = EXCLUDED.score, not_assessed = EXCLUDED.not_assessed,"
                "   evidence = EXCLUDED.evidence, updated_at = now()"
            ),
            {"s": scorecard_id, "c": company_id, "r": card["round_id"],
             "cid": item["competency_id"], "score": item.get("score"),
             "na": bool(item.get("not_assessed")), "ev": evidence},
        )

    first_save = card["status"] == "assigned"
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET summary = :sum, updated_at = now(),"
            " status = CASE WHEN status = 'assigned' THEN 'in_progress' ELSE status END,"
            " started_at = COALESCE(started_at, now())"
            " WHERE id = :s"
        ),
        {"s": scorecard_id, "sum": (summary or "").strip() or None},
    )
    if first_save:
        _audit(
            db, actor_id=interviewer_user_id, action="scorecard.started",
            scorecard_id=scorecard_id,
            details={"company_id": str(company_id), "enrolment_id": str(card["enrolment_id"])},
            meta=meta,
        )
    return {"scorecard_id": str(scorecard_id), "status": "in_progress"}


async def submit(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
    scores: list[dict[str, Any]],
    summary: str | None,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Save, check completeness, and submit. Caller commits.

    Complete means every criterion is either scored or explicitly marked not
    assessed, and at least one is scored. "Not assessed" is a real answer — an
    interviewer who ran out of time on a competency should say so rather than
    invent a number, and a blank should never be mistaken for either.
    """
    await save_draft(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id, scores=scores, summary=summary, meta=meta,
    )
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id, for_update=True,
    )
    criteria = (await load_criteria(db, [card["round_id"]])).get(str(card["round_id"]), [])
    stored = await _scores_for(db, scorecard_id)
    missing = [
        c["competency_name"] for c in criteria
        if not (
            stored.get(c["competency_id"], {}).get("score") is not None
            or stored.get(c["competency_id"], {}).get("not_assessed")
        )
    ]
    if missing:
        raise ScorecardError(
            422, "Score or mark as not assessed: " + ", ".join(missing) + "."
        )
    if not any(v.get("score") is not None for v in stored.values()):
        raise ScorecardError(422, "Score at least one criterion before submitting.")

    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET status = 'submitted', submitted_at = :n,"
            " updated_at = :n WHERE id = :s"
        ),
        {"s": scorecard_id, "n": now},
    )
    late = card["due_at"] is not None and now > card["due_at"]
    _audit(
        db, actor_id=interviewer_user_id, action="scorecard.submitted",
        scorecard_id=scorecard_id,
        details={"company_id": str(company_id), "enrolment_id": str(card["enrolment_id"]),
                 "round_id": str(card["round_id"]), "late": late,
                 "scored": sum(1 for v in stored.values() if v.get("score") is not None),
                 "not_assessed": sum(1 for v in stored.values() if v.get("not_assessed")),
                 "is_correction": card["corrects_id"] is not None},
        meta=meta,
    )
    await _notify_hr(db, card=card, title=f"Scorecard submitted for {card['candidate_name']}")

    # "Everyone is in" is the moment HR actually acts on, so it is said once.
    outstanding = await db.scalar(
        text(
            "SELECT count(*) FROM interviewer_scorecards"
            " WHERE enrolment_id = :e AND round_id = :r AND superseded_at IS NULL"
            "   AND status IN ('assigned', 'in_progress')"
        ),
        {"e": card["enrolment_id"], "r": card["round_id"]},
    )
    if not outstanding:
        await _notify_hr(
            db, card=card,
            title=f"All scorecards are in for {card['candidate_name']}",
            dedupe=f"scorecards-complete:{card['enrolment_id']}:{card['round_id']}:{scorecard_id}",
        )
    log.info("scorecard.submitted", scorecard_id=str(scorecard_id), late=late)
    return {"scorecard_id": str(scorecard_id), "status": "submitted",
            "submitted_at": now.isoformat(), "late": late}


async def open_correction(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
    reason: str,
    meta: RequestMeta,
) -> dict[str, Any]:
    """The explicit, audited way to change submitted evidence. Caller commits.

    Nothing is overwritten. The submitted scorecard is marked superseded by a new
    draft that starts as a copy of it; the interviewer edits and resubmits that.
    Both versions stay readable to HR, with the reason attached.

    Refused once a final decision is recorded: the evidence a decision rested on
    must stay the evidence it rested on.
    """
    why = (reason or "").strip()
    if not CORRECTION_REASON_MIN <= len(why) <= CORRECTION_REASON_MAX:
        raise ScorecardError(
            422, f"Say why the scorecard needs correcting "
                 f"({CORRECTION_REASON_MIN}-{CORRECTION_REASON_MAX} characters)."
        )
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id, for_update=True,
    )
    if card["status"] != "submitted" or card["superseded_at"] is not None:
        raise ScorecardError(409, "Only the latest submitted scorecard can be corrected.")
    if card["enrolment_status"] in TERMINAL_STATUSES:
        raise ScorecardError(
            409, "A final decision has been recorded for this candidate, so the evidence it "
                 "rested on is locked."
        )

    new_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    # Supersede FIRST, then insert: the live unique index refuses the new row
    # while the old one is live. The superseded_by foreign key is DEFERRED for
    # exactly this reason and is checked at commit, when both rows exist.
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET superseded_at = :n, superseded_by_id = :new"
            " WHERE id = :old"
        ),
        {"n": now, "new": new_id, "old": scorecard_id},
    )
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, assigned_by_user_id, status, due_at, summary, started_at,"
            " corrects_id, correction_reason, created_at, updated_at)"
            " SELECT :new, company_id, enrolment_id, round_id, interviewer_user_id,"
            "        assigned_by_user_id, 'in_progress', due_at, summary, :n, id, :why, :n, :n"
            "   FROM interviewer_scorecards WHERE id = :old"
        ),
        {"new": new_id, "old": scorecard_id, "why": why, "n": now},
    )
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
            " competency_id, score, not_assessed, evidence, updated_at)"
            " SELECT :new, company_id, round_id, competency_id, score, not_assessed,"
            "        evidence, :n"
            "   FROM interviewer_scorecard_scores WHERE scorecard_id = :old"
        ),
        {"new": new_id, "old": scorecard_id, "n": now},
    )
    _audit(
        db, actor_id=interviewer_user_id, action="scorecard.correction_opened",
        scorecard_id=new_id,
        details={"company_id": str(company_id), "enrolment_id": str(card["enrolment_id"]),
                 "corrects": str(scorecard_id), "reason": why},
        meta=meta,
    )
    await _notify_hr(
        db, card=card, title=f"A scorecard for {card['candidate_name']} is being corrected",
        body=why,
    )
    log.info("scorecard.correction_opened", corrects=str(scorecard_id), new=str(new_id))
    return {"scorecard_id": str(new_id), "corrects": str(scorecard_id)}


async def _notify_hr(
    db: AsyncSession,
    *,
    card: dict[str, Any],
    title: str,
    body: str | None = None,
    dedupe: str | None = None,
) -> None:
    """Tell the people who can act: whoever assigned it, and the opening's owner."""
    recipients = {
        uid for uid in (card.get("assigned_by_user_id"), card.get("requisition_owner"))
        if uid is not None
    }
    for uid in recipients:
        await create_notification(
            db, user_id=uid, kind="scorecard_update", title=title,
            body=body or f"{card['round_title']} · {card['job_title']}",
            link=f"/hr/requisitions/{card['requisition_id']}/decisions"
            if card.get("requisition_id") else "/hr",
            dedupe_key=f"{dedupe}:{uid}" if dedupe else None,
        )


# ---------------------------------------------------------------------------
# The HR evidence view
# ---------------------------------------------------------------------------
async def scorecards_for_enrolment(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
) -> dict[str, Any]:
    """Every scorecard for an application, grouped by round, history included.

    Only SUBMITTED content is shown. A draft is an interviewer's unfinished
    thinking; showing it to HR would both mislead and invite anchoring.

    A viewer who owes their own scorecard for a round sees the other
    interviewers' content for that round hidden until they submit — see the
    module docstring on independence.
    """
    exists = await db.scalar(
        text("SELECT 1 FROM enrolments WHERE id = :e AND company_id = :c AND deleted_at IS NULL"),
        {"e": enrolment_id, "c": company_id},
    )
    if not exists:
        raise ScorecardError(404, "Application not found.")

    rows = (
        await db.execute(
            text(
                "SELECT s.id, s.round_id, s.interviewer_user_id, s.status, s.due_at, s.summary,"
                "       s.submitted_at, s.withdrawn_at, s.withdrawn_reason, s.corrects_id,"
                "       s.correction_reason, s.superseded_at, s.redacted_at, s.created_at,"
                "       u.full_name AS interviewer_name, u.email AS interviewer_email,"
                "       wr.title AS round_title, wr.position"
                "  FROM interviewer_scorecards s"
                "  JOIN users u ON u.id = s.interviewer_user_id"
                "  JOIN workflow_rounds wr ON wr.id = s.round_id"
                " WHERE s.enrolment_id = :e AND s.company_id = :c"
                " ORDER BY wr.position, s.created_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    if not rows:
        return {"rounds": []}

    round_ids = list({r["round_id"] for r in rows})
    criteria = await load_criteria(db, round_ids)
    submitted_ids = [r["id"] for r in rows if r["status"] == "submitted"]
    score_rows = (
        await db.execute(
            text(
                "SELECT scorecard_id, competency_id, score, not_assessed, evidence"
                "  FROM interviewer_scorecard_scores WHERE scorecard_id = ANY(:ids)"
            ),
            {"ids": submitted_ids},
        )
    ).mappings().all() if submitted_ids else []
    scores: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
    for s in score_rows:
        scores.setdefault(s["scorecard_id"], {})[s["competency_id"]] = {
            "score": s["score"], "not_assessed": s["not_assessed"], "evidence": s["evidence"],
        }

    # Rounds where THIS viewer still owes a scorecard.
    blinded = {
        r["round_id"] for r in rows
        if r["interviewer_user_id"] == viewer_user_id
        and r["status"] in EDITABLE and r["superseded_at"] is None
    }

    now = datetime.now(tz=UTC)
    by_round: dict[uuid.UUID, dict[str, Any]] = {}
    for r in rows:
        bucket = by_round.setdefault(r["round_id"], {
            "round_id": str(r["round_id"]),
            "round_title": r["round_title"],
            "position": r["position"],
            "criteria": [
                {"competency_id": c["competency_id"], "competency_name": c["competency_name"],
                 "weight": c["weight"]}
                for c in criteria.get(str(r["round_id"]), [])
            ],
            "hidden_until_you_submit": r["round_id"] in blinded,
            "scorecards": [],
        })
        entry: dict[str, Any] = {
            "scorecard_id": str(r["id"]),
            "interviewer_user_id": str(r["interviewer_user_id"]),
            "interviewer_name": r["interviewer_name"] or r["interviewer_email"],
            "state": derived_state(r["status"], r["due_at"], now),
            "due_at": r["due_at"].isoformat() if r["due_at"] else None,
            "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
            "superseded": r["superseded_at"] is not None,
            "is_correction": r["corrects_id"] is not None,
            "correction_reason": r["correction_reason"],
            "withdrawn_reason": r["withdrawn_reason"],
            "redacted": r["redacted_at"] is not None,
            "summary": None,
            "scores": None,
        }
        own = r["interviewer_user_id"] == viewer_user_id
        if r["status"] == "submitted" and (own or r["round_id"] not in blinded):
            entry["summary"] = r["summary"]
            entry["scores"] = scores.get(r["id"], {})
        bucket["scorecards"].append(entry)

    return {"rounds": sorted(by_round.values(), key=lambda b: b["position"])}


async def summary_for_enrolments(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_ids: list[uuid.UUID]
) -> dict[str, dict[str, int]]:
    """Counts for list views — how many assigned, submitted, late."""
    if not enrolment_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT enrolment_id,"
                "       count(*) AS assigned,"
                "       count(*) FILTER (WHERE status = 'submitted') AS submitted,"
                "       count(*) FILTER (WHERE status IN ('assigned', 'in_progress')"
                "                          AND due_at < now()) AS late"
                "  FROM interviewer_scorecards"
                " WHERE company_id = :c AND enrolment_id = ANY(:ids)"
                "   AND superseded_at IS NULL AND status <> 'withdrawn'"
                " GROUP BY enrolment_id"
            ),
            {"c": company_id, "ids": enrolment_ids},
        )
    ).mappings().all()
    return {
        str(r["enrolment_id"]): {
            "assigned": int(r["assigned"]), "submitted": int(r["submitted"]),
            "late": int(r["late"]),
        }
        for r in rows
    }
