"""HR hiring pipeline + analytics + hire/reject decision — HR workflow Phase 4.

One place to see each applicant's whole funnel (resume ATS → exam → interview →
decision) and company-level analytics, plus the terminal hire/reject action.

MULTI-TENANT: every query is company-scoped (reuses get_hr_company/HrCtxDep). The
scorecards table has no company_id and no cross-service FK, so it is reached ONLY
via THIS company's interview-invite session_id (globally unique) — a foreign
scorecard can never attach to a local applicant.

READ paths are pure (no writes on GET): the 'interviewed' stage is a DERIVED
display value computed in SQL, never persisted on read.
PII: the pipeline/analytics rows never expose resume text, JD, exam answer keys,
or scorecard summaries — only scores/statuses/ids.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.models import AuditLog
from app.requisitions import ambiguous_decision_detail, choose_application, record_transition
from app.routers.hr_applicants import (
    ApplicantOut,
    _get_owned,
    _to_out,
    email_applicant_decision,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-pipeline"])

_VALID_DECISIONS = {"hired", "rejected"}
_VALID_STAGES = {"all", "shortlisted", "exam_passed", "interviewed", "decided"}
_VALID_STATUS_FILTERS = {"new", "shortlisted", "rejected", "interviewed", "hired", "held"}
_PIPELINE_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PipelineRow(BaseModel):
    """One APPLICATION (B5), read from the application_progress view.

    A person with two applications is two rows, each with its own status,
    score, exam and interview. ``enrolment_id`` is None only for someone filed
    under no opening; decisions on a row send it back so they land on that
    application rather than on whichever one the person-level fields last
    described.
    """

    applicant_id: str
    enrolment_id: str | None = None
    requisition_id: str | None = None
    opening_title: str | None = None
    full_name: str
    target_job_title: str
    target_level: str
    status: str  # DERIVED display value (not persisted on read)
    ats_overall: int | None
    ats_recommendation: str | None
    best_exam_percent: int | None
    exam_passed: bool | None
    total_exam_attempts: int
    interview_status: str | None
    interview_score: float | None
    scorecard_id: str | None
    updated_at: str


class PipelineResponse(BaseModel):
    items: list[PipelineRow]
    count: int
    limit: int
    offset: int


class HrFunnel(BaseModel):
    """Counts of APPLICATIONS by where they are now (B5), except
    ``total_applicants``, which is people. Someone shortlisted for one opening
    and rejected for another is one shortlist and one rejection — it used to be
    whichever of the two wrote the person's row last."""

    total_applicants: int
    total_applications: int = 0
    shortlisted: int
    exam_taken: int
    exam_passed: int
    interview_invited: int
    interview_completed: int
    hired: int
    rejected: int


class HrAverages(BaseModel):
    avg_ats: float | None
    avg_exam_percent: float | None
    avg_interview_composite: float | None


class HrOpenings(BaseModel):
    """The headline counts. Openings are counted by state, not summed — an
    opening that is closed is not hiring, and a single "openings" number that
    includes it answers the wrong question."""

    open: int = 0
    paused: int = 0
    closed: int = 0


class HrVelocity(BaseModel):
    """How long hiring takes, and whether it is speeding up.

    ``median_time_to_hire_days`` is a MEDIAN, deliberately. One candidate who
    sat in a pipeline for eight months drags a mean somewhere no real hire ever
    was, and the number is read as "how long should I expect this to take".

    None means nobody has been hired yet. Not zero — zero is a claim about
    speed, and "no data" is not a fast hire.
    """

    median_time_to_hire_days: float | None = None
    hires_measured: int = 0
    applications_last_7d: int = 0
    applications_prev_7d: int = 0


