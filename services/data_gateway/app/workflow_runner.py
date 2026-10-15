"""The workflow runner — Group C, features C6, C7 and C9.

One component advances every enrolled candidate. It is **event-driven, not
polled**: it reacts to three things happening and consults the enrolment's own
workflow version to decide what comes next.

    application received  ->  enrol, score, wait for the shortlist (a person, or
                             auto_shortlist above the bar — maybe_auto_shortlist)
    round submitted       ->  record the result, then advance or hold
    interview scored      ->  the same, via the same path

The first two arrive as requests. The third cannot: an interview is scored in
another service, at the end of a call nobody in this one is watching. So it is
found instead — record_scored_interviews, run by the reminder sweep every few
minutes — and then takes exactly the same path as an exam result.

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
from shared.agents.guardrails import detect_injection
from shared.intelligence import (
    FrozenRubricError,
    composite_from_criteria,
    profile_from_round_criteria,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import accommodations, job_tasks
from app.application_source import SOURCES, UNTRACKED, normalise_detail
from app.config import settings
from app.exam_link import hash_exam_token, mint_exam_token
from app.mailer import candidate_language, enqueue_email
from app.notifications_util import create_notification
from app.requisitions import TERMINAL_STATUSES, record_round_move, record_transition
from app.workflows import (
    AI_GRADED_KINDS,
    EXAM_BACKED_KINDS,
    HUMAN_EVALUATED_KINDS,
    TASK_KINDS,
    Route,
    auto_advance_for,
    exam_round_problem,
    exam_round_readiness,
    load_criteria,
    published_workflow,
    route_after_result,
)

log = structlog.get_logger(__name__)

# ``pass_threshold`` is ALWAYS a percentage, 0-100, for every round kind. Scores
# arrive on their round's natural scale, so they are converted before comparison.
# This used to be ambiguous — the column was documented as "percent for mcq and
# coding, 0-10 composite for ai_interview" — and a 7.5 composite was read as
# 7.5%, holding a candidate who had comfortably passed. One unit, converted at
# the edge, is the only version of this that cannot be misread.
INTERVIEW_SCORE_MAX = 10.0

#: Where a released hold may send a candidate. Continuing is the only thing a
#: release means; a final decision goes through app.final_decision, which
#: needs a reason and a reason code this path never collects.
RELEASE_TO_STATUSES: frozenset[str] = frozenset({"shortlisted", "interviewed"})


@dataclass
class RunnerOutcome:
    """What the runner did, for logging, tests and the ops surface."""

    action: str  # enrolled | advanced | routed (a fail branch) | held | completed | noop
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
# The ledger reason ONLY the public apply form writes on an enrolment's
# creating row. Auto-shortlist requires it (with a NULL, automated actor), so an
# enrolment some other system path creates with no person named — a bulk upload
# whose uploader is unknown, a backfill — is never mistaken for a
# self-application. The string predates the constant: every public-apply row
# already written carries it, which is why it was kept.
PUBLIC_APPLY_REASON = "enrolled on application"


async def enrol_applicant(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    requisition_id: uuid.UUID,
    target_job_title: str,
    target_level: str = "mid",
    target_jd_text: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    # Not PUBLIC_APPLY_REASON: a caller that does not say why it enrols someone
    # must not be read as the public form (auto-shortlist keys on that reason).
    reason: str = "enrolled",
    resume_s3_key: str | None = None,
    source: str = UNTRACKED,
    source_detail: str | None = None,
) -> RunnerOutcome:
    """Place an applicant into the requisition's published workflow. Caller commits.

    Idempotent by the partial unique index on (requisition_id, applicant_id):
    a second application to the same opening returns the existing enrolment
    rather than creating a duplicate.

    The candidate does NOT get a round here. The first round is assigned after
    the shortlist — ordinarily a person's, or on an opted-in workflow the
    automatic one in :func:`maybe_auto_shortlist`. Either way, not at enrolment.

    ``actor_user_id`` is the person who filed the applicant (HR's upload); the
    ledger entry is then marked manual. None — a candidate applying — is the
    system's doing.

    ``resume_s3_key`` is the CV submitted with THIS application. It is pinned
    on the enrolment so the application is scored against it even if the
    person uploads a newer CV before the reconciler gets to it.

    ``source`` is the acquisition channel (PH3-B1), written here because this is
    the only place an enrolment is created. It defaults to ``unknown`` rather
    than to the caller's most likely channel: a caller that does not say where
    an application came from has not tracked it, and guessing on its behalf
    would put invented attribution into PH5-C1's funnel. The two real callers
    both pass one.
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

    # A CLOSED OPENING TAKES NO NEW CANDIDATES. Checked here, the one place an
    # enrolment is created, because the callers check at different moments or
    # not at all — a bulk upload is accepted while the opening is open and
    # ingested later. FOR SHARE conflicts with app/auto_close.py's FOR UPDATE,
    # so the two serialise: either this enrolment lands first and auto-close
    # (SKIP LOCKED, then its own re-check) leaves the opening open, or the
    # close lands first and this refuses — never a closed opening holding
    # people nobody was asked about (AC-12).
    requisition_status = await db.scalar(
        text(
            "SELECT status FROM job_requisitions"
            " WHERE id = :r AND deleted_at IS NULL FOR SHARE"
        ),
        {"r": requisition_id},
    )
    if requisition_status == "closed":
        log.warning("runner.enrol_refused_closed_opening",
                    requisition_id=str(requisition_id), applicant_id=str(applicant_id))
        return RunnerOutcome(action="noop", reason="opening is closed")

    wf = await published_workflow(db, requisition_id)
    now = datetime.now(tz=UTC)
    enrolment_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
            " target_job_title, target_level, target_jd_text, workflow_id,"
            " applied_resume_s3_key, source, source_detail, created_at, updated_at)"
            " VALUES (:i,:c,:r,:a,'new',:tt,:tl,:jd,:w,:k,:src,:srcd,:n,:n)"
        ),
        {"i": enrolment_id, "c": company_id, "r": requisition_id, "a": applicant_id,
         "tt": target_job_title, "tl": target_level, "jd": target_jd_text,
         "k": resume_s3_key,
         # Normalised again here rather than trusted. This is the only writer,
         # so a caller that passes a value the CHECK constraint would reject
         # should fail as an attribution of 'unknown', not as a 500 that loses
         # the application.
         "src": source if source in SOURCES else UNTRACKED,
         "srcd": normalise_detail(source_detail),
         # NULL when nothing is published yet: the candidate is still a real
         # applicant and must not be lost. They join a workflow when one goes
         # live, rather than being rejected for arriving early.
         "w": wf["id"] if wf else None, "n": now},
    )
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at)"
            " VALUES (:c,:e,NULL,'new',:a,:auto,:r,:n)"
        ),
        {"c": company_id, "e": enrolment_id, "a": actor_user_id,
         "auto": actor_user_id is None, "r": reason, "n": now},
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
                "   AND e.status NOT IN ('hired','rejected')"
                " ORDER BY e.created_at DESC, e.id"
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
async def _load_enrolment(
    db: AsyncSession, enrolment_id: uuid.UUID, *, lock: bool = False
) -> dict[str, Any] | None:
    """The enrolment plus its applicant's name/email.

    ``lock`` takes ``FOR UPDATE OF e`` (the enrolment row only, not the
    applicant) so a caller that is about to check-then-write on the enrolment's
    status or round is serialised against a concurrent decision.
    """
    row = (
        await db.execute(
            # nosec B608: the only thing concatenated is a fixed " FOR UPDATE OF e"
            # chosen by a bool. No caller value reaches the SQL text — the one
            # value here is :i, bound. Bandit flags any `+` on a query string and
            # cannot see that both sides are literals.
            text(
                "SELECT e.id, e.company_id, e.applicant_id, e.requisition_id, e.status,"  # nosec B608
                "       e.workflow_id, e.current_round_id, e.target_job_title,"
                "       a.full_name, a.email"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.id = :i AND e.deleted_at IS NULL"
                + (" FOR UPDATE OF e" if lock else "")
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
                "       time_limit_seconds,"
                "       deadline_days, on_pass_next_round_id, exam_round_id,"
                "       on_fail_next_round_id, fast_track_min_percent,"
                "       on_fast_track_next_round_id, auto_advance"
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
                "       time_limit_seconds,"
                "       deadline_days, on_pass_next_round_id, exam_round_id,"
                "       on_fail_next_round_id, fast_track_min_percent,"
                "       on_fast_track_next_round_id, auto_advance"
                "  FROM workflow_rounds WHERE workflow_id = :w AND deleted_at IS NULL"
                " ORDER BY position LIMIT 1"
            ),
            {"w": workflow_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _hold(
    db: AsyncSession,
    enrolment: dict[str, Any],
    reason: str,
    actor: uuid.UUID | None = None,
    *,
    round_: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
) -> RunnerOutcome:
    """Stop a candidate's progression without ending their candidacy.

    Held is neutral. The candidate keeps every score they earned, stops
    consuming further rounds, and appears in the final decision queue beside
    those who advanced. Only a person may move them out of it, in either
    direction (D-05).

    ``round_`` and ``workflow`` are what let this function TELL SOMEONE. A hold
    is the one runner outcome whose whole purpose is to summon a person, and it
    used to summon nobody: the candidate appeared in ``decision_queue`` and
    waited there until an HR manager happened to open the screen. The per-round
    "Hold for my review" toggle made that the normal path rather than an edge
    case, so the notification is part of the feature, not a nicety. Omit them
    and the hold still happens silently — no caller should, but a hold must
    never fail for want of a notification.
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
    if workflow is not None and round_ is not None:
        # Same kind as a human_review assignment: to the reviewer this IS the
        # same event — someone is waiting for your decision — and reusing it
        # means the feed tone and the live-refresh keys (which already point at
        # the decision queue) need no new wiring.
        #
        # Deduped per (enrolment, round): a re-grade of the same round re-holds
        # the same candidate at the same place, and announcing that twice would
        # train HR to ignore the feed. A hold at a DIFFERENT round is a new
        # event and does announce.
        await create_notification(
            db,
            user_id=workflow.get("created_by_user_id"),
            kind="review_due",
            title=f"{enrolment['full_name']} is waiting for your decision",
            body=f"{enrolment['target_job_title']} · {reason}",
            link="/hr/requisitions",
            dedupe_key=f"hold:{enrolment['id']}:{round_['id']}",
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
        readiness = (
            await exam_round_readiness(db, [round_["exam_round_id"]])
        ).get(str(round_["exam_round_id"]))
        problem = exam_round_problem(readiness)
        if problem is not None or readiness is None:
            # exam_take refuses a link to a draft or empty exam round, so minting
            # one would email the candidate a link that reads "not valid".
            # Publish validation blocks this; this catches an exam round changed
            # after the workflow went live. The candidate keeps their place and
            # the owner is told what to fix.
            log.warning(
                "runner.assign.exam_round_not_live",
                round_id=str(round_["id"]),
                exam_round_id=str(round_["exam_round_id"]),
                problem=problem,
            )
            if workflow.get("created_by_user_id"):
                await create_notification(
                    db,
                    user_id=workflow["created_by_user_id"],
                    kind="round_not_ready",
                    title=f"{enrolment['full_name']} could not start {round_['title']}",
                    body=(
                        f"The exam round attached to {round_['title']} {problem}. "
                        "Fix it, then assign the exam to them from the exam editor."
                    ),
                    link="/hr/exams",
                )
            return
        exam_id = readiness["exam_id"]
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
        # PH4-D2: the link's deadline gains any recorded extension — the exam's
        # own time limit is scaled separately, at /exam/start.
        adj_row = await accommodations.effective_for(
            db, company_id=company_id, applicant_id=enrolment["applicant_id"],
            enrolment_id=enrolment["id"], workflow_round_id=round_["id"],
            exam_round_id=round_["exam_round_id"],
        )
        extra_days = adj_row.deadline_extension_days if adj_row else None
        raw = mint_exam_token()
        asn_id = uuid.uuid4()
        expires = now + timedelta(days=int(round_["deadline_days"] or 7) + (extra_days or 0))
        await db.execute(
            text(
                "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
                " enrolment_id, created_by_user_id, token_hash, expires_at, accommodation_id,"
                " status, created_at, updated_at)"
                " VALUES (:i,:c,:e,:r,:a,:en,:cb,:th,:x,:acc,'invited',:n,:n)"
            ),
            # Attributed to the workflow's owner, as the interview branch below
            # already does. Left NULL, the assignment had no owner, and a lapse
            # of this link was announced to nobody.
            {"i": asn_id, "c": company_id, "e": exam_id, "r": round_["exam_round_id"],
             "a": enrolment["applicant_id"], "en": enrolment["id"],
             "cb": workflow.get("created_by_user_id"),
             "th": hash_exam_token(raw, settings.exam_link_secret), "x": expires,
             "acc": adj_row.id if adj_row else None, "n": now},
        )
        if adj_row is not None:
            await accommodations.record_applied(
                db, company_id=company_id, accommodation_id=adj_row.id,
                target_kind="exam_assignment", target_id=asn_id,
            )
        base = settings.exam_link_base_url.rstrip("/")
        await enqueue_email(
            db,
            to=enrolment["email"],
            template="exam_link",
            lang=await candidate_language(db, enrolment["applicant_id"]),
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
            language=await candidate_language(db, applicant.id),
            created_by_user_id=workflow.get("created_by_user_id"),
            notify_user_id=workflow.get("created_by_user_id"),
            # Created linked to this application, and grounded in its role.
            enrolment_id=enrolment["id"],
            # PH4-D2: this workflow round is the accommodation scope, when one
            # was recorded against it specifically.
            workflow_round_id=round_["id"],
        )
        log.info("runner.assigned.interview", enrolment_id=str(enrolment["id"]),
                 created=invite is not None)

    elif kind in TASK_KINDS:
        # PH4-D4: a job simulation or portfolio round. app.job_tasks mints the
        # link, scales the allowance with any D2 accommodation, and emails the
        # candidate — the same shape the exam branch above follows.
        await job_tasks.issue(db, enrolment=enrolment, round_=round_, workflow=workflow)

    elif kind == "human_review":  # nothing to mint; it appears in the reviewer's queue.
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

    else:
        # An unknown kind should never reach here — validate_chain refuses it
        # at publish. Logged rather than raised: the candidate is already on
        # the round (record_round_move happened first), and raising here
        # would leave them on a round the runner then never returns to.
        log.warning("runner.assign.unknown_kind", round_id=str(round_["id"]), kind=kind)


async def _move_to_round(
    db: AsyncSession,
    *,
    enrolment: dict[str, Any],
    round_: dict[str, Any],
    workflow: dict[str, Any],
) -> None:
    # Through the ledger (B2): a round move is a stage change even when the
    # status does not change, and it used to leave no trace at all. Recorded as
    # the system's doing — the person's decision (the shortlist, the pass) is
    # the transition that led here, recorded where it was made.
    await record_round_move(
        db,
        enrolment_id=enrolment["id"],
        company_id=enrolment["company_id"],
        to_round_id=round_["id"],
        actor_user_id=None,
        automated=True,
        reason=f"assigned {round_['title']}",
    )
    await _assign_round(db, enrolment=enrolment, round_=round_, workflow=workflow)


# ---------------------------------------------------------------------------
# C6 — the shortlist gate opens the workflow
# ---------------------------------------------------------------------------
async def on_shortlisted(
    db: AsyncSession, *, enrolment_id: uuid.UUID, actor_user_id: uuid.UUID | None
) -> RunnerOutcome:
    """A shortlist was confirmed — assign the first round. Caller commits.

    This is the only place the workflow starts moving. Ordinarily it is
    downstream of a person pressing a button, and an ATS threshold decides only
    who is *offered* for confirmation.

    The one exception is opt-in: a workflow with ``auto_shortlist`` on is
    shortlisted by :func:`maybe_auto_shortlist`, which then calls this with
    ``actor_user_id=None``. This docstring used to say the runner never
    shortlists anyone itself; that stopped being true in migration a2c4e6f8b0d3
    and is corrected rather than left standing. What did NOT change: nothing
    here, or anywhere in the runner, can end a candidacy (D-05).
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
# Auto-shortlist — the optional removal of the human shortlist gate
# ---------------------------------------------------------------------------
def clears_shortlist_bar(ats_overall: int | None, threshold: int | None) -> bool:
    """Whether an ATS score clears a workflow's shortlist bar.

    ONE DEFINITION, because the scale is a trap. ``shortlist_ats_threshold`` is
    stored 0-10 and ``ats_overall`` is 0-100; every reader compares
    ``ats_overall >= threshold * 10`` (requisition_dashboard's
    ``ready_to_shortlist``, agents/watch_runner). Compare without the ``* 10``
    and a bar of 7 admits a score of 8 out of 100 — i.e. everyone.

    No score, or no bar, is never a pass: an applicant whose CV has not been
    read yet must not be advanced on the absence of evidence.
    """
    if ats_overall is None or threshold is None:
        return False
    return ats_overall >= threshold * 10


class _RoundOneNotStartedError(Exception):
    """Raised inside the savepoint to UNDO a shortlist whose round one did not
    start. Private: it never leaves maybe_auto_shortlist."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def maybe_auto_shortlist(db: AsyncSession, *, enrolment_id: uuid.UUID) -> RunnerOutcome:
    """Shortlist an applicant the runner's own way, when — and only when — the
    workflow says so and their ATS clears the bar. Caller commits. NEVER RAISES.

    Called immediately after an ATS score is written (every caller of
    ``applicant_enrichment.apply_ats_to_enrolment``; a test asserts none is
    missed). The ordinary path is still :func:`on_shortlisted`, downstream of a
    person pressing a button. This is the opt-in alternative, added in
    migration a2c4e6f8b0d3 — read that docstring for why it is inside D-05.

    WHAT IT WILL NOT DO, each a guard below rather than a hope:
      * act on anyone not in status ``new``. In particular not ``held``: moving
        a held applicant to shortlisted would silently undo a hold.
      * act below the bar. An applicant who misses it is left exactly where
        they were, in HR's queue — never held, never rejected (D-05).
      * act on a workflow that is not the published one, has the switch off,
        or has no bar.
      * raise. It runs inside the transaction that just wrote the ATS score,
        and losing a candidate's score because the shortlist attempt failed
        would be a worse outcome than not shortlisting them. A savepoint
        confines any failure to this attempt; the score still commits.
    """
    try:
        async with db.begin_nested():
            return await _auto_shortlist(db, enrolment_id)
    except _RoundOneNotStartedError as stop:
        # Not a failure — a deliberate undo. The savepoint has rolled the
        # shortlist back, so the applicant is still 'new' and still in HR's
        # queue rather than stranded as shortlisted-with-no-round.
        log.info("runner.auto_shortlist_undone", enrolment_id=str(enrolment_id),
                 reason=stop.reason)
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason=f"undone: {stop.reason}")
    except Exception as exc:  # noqa: BLE001 — see "NEVER RAISES" above
        # exc_type ONLY. str() of a SQLAlchemy/asyncpg error carries the SQL and
        # its bound parameters, and this savepoint wraps round assignment, whose
        # parameters include the candidate's name and email. The structlog PII
        # processor matches key NAMES, never values, so it would not catch them —
        # and because this swallows the exception, it never reaches the scrubbed
        # 500 handler either (shared/http_observability.py explains the trap).
        log.error(
            "runner.auto_shortlist_failed",
            enrolment_id=str(enrolment_id),
            exc_type=type(exc).__name__,
        )
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason=f"auto-shortlist failed: {type(exc).__name__}")


async def _auto_shortlist(db: AsyncSession, enrolment_id: uuid.UUID) -> RunnerOutcome:
    # LOCKED, and the status is checked under the lock. record_transition only
    # refuses a no-op move — it cannot be told what the FROM status must be —
    # so checking 'new' first and transitioning second would let a hold that
    # landed in between be overwritten with a shortlist.
    row = (
        await db.execute(
            text(
                "SELECT e.status, e.workflow_id, e.current_round_id, e.company_id,"
                "       e.ats_overall, e.ats_injection_markers, e.scored_resume_s3_key,"
                "       a.resume_s3_key AS current_resume_key, a.resume_text,"
                # PROVENANCE, from facts HR cannot set (see the guard below).
                # Created by nobody: the enrolment's creating ledger row is the
                # system's (enrol_applicant writes automated = actor IS NULL),
                # and no creating row names a person. `automated` is checked as
                # well as the NULL actor because actor_user_id is ON DELETE SET
                # NULL — deleting the HR account that uploaded a CV must not
                # turn its upload into a self-application.
                "       (EXISTS (SELECT 1 FROM stage_transitions st"
                "                 WHERE st.enrolment_id = e.id AND st.from_status IS NULL"
                "                   AND st.to_status = 'new' AND st.actor_user_id IS NULL"
                "                   AND st.automated AND st.reason = :public_reason)"
                "        AND NOT EXISTS (SELECT 1 FROM stage_transitions st"
                "                 WHERE st.enrolment_id = e.id AND st.from_status IS NULL"
                "                   AND (st.actor_user_id IS NOT NULL OR NOT st.automated))"
                "       ) AS self_created,"
                # Consented themselves: the public apply form is the only writer
                # of application_data/recruitment (public_apply._record_apply_consent),
                # against the identity that owns the applicant row.
                "       (a.user_id IS NOT NULL AND EXISTS ("
                "           SELECT 1 FROM dpdp_consent_ledger c"
                "            WHERE c.user_id = a.user_id"
                "              AND c.consent_type = 'application_data'"
                "              AND c.purpose = 'recruitment'"
                "              AND c.granted AND c.revoked_at IS NULL"
                # To THIS company. One identity can hold applicant rows at
                # several companies once activation links them, and a consent
                # given to one is not a consent to another's automated step.
                "              AND c.evidence ->> 'company_id' = e.company_id::text)"
                "       ) AS self_consented,"
                # THE ADDRESS WAS PROVEN, AFTER THIS APPLICATION. The public form
                # takes any typed address unverified, so an anonymous caller can
                # make both facts above true for somebody else's address — and
                # auto-shortlist would then email that person an assessment.
                # Activation (the emailed link) is the only proof of the mailbox
                # this product has, and it must postdate the enrolment: an
                # address proven before, for an earlier application, says
                # nothing about who submitted this one. A candidate who claims
                # their account is offered to auto-shortlist at that moment
                # (apply_activation._offer_to_auto_shortlist).
                "       EXISTS (SELECT 1 FROM users u"
                "                WHERE u.id = a.user_id AND u.deleted_at IS NULL"
                "                  AND u.email_verified_at IS NOT NULL"
                "                  AND u.email_verified_at >= e.created_at"
                "       ) AS address_proven"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.id = :i AND e.deleted_at IS NULL"
                # OF e: the enrolment row only. The applicant row is read, not
                # locked — it is the CV text, which nothing here writes.
                " FOR UPDATE OF e"
            ),
            {"i": enrolment_id, "public_reason": PUBLIC_APPLY_REASON},
        )
    ).mappings().first()
    if row is None:
        return RunnerOutcome(action="noop", reason="enrolment not found")
    eid = str(enrolment_id)
    if row["status"] != "new":
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason=f"status is {row['status']}, not new")
    if row["current_round_id"] is not None:
        return RunnerOutcome(action="noop", enrolment_id=eid, reason="already in a round")
    if not row["workflow_id"]:
        return RunnerOutcome(action="noop", enrolment_id=eid, reason="no workflow attached")
    # ONLY PEOPLE WHO APPLIED THEMSELVES ARE AUTO-SHORTLISTED. An HR-uploaded
    # CV (bulk, single, or added from a talent pool) is a person who did not
    # apply through the product and whose processing rests on the
    # hr_collected_application basis. Sending them an exam link or an
    # AI-interview invite because a model scored their CV, with no human
    # choosing to contact them, is a different act from advancing someone who
    # applied. HR can still shortlist them by hand; that is a person deciding to
    # reach out.
    #
    # NOT `e.source`. Source is an attribution label HR picks at upload
    # (application_source.validate_hr_source accepts any governed channel), so
    # an upload tagged 'referral' would have passed a source check. Both facts
    # below are written by code HR cannot steer, and BOTH are required, so a
    # future mistake in either one still fails closed.
    if not row["self_created"]:
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason="HR-created applicant — a person decides to contact them")
    if not row["self_consented"]:
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason="no application consent from the applicant on record")
    if not row["address_proven"]:
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason="applicant has not confirmed their email since applying")

    workflow = await _load_workflow(db, row["workflow_id"])
    if workflow is None:
        return RunnerOutcome(action="noop", enrolment_id=eid, reason="workflow missing")
    # The LIVE policy decides. An applicant still attached to an archived
    # version is not started on a policy the team has since replaced.
    if workflow.get("status") != "published":
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason=f"workflow is {workflow.get('status')}, not published")
    if not workflow.get("auto_shortlist"):
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason="auto-shortlist is off for this workflow")

    threshold = workflow.get("shortlist_ats_threshold")
    score = row["ats_overall"]
    # A bar of 0 admits every scored applicant — including one scored 0
    # because the scorer's response had no "overall" (apply_ats_to_enrolment
    # defaults it to 0). As a SUGGESTION that is harmless; as an automatic
    # shortlist it is "everyone", which the settings layer and
    # ck_workflows_auto_shortlist_needs_threshold refuse. Refused here too, so
    # a row that predates or bypasses those still advances no one.
    if threshold is None or threshold < 1:
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason=f"bar {threshold} is not a usable automatic bar")
    if not clears_shortlist_bar(score, threshold):
        # The ONLY outcome below the bar is nothing. Not held, not rejected:
        # still 'new', still in HR's queue, still shortlistable by hand.
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason=f"ATS {score} below the bar of {threshold}/10")
    # A CV CARRYING INSTRUCTIONS AIMED AT THE SCORER IS NOT ADVANCED ON ITS SCORE.
    # The injection defence was built on a person looking before anything
    # happens: detection warns, it never rejects (shared/agents/guardrails.py).
    # Auto-shortlist removes that person, so a CV that may have talked its own
    # score up would go straight to an assessment. Checked AFTER the bar so the
    # scan runs only for applicants who would otherwise advance. The outcome is
    # still nothing — left 'new', in HR's queue — which keeps it inside D-05; and
    # the markers are recomputed from the CV wherever HR looks at the applicant
    # (agents/tools.py), so a person sees the warning rather than losing it.
    #
    # THE SCAN MUST CONCERN THE CV THAT WAS SCORED. A returning applicant's new
    # CV belongs to the enrolment (applied_resume_s3_key), not the applicant
    # row, so applicants.resume_text can be a different document from the one
    # that produced this number. The scorer's own markers, stored with the
    # score, describe exactly that CV. With none on record (scored before they
    # were stored), a live scan is trustworthy only when the scored CV IS the
    # current one; otherwise there is no evidence either way, and no evidence
    # is not clean.
    stored = row["ats_injection_markers"]
    scored_is_current = (
        row["scored_resume_s3_key"] is not None
        and row["scored_resume_s3_key"] == row["current_resume_key"]
    )
    if stored is None and not scored_is_current:
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason="no injection check on record for the scored CV — left for a person")
    markers = list(stored or [])
    if scored_is_current:
        # Belt and braces: the shared detector HR's tools show, over the same
        # CV. A marker either source finds is enough to decline.
        markers += detect_injection(row["resume_text"] or "")
    if markers:
        log.warning("runner.auto_shortlist_declined_injection",
                    enrolment_id=eid, marker_count=len(markers))
        return RunnerOutcome(action="noop", enrolment_id=eid,
                             reason="CV flagged for prompt injection — left for a person")

    previous = await record_transition(
        db,
        enrolment_id=enrolment_id,
        company_id=row["company_id"],
        to_status="shortlisted",
        # No person did this, and the ledger must not say one did. The decision
        # queue, analytics and an audit all read `automated` to tell the two
        # apart; attributing it to the workflow's author would be a lie.
        actor_user_id=None,
        automated=True,
        reason=f"Auto-shortlisted: ATS {score} cleared the bar of {threshold}/10",
    )
    if previous is None:
        return RunnerOutcome(action="noop", enrolment_id=eid, reason="already shortlisted")

    outcome = await on_shortlisted(db, enrolment_id=enrolment_id, actor_user_id=None)
    if outcome.action != "advanced":
        # SHORTLISTED BUT NOT STARTED IS THE WORST OF BOTH. The manual path has a
        # person looking at the result; this one does not. Left committed, the
        # applicant would sit as 'shortlisted' with no round, drop out of HR's
        # "ready to shortlist" count, and never be retried — a later rescore is
        # a no-op because they are no longer 'new'. So roll the shortlist back
        # and leave them where a human will see them. (update_settings refuses
        # auto_shortlist without auto_assign_first_round, so the usual cause is
        # a workflow with no rounds — but this catches every cause.)
        raise _RoundOneNotStartedError(outcome.reason or outcome.action)
    log.info(
        "runner.auto_shortlisted",
        enrolment_id=eid,
        ats=score,
        threshold=threshold,
        then=outcome.action,
        to_round=outcome.to_round,
    )
    return RunnerOutcome(action="advanced", enrolment_id=eid, to_round=outcome.to_round,
                         reason="auto-shortlisted")


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
    # Locked, so the guards below and the writes after them are one serialised
    # step: a final decision landing between the check and the hold cannot be
    # overwritten by a late result.
    enrolment = await _load_enrolment(db, enrolment_id, lock=True)
    if enrolment is None:
        return RunnerOutcome(action="noop", reason="enrolment not found")
    # A result for a candidacy a person has already ended, or for a round the
    # candidate is no longer on (a late or replayed submit), must not write a
    # round_results row or move anyone. _advance has always had the round guard;
    # the hold paths did not, so a late exam submit could pull a rejected
    # enrolment back to held, and for a hired one record_transition's 409 escaped
    # past the exam submit handler and the graded attempt was never saved.
    # (A final decision also clears current_round_id, but the pipeline board's
    # status change does not, so the status check is not redundant.)
    if str(enrolment["status"]) in TERMINAL_STATUSES:
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="candidacy already decided")
    if str(enrolment["current_round_id"] or "") != str(round_id):
        return RunnerOutcome(action="noop", enrolment_id=str(enrolment_id),
                             reason="not the candidate's current round")
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

    # PH4-O3: the one function that turns a result into a destination — the
    # O2 simulation calls it too, so a dry run tests exactly this.
    route = route_after_result(round_, passed=passed, percent=percent)

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
        # A fail branch routes rather than holds — to another round, never to an
        # outcome. With auto-advance off, a person moves people, so it holds.
        if route.kind == "advance" and auto_advance_for(round_, workflow):
            return await _advance(
                db, enrolment=enrolment, round_=round_, workflow=workflow, route=route,
                why=reason,
            )
        return await _hold(db, enrolment, reason, round_=round_, workflow=workflow)

    if not auto_advance_for(round_, workflow):
        # Held for a person BY DESIGN, not stalled. The reason names which
        # setting decided it, because "why has this candidate not moved?" is
        # the question this surface exists to answer.
        why = (
            "this round is set to hold for HR review"
            if round_.get("auto_advance") is False
            else "auto-advance disabled for this workflow"
        )
        return await _hold(
            db, enrolment, f"{round_['title']}: passed — {why}",
            round_=round_, workflow=workflow,
        )

    return await _advance(db, enrolment=enrolment, round_=round_, workflow=workflow, route=route)


# ---------------------------------------------------------------------------
# C6/C7 — an interview was scored
# ---------------------------------------------------------------------------
# The third event this module's docstring names, and until now the only one
# with no caller: exams reported their results from the submit handler, but a
# scored interview reached nobody, so every candidate who cleared an AI
# interview round simply stayed on it until a person noticed.
#
# Found by absence, like the rest of Group A: a workflow-issued invite whose
# session has a scorecard, whose enrolment is still on an AI round, and whose
# scorecard has not been recorded as any round's result. The guards, in order:
#
# * inv.enrolment_id — only invites the runner issued. HR's hand-made invites
#   carry none and stay out of the workflow, exactly as hand-assigned exams do.
# * inv.status = 'completed' — the completion stage has already closed the
#   invite. That ordering is load-bearing: advancing into a second interview
#   round mints a new invite, and advance_applicant_to_interview refuses while
#   the old one still reads 'consumed', which would strand the candidate on the
#   new round with no link.
# * the latest runner invite for the enrolment — an older round's interview
#   cannot be read as this round's result.
# * the scorecard is no round's attempt_ref yet — the idempotency guard, on top
#   of _advance's own "only from the current round" check.
# * not held, hired or rejected — a held candidate moves only when a person
#   releases them (D-05), and a decided one does not move at all.
_SCORED_INTERVIEWS_SQL = """
SELECT e.id AS enrolment_id, e.current_round_id AS round_id, e.target_job_title,
       sc.scorecard_id, sc.composite_score, sc.scores, sc.rationale, sc.summary
  FROM interview_invites inv
  JOIN scorecards      sc ON sc.session_id = inv.session_id
  JOIN enrolments      e  ON e.id = inv.enrolment_id AND e.deleted_at IS NULL
  JOIN workflow_rounds wr ON wr.id = e.current_round_id AND wr.deleted_at IS NULL
 WHERE inv.deleted_at IS NULL
   AND inv.status = 'completed'
   AND wr.kind = ANY(CAST(:kinds AS text[]))
   AND e.status <> 'held'
   AND e.status NOT IN ('hired','rejected')
   AND inv.created_at = (SELECT max(i2.created_at) FROM interview_invites i2
                          WHERE i2.enrolment_id = e.id AND i2.deleted_at IS NULL)
   AND NOT EXISTS (SELECT 1 FROM round_results rr WHERE rr.attempt_ref = sc.scorecard_id)
 ORDER BY sc.created_at
 LIMIT :lim
"""


def _as_obj(val: Any) -> Any:
    import json  # noqa: PLC0415

    return json.loads(val) if isinstance(val, str) else val


async def _criteria_for(
    db: AsyncSession, *, round_id: uuid.UUID, job_title: str, rationale: dict[str, Any]
) -> dict[str, Any] | None:
    """The scorecard's per-criterion scores, if they ARE this round's evaluation.

    Only when they cover every criterion the round was published with. A partial
    breakdown would be averaged over the criteria that happen to be present, so
    one the scorer skipped would silently drop out of the decision; and a
    breakdown against some other rubric (a practice profile, an earlier version)
    is not this round's evaluation at all. In either case the four-axis
    composite decides instead — the same number HR sees on the scorecard.
    """
    breakdown = rationale.get("_competencies")
    if not isinstance(breakdown, dict) or not breakdown:
        return None
    frozen = await frozen_rubric_for_round(db, round_id=round_id, job_title=job_title)
    if frozen is None:
        return None
    if {c.id for c in frozen.competencies} != set(breakdown):
        return None
    return breakdown


async def record_scored_interviews(db: AsyncSession, *, limit: int = 50) -> list[RunnerOutcome]:
    """Record every newly scored workflow interview as its round's result.

    Commits per enrolment, so one bad row cannot roll back the others, and
    returns what happened to each for the caller to log and count.
    """
    rows = (
        await db.execute(
            text(_SCORED_INTERVIEWS_SQL),
            {"kinds": sorted(AI_GRADED_KINDS), "lim": limit},
        )
    ).mappings().all()

    outcomes: list[RunnerOutcome] = []
    for r in rows:
        try:
            rationale = _as_obj(r["rationale"]) or {}
            criteria = await _criteria_for(
                db, round_id=r["round_id"], job_title=r["target_job_title"], rationale=rationale
            )
            outcome = await record_result(
                db,
                enrolment_id=r["enrolment_id"],
                round_id=r["round_id"],
                # With usable criteria, score stays None so record_result takes
                # the round's own weighted composite (C8). Otherwise the
                # scorecard's composite, out of 10.
                score=None if criteria else (
                    float(r["composite_score"]) if r["composite_score"] is not None else None
                ),
                max_score=INTERVIEW_SCORE_MAX,
                graded_by="ai",
                attempt_ref=r["scorecard_id"],
                criterion_scores=criteria,
                axes=_as_obj(r["scores"]),
                evidence=r["summary"],
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001 — one enrolment must not stop the rest
            await db.rollback()
            log.error("runner.interview_result_failed", enrolment_id=str(r["enrolment_id"]),
                      error_type=type(exc).__name__, error=str(exc)[:300])
            continue
        log.info("runner.interview_recorded", **outcome.as_dict())
        outcomes.append(outcome)
    return outcomes


async def _advance(
    db: AsyncSession,
    *,
    enrolment: dict[str, Any],
    round_: dict[str, Any],
    workflow: dict[str, Any],
    route: Route | None = None,
    why: str | None = None,
) -> RunnerOutcome:
    """Move along the route a result chose, or finish at the human decision.

    ``route`` defaults to the pass branch, which is what every caller before
    PH4-O3 meant. A fail branch arrives here too (with ``why``, the reason it
    was taken): it moves the candidate to another round, exactly like a pass,
    and never changes their status to anything a person has not chosen.
    """
    if route is None:
        route = route_after_result(round_, passed=True, percent=None)
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

    nxt_id = route.next_round_id if route.kind == "advance" else None
    if route.kind == "hold":
        return await _hold(
            db, enrolment, why or f"{round_['title']}: needs a human decision",
            round_=round_, workflow=workflow,
        )
    if nxt_id is None:
        # End of the workflow. NOT an outcome — the candidate is queued for the
        # final human decision, which is the only thing that ends a candidacy.
        await record_round_move(
            db,
            enrolment_id=enrolment["id"],
            company_id=enrolment["company_id"],
            to_round_id=None,
            actor_user_id=None,
            automated=True,
            reason=f"completed {round_['title']} — the last round",
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
        return await _hold(
            db, enrolment, f"{round_['title']}: the next round no longer exists",
            round_=round_, workflow=workflow,
        )

    await _move_to_round(db, enrolment=enrolment, round_=nxt, workflow=workflow)
    moved = {
        "pass": f"advanced from {round_['title']} to {nxt['title']}",
        "fast_track": f"fast-tracked from {round_['title']} to {nxt['title']}",
        "fail": f"routed from {round_['title']} to {nxt['title']}"
        + (f" ({why})" if why else ""),
    }[route.branch]
    await record_transition(
        db,
        enrolment_id=enrolment["id"],
        company_id=enrolment["company_id"],
        to_status="shortlisted" if enrolment["status"] == "new" else enrolment["status"],
        actor_user_id=None,
        automated=True,
        reason=moved,
    )
    log.info(
        "runner.advanced",
        enrolment_id=str(enrolment["id"]), **{"from": round_["title"]}, to=nxt["title"],
    )
    return RunnerOutcome(action="routed" if route.branch == "fail" else "advanced",
                         enrolment_id=str(enrolment["id"]),
                         from_round=round_["title"], to_round=nxt["title"],
                         reason=route.branch)


async def release_hold(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    to_status: str = "shortlisted",
    reason: str | None = None,
) -> RunnerOutcome:
    """A person decided a held candidate should continue. Caller commits.

    The mirror of ``_hold``, and deliberately human-only: there is no automated
    caller and no setting that releases a hold, because deciding that a
    below-threshold candidate should proceed is exactly the judgement D-05
    reserves for a person.

    "Continue" means ONTO THE NEXT ROUND. This used to clear ``held_at`` and
    record a transition without touching ``current_round_id``, so a released
    candidate came back to the pipeline still sitting on the round they had
    already finished — no exam link, no interview invite, nothing assigned, and
    no second event coming to move them. HR's only remaining option was to drag
    them by hand. The release now takes the round's PASS branch through
    ``_advance``, which is the same code an automatic advance runs: it mints
    whatever the next round needs and emails the candidate. A release on the
    last round ends at the final human decision, exactly as finishing it
    automatically would.

    Taking the pass branch on a candidate who FAILED is the point, not an
    oversight: releasing a below-threshold candidate is a reviewer overriding
    the threshold, and the fail branch is where the runner already sent the ones
    it could route by itself. Nothing here can reject anyone — see
    ``app.final_decision``.
    """
    if to_status not in RELEASE_TO_STATUSES:
        return RunnerOutcome(
            action="noop", enrolment_id=str(enrolment_id),
            reason="a release continues the candidate; record a final decision instead",
        )
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
        # The reviewer's own words when they gave them: this is a person
        # overriding a threshold, and the ledger is where anyone later asks why.
        reason=f"hold released by a reviewer: {reason}" if reason else "hold released by a reviewer",
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

    # Now actually move them. The status the caller chose is written into the
    # dict we hand _advance, because _advance carries the current status forward
    # onto the next round and would otherwise re-record "held" on a candidate
    # who has just been released.
    released = {**enrolment, "status": to_status}
    round_id = enrolment["current_round_id"]
    if round_id is None:
        # Held with no current round: the candidate had already reached the end
        # of the workflow, so there is nowhere to advance to and the release
        # simply returns them to the final decision queue.
        return RunnerOutcome(action="advanced", enrolment_id=str(enrolment_id),
                             reason="hold released — awaiting the final decision")
    round_ = await _load_round(db, round_id)
    workflow = (
        await _load_workflow(db, enrolment["workflow_id"]) if enrolment["workflow_id"] else None
    )
    if round_ is None or workflow is None:
        # The round or the workflow was deleted under them. The hold is still
        # lifted — refusing that would strand the candidate in held forever —
        # but there is no route to follow, so say so rather than implying a move.
        log.warning("runner.hold_released.no_route", enrolment_id=str(enrolment_id),
                    round_id=str(round_id))
        return RunnerOutcome(action="advanced", enrolment_id=str(enrolment_id),
                             reason="hold released — the round no longer exists, "
                                    "so the candidate did not move")
    route = route_after_result(round_, passed=True, percent=None)
    return await _advance(
        db, enrolment=released, round_=round_, workflow=workflow, route=route,
        why=f"hold released by a reviewer: {reason}" if reason else "hold released by a reviewer",
    )


async def decision_queue(
    db: AsyncSession, *, company_id: uuid.UUID, requisition_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Everyone awaiting a person — advanced, held, AND mid-workflow reviews.

    Deliberately one list. Held candidates buried behind a filter would make
    "every candidate reaches a human decision" true on paper and false in
    practice, so they are returned alongside those who completed, with the
    reason they stopped.

    A candidate parked on a ``human_review`` round belongs here for the same
    reason and was missing: the runner moved them onto the round, notified the
    workflow's owner, and then they sat with a current round set and no hold —
    invisible to the only screen built to act on them. They come back with
    ``awaiting_review`` and the round's own competencies, which are the
    reviewer's checklist (C3/C4).
    """
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.status, e.held_reason, e.held_at, e.ats_overall,"
                "       e.applicant_id, e.ats_recommendation, e.ats_summary,"
                "       e.ats_strengths, e.ats_concerns,"
                "       w.version AS workflow_version,"
                "       cur.title AS current_round_title,"
                "       cur.position AS current_round_position,"
                "       (SELECT count(*) FROM workflow_rounds x"
                "         WHERE x.workflow_id = e.workflow_id AND x.deleted_at IS NULL"
                "       ) AS total_rounds,"
                "       EXTRACT(EPOCH FROM (NOW() - enrolment_state_since(e.id, e.created_at)))"
                "         / 86400.0 AS waiting_days,"
                "       (SELECT avg(rr.percent) FROM round_results rr"
                "         WHERE rr.enrolment_id = e.id AND rr.superseded_at IS NULL"
                "           AND rr.percent IS NOT NULL) AS composite_percent,"
                "       COALESCE(("
                "           SELECT json_agg(json_build_object("
                "                      'round_id', rr.round_id, 'title', xr.title,"
                "                      'position', xr.position, 'kind', xr.kind,"
                "                      'percent', rr.percent, 'passed', rr.passed,"
                "                      'graded_by', rr.graded_by)"
                "                  ORDER BY xr.position, rr.created_at)"
                "             FROM round_results rr"
                "             JOIN workflow_rounds xr ON xr.id = rr.round_id"
                "            WHERE rr.enrolment_id = e.id AND rr.superseded_at IS NULL"
                "       ), '[]'::json) AS round_results,"
                "       a.full_name, a.email,"
                "       wr.id AS review_round_id, wr.title AS review_round_title,"
                "       COALESCE(("
                "           SELECT json_agg(json_build_object("
                "                      'competency_id', rc.competency_id,"
                "                      'name', rc.competency_name,"
                "                      'weight', rc.weight)"
                "                  ORDER BY rc.weight DESC)"
                "             FROM round_criteria rc WHERE rc.round_id = wr.id"
                "       ), '[]'::json) AS review_criteria,"
                "       (SELECT count(*) FROM round_results rr"
                "         WHERE rr.enrolment_id = e.id AND rr.superseded_at IS NULL) AS rounds_taken,"
                "       (SELECT max(rr.percent) FROM round_results rr"
                "         WHERE rr.enrolment_id = e.id AND rr.superseded_at IS NULL) AS best_percent,"
                "       ts.task_submission"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL"
                "  LEFT JOIN workflows w ON w.id = e.workflow_id"
                "  LEFT JOIN workflow_rounds cur ON cur.id = e.current_round_id"
                # PH4-D4: a task round (job_simulation/portfolio) is a person's
                # decision exactly like human_review — :human_kinds is the
                # three-member set app.workflows.HUMAN_EVALUATED_KINDS.
                "  LEFT JOIN workflow_rounds wr ON wr.id = e.current_round_id"
                "                              AND wr.kind = ANY(CAST(:human_kinds AS text[]))"
                "                              AND wr.deleted_at IS NULL"
                "  LEFT JOIN LATERAL ("
                "      SELECT json_build_object('status', s.status, 'due_at', s.due_at,"
                "                                'submitted_at', s.submitted_at) AS task_submission"
                "        FROM task_submissions s"
                "       WHERE s.enrolment_id = e.id AND s.round_id = e.current_round_id"
                "         AND s.superseded_at IS NULL"
                "       ORDER BY s.created_at DESC LIMIT 1"
                "  ) ts ON cur.kind = ANY(CAST(:task_kinds AS text[]))"
                " WHERE e.company_id = :c AND e.requisition_id = :r"
                "   AND e.deleted_at IS NULL"
                # One definition, shared with the watcher and the requisition
                # counts (migration a1c3e5f7b9d2). The inline version this
                # replaces also took every new or shortlisted applicant with no
                # round yet, and the queue told HR they had finished the workflow.
                "   AND enrolment_awaits_human(e.status, e.current_round_id)"
                " ORDER BY (e.status = 'held') DESC, e.ats_overall DESC NULLS LAST"
            ),
            {"c": company_id, "r": requisition_id,
             "human_kinds": list(HUMAN_EVALUATED_KINDS), "task_kinds": list(TASK_KINDS)},
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
            "applicant_id": str(r["applicant_id"]),
            "ats_overall": r["ats_overall"],
            # The application's own resume match, with what produced it.
            "ats_recommendation": r["ats_recommendation"],
            "ats_summary": r["ats_summary"],
            "ats_strengths": _as_json(r["ats_strengths"]) or [],
            "ats_concerns": _as_json(r["ats_concerns"]) or [],
            # Where they are: the version they are running and the round, if any.
            "workflow_version": r["workflow_version"],
            "current_round_title": r["current_round_title"],
            "current_round_position": r["current_round_position"],
            "total_rounds": int(r["total_rounds"] or 0),
            # Since their last move, from the stage ledger.
            "waiting_days": (
                round(float(r["waiting_days"]), 1) if r["waiting_days"] is not None else None
            ),
            # The mean of their scored rounds. Labelled as exactly that on screen:
            # it summarises, it does not decide (D-05).
            "composite_percent": (
                round(float(r["composite_percent"]), 1)
                if r["composite_percent"] is not None
                else None
            ),
            "round_results": [
                {**x, "percent": float(x["percent"]) if x.get("percent") is not None else None}
                for x in (_as_json(r["round_results"]) or [])
            ],
            "rounds_taken": int(r["rounds_taken"] or 0),
            "best_percent": float(r["best_percent"]) if r["best_percent"] is not None else None,
            # Waiting on a review round rather than on the final decision.
            #
            # A HELD candidate keeps their place, so they are still sitting on
            # the round — but "held" is the stronger statement and carries its
            # own actions (release, or decide). Saying both would offer a
            # reviewer two different next steps for one person.
            "awaiting_review": r["review_round_id"] is not None and r["status"] != "held",
            "review_round_id": (
                str(r["review_round_id"])
                if r["review_round_id"] and r["status"] != "held"
                else None
            ),
            "review_round_title": r["review_round_title"] if r["status"] != "held" else None,
            "review_criteria": (
                _as_json(r["review_criteria"])
                if r["review_round_id"] and r["status"] != "held"
                else []
            ),
            # PH4-D4: the submission's own state, when the candidate is
            # sitting on a job_simulation/portfolio round — so the queue
            # tells "not started" apart from "submitted, awaiting review"
            # rather than showing both as the same bare "awaiting_review".
            "task_submission": (
                _as_json(r["task_submission"])
                if r["review_round_id"] and r["status"] != "held"
                else None
            ),
        }
        for r in rows
    ]


def _as_json(value: Any) -> Any:
    """A json/jsonb column as Python. The driver may hand these back as text."""
    import json  # noqa: PLC0415 — only needed on this read path

    return json.loads(value) if isinstance(value, str) else value


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
