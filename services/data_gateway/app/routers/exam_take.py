"""Applicant exam-taking — HR workflow Phase 2 (PUBLIC, no login).

An applicant opens a magic link and takes ONE ROUND of an exam WITHOUT an account.
Auth is the opaque token in the ``X-Exam-Token`` header (never the URL path/query;
the HR mint puts it in the URL *fragment*, which browsers don't send to servers).
Every request re-resolves the assignment row by token hash and re-checks tenant +
status + expiry, so revocation is immediate.

A round groups one or more SECTIONS, each of kind 'mcq' or 'coding' — so a single
round can mix MCQ + coding. Grading sums all sections to a round score compared to
the ROUND's pass_threshold. Passing the terminal round (advances_to_interview) can
auto-advance the candidate to an interview when the exam has auto_advance_on_pass.

HARD SECURITY GUARANTEES (unchanged):
  - correct_index / reference_solution / hidden expected_output / pass_threshold are
    NEVER in any response on this router.
  - Grading is 100% server-side; the client submits only chosen indices + source.
  - The round time limit is enforced on the SERVER at submit (client countdown is UX).
  - Submit is idempotent; a second submit returns the stored result (no re-grade).
  - One live attempt per applicant+ROUND (DB partial-unique index); a retake is
    allowed only when the exam permits it.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import accommodations, exam_camera
from app.coding_grader import run_tests, weighted_raw
from app.config import settings
from app.database import DbSessionDep
from app.exam_grading import GradeInput, GradeQuestion, grade_exam
from app.exam_link import hash_exam_token
from app.execution import run_code
from app.models import (
    Applicant,
    CodingQuestion,
    Exam,
    ExamAssignment,
    ExamAttempt,
    ExamIntegrityEvent,
    ExamQuestion,
    ExamRound,
    ExamSection,
)
from app.notifications_util import create_notification
from app.rate_limit import enforce_token_budget, rate_limit_link
from app.redis_client import get_redis
from app.routers.hr_interviews import advance_applicant_to_interview
from app.utils.request_ip import extract_client_ip, extract_user_agent
from app.workflow_runner import enrolment_awaiting_exam_round, record_result

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/exam", tags=["exam-take"])

_NOT_AVAILABLE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not available.")

# Integrity event types that count toward the auto-submit violation threshold.
# Camera proctoring contract: extended from the original two browser signals
# to include the two higher-severity camera signals; gaze_away deliberately
# never counts here — see app/exam_camera.py's module docstring.
_VIOLATION_EVENTS = exam_camera.VIOLATION_EVENT_TYPES


# ---------------------------------------------------------------------------
# Magic-link resolution (the applicant's only auth)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExamTakeCtx:
    company_id: uuid.UUID
    exam: Exam
    exam_round: ExamRound
    applicant: Applicant
    assignment: ExamAssignment


async def get_exam_link_ctx(
    db: DbSessionDep,
    x_exam_token: Annotated[str | None, Header(alias="X-Exam-Token")] = None,
) -> ExamTakeCtx:
    """Resolve + validate a magic link → the ROUND it grants. Always 404 on any
    failure (never reveals whether a link/exam exists)."""
    if not x_exam_token:
        raise _NOT_AVAILABLE
    token_hash = hash_exam_token(x_exam_token, settings.exam_link_secret)
    asn = await db.scalar(
        select(ExamAssignment).where(
            ExamAssignment.token_hash == token_hash,
            ExamAssignment.deleted_at.is_(None),
            ExamAssignment.status.notin_(("revoked", "expired")),
            ExamAssignment.expires_at > datetime.now(tz=UTC),
        )
    )
    if asn is None:
        raise _NOT_AVAILABLE
    # The ROUND's published status is the authoritative gate (rounds are published
    # + assigned independently); the exam-level 'closed' is a kill switch for the
    # whole exam. We do NOT require exam.status=='published' — publishing a round
    # is the deliberate HR action that makes its link live.
    exam = await db.scalar(
        select(Exam).where(
            Exam.id == asn.exam_id,
            Exam.company_id == asn.company_id,
            Exam.status != "closed",
            Exam.deleted_at.is_(None),
        )
    )
    rnd = await db.scalar(
        select(ExamRound).where(
            ExamRound.id == asn.round_id,
            ExamRound.company_id == asn.company_id,
            ExamRound.status == "published",
            ExamRound.deleted_at.is_(None),
        )
    )
    applicant = await db.scalar(
        select(Applicant).where(
            Applicant.id == asn.applicant_id,
            Applicant.company_id == asn.company_id,
            Applicant.deleted_at.is_(None),
        )
    )
    if exam is None or rnd is None or applicant is None:
        raise _NOT_AVAILABLE
    return ExamTakeCtx(
        company_id=asn.company_id, exam=exam, exam_round=rnd,
        applicant=applicant, assignment=asn,
    )


ExamTakeCtxDep = Annotated[ExamTakeCtx, Depends(get_exam_link_ctx)]


# ---------------------------------------------------------------------------
# Schemas (NO correct_index / reference_solution / pass_threshold anywhere here)
# ---------------------------------------------------------------------------
class PublicQuestionOut(BaseModel):
    id: str
    position: int
    prompt: str
    options: list[str]
    points: int


class PublicSampleTest(BaseModel):
    """A SAMPLE test case shown to the candidate (hidden cases never appear here)."""

    stdin: str
    expected_output: str


class PublicCodingQuestionOut(BaseModel):
    """Candidate-facing coding question. NEVER carries reference_solution or any
    hidden test case's expected_output (mirrors how MCQ hides correct_index)."""

    id: str
    position: int
    prompt: str
    starter_code: str | None
    allowed_languages: list[str]
    points: int
    time_limit_ms: int
    sample_tests: list[PublicSampleTest]


class PublicSectionOut(BaseModel):
    """One section of the round — typed; carries only the questions of its kind."""

    id: str
    title: str
    kind: str
    position: int
    time_limit_seconds: int | None
    questions: list[PublicQuestionOut] = Field(default_factory=list)
    coding_questions: list[PublicCodingQuestionOut] = Field(default_factory=list)


class AdjustmentsOut(BaseModel):
    """PH4-D2 — told to the candidate so the page can explain the clock. Never
    the notes, never which accommodation, never who recorded it."""

    extra_time_percent: int | None = None
    deadline_extended: bool = False


class TakeExamOut(BaseModel):
    exam_id: str
    title: str
    description: str | None
    # Round context
    round_id: str
    round_title: str
    round_number: int
    # back-compat: 'mcq' | 'coding' | 'mixed' (first section's kind for single-kind
    # rounds; 'mixed' when sections differ). New UI keys off `sections` instead.
    kind: str = "mcq"
    time_limit_seconds: int | None
    total_questions: int
    allow_retake: bool
    already_submitted: bool
    server_now: str
    deadline: str | None
    scheduled_at: str | None = None
    max_integrity_violations: int
    adjustments: AdjustmentsOut | None = None
    # Camera proctoring contract §3: the company's OWN round setting, and
    # whether THIS candidate already holds an active video_capture consent —
    # so the UI knows whether to show the camera-consent screen before /start,
    # and whether it can skip straight past it.
    camera_required: bool = False
    camera_consent_granted: bool = False
    sections: list[PublicSectionOut] = Field(default_factory=list)
    # Flattened, back-compat with the pre-rounds single-section taker.
    questions: list[PublicQuestionOut] = Field(default_factory=list)
    coding_questions: list[PublicCodingQuestionOut] = Field(default_factory=list)


