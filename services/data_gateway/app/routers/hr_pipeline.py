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
from app.decision_reasons import ReasonError
from app.decision_reasons import resolve as resolve_reason
from app.dependencies import HrCtxDep
from app.interviewer_scorecards import RequestMeta
from app.metrics.compute import CohortWindow, FunnelFilters, compute_funnel
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
    # PH4-A3 — offer_accepted | offer_declined | offer_expired | offer_withdrawn.
    offer_outcome: str | None = None


class PipelineResponse(BaseModel):
    items: list[PipelineRow]
    count: int
    limit: int
    offset: int


class HrFunnel(BaseModel):
    """Counts of APPLICATIONS by where they are now (B5), except
    ``total_applicants``, which is people. Someone shortlisted for one opening
    and rejected for another is one shortlist and one rejection — it used to be
    whichever of the two wrote the person's row last.

    GOVERNED (PH5 Wave 1 close-out, C2-5/C1-9): ``total_applications``,
    ``exam_taken``, ``interview_completed`` and ``hired`` are no longer this
    router's own count over ``application_progress`` — they ARE the governed
    metrics ``HrConversion``/``HrAnalytics.definitions`` already name
    (``applications@1``, ``assessed@1``, ``interviewed@1``, ``hires@1``
    respectively), read off the SAME all-time ``application``-cohort
    ``compute_funnel`` call ``conversion`` already makes (no second query).
    This closes the exact defect the acceptance criteria named:
    ``interview_completed`` used to count only a completed AI scorecard
    (``scorecard_id IS NOT NULL`` on ``application_progress``), so the HR
    console's "Interviewed" tile showed a different number from the Analytics
    page's "Interviewed", which already counted a submitted human interviewer
    scorecard too — both now read ``interviewed@1`` and cannot disagree.
    ``exam_taken`` similarly now also counts a ``round_results`` row with no
    exam attempt behind it (``assessed@1``'s definition), which the old
    ``total_exam_attempts > 0`` count missed. ``hired`` was already
    numerically identical to ``hires@1`` for real data (see
    ``test_cross_consumer_consistency``) and is now computed FROM it
    directly, rather than a second, independently-written expression that
    happened to agree.

    UNGOVERNED, deliberately, because no governed metric matches what these
    ask: ``total_applicants`` counts PEOPLE, not applications, and a governed
    metric is always over applications; ``shortlisted``/``rejected`` are
    CURRENT STATUS — where an application sits right now — not a cumulative
    "ever" count, and nothing in the registry describes a snapshot;
    ``exam_passed`` has no governed pass/fail metric (only ``assessed`` —
    that someone sat an exam at all); ``interview_invited`` has no governed
    definition of what an "invite" is. These five stay on the
    ``application_progress`` view, exactly as before.
    """

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

    GOVERNED (PH5-C2): ``median_time_to_hire_days``/``hires_measured`` are now
    the ``time_to_hire_days@1`` metric (hire cohort) — its population is only
    hires that stand, where the ledger-only version this replaces counted any
    move to hired, including one later reversed. ``applications_last_7d``/
    ``applications_prev_7d`` are unchanged (ungoverned; see
    ``requisition_dashboard``'s docstring for the reasoning that applies here
    too — a rolling window is an operational count, not a governed metric).

    ``None`` STILL means only "nobody has been hired yet" (a zero
    population) — never small-cell suppression (lead policy decision,
    C2-13). ``time_to_hire_days`` reads no check-in flag, so a company with a
    handful of hires (below the registry's ``rule:min_cell`` floor) sees its
    REAL median here, exactly as before this metric was governed; nulling on
    smallness is reserved for a check-in OUTCOME metric (``retention_90d``,
    ``performance_90d`` — neither exposed on this model), never an ordinary
    hiring number. See ``app.metrics.compute._read_metric_value``.
    """

    median_time_to_hire_days: float | None = None
    hires_measured: int = 0
    applications_last_7d: int = 0
    applications_prev_7d: int = 0


class HrConversion(BaseModel):
    """Stage-to-stage conversion, as percentages of APPLICATIONS (not of the
    stage before — a "shortlist → exam" rate would report as an impossibility
    the legitimate route where HR assigns an exam by hand, outside any
    workflow, to more people than were ever shortlisted).

    GOVERNED (PH5-C2). Every field here is now a metric from
    ``app.metrics.definitions`` — ``applied`` is ``applications@1``,
    ``ever_shortlisted`` is ``screened@1``, ``ever_sat_exam`` is
    ``assessed@1``, ``ever_interviewed`` is ``interviewed@1``, ``ever_hired``
    is ``hires@1``, and each ``pct_*`` is the matching ``application_to_*@1``
    rate — see ``HrAnalytics.definitions`` for the exact mapping and
    ``GET /hr/metrics/definitions`` for what each one means. Three meanings
    changed from the ledger-based version this replaces (each metric's
    ``change_note`` has the detail):

    * ``ever_interviewed`` is now an AI session or a submitted human
      interviewer scorecard, not the ledger's ``interviewed`` status (which
      means "finished the workflow, awaiting a decision");
    * ``ever_sat_exam`` no longer bleeds an attempt across every application
      the same person holds;
    * ``ever_hired`` counts only a hire that stands (not one later reversed).

    None where nobody has applied — a rate out of nothing is not 0%. The raw
    counts ship alongside every percentage because "50%" out of two
    candidates and out of two hundred are different facts.

    ``None`` never means "too few to show": none of these fields reads a
    check-in flag, so small-cell suppression (lead policy decision, C2-13)
    never nulls a value or a count here — a small company's real numbers are
    shown, same as before this block was governed. See
    ``app.metrics.compute._read_metric_value``.
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
    # PH5-C2: which governed metric (name@version) computed each conversion/
    # velocity field, and the hash of the whole locked registry that produced
    # them — "How is this calculated?" reads this rather than a hard-coded
    # explanation that can drift from what actually ran.
    definitions: dict[str, str] = Field(default_factory=dict)
    registry_hash: str = ""


