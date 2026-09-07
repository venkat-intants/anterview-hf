"""The workflow runner — Group C, features C6, C7 and C9.

One component advances every enrolled candidate. It is **event-driven, not
polled**: it reacts to three things happening and consults the enrolment's own
workflow version to decide what comes next.

    application received  ->  enrol, score, wait for the human shortlist gate
    round submitted       ->  record the result, then advance or hold
    interview scored      ->  the same, via the same path

Why the enrolment's version and not the requisition's
-----------------------------------------------------
``enrolments.workflow_id`` pins the version a candidate started on. Reading the
*currently published* workflow instead would mean a mid-cohort publish silently
re-graded people against thresholds they never agreed to sit.

The rule this module exists to enforce
--------------------------------------
**Nothing here can end a candidacy.** A threshold decides *advancement*; a
candidate below it becomes ``held`` — neutral, non-terminal, and surfaced in the
final decision queue alongside everyone who advanced. There is no code path,
setting or return value in this file that produces ``rejected``. That is D-05,
and it is enforced by the absence of the capability rather than by a default
that could be flipped.

Idempotency
-----------
Every entry point is safe to call twice with the same event. Grading writes
through a partial unique index on ``(enrolment_id, round_id) WHERE
superseded_at IS NULL``; advancement is a no-op when the enrolment has already
moved past the round in question. Duplicate webhooks, retried jobs and a
double-clicked submit all collapse to one advancement.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from shared.intelligence import (
    FrozenRubricError,
    composite_from_criteria,
    profile_from_round_criteria,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exam_link import hash_exam_token, mint_exam_token
from app.mailer import enqueue_email
from app.notifications_util import create_notification
from app.requisitions import record_transition
from app.workflows import AI_GRADED_KINDS, EXAM_BACKED_KINDS, load_criteria, published_workflow

log = structlog.get_logger(__name__)

# ``pass_threshold`` is ALWAYS a percentage, 0-100, for every round kind. Scores
# arrive on their round's natural scale, so they are converted before comparison.
# This used to be ambiguous — the column was documented as "percent for mcq and
# coding, 0-10 composite for ai_interview" — and a 7.5 composite was read as
# 7.5%, holding a candidate who had comfortably passed. One unit, converted at
# the edge, is the only version of this that cannot be misread.
INTERVIEW_SCORE_MAX = 10.0


@dataclass
class RunnerOutcome:
    """What the runner did, for logging, tests and the ops surface."""

    action: str  # enrolled | advanced | held | completed | noop
    enrolment_id: str | None = None
    from_round: str | None = None
    to_round: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "enrolment_id": self.enrolment_id,
            "from_round": self.from_round,
            "to_round": self.to_round,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# C5 — enrolment
# ---------------------------------------------------------------------------
async def enrol_applicant(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    requisition_id: uuid.UUID,
    target_job_title: str,
    target_level: str = "mid",
    target_jd_text: str | None = None,
) -> RunnerOutcome:
    """Place an applicant into the requisition's published workflow. Caller commits.

    Idempotent by the partial unique index on (requisition_id, applicant_id):
    a second application to the same opening returns the existing enrolment
    rather than creating a duplicate.

    The candidate does NOT get a round here. The first round is assigned after
    the human shortlist gate, which is the whole point of the gate.
    """
    existing = await db.scalar(
        text(
            "SELECT id FROM enrolments"
            " WHERE requisition_id = :r AND applicant_id = :a AND deleted_at IS NULL"
        ),
        {"r": requisition_id, "a": applicant_id},
    )
    if existing is not None:
        return RunnerOutcome(action="noop", enrolment_id=str(existing),
                             reason="already enrolled in this opening")

    wf = await published_workflow(db, requisition_id)
    now = datetime.now(tz=UTC)
    enrolment_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
            " target_job_title, target_level, target_jd_text, workflow_id, created_at, updated_at)"
            " VALUES (:i,:c,:r,:a,'new',:tt,:tl,:jd,:w,:n,:n)"
        ),
        {"i": enrolment_id, "c": company_id, "r": requisition_id, "a": applicant_id,
         "tt": target_job_title, "tl": target_level, "jd": target_jd_text,
         # NULL when nothing is published yet: the candidate is still a real
         # applicant and must not be lost. They join a workflow when one goes
         # live, rather than being rejected for arriving early.
         "w": wf["id"] if wf else None, "n": now},
    )
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at)"
            " VALUES (:c,:e,NULL,'new',NULL,true,'enrolled on application',:n)"
        ),
        {"c": company_id, "e": enrolment_id, "n": now},
    )
    log.info(
        "runner.enrolled",
        enrolment_id=str(enrolment_id), requisition_id=str(requisition_id),
        workflow_id=str(wf["id"]) if wf else None,
    )
    return RunnerOutcome(action="enrolled", enrolment_id=str(enrolment_id),
                         reason=None if wf else "no published workflow yet")


# ---------------------------------------------------------------------------
# Lookups the routers need to reach the runner
#
# The runner is enrolment-shaped; the two things that call it are not. An exam
# submit knows an applicant and an exam round, and the applicant board knows a
# person. These translate, and they live here rather than in the routers so the
# SQL that decides "which enrolment does this event belong to?" sits beside the
# SQL that acts on it.
# ---------------------------------------------------------------------------
async def enrolment_awaiting_exam_round(
    db: AsyncSession, *, applicant_id: uuid.UUID, exam_round_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """The enrolment currently sitting on the workflow round this exam backs.

    Returns ``(enrolment_id, workflow_round_id)``, or None when this exam round
    is not part of any workflow the applicant is running — which is the normal
    case for an exam HR assigned by hand, and must stay a no-op rather than an
    error.

    Matched through ``current_round_id`` rather than by workflow membership
    alone, for two reasons. A workflow can reuse the same exam round in more
    than one place, and an applicant can hold several enrolments; between them,
    "which round is this result for?" has no answer unless it is the round the
    candidate was actually sent to.
    """
    row = (
        await db.execute(
            text(
                "SELECT e.id AS enrolment_id, wr.id AS round_id"
                "  FROM enrolments e"
                "  JOIN workflow_rounds wr ON wr.id = e.current_round_id"
                "                         AND wr.deleted_at IS NULL"
                " WHERE e.applicant_id = :a"
                "   AND wr.exam_round_id = :er"
                "   AND e.deleted_at IS NULL"
                " LIMIT 1"
            ),
            {"a": applicant_id, "er": exam_round_id},
        )
    ).first()
    if row is None:
        return None
    return uuid.UUID(str(row.enrolment_id)), uuid.UUID(str(row.round_id))


async def sole_live_enrolment(
    db: AsyncSession, *, applicant_id: uuid.UUID, company_id: uuid.UUID
) -> uuid.UUID | None:
    """This applicant's one open enrolment, or None when it is not one.

    None covers both "no applications" and "more than one", deliberately.
    Shortlisting from the applicant board says something about the person, not
    about which opening they should start — and starting rounds on every
    application somebody has because a recruiter shortlisted them once would
    send exam links for jobs nobody decided on. When it is ambiguous the
    enrolment-level action is the one that can answer it.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id FROM enrolments"
                " WHERE applicant_id = :a AND company_id = :c"
                "   AND deleted_at IS NULL AND status NOT IN ('hired','rejected')"
                " LIMIT 2"
            ),
            {"a": applicant_id, "c": company_id},
        )
    ).all()
    if len(rows) != 1:
        return None
    return uuid.UUID(str(rows[0].id))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _load_enrolment(db: AsyncSession, enrolment_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT e.id, e.company_id, e.applicant_id, e.requisition_id, e.status,"
                "       e.workflow_id, e.current_round_id, e.target_job_title,"
                "       a.full_name, a.email"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.id = :i AND e.deleted_at IS NULL"
            ),
            {"i": enrolment_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _load_workflow(db: AsyncSession, workflow_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text("SELECT * FROM workflows WHERE id = :i AND deleted_at IS NULL"),
            {"i": workflow_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _load_round(db: AsyncSession, round_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT id, workflow_id, position, title, kind, pass_threshold,"
                "       deadline_days, on_pass_next_round_id, exam_round_id"
                "  FROM workflow_rounds WHERE id = :i AND deleted_at IS NULL"
            ),
            {"i": round_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _first_round(db: AsyncSession, workflow_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT id, workflow_id, position, title, kind, pass_threshold,"
                "       deadline_days, on_pass_next_round_id, exam_round_id"
                "  FROM workflow_rounds WHERE workflow_id = :w AND deleted_at IS NULL"
                " ORDER BY position LIMIT 1"
            ),
            {"w": workflow_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _hold(
    db: AsyncSession, enrolment: dict[str, Any], reason: str, actor: uuid.UUID | None = None
) -> RunnerOutcome:
    """Stop a candidate's progression without ending their candidacy.

    Held is neutral. The candidate keeps every score they earned, stops
    consuming further rounds, and appears in the final decision queue beside
    those who advanced. Only a person may move them out of it, in either
    direction (D-05).
    """
    now = datetime.now(tz=UTC)
    await record_transition(
        db,
        enrolment_id=enrolment["id"],
        company_id=enrolment["company_id"],
        to_status="held",
        actor_user_id=actor,
        automated=actor is None,
        reason=reason,
    )
    await db.execute(
        text(
            "UPDATE enrolments SET held_at = :n, held_reason = :r, updated_at = :n"
            " WHERE id = :i"
        ),
        {"n": now, "r": reason, "i": enrolment["id"]},
    )
    log.info("runner.held", enrolment_id=str(enrolment["id"]), reason=reason)
    return RunnerOutcome(action="held", enrolment_id=str(enrolment["id"]), reason=reason)


async def _assign_round(
    db: AsyncSession,
    *,
    enrolment: dict[str, Any],
    round_: dict[str, Any],
    workflow: dict[str, Any],
) -> None:
    """Materialise whatever a candidate needs in order to sit this round.

    Exam-backed rounds mint a single-use magic link and email it, mirroring
    ``hr_exams.assign_exam``. AI interview rounds delegate to the existing
    ``advance_applicant_to_interview``, which already resolves the job, mints the
    invite, emails the candidate and is itself idempotent. Human-review rounds
    need no artefact — they surface in the reviewer's queue by virtue of the
    enrolment sitting on them.
    """
    kind = round_["kind"]
    company_id = enrolment["company_id"]
    now = datetime.now(tz=UTC)

    if kind in EXAM_BACKED_KINDS:
        if not round_["exam_round_id"]:
            log.warning(
                "runner.assign.no_exam_round",
                round_id=str(round_["id"]), kind=kind,
            )
            return
        exam_id = await db.scalar(
            text("SELECT exam_id FROM exam_rounds WHERE id = :r"),
            {"r": round_["exam_round_id"]},
        )
        if exam_id is None:
            log.warning("runner.assign.exam_missing", round_id=str(round_["id"]))
            return
        # One live link per (round, applicant): rotate any prior active one so
        # the candidate is never holding two working links for the same round.
        await db.execute(
            text(
                "UPDATE exam_assignments SET status = 'revoked', updated_at = :n"
                " WHERE round_id = :r AND applicant_id = :a AND status = 'invited'"
                "   AND deleted_at IS NULL"
            ),
            {"n": now, "r": round_["exam_round_id"], "a": enrolment["applicant_id"]},
        )
        raw = mint_exam_token()
        asn_id = uuid.uuid4()
        expires = now + timedelta(days=int(round_["deadline_days"] or 7))
        await db.execute(
            text(
                "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
                " enrolment_id, token_hash, expires_at, status, created_at, updated_at)"
                " VALUES (:i,:c,:e,:r,:a,:en,:th,:x,'invited',:n,:n)"
            ),
            {"i": asn_id, "c": company_id, "e": exam_id, "r": round_["exam_round_id"],
             "a": enrolment["applicant_id"], "en": enrolment["id"],
             "th": hash_exam_token(raw, settings.exam_link_secret), "x": expires, "n": now},
        )
        base = settings.exam_link_base_url.rstrip("/")
        await enqueue_email(
            db,
            to=enrolment["email"],
            template="exam_link",
            lang="en",
            ctx={
                "name": enrolment["full_name"],
                "exam_title": round_["title"],
                "exam_url": f"{base}/exam#{raw}",
                "expires": expires.strftime("%d %b %Y, %H:%M UTC"),
            },
            company_id=company_id,
            related_kind="exam_assignment",
            related_id=asn_id,
            # One invitation per assignment, so a retried event cannot email the
            # candidate twice for the same round.
            dedupe_key=f"workflow_assign:{asn_id}",
        )
        log.info("runner.assigned.exam", enrolment_id=str(enrolment["id"]),
                 round_id=str(round_["id"]))

    elif kind in AI_GRADED_KINDS:
        # Local import: hr_interviews imports app.models and its own router
        # dependencies, and importing it at module scope would make the runner
        # depend on FastAPI route registration.
        from app.models import Applicant  # noqa: PLC0415
        from app.routers.hr_interviews import (  # noqa: PLC0415
            advance_applicant_to_interview,
        )

        applicant = await db.get(Applicant, enrolment["applicant_id"])
        if applicant is None:
            return
        invite = await advance_applicant_to_interview(
            db,
            company_id=company_id,
            applicant=applicant,
            created_by_user_id=workflow.get("created_by_user_id"),
            notify_user_id=workflow.get("created_by_user_id"),
        )
        if invite is not None:
            await db.execute(
                text("UPDATE interview_invites SET enrolment_id = :e WHERE id = :i"),
                {"e": enrolment["id"], "i": invite.id},
            )
        log.info("runner.assigned.interview", enrolment_id=str(enrolment["id"]),
                 created=invite is not None)

    else:  # human_review — nothing to mint; it appears in the reviewer's queue.
        if workflow.get("created_by_user_id"):
            await create_notification(
                db,
                user_id=workflow["created_by_user_id"],
                kind="review_due",
                title=f"{enrolment['full_name']} is ready for {round_['title']}",
                body=f"{enrolment['target_job_title']} · awaiting your review",
                link="/hr/requisitions",
            )
        log.info("runner.assigned.human_review", enrolment_id=str(enrolment["id"]))


async def _move_to_round(
    db: AsyncSession,
    *,
    enrolment: dict[str, Any],
    round_: dict[str, Any],
    workflow: dict[str, Any],
) -> None:
    await db.execute(
        text("UPDATE enrolments SET current_round_id = :r, updated_at = :n WHERE id = :i"),
        {"r": round_["id"], "n": datetime.now(tz=UTC), "i": enrolment["id"]},
    )
    await _assign_round(db, enrolment=enrolment, round_=round_, workflow=workflow)


# ---------------------------------------------------------------------------
# C6 — the shortlist gate opens the workflow
# ---------------------------------------------------------------------------
async def on_shortlisted(
    db: AsyncSession, *, enrolment_id: uuid.UUID, actor_user_id: uuid.UUID | None
) -> RunnerOutcome:
    """A human confirmed the shortlist — assign the first round. Caller commits.

    This is the only place the workflow starts moving, and it is downstream of a
    person pressing a button. The runner never shortlists anyone itself: an ATS
    threshold decides who is *offered* for confirmation, never who proceeds.
    """
    enrolment = await _load_enrolment(db, enrolment_id)
    if enrolment is None:
        return RunnerOutcome(action="noop", reason="enrolment not found")
    if enrolment["current_round_id"] is not None:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="already in a round")
    if not enrolment["workflow_id"]:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="no workflow attached")

    workflow = await _load_workflow(db, enrolment["workflow_id"])
    if workflow is None:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="workflow missing")
    if not workflow["auto_assign_first_round"]:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="auto-assign disabled for this workflow")

    first = await _first_round(db, enrolment["workflow_id"])
    if first is None:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="workflow has no rounds")

    await _move_to_round(db, enrolment=enrolment, round_=first, workflow=workflow)
    log.info("runner.started", enrolment_id=str(enrolment_id), round_id=str(first["id"]))
    _ = actor_user_id
    return RunnerOutcome(action="advanced", enrolment_id=str(enrolment_id),
                         to_round=first["title"])


