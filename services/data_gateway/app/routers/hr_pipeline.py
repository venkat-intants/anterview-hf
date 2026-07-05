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

from app.models import AuditLog
from app.routers.consent import _extract_client_ip, _extract_user_agent
from app.routers.hr_applicants import (
    ApplicantOut,
    DbSessionDep,
    HrCtxDep,
    _get_owned,
    _to_out,
    email_applicant_decision,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-pipeline"])

_VALID_DECISIONS = {"hired", "rejected"}
_VALID_STAGES = {"all", "shortlisted", "exam_passed", "interviewed", "decided"}
_VALID_STATUS_FILTERS = {"new", "shortlisted", "rejected", "interviewed", "hired"}
_PIPELINE_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PipelineRow(BaseModel):
    applicant_id: str
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
    total_applicants: int
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


class HrAnalytics(BaseModel):
    funnel: HrFunnel
    averages: HrAverages


class DecisionIn(BaseModel):
    decision: str
    rationale: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Pipeline (paginated, READ-ONLY)
# ---------------------------------------------------------------------------
# Single tenant-scoped statement. 'interviewed' is derived (CASE), never written.
# exam_passed = bool_or across submitted attempts (matches _has_passed_exam);
# best_exam_percent is an independent MAX. The interview/scorecard is the latest
# invite WITH a session (identical predicate to analytics, so a score never
# disagrees between screens). status/stage filters + pagination are in SQL; count
# is the filtered total via a window COUNT(*) OVER ().
_PIPELINE_SQL = text(
    """
WITH agg AS (
  SELECT
      a.id, a.full_name, a.target_job_title, a.target_level,
      a.ats_overall, a.ats_recommendation, a.updated_at,
      ea.best_exam_percent,
      ep.ever_passed AS exam_passed,
      COALESCE(ec.total_exam_attempts, 0) AS total_exam_attempts,
      li.interview_status,
      sc.composite_score AS interview_score,
      sc.scorecard_id,
      CASE
        WHEN a.status IN ('hired','rejected') THEN a.status
        WHEN sc.scorecard_id IS NOT NULL AND a.status IN ('new','shortlisted')
             THEN 'interviewed'
        ELSE a.status
      END AS status
  FROM applicants a
  LEFT JOIN LATERAL (
      SELECT t.score_percent AS best_exam_percent
      FROM exam_attempts t
      WHERE t.applicant_id = a.id AND t.company_id = a.company_id
        AND t.status = 'submitted' AND t.deleted_at IS NULL
      ORDER BY t.score_percent DESC NULLS LAST, t.submitted_at DESC
      LIMIT 1
  ) ea ON TRUE
  LEFT JOIN LATERAL (
      SELECT bool_or(t.passed) AS ever_passed
      FROM exam_attempts t
      WHERE t.applicant_id = a.id AND t.company_id = a.company_id
        AND t.status = 'submitted' AND t.deleted_at IS NULL
  ) ep ON TRUE
  LEFT JOIN LATERAL (
      SELECT COUNT(*) AS total_exam_attempts
      FROM exam_attempts t
      WHERE t.applicant_id = a.id AND t.company_id = a.company_id
        AND t.status = 'submitted' AND t.deleted_at IS NULL
  ) ec ON TRUE
  LEFT JOIN LATERAL (
      SELECT i.status AS interview_status, i.session_id
      FROM interview_invites i
      WHERE i.applicant_id = a.id AND i.company_id = a.company_id
        AND i.deleted_at IS NULL AND i.session_id IS NOT NULL
      ORDER BY i.created_at DESC
      LIMIT 1
  ) li ON TRUE
  LEFT JOIN scorecards sc ON sc.session_id = li.session_id
  WHERE a.company_id = :cid AND a.deleted_at IS NULL
)
SELECT *, COUNT(*) OVER () AS total_count
FROM agg
WHERE (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))
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
            applicant_id=str(r["id"]),
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
_FUNNEL_SQL = text(
    """
SELECT
  COUNT(DISTINCT a.id)                                             AS total_applicants,
  COUNT(DISTINCT a.id) FILTER (WHERE a.status = 'shortlisted')     AS shortlisted,
  COUNT(DISTINCT a.id) FILTER (WHERE a.status = 'hired')           AS hired,
  COUNT(DISTINCT a.id) FILTER (WHERE a.status = 'rejected')        AS rejected,
  COUNT(DISTINCT ea.applicant_id)                                  AS exam_taken,
  COUNT(DISTINCT ea.applicant_id) FILTER (WHERE ea.passed IS TRUE) AS exam_passed,
  COUNT(DISTINCT ii.applicant_id) FILTER (
      WHERE ii.status IN ('invited','consumed','completed'))       AS interview_invited,
  COUNT(DISTINCT ii.applicant_id) FILTER (WHERE sc.scorecard_id IS NOT NULL)
                                                                   AS interview_completed
FROM applicants a
LEFT JOIN exam_attempts ea
       ON ea.applicant_id = a.id AND ea.company_id = a.company_id
      AND ea.status = 'submitted' AND ea.deleted_at IS NULL
LEFT JOIN interview_invites ii
       ON ii.applicant_id = a.id AND ii.company_id = a.company_id
      AND ii.deleted_at IS NULL AND ii.status <> 'revoked'
LEFT JOIN scorecards sc ON sc.session_id = ii.session_id
WHERE a.company_id = :cid AND a.deleted_at IS NULL
"""
)

_AVERAGES_SQL = text(
    """
SELECT
  AVG(a.ats_overall)      AS avg_ats,
  AVG(best.best_pct)      AS avg_exam_percent,
  AVG(sc.composite_score) AS avg_interview_composite
FROM applicants a
LEFT JOIN LATERAL (
    SELECT MAX(t.score_percent) AS best_pct
    FROM exam_attempts t
    WHERE t.applicant_id = a.id AND t.company_id = a.company_id
      AND t.status = 'submitted' AND t.deleted_at IS NULL
) best ON TRUE
LEFT JOIN LATERAL (
    SELECT i.session_id FROM interview_invites i
    WHERE i.applicant_id = a.id AND i.company_id = a.company_id
      AND i.deleted_at IS NULL AND i.session_id IS NOT NULL
    ORDER BY i.created_at DESC LIMIT 1
) li ON TRUE
LEFT JOIN scorecards sc ON sc.session_id = li.session_id
WHERE a.company_id = :cid AND a.deleted_at IS NULL
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
    return HrAnalytics(
        funnel=HrFunnel(
            total_applicants=int(f["total_applicants"]),
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

    if body.decision == "hired":
        if a.status == "hired":
            raise HTTPException(status_code=409, detail="Applicant is already hired.")
        if a.status not in ("shortlisted", "interviewed"):
            raise HTTPException(
                status_code=409,
                detail="Only shortlisted or interviewed applicants can be hired.",
            )
    # 'rejected' is reachable from any non-terminal state AND from 'hired'
    # (an audited reversal — details.reversal=true).

    now = datetime.now(tz=UTC)
    prev = a.status
    a.status = body.decision
    a.updated_at = now

    db.add(
        AuditLog(
            actor_id=hr_uid,
            actor_type="user",
            action=f"applicant.decision.{body.decision}",
            resource_type="applicant",
            resource_id=applicant_id,
            details={
                "company_id": str(company_id),
                "decision": body.decision,
                "previous_status": prev,
                "rationale": body.rationale,
                "reversal": prev == "hired",
            },
            ip_address=_extract_client_ip(request),
            user_agent=_extract_user_agent(request),
            event_ts=now,
        )
    )
    # Email the candidate their hire/reject decision (staged on this transaction →
    # atomic with the decision + audit row, then delivered by the outbox worker).
    if body.decision != prev:
        await email_applicant_decision(
            db, applicant=a, decision=body.decision, company_id=company_id
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