class DecisionIn(BaseModel):
    decision: str
    rationale: str | None = Field(default=None, max_length=2000)
    # PH4-O4 — required: the pipeline board records final decisions too, and a
    # category captured on one decision path but not the other would make every
    # "why do we reject?" figure quietly incomplete.
    reason_code: str | None = Field(default=None, max_length=64)
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
SELECT *,
       -- PH4-A3: the offer's outcome sits beside the decision on the
       -- application, not in the shared view (the copilot reads that).
       (SELECT e.offer_outcome FROM enrolments e
         WHERE e.id = application_progress.enrolment_id) AS offer_outcome,
       COUNT(*) OVER () AS total_count
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
            offer_outcome=r["offer_outcome"],
        )
        for r in rows
    ]
    return PipelineResponse(items=items, count=count, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Analytics (funnel + averages)
# ---------------------------------------------------------------------------
# PH5 Wave 1 close-out (C2-5/C1-9): only the UNGOVERNED fields are read here
# now — total_applications/exam_taken/interview_completed/hired moved to the
# governed compute_funnel call get_analytics already makes for `conversion`
# (see HrFunnel's docstring for exactly which four and why). Filed-under-no-
# opening people count as a person but not as an application (they have not
# applied to anything).
_FUNNEL_SQL = text(
    """
SELECT
  COUNT(DISTINCT applicant_id)                          AS total_applicants,
  COUNT(*) FILTER (WHERE stored_status = 'shortlisted') AS shortlisted,
  COUNT(*) FILTER (WHERE stored_status = 'rejected')    AS rejected,
  COUNT(*) FILTER (WHERE exam_passed IS TRUE)           AS exam_passed,
  COUNT(*) FILTER (WHERE ever_invited)                  AS interview_invited
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

# PH5-C2: velocity's median/hires-measured and every `conversion` field used
# to be computed here (the ledger's `_VELOCITY_SQL`/`_CONVERSION_SQL`, since
# removed — see tests/unit/test_analytics_rollup.py for what changed and
# why). They are now `app.metrics.compute.compute_funnel`, over the
# `application` cohort (conversion) and the `hire` cohort
# (time_to_hire_days) — the same governed metrics `/hr/analytics/funnel`,
# the copilot and the watcher read, so this board cannot disagree with any
# of them about what "interviewed" or "hired" means (the cross-consumer
# consistency test).
_CONVERSION_METRIC_MAP: dict[str, str] = {
    "applied": "applications", "ever_shortlisted": "screened",
    "ever_sat_exam": "assessed", "ever_interviewed": "interviewed",
    "ever_hired": "hires",
}
_CONVERSION_RATE_MAP: dict[str, str] = {
    "pct_shortlisted": "application_to_screen", "pct_sat_exam": "application_to_assess",
    "pct_interviewed": "application_to_interview", "pct_hired": "application_to_hire",
}

# PH5 Wave 1 close-out (C2-5/C1-9): the four HrFunnel fields that have a
# governed equivalent, read off the SAME `pipeline` (application-cohort)
# FunnelResult `conversion` already uses above — one query serves both. See
# HrFunnel's docstring for why these four moved and the other five did not.
_FUNNEL_METRIC_MAP: dict[str, str] = {
    "total_applications": "applications", "exam_taken": "assessed",
    "interview_completed": "interviewed", "hired": "hires",
}

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
    """Company-scoped funnel counts + averages. NULL-safe (empty company → zeros/None).

    ``funnel``'s UNGOVERNED fields (``total_applicants``, ``shortlisted``,
    ``rejected``, ``exam_passed``, ``interview_invited``) and ``averages``
    stay on the ``application_progress`` view. ``funnel``'s GOVERNED fields
    (``total_applications``, ``exam_taken``, ``interview_completed``,
    ``hired`` — PH5 Wave 1 close-out, C2-5/C1-9) and ``conversion``/
    ``velocity`` all come from the SAME ``pipeline``/``hire`` ``compute_funnel``
    calls below — no field here is computed twice. See ``HrFunnel``'s
    docstring for exactly which four fields moved and why.
    """
    _hr_uid, company_id = ctx
    f = (await db.execute(_FUNNEL_SQL, {"cid": company_id})).mappings().one()
    avg = (await db.execute(_AVERAGES_SQL, {"cid": company_id})).mappings().one()
    openings = {
        r["status"]: int(r["n"])
        for r in (await db.execute(_OPENINGS_SQL, {"cid": company_id})).mappings().all()
    }
    recent = (await db.execute(_RECENT_SQL, {"cid": company_id})).mappings().one()

    pipeline = await compute_funnel(
        db, company_id=company_id, cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(),
    )
    hire = await compute_funnel(
        db, company_id=company_id, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
    )
    pipeline_metrics = pipeline.groups[0].metrics
    hire_metrics = hire.groups[0].metrics
    time_to_hire = hire_metrics["time_to_hire_days"]

    definitions = {
        f"conversion.{field}": f"{metric}@1" for field, metric in _CONVERSION_METRIC_MAP.items()
    } | {
        f"conversion.{field}": f"{metric}@1" for field, metric in _CONVERSION_RATE_MAP.items()
    } | {
        f"funnel.{field}": f"{metric}@1" for field, metric in _FUNNEL_METRIC_MAP.items()
    } | {"velocity.median_time_to_hire_days": "time_to_hire_days@1",
         "velocity.hires_measured": "time_to_hire_days@1"}

    return HrAnalytics(
        funnel=HrFunnel(
            total_applicants=int(f["total_applicants"]),
            shortlisted=int(f["shortlisted"]),
            exam_passed=int(f["exam_passed"]),
            interview_invited=int(f["interview_invited"]),
            rejected=int(f["rejected"]),
            **{
                field: int(pipeline_metrics[metric]["value"] or 0)
                for field, metric in _FUNNEL_METRIC_MAP.items()
            },
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
            median_time_to_hire_days=time_to_hire["value"],
            hires_measured=int(time_to_hire["n"] or 0),
            applications_last_7d=int(recent["last_7d"] or 0),
            applications_prev_7d=int(recent["prev_7d"] or 0),
        ),
        conversion=HrConversion(
            **{
                field: int(pipeline_metrics[metric]["value"] or 0)
                for field, metric in _CONVERSION_METRIC_MAP.items()
            },
            **{
                field: pipeline_metrics[metric]["value"]
                for field, metric in _CONVERSION_RATE_MAP.items()
            },
        ),
        definitions=definitions,
        registry_hash=pipeline.registry_hash,
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

    # PH4-O4. After the guards above, before anything is written: a decision
    # blocked for its own reasons should say why ("already hired"), not complain
    # about a missing category — the order record_final_decision uses.
    try:
        chosen = await resolve_reason(
            db, company_id=company_id, code=body.reason_code, decision=body.decision,
            reason=body.rationale or "",
        )
    except ReasonError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

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
            reason_code=chosen.code,
            reason_label=chosen.label,
        )
        # PH4-A2: the same as a decision from the decision queue — interviews
        # not yet held are no longer needed, so the panel's calendar is freed.
        from app.interview_scheduling import close_for_decision  # noqa: PLC0415 — import cycle

        await close_for_decision(
            db, company_id=company_id, enrolment_id=app_.enrolment_id, actor=hr_uid,
            decision=body.decision,
        )
        # PH4-D4 M5, as app/final_decision.py does: an open task link must not
        # outlive the application. This board records its decision without
        # going through final_decision, so it has to close tasks itself —
        # otherwise a rejected candidate's draft was later auto-submitted by
        # the deadline sweep and they were emailed about it (NEW-4).
        from app.job_tasks import close_for_decision as close_tasks_for_decision  # noqa: PLC0415

        await close_tasks_for_decision(
            db, company_id=company_id, enrolment_id=app_.enrolment_id, actor=hr_uid,
            meta=RequestMeta(
                ip_address=extract_client_ip(request), user_agent=extract_user_agent(request),
            ),
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
                "reason_code": chosen.code,
                "reason_label": chosen.label,
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