# ---------------------------------------------------------------------------
# C6/C7 — a round produced a result
# ---------------------------------------------------------------------------
async def record_result(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    round_id: uuid.UUID,
    score: float | None,
    max_score: float | None = None,
    graded_by: str = "deterministic",
    grader_user_id: uuid.UUID | None = None,
    attempt_ref: uuid.UUID | None = None,
    criterion_scores: dict[str, Any] | None = None,
    axes: dict[str, Any] | None = None,
    evidence: str | None = None,
    passed_override: bool | None = None,
) -> RunnerOutcome:
    """Record a round result, then advance or hold. Caller commits.

    ``passed_override`` is for ``human_review``, where a person decides rather
    than a threshold. Everything else derives ``passed`` from the round's
    advance threshold.

    A repeat call supersedes the previous result rather than overwriting it, so
    an earlier attempt stays readable for an appeal or an audit.
    """
    enrolment = await _load_enrolment(db, enrolment_id)
    if enrolment is None:
        return RunnerOutcome(action="noop", reason="enrolment not found")
    round_ = await _load_round(db, round_id)
    if round_ is None:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="round not found")
    workflow = await _load_workflow(db, enrolment["workflow_id"]) if enrolment["workflow_id"] else None
    if workflow is None:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="no workflow attached")

    # C8, upper layer. When the grader returned per-criterion scores, THEY are
    # the evaluation — the round's own weighted composite decides advancement,
    # not whatever headline number came alongside. The four canonical axes stay
    # on the result as the comparison layer (D-02) and are never used here.
    if criterion_scores and score is None:
        derived = composite_from_criteria(criterion_scores)
        if derived is not None:
            score = derived
            max_score = max_score or INTERVIEW_SCORE_MAX

    # Convert to a percentage on the round's own scale. An AI interview
    # composite is out of 10 unless the caller says otherwise; exam rounds
    # always supply their own max.
    effective_max = max_score
    if effective_max is None and round_["kind"] in AI_GRADED_KINDS:
        effective_max = INTERVIEW_SCORE_MAX
    percent: float | None = None
    if score is not None and effective_max:
        percent = round(float(score) / float(effective_max) * 100, 2)
    elif score is not None:
        # No scale to convert from: compare as-is rather than inventing one.
        percent = float(score)

    threshold = float(round_["pass_threshold"]) if round_["pass_threshold"] is not None else None
    if passed_override is not None:
        passed = passed_override
    elif threshold is None or percent is None:
        # No threshold and no explicit decision: this needs a person. Holding is
        # the honest outcome — it stops progression without inventing a verdict.
        passed = False
    else:
        passed = percent >= threshold

    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE round_results SET superseded_at = :n"
            " WHERE enrolment_id = :e AND round_id = :r AND superseded_at IS NULL"
        ),
        {"n": now, "e": enrolment_id, "r": round_id},
    )
    await db.execute(
        text(
            "INSERT INTO round_results (id, company_id, enrolment_id, round_id, attempt_ref,"
            " score, max_score, percent, passed, criterion_scores, axes, graded_by,"
            " grader_user_id, evidence, created_at)"
            " VALUES (:i,:c,:e,:r,:ar,:s,:ms,:p,:pa,"
            "         CAST(:cs AS jsonb), CAST(:ax AS jsonb),:gb,:gu,:ev,:n)"
        ),
        {"i": uuid.uuid4(), "c": enrolment["company_id"], "e": enrolment_id, "r": round_id,
         "ar": attempt_ref, "s": score, "ms": effective_max, "p": percent, "pa": passed,
         "cs": _json(criterion_scores), "ax": _json(axes), "gb": graded_by,
         "gu": grader_user_id, "ev": evidence, "n": now},
    )

    if not passed:
        band = int(workflow["hold_band"] or 0)
        near = (
            threshold is not None and percent is not None and percent >= threshold - band
        )
        reason = (
            f"{round_['title']}: {percent:.0f}% against a {threshold:.0f}% threshold"
            if percent is not None and threshold is not None
            else f"{round_['title']}: needs a human decision"
        )
        if near:
            reason += f" — within {band} points"
        return await _hold(db, enrolment, reason)

    if not workflow["auto_advance_rounds"]:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             from_round=round_["title"],
                             reason="auto-advance disabled for this workflow")

    return await _advance(db, enrolment=enrolment, round_=round_, workflow=workflow)