class HrConversion(BaseModel):
    """Stage-to-stage conversion, as percentages of the stage before.

    Two things had to be got right here, and both were wrong first time.

    THE SOURCE. Read from the transition LEDGER, not from ``HrFunnel``. The
    funnel counts applicants by current status, so somebody shortlisted and
    then hired has left "shortlisted" — dividing one of those fields by another
    produced 175%, which is the arithmetic saying the stages are a snapshot
    rather than a sequence.

    THE DENOMINATOR. Every rate is a share of APPLICATIONS rather than of the
    stage before it. Stage-to-stage assumes a chain, and this product does not
    enforce one: HR can assign an exam by hand, outside any workflow, so more
    people can sit an exam than were ever shortlisted. That is a legitimate
    route through the product, not bad data, and a "shortlist → exam" rate
    would report it as an impossibility.

    Computed here rather than in the client so the definition lives in one
    place, and the raw counts ship alongside because a percentage with no
    denominator beside it is unreadable at small numbers — "50%" out of two
    candidates and out of two hundred are different facts.

    None where nobody has applied — a rate out of nothing is not 0%.

    The counts are also exposed, because a percentage with no denominator
    beside it is unreadable at small numbers: "50%" out of two candidates and
    out of two hundred are different facts.
    """

    applied: int = 0
    ever_shortlisted: int = 0
    ever_sat_exam: int = 0
    ever_interviewed: int = 0
    ever_hired: int = 0

    # Each as a share of APPLICATIONS, not of the stage before. Named so it
    # cannot be misread: pct_shortlisted is "of everyone who applied", which is
    # how a funnel chart is read and the only denominator that stays valid when
    # a candidate skips a stage.
    pct_shortlisted: float | None = None
    pct_sat_exam: float | None = None
    pct_interviewed: float | None = None
    pct_hired: float | None = None


class HrAnalytics(BaseModel):
    funnel: HrFunnel
    averages: HrAverages
    openings: HrOpenings = Field(default_factory=HrOpenings)
    velocity: HrVelocity = Field(default_factory=HrVelocity)
    conversion: HrConversion = Field(default_factory=HrConversion)


class DecisionIn(BaseModel):
    decision: str
    rationale: str | None = Field(default=None, max_length=2000)
    # The application decided on — the board sends the row's enrolment_id.
    # Optional so an older client still works for someone with one application.
    enrolment_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Pipeline (paginated, READ-ONLY)
# ---------------------------------------------------------------------------
# One row per application, from the application_progress view (migration
# e2a4c6b8d0f1), which is the single definition of an application's status,
# score, exam and interview — the copilot and the watchers read the same view,
# so they cannot disagree with this board. 'interviewed' is derived there
# (CASE), never written. status/stage filters + pagination are in SQL; count is
# the filtered total via a window COUNT(*) OVER ().
_PIPELINE_SQL = text(
    """
SELECT *, COUNT(*) OVER () AS total_count
FROM application_progress
WHERE company_id = :cid
  AND (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))
  AND (
    CAST(:stage AS text) IS NULL
    OR (CAST(:stage AS text) = 'all')
    OR (CAST(:stage AS text) = 'shortlisted' AND status = 'shortlisted')
    OR (CAST(:stage AS text) = 'exam_passed' AND exam_passed IS TRUE)
    OR (CAST(:stage AS text) = 'interviewed' AND scorecard_id IS NOT NULL)
    OR (CAST(:stage AS text) = 'decided'     AND status IN ('hired','rejected'))
  )
ORDER BY ats_overall DESC NULLS LAST, updated_at DESC
LIMIT :limit OFFSET :offset
"""
)