class RunCodeIn(BaseModel):
    """Candidate's 'Run' against the SAMPLE tests (no scoring, no persistence)."""

    question_id: uuid.UUID
    language: str = Field(min_length=1, max_length=40)
    source: str = Field(default="")

    @field_validator("source")
    @classmethod
    def _cap_source(cls, v: str) -> str:
        if len(v.encode("utf-8", "ignore")) > settings.code_max_source_bytes:
            raise ValueError("source too large")
        return v


class RunCodeCustomIn(BaseModel):
    """Candidate's 'Run with custom input' — one execution against THEIR own stdin.
    No scoring, no persistence; never touches the graded/hidden test cases."""

    question_id: uuid.UUID
    language: str = Field(min_length=1, max_length=40)
    source: str = Field(default="")
    stdin: str = Field(default="")

    @field_validator("source")
    @classmethod
    def _cap_source(cls, v: str) -> str:
        if len(v.encode("utf-8", "ignore")) > settings.code_max_source_bytes:
            raise ValueError("source too large")
        return v

    @field_validator("stdin")
    @classmethod
    def _cap_stdin(cls, v: str) -> str:
        if len(v.encode("utf-8", "ignore")) > settings.code_max_stdin_bytes:
            raise ValueError("input too large")
        return v


class PublicTestResult(BaseModel):
    index: int
    passed: bool
    stdin: str
    expected_output: str
    actual_output: str
    stderr: str
    timed_out: bool
    error: str | None = None


class RunCodeOut(BaseModel):
    results: list[PublicTestResult]