async def _advance(
    db: AsyncSession,
    *,
    enrolment: dict[str, Any],
    round_: dict[str, Any],
    workflow: dict[str, Any],
) -> RunnerOutcome:
    """Move to the next round, or finish the workflow at the human decision."""
    # Idempotency: a candidate can only advance FROM the round they are on.
    #
    # This was previously a truthiness check on current_round_id, which left a
    # hole at exactly the worst moment: completing the workflow sets the column
    # to NULL, so a replayed event for an early round found no current round,
    # skipped the guard, and dragged a finished candidate back to round two —
    # re-emailing them a link they had already used. Comparing outright closes
    # both the completed case and the held case.
    if str(enrolment["current_round_id"] or "") != str(round_["id"]):
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment["id"]),
                             reason="not the candidate's current round")

    nxt_id = round_["on_pass_next_round_id"]
    if nxt_id is None:
        # End of the workflow. NOT an outcome — the candidate is queued for the
        # final human decision, which is the only thing that ends a candidacy.
        await db.execute(
            text(
                "UPDATE enrolments SET current_round_id = NULL, updated_at = :n WHERE id = :i"
            ),
            {"n": datetime.now(tz=UTC), "i": enrolment["id"]},
        )
        await record_transition(
            db,
            enrolment_id=enrolment["id"],
            company_id=enrolment["company_id"],
            to_status="interviewed",
            actor_user_id=None,
            automated=True,
            reason=f"completed {round_['title']} — awaiting final decision",
        )
        log.info("runner.completed", enrolment_id=str(enrolment["id"]))
        return RunnerOutcome(action="completed", enrolment_id=str(enrolment["id"]),
                             from_round=round_["title"],
                             reason="awaiting the final human decision")

    nxt = await _load_round(db, nxt_id)
    if nxt is None:
        return await _hold(db, enrolment, f"{round_['title']}: the next round no longer exists")

    await _move_to_round(db, enrolment=enrolment, round_=nxt, workflow=workflow)
    await record_transition(
        db,
        enrolment_id=enrolment["id"],
        company_id=enrolment["company_id"],
        to_status="shortlisted" if enrolment["status"] == "new" else enrolment["status"],
        actor_user_id=None,
        automated=True,
        reason=f"advanced from {round_['title']} to {nxt['title']}",
    )
    log.info(
        "runner.advanced",
        enrolment_id=str(enrolment["id"]), **{"from": round_["title"]}, to=nxt["title"],
    )
    return RunnerOutcome(action="advanced", enrolment_id=str(enrolment["id"]),
                         from_round=round_["title"], to_round=nxt["title"])