@router.get("/pipeline", response_model=PipelineResponse)
async def get_pipeline(
    ctx: HrCtxDep,
    db: DbSessionDep,
    stage: Annotated[str | None, Query()] = None,
    status_f: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=_PIPELINE_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PipelineResponse:
    """Per-applicant funnel rollup for the caller's company. Pure read."""
    _hr_uid, company_id = ctx
    if stage is not None and stage not in _VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"stage must be one of {sorted(_VALID_STAGES)}")
    if status_f is not None and status_f not in _VALID_STATUS_FILTERS:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(_VALID_STATUS_FILTERS)}"
        )

    rows = (
        await db.execute(
            _PIPELINE_SQL,
            {"cid": company_id, "status": status_f, "stage": stage, "limit": limit, "offset": offset},
        )
    ).mappings().all()

    count = int(rows[0]["total_count"]) if rows else 0
    items = [
        PipelineRow(
            applicant_id=str(r["applicant_id"]),
            enrolment_id=str(r["enrolment_id"]) if r["enrolment_id"] else None,
            requisition_id=str(r["requisition_id"]) if r["requisition_id"] else None,
            opening_title=r["opening_title"],
            full_name=r["full_name"],
            target_job_title=r["target_job_title"],
            target_level=r["target_level"],
            status=r["status"],
            ats_overall=r["ats_overall"],
            ats_recommendation=r["ats_recommendation"],
            best_exam_percent=int(r["best_exam_percent"]) if r["best_exam_percent"] is not None else None,
            exam_passed=r["exam_passed"],
            total_exam_attempts=int(r["total_exam_attempts"]),
            interview_status=r["interview_status"],
            interview_score=float(r["interview_score"]) if r["interview_score"] is not None else None,
            scorecard_id=str(r["scorecard_id"]) if r["scorecard_id"] else None,
            updated_at=r["updated_at"].isoformat(),
        )
        for r in rows
    ]
    return PipelineResponse(items=items, count=count, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Analytics (funnel + averages)
# ---------------------------------------------------------------------------
# Applications, from the same view as the board — so "Shortlisted 4" here is
# the four shortlisted cards there. Filed-under-no-opening people count as a
# person but not as an application (they have not applied to anything).
_FUNNEL_SQL = text(
    """
SELECT
  COUNT(DISTINCT applicant_id)                                        AS total_applicants,
  COUNT(*) FILTER (WHERE enrolment_id IS NOT NULL)                    AS total_applications,
  COUNT(*) FILTER (WHERE stored_status = 'shortlisted')               AS shortlisted,
  COUNT(*) FILTER (WHERE stored_status = 'hired')                     AS hired,
  COUNT(*) FILTER (WHERE stored_status = 'rejected')                  AS rejected,
  COUNT(*) FILTER (WHERE total_exam_attempts > 0)                     AS exam_taken,
  COUNT(*) FILTER (WHERE exam_passed IS TRUE)                         AS exam_passed,
  COUNT(*) FILTER (WHERE ever_invited)                                AS interview_invited,
  COUNT(*) FILTER (WHERE scorecard_id IS NOT NULL)                    AS interview_completed
FROM application_progress
WHERE company_id = :cid
"""
)

_OPENINGS_SQL = text(
    """
SELECT status, COUNT(*) AS n
FROM job_requisitions
WHERE company_id = :cid AND deleted_at IS NULL
GROUP BY status
"""
)

# Time from the application landing to the hire being recorded, per hired
# candidate. Read off the transition ledger rather than from
# updated_at: the ledger is what actually records when each move happened,
# and updated_at moves for reasons that have nothing to do with a stage.
_VELOCITY_SQL = text(
    """
WITH hires AS (
    SELECT e.id,
           MIN(t.occurred_at) FILTER (WHERE t.to_status = 'new')   AS applied_at,
           MIN(t.occurred_at) FILTER (WHERE t.to_status = 'hired') AS hired_at
    FROM enrolments e
    JOIN stage_transitions t ON t.enrolment_id = e.id
    WHERE e.company_id = :cid AND e.deleted_at IS NULL
    GROUP BY e.id
)
SELECT
  PERCENTILE_CONT(0.5) WITHIN GROUP (
      ORDER BY EXTRACT(EPOCH FROM (hired_at - applied_at)) / 86400.0
  ) FILTER (WHERE hired_at IS NOT NULL AND applied_at IS NOT NULL) AS median_days,
  COUNT(*) FILTER (WHERE hired_at IS NOT NULL AND applied_at IS NOT NULL) AS hires_measured
FROM hires
"""
)

# Cumulative-ever, off the transition ledger. Scoped to ENROLMENTS, which is
# what a stage is a property of — an applicant HR uploaded by hand has no
# enrolment and no stages, so including them would put people in the
# denominator who were never in the process being measured.
_CONVERSION_SQL = text(
    """
WITH mine AS (
    SELECT id, applicant_id FROM enrolments
    WHERE company_id = :cid AND deleted_at IS NULL
),
reached AS (
    SELECT m.id,
           bool_or(t.to_status = 'shortlisted') AS ever_shortlisted,
           bool_or(t.to_status = 'interviewed') AS ever_interviewed,
           bool_or(t.to_status = 'hired')       AS ever_hired
    FROM mine m
    LEFT JOIN stage_transitions t ON t.enrolment_id = m.id
    GROUP BY m.id
),
sat_exam AS (
    SELECT DISTINCT m.id
    FROM mine m
    JOIN exam_attempts ea ON ea.applicant_id = m.applicant_id
    WHERE ea.status = 'submitted' AND ea.deleted_at IS NULL
)
SELECT
  (SELECT COUNT(*) FROM mine)                                        AS applied,
  COUNT(*) FILTER (WHERE r.ever_shortlisted)                         AS shortlisted,
  (SELECT COUNT(*) FROM sat_exam)                                    AS sat_exam,
  COUNT(*) FILTER (WHERE r.ever_interviewed)                         AS interviewed,
  COUNT(*) FILTER (WHERE r.ever_hired)                               AS hired
FROM reached r
"""
)

# "Applications in the last 7 days" counts applications, by when each was made
# — a returning candidate's second application is new this week even though
# the person is not.
_RECENT_SQL = text(
    """
SELECT
  COUNT(*) FILTER (WHERE applied_at >= now() - interval '7 days')  AS last_7d,
  COUNT(*) FILTER (WHERE applied_at >= now() - interval '14 days'
                     AND applied_at <  now() - interval '7 days')  AS prev_7d
FROM application_progress
WHERE company_id = :cid AND enrolment_id IS NOT NULL
"""
)

# Averaged over applications: a CV scores differently against different roles,
# so a person's "ATS score" is not one number any more.
_AVERAGES_SQL = text(
    """
SELECT
  AVG(ats_overall)       AS avg_ats,
  AVG(best_exam_percent) AS avg_exam_percent,
  AVG(interview_score)   AS avg_interview_composite
FROM application_progress
WHERE company_id = :cid
"""
)


def _round2(x: Any) -> float | None:
    return round(float(x), 2) if x is not None else None


@router.get("/analytics", response_model=HrAnalytics)
async def get_analytics(ctx: HrCtxDep, db: DbSessionDep) -> HrAnalytics:
    """Company-scoped funnel counts + averages. NULL-safe (empty company → zeros/None)."""
    _hr_uid, company_id = ctx
    f = (await db.execute(_FUNNEL_SQL, {"cid": company_id})).mappings().one()
    avg = (await db.execute(_AVERAGES_SQL, {"cid": company_id})).mappings().one()
    openings = {
        r["status"]: int(r["n"])
        for r in (await db.execute(_OPENINGS_SQL, {"cid": company_id})).mappings().all()
    }
    vel = (await db.execute(_VELOCITY_SQL, {"cid": company_id})).mappings().one()
    conv = (await db.execute(_CONVERSION_SQL, {"cid": company_id})).mappings().one()
    recent = (await db.execute(_RECENT_SQL, {"cid": company_id})).mappings().one()

    def _rate(part: int, whole: int) -> float | None:
        """A percentage, or None when there is nothing to divide by.

        Zero would be a claim — "nobody converted" — and an empty funnel has
        not made that claim.
        """
        return round(100.0 * part / whole, 1) if whole else None

    return HrAnalytics(
        funnel=HrFunnel(
            total_applicants=int(f["total_applicants"]),
            total_applications=int(f["total_applications"] or 0),
            shortlisted=int(f["shortlisted"]),
            exam_taken=int(f["exam_taken"]),
            exam_passed=int(f["exam_passed"]),
            interview_invited=int(f["interview_invited"]),
            interview_completed=int(f["interview_completed"]),
            hired=int(f["hired"]),
            rejected=int(f["rejected"]),
        ),
        averages=HrAverages(
            avg_ats=_round2(avg["avg_ats"]),
            avg_exam_percent=_round2(avg["avg_exam_percent"]),
            avg_interview_composite=_round2(avg["avg_interview_composite"]),
        ),
        openings=HrOpenings(
            open=openings.get("open", 0),
            paused=openings.get("paused", 0),
            closed=openings.get("closed", 0),
        ),
        velocity=HrVelocity(
            median_time_to_hire_days=_round2(vel["median_days"]),
            hires_measured=int(vel["hires_measured"] or 0),
            applications_last_7d=int(recent["last_7d"] or 0),
            applications_prev_7d=int(recent["prev_7d"] or 0),
        ),
        conversion=HrConversion(
            applied=int(conv["applied"] or 0),
            ever_shortlisted=int(conv["shortlisted"] or 0),
            ever_sat_exam=int(conv["sat_exam"] or 0),
            ever_interviewed=int(conv["interviewed"] or 0),
            ever_hired=int(conv["hired"] or 0),
            pct_shortlisted=_rate(
                int(conv["shortlisted"] or 0), int(conv["applied"] or 0)
            ),
            pct_sat_exam=_rate(int(conv["sat_exam"] or 0), int(conv["applied"] or 0)),
            pct_interviewed=_rate(
                int(conv["interviewed"] or 0), int(conv["applied"] or 0)
            ),
            pct_hired=_rate(int(conv["hired"] or 0), int(conv["applied"] or 0)),
        ),
    )


# ---------------------------------------------------------------------------
# Decision (hire / reject) — terminal, audited
# ---------------------------------------------------------------------------
@router.post("/applicants/{applicant_id}/decision", response_model=ApplicantOut)
async def decide_applicant(
    applicant_id: uuid.UUID,
    body: DecisionIn,
    request: Request,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> ApplicantOut:
    """Record a hire/reject decision on an applicant (company-scoped, audited)."""
    hr_uid, company_id = ctx
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(
            status_code=400, detail=f"decision must be one of {sorted(_VALID_DECISIONS)}"
        )

    a = await _get_owned(db, company_id, applicant_id)  # 404 cross-tenant

    # B2/B5: a decision is about ONE application and goes through the ledger.
    # The board is one row per application and names it. Without a name, one
    # application is unambiguous; several are refused rather than guessed — a
    # terminal decision is D-05's to make deliberately.
    try:
        app_ = await choose_application(
            db, applicant_id=a.id, company_id=company_id, enrolment_id=body.enrolment_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found.") from exc
    if app_.ambiguous:
        raise HTTPException(
            status_code=409, detail=ambiguous_decision_detail(a.full_name, len(app_.live))
        )

    # Guarded on the application's own status — the person's row describes
    # only their latest application, which may not be this one.
    current = app_.status if app_.enrolment_id is not None else a.status
    if body.decision == "hired":
        if current == "hired":
            raise HTTPException(status_code=409, detail="Applicant is already hired.")
        if current not in ("shortlisted", "interviewed"):
            raise HTTPException(
                status_code=409,
                detail="Only shortlisted or interviewed applicants can be hired.",
            )
    # 'rejected' is reachable from any non-terminal state AND from 'hired'
    # (an audited reversal — details.reversal=true).

    now = datetime.now(tz=UTC)
    prev = current
    # The person-level mirror moves only when this is the application it
    # mirrors (their latest), or when they have no application at all.
    if app_.enrolment_id is None or app_.is_latest:
        a.status = body.decision
        a.updated_at = now
    if app_.enrolment_id is not None:
        await record_transition(
            db,
            enrolment_id=app_.enrolment_id,
            company_id=company_id,
            to_status=body.decision,
            actor_user_id=hr_uid,
            automated=False,
            reason=body.rationale or f"{body.decision} from the pipeline board",
        )

    db.add(
        AuditLog(
            actor_id=hr_uid,
            actor_type="user",
            action=f"applicant.decision.{body.decision}",
            resource_type="applicant",
            resource_id=applicant_id,
            details={
                "company_id": str(company_id),
                "enrolment_id": str(app_.enrolment_id) if app_.enrolment_id else None,
                "decision": body.decision,
                "previous_status": prev,
                "rationale": body.rationale,
                "reversal": prev == "hired",
            },
            ip_address=extract_client_ip(request),
            user_agent=extract_user_agent(request),
            event_ts=now,
        )
    )
    # Email the candidate their hire/reject decision (staged on this transaction →
    # atomic with the decision + audit row, then delivered by the outbox worker).
    if body.decision != prev:
        await email_applicant_decision(
            db, applicant=a, decision=body.decision, company_id=company_id,
            job_title=app_.title,
        )
    await db.commit()
    log.info(
        "hr.applicant.decided",
        applicant_id=str(applicant_id),
        company_id=str(company_id),
        decision=body.decision,
        previous=prev,
    )
    return _to_out(a)