class RunCodeCustomOut(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    error: str | None = None


class CodingAnswer(BaseModel):
    language: str = Field(min_length=1, max_length=40)
    source: str = Field(default="")

    @field_validator("source")
    @classmethod
    def _cap_source(cls, v: str) -> str:
        if len(v.encode("utf-8", "ignore")) > settings.code_max_source_bytes:
            raise ValueError("source too large")
        return v


class SubmitIn(BaseModel):
    """Round submit. Carries MCQ answers and/or coding submissions — a mixed round
    sends both; a single-kind round sends just one (back-compat)."""

    attempt_id: uuid.UUID
    answers: dict[str, int] = Field(default_factory=dict)
    submissions: dict[str, CodingAnswer] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def _cap_answers(cls, v: dict[str, int]) -> dict[str, int]:
        if len(v) > settings.exam_max_answers:
            raise ValueError("too many answers")
        return v

    @field_validator("submissions")
    @classmethod
    def _cap_submissions(cls, v: dict[str, CodingAnswer]) -> dict[str, CodingAnswer]:
        if len(v) > settings.code_max_questions_per_exam:
            raise ValueError("too many submissions")
        return v


class CodingSubmitIn(BaseModel):
    """Back-compat coding submit (coding-only rounds). Also accepts answers so a
    mixed round can submit through this endpoint too."""

    attempt_id: uuid.UUID
    submissions: dict[str, CodingAnswer] = Field(default_factory=dict)
    answers: dict[str, int] = Field(default_factory=dict)

    @field_validator("submissions")
    @classmethod
    def _cap_submissions(cls, v: dict[str, CodingAnswer]) -> dict[str, CodingAnswer]:
        if len(v) > settings.code_max_questions_per_exam:
            raise ValueError("too many submissions")
        return v

    @field_validator("answers")
    @classmethod
    def _cap_answers(cls, v: dict[str, int]) -> dict[str, int]:
        # Same DoS guard as SubmitIn — this endpoint also grades the MCQ portion.
        if len(v) > settings.exam_max_answers:
            raise ValueError("too many answers")
        return v


class IntegrityEventIn(BaseModel):
    """One proctoring event. ``extra="forbid"`` is structural, not incidental:
    a stray top-level ``frame``/``image``/``landmarks`` field is refused
    outright rather than silently ignored — see app/exam_camera.py's module
    docstring for why that distinction matters here."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: uuid.UUID
    event_type: str = Field(min_length=1, max_length=40)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    # NO `metadata` field, deliberately (code review FIX 3). It previously
    # existed with a shape-checker that rejected lists, nested objects and long
    # strings — but "looks innocuous" is not the same as "is not PII": a name, a
    # phone number or a health detail all pass such a check comfortably. With
    # the field gone, `extra="forbid"` above refuses every one of them, and
    # `event_metadata` is NULL by construction for every row this endpoint
    # writes rather than by convention.
    @field_validator("event_type")
    @classmethod
    def _known_event_type(cls, v: str) -> str:
        if v not in exam_camera.KNOWN_EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(exam_camera.KNOWN_EVENT_TYPES)}")
        return v

    @model_validator(mode="after")
    def _ranged_needs_ended_at(self) -> IntegrityEventIn:
        if self.event_type in exam_camera.RANGED_EVENT_TYPES and self.ended_at is None:
            raise ValueError(f"{self.event_type} is a ranged event and needs ended_at")
        return self


class IntegrityIngestOut(BaseModel):
    accepted: bool
    violation_count: int
    # None when this attempt's auto-submit is relaxed (PH4-D2): the client
    # then never auto-submits on violation count alone. Events and the
    # integrity score are still recorded either way.
    max_violations: int | None
    integrity_score: int


class AttemptStartOut(BaseModel):
    attempt_id: str
    started_at: str
    deadline: str | None
    # PH4-D2. None means this attempt never auto-submits on violation count
    # alone, exactly as on IntegrityIngestOut. It is frozen on the attempt at
    # /start, so it is the same answer for the life of the attempt.
    #
    # It is here, and not only on the integrity-event response, because the
    # client otherwise learns it only AFTER a violation -- and the event POST
    # swallows network failures, so on a poor connection a candidate with a
    # relax-auto-submit accommodation never learns it at all and is cut off at
    # the global threshold. That is the accommodation failing the one person
    # it exists for, on exactly the connections our market has.
    #
    # It discloses nothing new: the same value already goes to the same client
    # on the first violation, and it is the candidate's own accommodation.
    max_violations: int | None
    # Camera proctoring contract: frozen from the round's camera_proctoring_
    # required setting the moment this attempt started (mirrors max_violations
    # above) — the definitive answer for whether THIS attempt should run the
    # camera pipeline at all, for the life of the attempt.
    camera_in_use: bool


class ExamResultOut(BaseModel):
    attempt_id: str
    score_raw: int
    score_max: int
    score_percent: int
    passed: bool
    status: str
    submitted_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _round_sections(db: AsyncSession, ctx: ExamTakeCtx) -> list[ExamSection]:
    rows = (
        await db.execute(
            select(ExamSection)
            .where(
                ExamSection.round_id == ctx.exam_round.id,
                ExamSection.company_id == ctx.company_id,
                ExamSection.deleted_at.is_(None),
            )
            .order_by(ExamSection.position.asc())
        )
    ).scalars().all()
    return list(rows)


async def _mcq_for_sections(
    db: AsyncSession, ctx: ExamTakeCtx, section_ids: list[uuid.UUID]
) -> list[ExamQuestion]:
    if not section_ids:
        return []
    rows = (
        await db.execute(
            select(ExamQuestion)
            .where(
                ExamQuestion.section_id.in_(section_ids),
                ExamQuestion.company_id == ctx.company_id,
                ExamQuestion.deleted_at.is_(None),
            )
            .order_by(ExamQuestion.position.asc())
        )
    ).scalars().all()
    return list(rows)


async def _coding_for_sections(
    db: AsyncSession, ctx: ExamTakeCtx, section_ids: list[uuid.UUID]
) -> list[CodingQuestion]:
    if not section_ids:
        return []
    rows = (
        await db.execute(
            select(CodingQuestion)
            .where(
                CodingQuestion.section_id.in_(section_ids),
                CodingQuestion.company_id == ctx.company_id,
                CodingQuestion.deleted_at.is_(None),
            )
            .order_by(CodingQuestion.position.asc())
        )
    ).scalars().all()
    return list(rows)


def _public_question(q: ExamQuestion) -> PublicQuestionOut:
    return PublicQuestionOut(
        id=str(q.id), position=q.position, prompt=q.prompt,
        options=list(q.options or []), points=q.points,
    )


def _public_coding_question(q: CodingQuestion) -> PublicCodingQuestionOut:
    """Serialize a coding question for the candidate — sample tests ONLY, never the
    reference_solution or any hidden test case's expected_output."""
    samples = [
        PublicSampleTest(
            stdin=str(tc.get("stdin") or ""),
            expected_output=str(tc.get("expected_output") or ""),
        )
        for tc in (q.test_cases or [])
        if bool(tc.get("is_sample"))
    ]
    return PublicCodingQuestionOut(
        id=str(q.id), position=q.position, prompt=q.prompt,
        starter_code=q.starter_code, allowed_languages=list(q.allowed_languages or []),
        points=q.points, time_limit_ms=q.time_limit_ms, sample_tests=samples,
    )


async def _in_progress_attempt(db: AsyncSession, ctx: ExamTakeCtx) -> ExamAttempt | None:
    attempt: ExamAttempt | None = await db.scalar(
        select(ExamAttempt).where(
            ExamAttempt.round_id == ctx.exam_round.id,
            ExamAttempt.applicant_id == ctx.applicant.id,
            ExamAttempt.company_id == ctx.company_id,
            ExamAttempt.status == "in_progress",
            ExamAttempt.deleted_at.is_(None),
        )
    )
    return attempt


async def _has_submitted(db: AsyncSession, ctx: ExamTakeCtx) -> bool:
    n = await db.scalar(
        select(func.count()).select_from(ExamAttempt).where(
            ExamAttempt.round_id == ctx.exam_round.id,
            ExamAttempt.applicant_id == ctx.applicant.id,
            ExamAttempt.company_id == ctx.company_id,
            ExamAttempt.status == "submitted",
            ExamAttempt.deleted_at.is_(None),
        )
    )
    return int(n or 0) > 0


def _deadline(rnd: ExamRound, started_at: datetime, extra_seconds: int = 0) -> datetime | None:
    """The round's own formula, unchanged, plus whatever extra time (PH4-D2)
    the attempt started with. ``extra_seconds=0`` — the default — is exactly
    the old formula: no adjustment means nothing changes."""
    if rnd.time_limit_seconds is None:
        return None
    return started_at + timedelta(seconds=rnd.time_limit_seconds + extra_seconds)


async def _accommodation_scope(
    db: AsyncSession, ctx: ExamTakeCtx
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """``(enrolment_id, workflow_round_id)`` for resolving PH4-D2 accommodation
    scope: the workflow round this exam round currently backs for this
    applicant, when there is one (a workflow-run round can be reused, and the
    same applicant can hold several applications — ``current_round_id`` is the
    one this candidate was actually sent to), else the application the link
    was minted for, when known (a hand-assigned exam)."""
    pair = await enrolment_awaiting_exam_round(
        db, applicant_id=ctx.applicant.id, exam_round_id=ctx.exam_round.id
    )
    if pair is not None:
        return pair
    return ctx.assignment.enrolment_id, None


async def _accommodation_params(
    db: AsyncSession, accommodation_id: uuid.UUID | None
) -> tuple[int | None, int | None]:
    """``(extra_time_percent, deadline_extension_days)`` for an accommodation
    already on record — used to redisplay the adjustment an attempt or a link
    was minted with, without a fresh (and possibly now-superseded) lookup."""
    if accommodation_id is None:
        return None, None
    row = (
        await db.execute(
            text(
                "SELECT extra_time_percent, deadline_extension_days"
                "  FROM candidate_accommodations WHERE id = :i"
            ),
            {"i": accommodation_id},
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=TakeExamOut)
async def get_take_exam(ctx: ExamTakeCtxDep, db: DbSessionDep) -> TakeExamOut:
    sections = await _round_sections(db, ctx)
    mcq_section_ids = [s.id for s in sections if s.kind == "mcq"]
    coding_section_ids = [s.id for s in sections if s.kind == "coding"]
    mcq = await _mcq_for_sections(db, ctx, mcq_section_ids)
    coding = await _coding_for_sections(db, ctx, coding_section_ids)
    mcq_by_section: dict[uuid.UUID, list[ExamQuestion]] = {}
    for mq in mcq:
        mcq_by_section.setdefault(mq.section_id, []).append(mq)
    coding_by_section: dict[uuid.UUID, list[CodingQuestion]] = {}
    for cq in coding:
        coding_by_section.setdefault(cq.section_id, []).append(cq)

    in_prog = await _in_progress_attempt(db, ctx)
    # PH4-D2: before an attempt exists, the currently effective accommodation
    # (which can change up to the moment of /start); once started, whatever
    # the attempt itself was frozen with — never a fresh, possibly-since-
    # revised, lookup.
    if in_prog is not None:
        extra_pct, extra_days = await _accommodation_params(db, in_prog.accommodation_id)
        deadline = _deadline(ctx.exam_round, in_prog.started_at, in_prog.extra_time_seconds)
    else:
        enrolment_id, workflow_round_id = await _accommodation_scope(db, ctx)
        adj_row = await accommodations.effective_for(
            db, company_id=ctx.company_id, applicant_id=ctx.applicant.id,
            enrolment_id=enrolment_id, workflow_round_id=workflow_round_id,
            exam_round_id=ctx.exam_round.id,
        )
        extra_pct = adj_row.extra_time_percent if adj_row else None
        extra_days = adj_row.deadline_extension_days if adj_row else None
        deadline = None

    section_out = [
        PublicSectionOut(
            id=str(s.id), title=s.title, kind=s.kind, position=s.position,
            time_limit_seconds=accommodations.scaled(s.time_limit_seconds, extra_pct),
            questions=[_public_question(q) for q in mcq_by_section.get(s.id, [])],
            coding_questions=[
                _public_coding_question(q) for q in coding_by_section.get(s.id, [])
            ],
        )
        for s in sections
    ]
    kinds = {s.kind for s in sections}
    kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"

    adjustments = (
        AdjustmentsOut(extra_time_percent=extra_pct, deadline_extended=bool(extra_days))
        if extra_pct or extra_days
        else None
    )
    return TakeExamOut(
        exam_id=str(ctx.exam.id),
        title=ctx.exam.title,
        description=ctx.exam.description,
        round_id=str(ctx.exam_round.id),
        round_title=ctx.exam_round.title,
        round_number=ctx.exam_round.round_number,
        kind=kind,
        time_limit_seconds=accommodations.scaled(ctx.exam_round.time_limit_seconds, extra_pct),
        total_questions=len(mcq) + len(coding),
        allow_retake=ctx.exam.allow_retake,
        already_submitted=await _has_submitted(db, ctx),
        server_now=datetime.now(tz=UTC).isoformat(),
        deadline=deadline.isoformat() if deadline else None,
        scheduled_at=(
            ctx.assignment.scheduled_at.isoformat() if ctx.assignment.scheduled_at else None
        ),
        max_integrity_violations=settings.exam_integrity_max_violations,
        adjustments=adjustments,
        camera_required=bool(ctx.exam_round.camera_proctoring_required),
        camera_consent_granted=await exam_camera.has_active_camera_consent(
            db, ctx.applicant.user_id
        ),
        sections=section_out,
        questions=[_public_question(q) for q in mcq],
        coding_questions=[_public_coding_question(q) for q in coding],
    )


class CameraConsentOut(BaseModel):
    consented: bool
    already_granted: bool
    granted_at: str


@router.post(
    "/camera-consent",
    response_model=CameraConsentOut,
    # Security review MEDIUM-2. Unauthenticated (magic-link), writes to
    # dpdp_consent_ledger and can provision a guest users row — 5 DB queries
    # per call, previously unbounded, against a serverless database this
    # project has already had exhausted once by its own pollers. Row growth is
    # bounded by the partial unique index and link_or_reuse_guest, so this is
    # a cost/availability control, not an integrity one. Modest cap: a
    # candidate grants consent once or twice per round.
    dependencies=[
        rate_limit_link(
            "exam_camera_consent", "X-Exam-Token", per_token=10, per_ip=300
        )
    ],
)
async def record_camera_consent(
    ctx: ExamTakeCtxDep, db: DbSessionDep, request: Request,
) -> CameraConsentOut:
    """Explicit, standalone DPDP video_capture consent for the candidate's
    camera during this round — asked before the exam starts, on its own
    screen, never bundled with anything else (camera-proctoring contract §3).
    Idempotent: calling this again while a grant is already active returns it
    unchanged. Recording is independent of whether the round actually
    REQUIRES the camera — a candidate may grant it ahead of time, and
    /exam/start is what actually gates on it for a required round."""
    result = await exam_camera.record_camera_consent(
        db,
        applicant=ctx.applicant,
        # Scopes the acceptance evidence to the round it was given for, which
        # is also what bounds it: one audit row per round, not one per POST.
        exam_round_id=ctx.exam_round.id,
        meta=exam_camera.CameraConsentMeta(
            ip_address=extract_client_ip(request), user_agent=extract_user_agent(request),
        ),
    )
    await db.commit()
    return CameraConsentOut(**result)


def _max_violations_for(attempt: ExamAttempt) -> int | None:
    """None when this attempt's auto-submit is relaxed (PH4-D2), otherwise the
    configured threshold. Read from the attempt, which froze the allowance at
    /start, so revoking the accommodation mid-attempt cannot shorten a clock
    the candidate has already been shown."""
    return None if attempt.auto_submit_relaxed else settings.exam_integrity_max_violations


@router.post(
    "/start",
    response_model=AttemptStartOut,
    # Security review MEDIUM-2: also an unauthenticated write with no cap.
    # Idempotent (it returns the open attempt), so the risk is cost rather
    # than duplicate attempts — but a retry loop on a flaky connection should
    # not be able to hammer a serverless database unbounded.
    dependencies=[
        rate_limit_link("exam_start", "X-Exam-Token", per_token=20, per_ip=600)
    ],
)
async def start_attempt(ctx: ExamTakeCtxDep, db: DbSessionDep) -> AttemptStartOut:
    # Idempotent: return the existing in-progress attempt if one is open.
    existing = await _in_progress_attempt(db, ctx)
    if existing is not None:
        d = _deadline(ctx.exam_round, existing.started_at, existing.extra_time_seconds)
        return AttemptStartOut(
            attempt_id=str(existing.id),
            started_at=existing.started_at.isoformat(),
            deadline=d.isoformat() if d else None,
            max_violations=_max_violations_for(existing),
            camera_in_use=existing.camera_in_use,
        )
    # Block a fresh attempt on a single-shot round already submitted.
    if await _has_submitted(db, ctx) and not ctx.exam.allow_retake:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You have already taken this round."
        )

    now = datetime.now(tz=UTC)
    # Scheduled-round join window gates the FIRST start (mirrors interview_take).
    if ctx.assignment.scheduled_at is not None:
        sched = ctx.assignment.scheduled_at
        window_end = sched + timedelta(minutes=settings.exam_join_window_minutes)
        if now < sched:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This round opens at {sched.isoformat()}.",
            )
        if now > window_end:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The scheduled join window for this round has closed.",
            )

    # Camera proctoring contract §3: a round the company marked as REQUIRING
    # the camera cannot be started without an active video_capture consent for
    # THIS applicant. Checked before creating the attempt, so a declined
    # candidate never gets one at all — the screen the frontend shows for this
    # 422 is what tells them why and what to do (grant it, or ask HR).
    camera_required = bool(ctx.exam_round.camera_proctoring_required)
    if camera_required and not await exam_camera.has_active_camera_consent(
        db, ctx.applicant.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Camera consent is required before starting this round.",
        )

    max_no = await db.scalar(
        select(func.max(ExamAttempt.attempt_no)).where(
            ExamAttempt.round_id == ctx.exam_round.id,
            ExamAttempt.applicant_id == ctx.applicant.id,
            ExamAttempt.company_id == ctx.company_id,
        )
    )
    # PH4-D2: resolve the effective accommodation NOW and freeze it onto the
    # attempt. Whatever HR does to it afterwards, this attempt keeps the
    # allowance it started with (exam_attempts_allowance_fixed).
    enrolment_id, workflow_round_id = await _accommodation_scope(db, ctx)
    adj_row = await accommodations.effective_for(
        db, company_id=ctx.company_id, applicant_id=ctx.applicant.id,
        enrolment_id=enrolment_id, workflow_round_id=workflow_round_id,
        exam_round_id=ctx.exam_round.id,
    )
    extra_secs = (
        accommodations.extra_seconds(ctx.exam_round.time_limit_seconds, adj_row.extra_time_percent)
        if adj_row else 0
    )
    attempt = ExamAttempt(
        id=uuid.uuid4(),
        company_id=ctx.company_id,
        exam_id=ctx.exam.id,
        round_id=ctx.exam_round.id,
        applicant_id=ctx.applicant.id,
        assignment_id=ctx.assignment.id,
        attempt_no=(int(max_no) + 1) if max_no is not None else 1,
        status="in_progress",
        started_at=now,
        accommodation_id=adj_row.id if adj_row else None,
        extra_time_seconds=extra_secs,
        auto_submit_relaxed=bool(adj_row.relax_auto_submit) if adj_row else False,
        # Frozen from the round's CURRENT setting, exactly like the allowance
        # fields above. Not required => camera not in use, full stop — even a
        # candidate who separately holds a video_capture consent (say, from an
        # earlier AI interview) does not get the camera pipeline turned on for
        # a round that does not ask for it (contract §3).
        camera_in_use=camera_required,
        created_at=now,
        updated_at=now,
    )
    db.add(attempt)
    if ctx.assignment.status == "invited":
        ctx.assignment.status = "started"
        ctx.assignment.updated_at = now
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent /start lost the race against uix_exam_attempts_one_live —
        # fall back to the attempt the winner created (which already recorded
        # its own "applied" event, if any — this request must not record a
        # second one for the same accommodation).
        await db.rollback()
        existing = await _in_progress_attempt(db, ctx)
        if existing is None:
            raise
        d = _deadline(ctx.exam_round, existing.started_at, existing.extra_time_seconds)
        return AttemptStartOut(
            attempt_id=str(existing.id),
            started_at=existing.started_at.isoformat(),
            deadline=d.isoformat() if d else None,
            max_violations=_max_violations_for(existing),
            camera_in_use=existing.camera_in_use,
        )
    if adj_row is not None:
        await accommodations.record_applied(
            db, company_id=ctx.company_id, accommodation_id=adj_row.id,
            target_kind="exam_attempt", target_id=attempt.id,
        )
        await db.commit()
    d = _deadline(ctx.exam_round, attempt.started_at, attempt.extra_time_seconds)
    return AttemptStartOut(
        attempt_id=str(attempt.id),
        started_at=attempt.started_at.isoformat(),
        deadline=d.isoformat() if d else None,
        max_violations=_max_violations_for(attempt),
        camera_in_use=attempt.camera_in_use,
    )


