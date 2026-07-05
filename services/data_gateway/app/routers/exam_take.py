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
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.coding_grader import run_tests, weighted_raw
from app.config import settings
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
from app.rate_limit import rate_limit
from app.redis_client import get_redis
from app.routers.hr_applicants import DbSessionDep
from app.routers.hr_interviews import advance_applicant_to_interview

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/exam", tags=["exam-take"])

_NOT_AVAILABLE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not available.")

# Integrity event types that count toward the auto-submit violation threshold.
_VIOLATION_EVENTS = {"fullscreen_exit", "tab_blur"}


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
    attempt_id: uuid.UUID
    event_type: str = Field(min_length=1, max_length=40)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, object] | None = None


class IntegrityIngestOut(BaseModel):
    accepted: bool
    violation_count: int
    max_violations: int
    integrity_score: int


class AttemptStartOut(BaseModel):
    attempt_id: str
    started_at: str
    deadline: str | None


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


def _deadline(rnd: ExamRound, started_at: datetime) -> datetime | None:
    if rnd.time_limit_seconds is None:
        return None
    return started_at + timedelta(seconds=rnd.time_limit_seconds)


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

    section_out = [
        PublicSectionOut(
            id=str(s.id), title=s.title, kind=s.kind, position=s.position,
            time_limit_seconds=s.time_limit_seconds,
            questions=[_public_question(q) for q in mcq_by_section.get(s.id, [])],
            coding_questions=[
                _public_coding_question(q) for q in coding_by_section.get(s.id, [])
            ],
        )
        for s in sections
    ]
    kinds = {s.kind for s in sections}
    kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"

    in_prog = await _in_progress_attempt(db, ctx)
    deadline = _deadline(ctx.exam_round, in_prog.started_at) if in_prog else None
    return TakeExamOut(
        exam_id=str(ctx.exam.id),
        title=ctx.exam.title,
        description=ctx.exam.description,
        round_id=str(ctx.exam_round.id),
        round_title=ctx.exam_round.title,
        round_number=ctx.exam_round.round_number,
        kind=kind,
        time_limit_seconds=ctx.exam_round.time_limit_seconds,
        total_questions=len(mcq) + len(coding),
        allow_retake=ctx.exam.allow_retake,
        already_submitted=await _has_submitted(db, ctx),
        server_now=datetime.now(tz=UTC).isoformat(),
        deadline=deadline.isoformat() if deadline else None,
        scheduled_at=(
            ctx.assignment.scheduled_at.isoformat() if ctx.assignment.scheduled_at else None
        ),
        max_integrity_violations=settings.exam_integrity_max_violations,
        sections=section_out,
        questions=[_public_question(q) for q in mcq],
        coding_questions=[_public_coding_question(q) for q in coding],
    )


@router.post("/start", response_model=AttemptStartOut)
async def start_attempt(ctx: ExamTakeCtxDep, db: DbSessionDep) -> AttemptStartOut:
    # Idempotent: return the existing in-progress attempt if one is open.
    existing = await _in_progress_attempt(db, ctx)
    if existing is not None:
        d = _deadline(ctx.exam_round, existing.started_at)
        return AttemptStartOut(
            attempt_id=str(existing.id),
            started_at=existing.started_at.isoformat(),
            deadline=d.isoformat() if d else None,
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

    max_no = await db.scalar(
        select(func.max(ExamAttempt.attempt_no)).where(
            ExamAttempt.round_id == ctx.exam_round.id,
            ExamAttempt.applicant_id == ctx.applicant.id,
            ExamAttempt.company_id == ctx.company_id,
        )
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
        # fall back to the attempt the winner created.
        await db.rollback()
        existing = await _in_progress_attempt(db, ctx)
        if existing is None:
            raise
        d = _deadline(ctx.exam_round, existing.started_at)
        return AttemptStartOut(
            attempt_id=str(existing.id),
            started_at=existing.started_at.isoformat(),
            deadline=d.isoformat() if d else None,
        )
    d = _deadline(ctx.exam_round, attempt.started_at)
    return AttemptStartOut(
        attempt_id=str(attempt.id),
        started_at=attempt.started_at.isoformat(),
        deadline=d.isoformat() if d else None,
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
        deadline = _deadline(ctx.exam_round, attempt.started_at)
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
                )
            except Exception as exc:  # noqa: BLE001 - never fail a submit on advance error
                log.warning(
                    "exam.auto_advance_failed", exam_id=str(ctx.exam.id), error=str(exc)
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
    dependencies=[rate_limit("exam_submit", settings.rate_limit_api_per_minute)],
)
async def submit_attempt(body: SubmitIn, ctx: ExamTakeCtxDep, db: DbSessionDep) -> ExamResultOut:
    """Submit a round (MCQ answers + optional coding submissions)."""
    attempt = await _load_attempt(db, ctx, body.attempt_id)
    return await _grade_and_finalize(db, ctx, attempt, body.answers, body.submissions)


@router.post(
    "/submit-round",
    response_model=ExamResultOut,
    dependencies=[rate_limit("exam_submit", settings.rate_limit_api_per_minute)],
)
async def submit_round(body: SubmitIn, ctx: ExamTakeCtxDep, db: DbSessionDep) -> ExamResultOut:
    """Explicit round submit (alias of /submit) — the round-aware UI's primary path."""
    attempt = await _load_attempt(db, ctx, body.attempt_id)
    return await _grade_and_finalize(db, ctx, attempt, body.answers, body.submissions)


@router.post(
    "/submit-coding",
    response_model=ExamResultOut,
    dependencies=[rate_limit("exam_submit", settings.rate_limit_api_per_minute)],
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
    dependencies=[rate_limit("exam_run_code", 20)],
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
    dependencies=[rate_limit("exam_run_code_custom", 20)],
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
def _score_from_violations(violations: int) -> int:
    """Rolling integrity score: 100 minus a flat penalty per violation, floored at 0."""
    return max(0, 100 - 15 * violations)


@router.post("/integrity-event", response_model=IntegrityIngestOut)
async def ingest_integrity_event(
    body: IntegrityEventIn, ctx: ExamTakeCtxDep, db: DbSessionDep
) -> IntegrityIngestOut:
    """Record one proctoring event (fullscreen-exit / tab-switch / ...) against the
    open attempt and update its rolling integrity score + summary. Detection is
    client-side; only the lightweight event reaches us (raw input never leaves the
    browser). The client decides auto-submit; this is the server-side audit trail."""
    attempt = await _in_progress_attempt(db, ctx)
    if attempt is None or attempt.id != body.attempt_id:
        raise _NOT_AVAILABLE
    now = datetime.now(tz=UTC)
    db.add(
        ExamIntegrityEvent(
            id=uuid.uuid4(),
            attempt_id=attempt.id,
            company_id=ctx.company_id,
            event_type=body.event_type[:40],
            started_at=body.started_at or now,
            ended_at=body.ended_at,
            event_metadata=body.metadata,
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
    score = _score_from_violations(violations)
    attempt.proctoring_summary = {"counts": counts, "violations": violations}
    attempt.integrity_score = score
    attempt.updated_at = now
    await db.commit()
    return IntegrityIngestOut(
        accepted=True,
        violation_count=violations,
        max_violations=settings.exam_integrity_max_violations,
        integrity_score=score,
    )
