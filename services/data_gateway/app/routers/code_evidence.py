"""Code quality + similarity evidence — PH4-D3.

``hr_router`` only (``/hr``, ``HrCtxDep``): only ``hr_manager`` ever reads a
named candidate's source or a similarity comparison — both audited every
time (D3 #24) — or records an integrity finding. There is no super-admin,
interviewer or agent route, and no candidate route at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import code_evidence as svc
from app.config import settings
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.interviewer_scorecards import RequestMeta
from app.models import Enrolment, Exam, ExamAttempt
from app.rate_limit import rate_limit_company
from app.utils.ownership import get_owned
from app.utils.request_ip import extract_client_ip, extract_user_agent

hr_router = APIRouter(prefix="/hr", tags=["code-evidence"])


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=extract_client_ip(request), user_agent=extract_user_agent(request))


async def _fail(exc: svc.CodeEvidenceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _owned_attempt(db: DbSessionDep, company_id: uuid.UUID, exam_id: uuid.UUID, aid: uuid.UUID) -> ExamAttempt:
    await get_owned(db, Exam, company_id, exam_id, noun="Exam")
    return await get_owned(db, ExamAttempt, company_id, aid, noun="Attempt", exam_id=exam_id)


async def _enrolment_for_attempt(db: DbSessionDep, company_id: uuid.UUID, attempt_id: uuid.UUID) -> uuid.UUID | None:
    row = await db.scalar(
        text(
            "SELECT asg.enrolment_id FROM exam_attempts a"
            "  JOIN exam_assignments asg ON asg.id = a.assignment_id AND asg.company_id = a.company_id"
            " WHERE a.id = :a AND a.company_id = :c"
        ),
        {"a": attempt_id, "c": company_id},
    )
    return row


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class FindingIn(BaseModel):
    attempt_id: uuid.UUID
    coding_question_id: uuid.UUID
    outcome: str = Field(pattern="^(no_concern|follow_up|confirmed)$")
    rationale: str = Field(min_length=20, max_length=2000)
    signal_id: uuid.UUID | None = None
    supersedes_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@hr_router.get("/exams/{exam_id}/attempts/{aid}/code-evidence")
async def get_code_evidence(
    exam_id: uuid.UUID, aid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep, request: Request,
) -> dict[str, Any]:
    """MEDIUM-3: audited every time — ``test_results`` carries the
    candidate's program stdout/stderr, the same kind of read source and
    compare views are already audited for (D3 #24)."""
    uid, company_id = ctx
    await _owned_attempt(db, company_id, exam_id, aid)
    try:
        return await svc.evidence_for_attempt(
            db, company_id=company_id, exam_id=exam_id, attempt_id=aid,
            actor=uid, meta=_meta(request),
        )
    except svc.CodeEvidenceError as exc:
        raise await _fail(exc) from exc


@hr_router.get("/exams/{exam_id}/attempts/{aid}/code/{question_id}")
async def get_code_source(
    exam_id: uuid.UUID, aid: uuid.UUID, question_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
    request: Request,
) -> dict[str, Any]:
    """The candidate's own submitted source — audited every time (D3 #24)."""
    uid, company_id = ctx
    await _owned_attempt(db, company_id, exam_id, aid)
    try:
        return await svc.source_for(
            db, company_id=company_id, exam_id=exam_id, attempt_id=aid, coding_question_id=question_id,
            actor=uid, meta=_meta(request),
        )
    except svc.CodeEvidenceError as exc:
        raise await _fail(exc) from exc


@hr_router.post(
    "/exams/{exam_id}/attempts/{aid}/code-analysis",
    dependencies=[rate_limit_company("code_analysis_ondemand", settings.code_analysis_ondemand_per_minute)],
)
async def trigger_code_analysis(
    exam_id: uuid.UUID, aid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, int]:
    """On-demand analysis for one attempt — the sweep already covers recent
    submissions; this is for an older one, or a re-run.

    MEDIUM-4: rate-limited per COMPANY, not just guarded by the sandbox's own
    concurrency cap — each call runs a full pairwise comparison pass for
    every coding question the attempt touches, so an unthrottled HR console
    (or a scripted loop against it) could still repeat that cost far more
    often than the nightly sweep ever would.
    """
    _uid, company_id = ctx
    await _owned_attempt(db, company_id, exam_id, aid)
    try:
        result = await svc.analyse_attempt(db, company_id=company_id, attempt_id=aid)
    except svc.CodeEvidenceError as exc:
        raise await _fail(exc) from exc
    return {
        "attempts_scanned": result.attempts_scanned, "reports_written": result.reports_written,
        "fingerprints_written": result.fingerprints_written, "signals_written": result.signals_written,
    }


@hr_router.get("/enrolments/{enrolment_id}/code-evidence-summary")
async def get_enrolment_code_evidence_summary(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, int]:
    """The one-enrolment read the candidate drawer calls — counts only, no
    source, no content, no names. The decision queue gets the same counts in
    bulk via ``summary_for_enrolments`` wired into ``get_decision_queue``
    (``app/routers/hr_workflows.py``), exactly as ``interviewer_scorecards``'s
    own summary is."""
    _uid, company_id = ctx
    await get_owned(db, Enrolment, company_id, enrolment_id, noun="Application")
    counts = await svc.summary_for_enrolments(db, company_id=company_id, enrolment_ids=[enrolment_id])
    return counts.get(str(enrolment_id)) or dict(svc.EMPTY_SUMMARY)


@hr_router.get("/exams/{exam_id}/similarity")
async def list_similarity_signals(
    exam_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    await get_owned(db, Exam, company_id, exam_id, noun="Exam")
    return await svc.signals_for_exam(db, company_id=company_id, exam_id=exam_id)


@hr_router.get("/code-similarity/{signal_id}")
async def get_similarity_compare(
    signal_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep, request: Request,
) -> dict[str, Any]:
    """Excerpts of each matched region — audited every time (D3 #24)."""
    uid, company_id = ctx
    try:
        return await svc.compare_view(db, company_id=company_id, signal_id=signal_id, actor=uid,
                                      meta=_meta(request))
    except svc.CodeEvidenceError as exc:
        raise await _fail(exc) from exc


@hr_router.post("/code-integrity-findings", status_code=201)
async def record_code_integrity_finding(
    body: FindingIn, ctx: HrCtxDep, db: DbSessionDep, request: Request,
) -> dict[str, str]:
    uid, company_id = ctx
    await get_owned(db, ExamAttempt, company_id, body.attempt_id, noun="Attempt")
    enrolment_id = await _enrolment_for_attempt(db, company_id, body.attempt_id)
    try:
        fid = await svc.record_finding(
            db, company_id=company_id, attempt_id=body.attempt_id,
            coding_question_id=body.coding_question_id, outcome=body.outcome,
            rationale=body.rationale, actor=uid, meta=_meta(request), signal_id=body.signal_id,
            enrolment_id=enrolment_id, supersedes_id=body.supersedes_id,
        )
    except svc.CodeEvidenceError as exc:
        await db.rollback()
        raise await _fail(exc) from exc
    return {"id": str(fid)}


@hr_router.get("/exams/{exam_id}/attempts/{aid}/integrity-findings")
async def list_integrity_findings(
    exam_id: uuid.UUID, aid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep, request: Request,
    include_redacted: bool = Query(default=False),
) -> list[dict[str, Any]]:
    uid, company_id = ctx
    await _owned_attempt(db, company_id, exam_id, aid)
    evidence = await svc.evidence_for_attempt(
        db, company_id=company_id, exam_id=exam_id, attempt_id=aid, actor=uid, meta=_meta(request),
    )
    findings = evidence["findings"]
    if not include_redacted:
        findings = [f for f in findings if not f["redacted"]]
    return findings