async def release_hold(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    to_status: str = "shortlisted",
) -> RunnerOutcome:
    """A person decided a held candidate should continue. Caller commits.

    The mirror of ``_hold``, and deliberately human-only: there is no automated
    caller and no setting that releases a hold, because deciding that a
    below-threshold candidate should proceed is exactly the judgement D-05
    reserves for a person.
    """
    enrolment = await _load_enrolment(db, enrolment_id)
    if enrolment is None:
        return RunnerOutcome(action="noop", reason="enrolment not found")
    if enrolment["status"] != "held":
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="not held")

    await record_transition(
        db,
        enrolment_id=enrolment_id,
        company_id=enrolment["company_id"],
        to_status=to_status,
        actor_user_id=actor_user_id,
        automated=False,
        reason="hold released by a reviewer",
    )
    await db.execute(
        text(
            "UPDATE enrolments SET held_at = NULL, held_reason = NULL, updated_at = :n"
            " WHERE id = :i"
        ),
        {"n": datetime.now(tz=UTC), "i": enrolment_id},
    )
    log.info("runner.hold_released", enrolment_id=str(enrolment_id),
             actor=str(actor_user_id))
    return RunnerOutcome(action="advanced", enrolment_id=str(enrolment_id),
                         reason="hold released")


async def decision_queue(
    db: AsyncSession, *, company_id: uuid.UUID, requisition_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Everyone awaiting a final decision — advanced AND held, together.

    Deliberately one list. Held candidates buried behind a filter would make
    "every candidate reaches a human decision" true on paper and false in
    practice, so they are returned alongside those who completed, with the
    reason they stopped.
    """
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.status, e.held_reason, e.held_at, e.ats_overall,"
                "       a.full_name, a.email,"
                "       (SELECT count(*) FROM round_results rr"
                "         WHERE rr.enrolment_id = e.id AND rr.superseded_at IS NULL) AS rounds_taken,"
                "       (SELECT max(rr.percent) FROM round_results rr"
                "         WHERE rr.enrolment_id = e.id AND rr.superseded_at IS NULL) AS best_percent"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL"
                " WHERE e.company_id = :c AND e.requisition_id = :r"
                "   AND e.deleted_at IS NULL"
                "   AND e.status NOT IN ('hired','rejected')"
                "   AND (e.status = 'held' OR e.current_round_id IS NULL)"
                " ORDER BY (e.status = 'held') DESC, e.ats_overall DESC NULLS LAST"
            ),
            {"c": company_id, "r": requisition_id},
        )
    ).mappings().all()
    return [
        {
            "enrolment_id": str(r["id"]),
            "full_name": r["full_name"],
            "email": r["email"],
            "status": r["status"],
            "held": r["status"] == "held",
            "held_reason": r["held_reason"],
            "ats_overall": r["ats_overall"],
            "rounds_taken": int(r["rounds_taken"] or 0),
            "best_percent": float(r["best_percent"]) if r["best_percent"] is not None else None,
        }
        for r in rows
    ]


def _json(value: dict[str, Any] | None) -> str | None:
    import json  # noqa: PLC0415 — only needed on the write path

    return json.dumps(value) if value is not None else None


async def frozen_rubric_for_round(
    db: AsyncSession, *, round_id: uuid.UUID, job_title: str
) -> Any | None:
    """The exact rubric a round was published with, as a RoleProfile.

    Handed to the interview planner and the scorer so both work against what the
    workflow actually promised rather than a freshly derived profile that may
    have drifted. Returns None when the round stores too few criteria to form a
    rubric, which tells the caller to fall back to the whole role model rather
    than score against a distorted one.
    """
    round_ = await _load_round(db, round_id)
    if round_ is None:
        return None
    criteria = (await load_criteria(db, [round_id])).get(str(round_id), [])
    try:
        return profile_from_round_criteria(
            criteria,
            job_title=job_title,
            round_title=round_["title"],
            workflow_id=str(round_["workflow_id"]),
            round_id=str(round_id),
        )
    except FrozenRubricError as exc:
        log.info("runner.frozen_rubric.unavailable", round_id=str(round_id), reason=str(exc))
        return None