async def _load_attempt(
    db: AsyncSession, ctx: ExamTakeCtx, attempt_id: uuid.UUID
) -> ExamAttempt:
    # No FOR UPDATE here: concurrent submit serialization is now handled by a
    # short-lived Redis SET NX EX claim in _grade_and_finalize, which avoids
    # holding a DB connection + row lock for the entire JDoodle round-trip.
    attempt: ExamAttempt | None = await db.scalar(
        select(ExamAttempt)
        .where(
            ExamAttempt.id == attempt_id,
            ExamAttempt.round_id == ctx.exam_round.id,
            ExamAttempt.applicant_id == ctx.applicant.id,
            ExamAttempt.company_id == ctx.company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    if attempt is None:
        raise _NOT_AVAILABLE
    return attempt


def _stored_result(attempt: ExamAttempt) -> ExamResultOut:
    return ExamResultOut(
        attempt_id=str(attempt.id),
        score_raw=attempt.score_raw or 0,
        score_max=attempt.score_max or 0,
        score_percent=attempt.score_percent or 0,
        passed=bool(attempt.passed),
        status=attempt.status,
        submitted_at=(attempt.submitted_at or attempt.started_at).isoformat(),
    )


async def _grade_and_finalize(
    db: AsyncSession,
    ctx: ExamTakeCtx,
    attempt: ExamAttempt,
    answers: dict[str, int],
    submissions: dict[str, CodingAnswer],
) -> ExamResultOut:
    """Grade the whole round (all sections), store the result, close the link, and
    auto-advance to interview when the terminal round is passed.

    Idempotency + pool safety:
    - A Redis SET NX EX claim (keyed by attempt_id) serializes concurrent submits of
      the SAME attempt so the slow JDoodle/Piston round-trip is never duplicated.
    - The DB connection is released (via an early commit) BEFORE the outbound grading
      calls, so a 5-30 s JDoodle round-trip no longer exhausts the connection pool.
    - A re-submit of an already-finished attempt returns the stored result instantly
      (no re-grade, no Redis claim needed).
    """
    # Fast path: already done — return stored without claiming Redis or re-grading.
    if attempt.status in ("submitted", "expired"):
        return _stored_result(attempt)

    # ---------------------------------------------------------------------------
    # Serialize concurrent submits of the SAME attempt via a short-lived Redis
    # claim. SET NX EX 180 — 3-minute window (well beyond any grading round-trip).
    # A second concurrent submit sees NX fail and either returns the stored result
    # (if grading finished) or a 409 (still in flight). Fails OPEN on Redis error
    # (same posture as rate_limit) so a cache hiccup never blocks a submit.
    # ---------------------------------------------------------------------------
    claim_key = f"exam:grading:{attempt.id}"
    # Resolve the client outside the try so the finally block can always DEL.
    try:
        redis = get_redis()
    except Exception as redis_init_exc:  # noqa: BLE001
        log.warning(
            "exam.grading_claim.redis_unavailable",
            attempt_id=str(attempt.id), error=str(redis_init_exc),
        )
        redis = None  # type: ignore[assignment]

    claimed = False
    if redis is not None:
        try:
            claimed = bool(await redis.set(claim_key, "1", nx=True, ex=180))
        except Exception as redis_exc:  # noqa: BLE001
            log.warning(
                "exam.grading_claim.skipped", attempt_id=str(attempt.id), error=str(redis_exc),
            )
            claimed = True  # fail open — let this request grade
    else:
        claimed = True  # Redis not available → fail open

    if not claimed:
        # Another submit is in flight (or just finished). Re-fetch from DB to check.
        refreshed: ExamAttempt | None = await db.scalar(
            select(ExamAttempt).where(ExamAttempt.id == attempt.id)
        )
        if refreshed is not None and refreshed.status in ("submitted", "expired"):
            return _stored_result(refreshed)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grading is already in progress for this attempt. Please retry shortly.",
        )

    # Initialise result variables before the try block so the finally can always log.
    now = datetime.now(tz=UTC)
    expired = False
    total_raw = 0
    total_max = 0
    percent = 0
    passed = False
    fresh_attempt: ExamAttempt | None = None

    try:
        deadline = _deadline(ctx.exam_round, attempt.started_at, attempt.extra_time_seconds)
        expired = bool(
            deadline and now > deadline + timedelta(seconds=settings.exam_submit_grace_seconds)
        )

        # --- Load question data while still in the same session (fast DB reads). ---
        sections = await _round_sections(db, ctx)
        mcq_section_ids = [s.id for s in sections if s.kind == "mcq"]
        coding_section_ids = [s.id for s in sections if s.kind == "coding"]
        mcq_questions = await _mcq_for_sections(db, ctx, mcq_section_ids)
        coding_questions = await _coding_for_sections(db, ctx, coding_section_ids)

        # --- MCQ portion (instant, server-side) ---
        mcq_answers = {k: int(v) for k, v in answers.items()}
        mcq_result = grade_exam(
            GradeInput(
                questions=[
                    GradeQuestion(
                        question_id=str(q.id), correct_index=q.correct_index, points=q.points
                    )
                    for q in mcq_questions
                ],
                answers=mcq_answers,
            ),
            ctx.exam_round.pass_threshold,
        )
        total_raw = mcq_result.score_raw
        total_max = mcq_result.score_max
        mcq_snapshot = {
            str(q.id): {"correct_index": q.correct_index, "points": q.points}
            for q in mcq_questions
        }

        # Snapshot the coding question metadata before releasing the DB connection.
        coding_meta = [
            {
                "id": str(q.id),
                "points": q.points,
                "allowed_languages": list(q.allowed_languages or []),
                "test_cases": list(q.test_cases or []),
                "time_limit_ms": q.time_limit_ms,
            }
            for q in coding_questions
        ]

        # ---------------------------------------------------------------------------
        # EARLY COMMIT — release the DB connection BEFORE the slow outbound calls.
        # The Redis claim above serializes concurrent submits so no second request
        # can race past this point to double-grade.
        # ---------------------------------------------------------------------------
        await db.commit()

        # --- Coding portion (JDoodle/Piston) — runs WITHOUT a DB connection held. ---
        coding_snapshot: dict[str, object] = {}
        coding_sanitized: dict[str, object] = {}
        coding_total_raw = 0
        for qmeta in coding_meta:
            qid = str(qmeta["id"])
            total_max += int(qmeta["points"])
            sub = submissions.get(qid)
            if sub is None:
                coding_snapshot[qid] = {
                    "points": qmeta["points"], "submitted": False, "raw": 0, "tests": [],
                }
                continue
            coding_sanitized[qid] = {"language": sub.language, "source": sub.source}
            if sub.language not in qmeta["allowed_languages"]:
                coding_snapshot[qid] = {
                    "points": qmeta["points"], "language": sub.language,
                    "error": "language not allowed", "raw": 0, "tests": [],
                }
                continue
            results = await run_tests(
                language=sub.language, source=sub.source,
                test_cases=list(qmeta["test_cases"]),
                time_limit_ms=int(qmeta["time_limit_ms"]), include_hidden=True,
            )
            raw = weighted_raw(results, int(qmeta["points"]))
            coding_total_raw += raw
            coding_snapshot[qid] = {
                "points": qmeta["points"], "language": sub.language, "raw": raw,
                "tests": [
                    {
                        "index": r.index, "is_sample": r.is_sample, "weight": r.weight,
                        "passed": r.passed, "timed_out": r.timed_out, "error": r.error,
                        "actual_output": r.actual_output, "stderr": r.stderr,
                    }
                    for r in results
                ],
            }
        total_raw += coding_total_raw

        # FIX: use math.floor (same as grade_exam/MCQ path) so a boundary candidate
        # is never rounded up across the pass_threshold.
        percent = math.floor(100 * total_raw / total_max) if total_max > 0 else 0
        passed = percent >= ctx.exam_round.pass_threshold

        # ---------------------------------------------------------------------------
        # Persist results in a NEW transaction on the same session object. Re-fetch
        # the attempt and assignment by PK so SQLAlchemy tracks them in this new
        # transaction (the old ORM objects were detached when we committed above).
        # ---------------------------------------------------------------------------
        fresh_attempt = await db.scalar(
            select(ExamAttempt).where(ExamAttempt.id == attempt.id)
        )
        fresh_assignment: ExamAssignment | None = await db.scalar(
            select(ExamAssignment).where(ExamAssignment.id == ctx.assignment.id)
        )
        # Guard against a race where a concurrent submit finished first while we were
        # grading (e.g. Redis failed open for both). Return stored result without
        # overwriting it.
        if fresh_attempt is not None and fresh_attempt.status in ("submitted", "expired"):
            return _stored_result(fresh_attempt)

        # The attempt row must still exist here. If it vanished between the early
        # commit and this persist (concurrent delete / DB fault), grading RAN but
        # cannot be saved — fail LOUDLY instead of returning a "success" the DB
        # never recorded (which would show the candidate a pass with no scorecard).
        # The Redis claim is released in `finally`, so a retry can re-grade.
        if fresh_attempt is None:
            log.error(
                "exam.grade.attempt_missing_on_persist",
                attempt_id=str(attempt.id),
                round_id=str(ctx.exam_round.id),
                company_id=str(ctx.company_id),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Your answers were graded but could not be saved. Please submit again.",
            )

        if fresh_attempt is not None:
            fresh_attempt.answers = {"mcq": mcq_answers, "coding": coding_sanitized}
            fresh_attempt.graded_snapshot = {"mcq": mcq_snapshot, "coding": coding_snapshot}
            fresh_attempt.score_raw = total_raw
            fresh_attempt.score_max = total_max
            fresh_attempt.score_percent = percent
            fresh_attempt.passed = passed
            # Only transition from 'in_progress' — never overwrite a 'submitted'
            # result (double-submit guard at DB write level, complementing the
            # Redis claim and the uix_exam_attempts_one_live partial index).
            if fresh_attempt.status == "in_progress":
                fresh_attempt.status = "expired" if expired else "submitted"
            fresh_attempt.submitted_at = now
            fresh_attempt.updated_at = now

        # Close the assignment (single-use): the link is consumed.
        if fresh_assignment is not None:
            fresh_assignment.status = "completed"
            fresh_assignment.consumed_at = now
            fresh_assignment.updated_at = now

        # Tell the workflow runner this round produced a result — the second
        # place the runner was never called from, and the reason a candidate who
        # sat and passed an exam still sat at the same round afterwards.
        #
        # NOT wrapped in try/except, unlike the auto-advance below. A graded
        # attempt whose enrolment did not move is the exact defect being fixed
        # here, so if this cannot be written the submit should fail and be
        # retried rather than quietly reproduce it. Grading is idempotent and
        # the Redis claim is released in `finally`, so a retry is safe.
        #
        # No enrolment is the normal case for an exam HR assigned by hand,
        # outside any workflow. It skips.
        pair = await enrolment_awaiting_exam_round(
            db, applicant_id=ctx.applicant.id, exam_round_id=ctx.exam_round.id
        )
        if pair is not None:
            enrolment_id, workflow_round_id = pair
            outcome = await record_result(
                db,
                enrolment_id=enrolment_id,
                round_id=workflow_round_id,
                # Already a percentage, so the max is 100 — record_result
                # converts to percent on the round's own scale, and letting it
                # infer one would treat 67% as 67 out of 10.
                score=float(percent),
                max_score=100.0,
                graded_by="deterministic",
                attempt_ref=fresh_attempt.id if fresh_attempt is not None else None,
            )
            log.info(
                "exam.round.runner",
                enrolment_id=str(enrolment_id),
                action=outcome.action,
                to_round=outcome.to_round,
                reason=outcome.reason,
            )

        # Auto-advance: best-effort + idempotent (advance_applicant_to_interview
        # guards duplicate invites). Staged on the SAME transaction so the invite
        # row is only written iff the attempt commit also succeeds — atomic.
        if passed and ctx.exam_round.advances_to_interview and ctx.exam.auto_advance_on_pass:
            try:
                await advance_applicant_to_interview(
                    db,
                    company_id=ctx.company_id,
                    applicant=ctx.applicant,
                    created_by_user_id=ctx.exam.created_by_user_id,
                    notify_user_id=ctx.exam.created_by_user_id,
                    # The application this exam was assigned for (B5), when
                    # known — the invite is then for that opening's role.
                    enrolment_id=ctx.assignment.enrolment_id,
                )
            except Exception as exc:  # noqa: BLE001 - never fail a submit on advance error
                log.warning(
                    "exam.auto_advance_failed", exam_id=str(ctx.exam.id), error=str(exc)
                )

        # Tell whoever sent the link (A4). HR otherwise learned an exam was done
        # only by opening the results page. Staged on this transaction, so it
        # exists exactly when the attempt does; keyed on the attempt, so a
        # retried submit cannot announce it twice. The score is stated as a
        # fact against the pass mark — never as a decision, which is HR's.
        if fresh_attempt is not None:
            await create_notification(
                db,
                user_id=ctx.assignment.created_by_user_id or ctx.exam.created_by_user_id,
                kind="exam_submitted",
                title=f"{ctx.applicant.full_name} submitted "
                      f"{ctx.exam_round.title or ctx.exam.title}",
                body=(
                    f"{percent:.0f}% — "
                    + ("at or above the pass mark" if passed else "below the pass mark")
                    + (" (time ran out)" if expired else "")
                ),
                link=f"/hr/exams/{ctx.exam.id}/attempts/{fresh_attempt.id}",
                dedupe_key=f"exam_submitted:{fresh_attempt.id}",
            )

        # Single commit — attempt + assignment + optional invite in one transaction.
        await db.commit()

    finally:
        # Always release the Redis claim so a retry after an unexpected error
        # can re-enter grading rather than waiting for the 180-second TTL.
        if redis is not None and claimed:
            try:
                await redis.delete(claim_key)
            except Exception as del_exc:  # noqa: BLE001
                log.warning(
                    "exam.grading_claim.delete_failed",
                    attempt_id=str(attempt.id), error=str(del_exc),
                )

    status_val = (
        fresh_attempt.status if fresh_attempt is not None
        else ("expired" if expired else "submitted")
    )
    log.info(
        "exam.round.submitted",
        exam_id=str(ctx.exam.id), round_id=str(ctx.exam_round.id),
        company_id=str(ctx.company_id), percent=percent, passed=passed, expired=expired,
    )
    return ExamResultOut(
        attempt_id=str(attempt.id), score_raw=total_raw, score_max=total_max,
        score_percent=percent, passed=passed, status=status_val,
        submitted_at=now.isoformat(),
    )


@router.post(
    "/submit",
    response_model=ExamResultOut,
    dependencies=[
        rate_limit_link(
            "exam_submit",
            "X-Exam-Token",
            per_token=settings.exam_submit_per_minute,
            per_ip=settings.exam_submit_per_ip_per_minute,
        )
    ],
)
async def submit_attempt(body: SubmitIn, ctx: ExamTakeCtxDep, db: DbSessionDep) -> ExamResultOut:
    """Submit a round (MCQ answers + optional coding submissions)."""
    attempt = await _load_attempt(db, ctx, body.attempt_id)
    return await _grade_and_finalize(db, ctx, attempt, body.answers, body.submissions)


@router.post(
    "/submit-round",
    response_model=ExamResultOut,
    dependencies=[
        rate_limit_link(
            "exam_submit",
            "X-Exam-Token",
            per_token=settings.exam_submit_per_minute,
            per_ip=settings.exam_submit_per_ip_per_minute,
        )
    ],
)
async def submit_round(body: SubmitIn, ctx: ExamTakeCtxDep, db: DbSessionDep) -> ExamResultOut:
    """Explicit round submit (alias of /submit) — the round-aware UI's primary path."""
    attempt = await _load_attempt(db, ctx, body.attempt_id)
    return await _grade_and_finalize(db, ctx, attempt, body.answers, body.submissions)


@router.post(
    "/submit-coding",
    response_model=ExamResultOut,
    dependencies=[
        rate_limit_link(
            "exam_submit",
            "X-Exam-Token",
            per_token=settings.exam_submit_per_minute,
            per_ip=settings.exam_submit_per_ip_per_minute,
        )
    ],
)
async def submit_coding(
    body: CodingSubmitIn, ctx: ExamTakeCtxDep, db: DbSessionDep
) -> ExamResultOut:
    """Back-compat coding submit (coding-only rounds). Grades the full round."""
    attempt = await _load_attempt(db, ctx, body.attempt_id)
    return await _grade_and_finalize(db, ctx, attempt, body.answers, body.submissions)


# ---------------------------------------------------------------------------
# Coding round — run samples / run custom input (no scoring)
# ---------------------------------------------------------------------------
async def _round_coding_question(
    db: AsyncSession, ctx: ExamTakeCtx, qid: uuid.UUID
) -> CodingQuestion | None:
    """A coding question that belongs to one of THIS round's coding sections."""
    sections = await _round_sections(db, ctx)
    section_ids = [s.id for s in sections if s.kind == "coding"]
    if not section_ids:
        return None
    q: CodingQuestion | None = await db.scalar(
        select(CodingQuestion).where(
            CodingQuestion.id == qid,
            CodingQuestion.section_id.in_(section_ids),
            CodingQuestion.company_id == ctx.company_id,
            CodingQuestion.deleted_at.is_(None),
        )
    )
    return q


@router.post(
    "/run-code",
    response_model=RunCodeOut,
    dependencies=[
        rate_limit_link(
            "exam_run_code",
            "X-Exam-Token",
            per_token=settings.exam_run_code_per_minute,
            per_ip=settings.exam_run_code_per_ip_per_minute,
        )
    ],
)
async def run_code_samples(body: RunCodeIn, ctx: ExamTakeCtxDep, db: DbSessionDep) -> RunCodeOut:
    """Candidate 'Run' — execute against the SAMPLE tests only. No score, no save."""
    # Require an open attempt — no anonymous Piston runs before /start (DoS guard).
    if await _in_progress_attempt(db, ctx) is None:
        raise _NOT_AVAILABLE
    q = await _round_coding_question(db, ctx, body.question_id)
    if q is None:
        raise _NOT_AVAILABLE
    if body.language not in (q.allowed_languages or []):
        raise HTTPException(status_code=400, detail="Language not allowed for this question.")
    results = await run_tests(
        language=body.language, source=body.source,
        test_cases=list(q.test_cases or []), time_limit_ms=q.time_limit_ms,
        include_hidden=False,
    )
    return RunCodeOut(
        results=[
            PublicTestResult(
                index=r.index, passed=r.passed, stdin=r.stdin,
                expected_output=r.expected_output, actual_output=r.actual_output,
                stderr=r.stderr, timed_out=r.timed_out, error=r.error,
            )
            for r in results
        ]
    )


@router.post(
    "/run-code-custom",
    response_model=RunCodeCustomOut,
    dependencies=[
        rate_limit_link(
            "exam_run_code_custom",
            "X-Exam-Token",
            per_token=settings.exam_run_code_per_minute,
            per_ip=settings.exam_run_code_per_ip_per_minute,
        )
    ],
)
async def run_code_custom(
    body: RunCodeCustomIn, ctx: ExamTakeCtxDep, db: DbSessionDep
) -> RunCodeCustomOut:
    """Candidate 'Run with custom input' — ONE execution against their own stdin.
    No scoring, no persistence; never reads the graded/hidden test cases."""
    if await _in_progress_attempt(db, ctx) is None:
        raise _NOT_AVAILABLE
    q = await _round_coding_question(db, ctx, body.question_id)
    if q is None:
        raise _NOT_AVAILABLE
    if body.language not in (q.allowed_languages or []):
        raise HTTPException(status_code=400, detail="Language not allowed for this question.")
    res = await run_code(
        language=body.language, source=body.source,
        stdin=body.stdin, time_limit_ms=q.time_limit_ms,
    )
    return RunCodeCustomOut(
        stdout=res.stdout[:20_000], stderr=res.stderr[:20_000],
        exit_code=res.exit_code, timed_out=res.timed_out, error=res.error,
    )


# ---------------------------------------------------------------------------
# Proctoring — integrity event ingest (exam analogue of interview integrity)
# ---------------------------------------------------------------------------
async def _note_events_dropped(db: AsyncSession, attempt: ExamAttempt) -> None:
    """Flag on the attempt that at least one proctoring event was refused by
    the rate limiter, so the HR timeline can say it is incomplete.

    Security review HIGH-2: the client swallows a 429 exactly like a lost
    packet, so without this the ONLY trace of a drop is a Prometheus counter
    nobody joins to an attempt. A reviewer would see a short timeline and have
    no way to tell "nothing happened" from "we stopped recording".

    Deliberately a flag and not a count: the number of refusals is not
    evidence about the candidate, and storing it would invite exactly the
    quantitative reading this feature is careful to avoid everywhere else.
    """
    summary = dict(attempt.proctoring_summary or {})
    if summary.get("events_dropped") is True:
        return  # already flagged; nothing to write
    # A new dict, not an in-place mutation: SQLAlchemy does not track changes
    # inside a JSONB value without MutableDict, so mutating would silently
    # persist nothing.
    summary["events_dropped"] = True
    attempt.proctoring_summary = summary
    attempt.updated_at = datetime.now(tz=UTC)
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — never convert a 429 into a 500
        # The caller is about to raise 429. If flagging fails we still want
        # that 429, not a 500: the client swallows both identically, but a 500
        # here would misreport a throttle as a server fault in every dashboard
        # and log. The flag is lost, which is the already-accepted worst case
        # of the drop itself (security re-audit LOW-4).
        log.warning("exam.integrity_event.drop_flag_failed", attempt_id=str(attempt.id))
        await db.rollback()



@router.post(
    "/integrity-event",
    response_model=IntegrityIngestOut,
    # Route level: the EDGE guard, cutting a flood off before any database
    # work. Note the bucket name differs from the in-handler one on purpose —
    # they are separate Redis keys, and reusing the name would charge every
    # request to the same counter twice and silently halve both ceilings.
    #
    # Its per-token ceiling deliberately sits ABOVE the in-handler one, so the
    # handler's cap always fires first and flags the attempt; traffic that
    # reaches this ceiling has therefore already been recorded as dropped.
    # That keeps the pre-DB cut-off without reintroducing an unflaggable token
    # refusal (security re-audit rounds 2-3, MEDIUM-1 then MEDIUM-2): without
    # it, requests past the in-handler cap each cost 5 SELECTs before being
    # refused, which on a serverless database this project has already had
    # exhausted by its own pollers is a real cost regression.
    #
    # A per-IP refusal here CANNOT flag anything: at dependency time there is
    # no attempt resolved to write to, and resolving one would forfeit the very
    # cut-off this exists for. It is therefore an OPERATIONAL signal, not an HR
    # one — watch rate_limit_exceeded_total{bucket="exam_integrity_edge"}.
    dependencies=[
        rate_limit_link(
            "exam_integrity_edge",
            "X-Exam-Token",
            per_token=settings.exam_integrity_event_edge_per_minute,
            per_ip=settings.exam_integrity_event_per_ip_per_minute,
        )
    ],
)
async def ingest_integrity_event(
    request: Request, body: IntegrityEventIn, ctx: ExamTakeCtxDep, db: DbSessionDep
) -> IntegrityIngestOut:
    """Record one proctoring event (fullscreen-exit / tab-switch / copy / paste
    / a camera signal) against the open attempt and update its rolling
    integrity score + summary. Detection is client-side; only the lightweight
    event reaches us (raw input never leaves the browser — see
    app/exam_camera.py for the structural guarantee). The client decides
    auto-submit; this is the server-side audit trail.

    Rate-limited in TWO tiers, for two different reasons (code review FIX 2 +
    security review HIGH-2, 2026-09-29). An unauthenticated magic-link
    candidate could originally post unboundedly — verified live, 150
    consecutive posts from one token all returned 200 — so:

    * The route-level cap is keyed on the candidate's OWN link
      (``X-Exam-Token``) with a much looser per-IP backstop, NOT the other way
      round: a lab NATs every seat behind one address, so an IP-keyed cap
      would let three ordinary candidates exhaust the budget and silently drop
      the 4th's real camera events. It is set above any rate a real client can
      reach, so it is a volumetric guard only.
    * The per-type cap below charges NON-VIOLATION events (copy / paste /
      gaze_away) to their own budget. Because the client swallows a 429 and a
      dropped camera event loses the row, the violation count AND the
      auto-submit trigger at once, a single shared budget would let a
      candidate spend it on cheap clipboard events and suppress their own
      ``multiple_faces`` for the rest of the minute — turning the rate limit
      into an off switch for the evidence this endpoint exists to collect.

    When either PER-TOKEN cap refuses an event the attempt records it, so HR is
    told the timeline is incomplete rather than shown a short one that looks
    clean. The per-IP edge refusal is the one exception and cannot be recorded:
    it happens in a route dependency, before any attempt is resolved, and
    resolving one there would forfeit the pre-database cut-off that guard
    exists for. It is an operational signal instead — see the route decorator.

    A camera-only event type (face_absent / multiple_faces / gaze_away) is
    refused for an attempt that never had the camera on
    (``attempt.camera_in_use`` is False) — that attempt was never watched, so
    an event claiming a camera signal for it can only be a forged or stale
    request, never a real one.
    """
    attempt = await _in_progress_attempt(db, ctx)
    if attempt is None or attempt.id != body.attempt_id:
        raise _NOT_AVAILABLE
    if body.event_type in exam_camera.CAMERA_EVENT_TYPES and not attempt.camera_in_use:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Camera proctoring is not enabled for this attempt.",
        )
    # Both per-token budgets, in the one place that can record a refusal.
    #
    # Outer: every event type, above any rate a real client produces — a
    # volumetric bound per candidate. Inner: only the types that are NOT
    # violations, on their own key, so clipboard noise can never starve a
    # camera signal (see this function's docstring).
    #
    # Each refusal is flagged on the attempt BEFORE the 429 is raised. An
    # incomplete timeline that says it is incomplete is honest; one that
    # silently omits events invites HR to read a short record as a clean one.
    try:
        await enforce_token_budget(
            request,
            bucket="exam_integrity_event",
            header="X-Exam-Token",
            per_minute=settings.exam_integrity_event_per_minute,
        )
        if body.event_type not in exam_camera.VIOLATION_EVENT_TYPES:
            await enforce_token_budget(
                request,
                bucket="exam_integrity_nonviolation",
                header="X-Exam-Token",
                per_minute=settings.exam_integrity_nonviolation_per_minute,
            )
    except HTTPException:
        await _note_events_dropped(db, attempt)
        raise
    now = datetime.now(tz=UTC)
    db.add(
        ExamIntegrityEvent(
            id=uuid.uuid4(),
            attempt_id=attempt.id,
            company_id=ctx.company_id,
            event_type=body.event_type,
            started_at=body.started_at or now,
            ended_at=body.ended_at,
            # No `metadata` field exists on IntegrityEventIn (code review FIX
            # 3) — event_metadata is therefore always NULL for every row this
            # endpoint creates, by construction rather than convention.
            created_at=now,
        )
    )
    # Persist the new event so the GROUP BY below counts it, then recompute the
    # rolling summary from the authoritative persisted set (no manual fold-in —
    # that would double-count the row we just flushed).
    await db.flush()
    counts_rows = (
        await db.execute(
            select(ExamIntegrityEvent.event_type, func.count())
            .where(ExamIntegrityEvent.attempt_id == attempt.id)
            .group_by(ExamIntegrityEvent.event_type)
        )
    ).all()
    counts: dict[str, int] = {et: int(n) for et, n in counts_rows}
    violations = sum(n for et, n in counts.items() if et in _VIOLATION_EVENTS)
    score = exam_camera.score_from_counts(counts)
    attempt.proctoring_summary = {
        "counts": counts, "violations": violations, "camera_in_use": attempt.camera_in_use,
    }
    attempt.integrity_score = score
    attempt.updated_at = now
    await db.commit()
    return IntegrityIngestOut(
        accepted=True,
        violation_count=violations,
        # PH4-D2: None tells the client never to auto-submit on violation count
        # alone for this attempt. The events and the score above are recorded
        # exactly the same either way.
        max_violations=(
            None if attempt.auto_submit_relaxed else settings.exam_integrity_max_violations
        ),
        integrity_score=score,
    )
