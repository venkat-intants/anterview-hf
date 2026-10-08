"""HR MCQ exam authoring + results — HR workflow Phase 2.

An HR manager authors an exam (MCQ questions + pass threshold), publishes it,
assigns it to applicants (minting an opaque magic-link the applicant uses to take
it WITHOUT logging in), and reviews graded results.

MULTI-TENANT: every endpoint is scoped to the caller's company_id (reusing the
Phase-1 get_hr_company/HrCtxDep). An HR can NEVER see or touch another company's
exams — all reads/writes filter by company_id, so a cross-company id returns 404.

SECURITY:
  - correct_index is returned ONLY to HR (authoring/detail/breakdown) — never on
    the applicant take path (that lives in exam_take.py).
  - Questions LOCK (409) once any attempt exists, so a graded exam can't be
    silently changed under candidates.
  - Magic-link mint returns the RAW token exactly once; only its HMAC hash is
    stored. Re-assigning rotates the link (revokes the prior active one).
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import accommodations, exam_locks, question_import
from app.code_evidence import coding_results_for_screen
from app.config import settings
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.exam_ai_client import ExamGenerationError, generate_exam_questions_remote
from app.exam_grading import GradeInput, GradeQuestion, grade_breakdown
from app.exam_link import hash_exam_token, mint_exam_token
from app.mailer import candidate_language, enqueue_email
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
from app.requisitions import enrolment_for_exam_round
from app.utils.ownership import get_owned
from app.workflows import round_rubric_for_generation, rubric_for_exam

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-exams"])

_VALID_STATUSES = {"draft", "published", "closed"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ExamCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    target_job_title: str | None = None
    pass_threshold: int = Field(default=60, ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86_400)
    allow_retake: bool = False
    # 'mcq' (default) or 'coding' — selects the question type + grader. This also
    # becomes the kind of the auto-created default section (back-compat).
    kind: str = Field(default="mcq")
    # When True, passing the terminal round auto-creates a scheduled interview
    # invite + emails the candidate; when False HR invites manually.
    auto_advance_on_pass: bool = False

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        v = (v or "mcq").lower()
        if v not in {"mcq", "coding"}:
            raise ValueError("kind must be mcq | coding")
        return v


class ExamUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    target_job_title: str | None = None
    pass_threshold: int | None = Field(default=None, ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86_400)
    allow_retake: bool | None = None
    auto_advance_on_pass: bool | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        return v


class QuestionIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0)
    points: int = Field(default=1, ge=1, le=100)

    @field_validator("options")
    @classmethod
    def _validate_options(cls, v: list[str]) -> list[str]:
        cleaned = [o.strip() for o in v]
        if any(not o for o in cleaned):
            raise ValueError("options must be non-empty strings")
        return cleaned

    @model_validator(mode="after")
    def _validate_correct_index(self) -> QuestionIn:
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index out of range for options")
        return self


class QuestionUpdateIn(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    options: list[str] | None = Field(default=None, min_length=2, max_length=6)
    correct_index: int | None = Field(default=None, ge=0)
    points: int | None = Field(default=None, ge=1, le=100)

    @field_validator("options")
    @classmethod
    def _validate_options(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        cleaned = [o.strip() for o in v]
        if any(not o for o in cleaned):
            raise ValueError("options must be non-empty strings")
        return cleaned


class ReorderIn(BaseModel):
    question_ids: list[uuid.UUID] = Field(min_length=1)


class BulkQuestionsIn(BaseModel):
    """Insert many questions at once (used by AI-generate 'add all' + Excel import)."""

    questions: list[QuestionIn] = Field(min_length=1, max_length=200)


class GenerateQuestionsIn(BaseModel):
    """Ask Gemini (via feedback_billing) to draft MCQs — returned for preview, NOT saved."""

    topic: str = Field(min_length=1, max_length=300)
    num_questions: int = Field(default=5, ge=1, le=30)
    difficulty: str = Field(default="medium")
    language: str = Field(default="en")
    # Optional role context. When set, the generator spreads questions across
    # this role's competencies using the same allocator the interview uses, so
    # the two rounds stop assessing subtly different jobs.
    job_title: str = Field(default="", max_length=300)
    experience_level: str = Field(default="mid")
    # The workflow round these questions are for (C3). When set, its published
    # competencies decide the spread — an aptitude round and a technical round
    # on the same role stop generating the same questions.
    workflow_round_id: uuid.UUID | None = None

    @field_validator("difficulty")
    @classmethod
    def _validate_difficulty(cls, v: str) -> str:
        v = (v or "medium").lower()
        if v not in {"easy", "medium", "hard", "mixed"}:
            raise ValueError("difficulty must be easy | medium | hard | mixed")
        return v

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        v = (v or "en").lower()
        if v not in {"en", "hi", "te"}:
            raise ValueError("language must be en | hi | te")
        return v


class GeneratedQuestionOut(BaseModel):
    """A drafted question — no id/position (it isn't persisted until HR adds it)."""

    prompt: str
    options: list[str]
    correct_index: int
    points: int = 1


class GenerateQuestionsOut(BaseModel):
    questions: list[GeneratedQuestionOut]


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportQuestionsOut(BaseModel):
    added: int
    errors: list[ImportRowError]
    questions: list[QuestionOut]


class AssignIn(BaseModel):
    # People to assign. The exam is attributed to an application when that can
    # be known (see enrolment_for_exam_round).
    applicant_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    # Or APPLICATIONS (B5): the exam is for exactly this one. How HR says which
    # opening an exam is for when a person has applied to several.
    enrolment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    ttl_hours: int | None = Field(default=None, ge=1, le=8760)
    # Optional: assign a SPECIFIC round (defaults to the exam's first round when
    # omitted — the back-compat single-round path). Optional scheduled start time.
    round_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None


class QuestionOut(BaseModel):
    """HR-facing question — INCLUDES correct_index (HR only; never on take path)."""

    id: str
    prompt: str
    options: list[str]
    correct_index: int
    points: int
    position: int
    # PH4-D1 -- where this question came from, when it was copied from a bank.
    # All three are NULL for a question written directly in the exam. The
    # exam editor's "From bank - vN" chip reads these; the columns existed and
    # the copy wrote them, but no response returned them, so the chip could
    # never render -- found by the acceptance evidence pass, not by a test.
    source_bank_question_id: str | None = None
    source_bank_root_id: str | None = None
    source_bank_version: int | None = None


class ExamOut(BaseModel):
    id: str
    title: str
    description: str | None
    target_job_title: str | None
    pass_threshold: int
    time_limit_seconds: int | None
    allow_retake: bool
    status: str
    kind: str
    auto_advance_on_pass: bool
    created_at: str


class ExamSummaryOut(ExamOut):
    question_count: int
    attempt_count: int


class ExamDetailOut(ExamOut):
    questions: list[QuestionOut]
    attempt_count: int


class AssignOut(BaseModel):
    assignment_id: str
    applicant_id: str
    enrolment_id: str | None = None
    applicant_name: str
    magic_link: str  # raw token embedded — returned ONCE, at mint time only
    expires_at: str
    status: str
    round_id: str | None = None
    scheduled_at: str | None = None


class AssignmentOut(BaseModel):
    assignment_id: str
    applicant_id: str
    applicant_name: str
    status: str
    expires_at: str
    consumed_at: str | None
    created_at: str


class AttemptResultOut(BaseModel):
    attempt_id: str
    applicant_id: str
    applicant_name: str
    score_raw: int | None
    score_max: int | None
    score_percent: int | None
    passed: bool | None
    status: str
    submitted_at: str | None
    attempt_no: int
    # Camera proctoring contract §7. The API carries both so a caller can tell
    # "not watched" from "watched and clean" without opening every attempt —
    # but note that the HR CONSOLE deliberately renders neither on this list.
    # A bare integrity score in a scannable table is exactly where a reviewer
    # under time pressure pattern-matches "low score = cheated", with none of
    # the context (which events, how long, and gaze called out as unreliable)
    # that makes the per-attempt panel honest. If a list-level indicator is
    # ever wanted, show camera on/off — never the score on its own.
    integrity_score: int | None = None
    camera_in_use: bool = False


class ProctoringEventOut(BaseModel):
    """One stored proctoring event, time-ordered. A ranged event (a camera
    signal) carries its duration; an instantaneous one (fullscreen-exit /
    tab-switch) does not."""

    event_type: str
    started_at: str
    ended_at: str | None
    duration_seconds: float | None


class AttemptProctoringOut(BaseModel):
    """The attempt's proctoring summary (camera-proctoring contract §7).

    ``camera_in_use`` says whether the ROUND REQUIRED a camera — it is frozen
    from the round's setting at /exam/start and cannot be updated afterwards
    (the exam_attempts_allowance_fixed trigger). It does NOT mean a camera
    actually ran.

    That distinction was previously stated the other way round here, claiming
    this field tells HR whether "no camera events" means clean or means never
    watched. It cannot: a candidate who denied the browser permission, or whose
    detector never loaded, produces camera_in_use=true with zero camera events
    — indistinguishable from a candidate who sat perfectly still. Answering
    that question needs a positive "detection started" signal the client does
    not send today, so the panel now says plainly that the two cases cannot be
    told apart rather than implying the clean one (review, 2026-09-30).
    """

    camera_in_use: bool
    integrity_score: int | None
    counts: dict[str, int]
    events: list[ProctoringEventOut]
    #: True when the rate limiter refused at least one event for this attempt
    #: (security review HIGH-2). The client swallows a 429 like a lost packet,
    #: so without this a reviewer cannot tell a quiet exam from one we stopped
    #: recording. Shown as a caveat on the timeline, never as a mark against
    #: the candidate — being throttled is not something they did.
    events_dropped: bool = False


# ---------------------------------------------------------------------------
# Helpers (tenant isolation lives here)
# ---------------------------------------------------------------------------
async def _get_owned_exam(db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID) -> Exam:
    return await get_owned(db, Exam, company_id, exam_id, noun="Exam")


async def _create_default_round_section(
    db: AsyncSession, exam: Exam, *, kind: str
) -> tuple[ExamRound, ExamSection]:
    """Create the exam's default Round 1 + one section (mirrors the migration
    backfill), so legacy exam-scoped question/assignment flows keep working on a
    fresh exam. Caller owns the commit. advances_to_interview=True — the single
    round is terminal until HR adds more."""
    now = datetime.now(tz=UTC)
    rnd = ExamRound(
        id=uuid.uuid4(),
        exam_id=exam.id,
        company_id=exam.company_id,
        round_number=1,
        title="Round 1",
        pass_threshold=exam.pass_threshold,
        time_limit_seconds=exam.time_limit_seconds,
        advances_to_interview=True,
        status="draft",
        position=1,
        created_at=now,
        updated_at=now,
    )
    db.add(rnd)
    sec = ExamSection(
        id=uuid.uuid4(),
        round_id=rnd.id,
        exam_id=exam.id,
        company_id=exam.company_id,
        title="Section 1",
        kind=kind,
        time_limit_seconds=None,
        position=1,
        created_at=now,
        updated_at=now,
    )
    db.add(sec)
    return rnd, sec


async def _default_round(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID
) -> ExamRound:
    """The exam's first live round (back-compat single-round path)."""
    rnd = await db.scalar(
        select(ExamRound)
        .where(
            ExamRound.exam_id == exam_id,
            ExamRound.company_id == company_id,
            ExamRound.deleted_at.is_(None),
        )
        .order_by(ExamRound.position.asc())
        .limit(1)
    )
    if rnd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam has no rounds.")
    return rnd


async def _default_section(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID, kind: str
) -> ExamSection:
    """The exam's first live section of the given kind (back-compat path: legacy
    exams have exactly one section, of kind == exam.kind)."""
    sec = await db.scalar(
        select(ExamSection)
        .where(
            ExamSection.exam_id == exam_id,
            ExamSection.company_id == company_id,
            ExamSection.kind == kind,
            ExamSection.deleted_at.is_(None),
        )
        .order_by(ExamSection.position.asc())
        .limit(1)
    )
    if sec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exam has no {kind} section.",
        )
    return sec


async def _question_count(db: AsyncSession, exam: Exam) -> int:
    """Count live questions in the table that matches the exam's kind."""
    model = CodingQuestion if exam.kind == "coding" else ExamQuestion
    n = await db.scalar(
        select(func.count()).select_from(model).where(
            model.exam_id == exam.id, model.deleted_at.is_(None)
        )
    )
    return int(n or 0)


async def _attempt_count(db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID) -> int:
    n = await db.scalar(
        select(func.count()).select_from(ExamAttempt).where(
            ExamAttempt.exam_id == exam_id,
            ExamAttempt.company_id == company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    return int(n or 0)


async def _live_questions(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID
) -> list[ExamQuestion]:
    rows = (
        await db.execute(
            select(ExamQuestion)
            .where(
                ExamQuestion.exam_id == exam_id,
                ExamQuestion.company_id == company_id,
                ExamQuestion.deleted_at.is_(None),
            )
            .order_by(ExamQuestion.position.asc())
        )
    ).scalars().all()
    return list(rows)


async def _get_owned_question(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID, qid: uuid.UUID
) -> ExamQuestion:
    return await get_owned(
        db, ExamQuestion, company_id, qid, noun="Question", exam_id=exam_id
    )


def _exam_out(e: Exam) -> ExamOut:
    return ExamOut(
        id=str(e.id),
        title=e.title,
        description=e.description,
        target_job_title=e.target_job_title,
        pass_threshold=e.pass_threshold,
        time_limit_seconds=e.time_limit_seconds,
        allow_retake=e.allow_retake,
        status=e.status,
        kind=e.kind,
        auto_advance_on_pass=e.auto_advance_on_pass,
        created_at=e.created_at.isoformat(),
    )


def _question_out(q: ExamQuestion) -> QuestionOut:
    return QuestionOut(
        id=str(q.id),
        prompt=q.prompt,
        options=list(q.options or []),
        correct_index=q.correct_index,
        points=q.points,
        position=q.position,
        source_bank_question_id=str(q.source_bank_question_id)
        if q.source_bank_question_id else None,
        source_bank_root_id=str(q.source_bank_root_id) if q.source_bank_root_id else None,
        source_bank_version=q.source_bank_version,
    )


async def _bulk_insert_questions(
    db: AsyncSession,
    company_id: uuid.UUID,
    section: ExamSection,
    items: list[QuestionIn],
) -> list[ExamQuestion]:
    """Append many already-validated questions at sequential positions WITHIN a
    section. Caller must have checked ownership + the attempt-lock first."""
    max_pos = await db.scalar(
        select(func.max(ExamQuestion.position)).where(
            ExamQuestion.section_id == section.id, ExamQuestion.deleted_at.is_(None)
        )
    )
    next_pos = (int(max_pos) + 1) if max_pos is not None else 0
    now = datetime.now(tz=UTC)
    created: list[ExamQuestion] = []
    for offset, item in enumerate(items):
        q = ExamQuestion(
            id=uuid.uuid4(),
            exam_id=section.exam_id,
            section_id=section.id,
            company_id=company_id,
            prompt=item.prompt.strip(),
            options=item.options,
            correct_index=item.correct_index,
            points=item.points,
            position=next_pos + offset,
            created_at=now,
            updated_at=now,
        )
        db.add(q)
        created.append(q)
    await exam_locks.commit_or_conflict(db)
    return created


# --- Spreadsheet (Excel / CSV) bulk-import helpers --------------------------
# The reading, the "Correct" resolution and the per-row errors now live in
# app.question_import, shared with the question-bank importer. They were private
# to this file until 2026-10-03; a second copy in the bank router would have
# drifted, and silently — a sheet that imports here but is refused there, or
# accepted with a different correct answer, with nothing to say which was right.
#
# What stays here is only the adaptation to this router's own models.
_TEMPLATE_HEADER: list[str] = list(question_import.TEMPLATE_HEADER)
_MAX_IMPORT_BYTES: int = question_import.MAX_IMPORT_BYTES


def _read_spreadsheet(filename: str, content: bytes) -> list[list[str]]:
    """Read an uploaded .xlsx or .csv into a list of string rows."""
    try:
        return question_import.read_spreadsheet(filename, content)
    except question_import.SpreadsheetError as exc:
        # 500 for a missing dependency, 400 for a file we cannot parse — the
        # same split this function made when it owned the openpyxl import.
        code = 500 if "not installed" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc


def _parse_question_rows(
    rows: list[list[str]],
) -> tuple[list[QuestionIn], list[ImportRowError]]:
    """Parse spreadsheet rows into QuestionIn objects + a per-row error list."""
    items, errors = question_import.parse_mcq_rows(rows)
    out: list[QuestionIn] = []
    row_errors = [ImportRowError(row=e.row, message=e.message) for e in errors]
    for item in items:
        # `row` is the parser's bookkeeping, not a model field — drop it before
        # QuestionIn sees it, and keep it so a validation failure still names
        # the line the HR manager has to go and fix.
        fields = {k: v for k, v in item.items() if k != "row"}
        try:
            out.append(QuestionIn(**fields))
        except ValidationError as exc:
            errs = exc.errors()
            msg = str(errs[0].get("msg", "invalid question")) if errs else "invalid question"
            row_errors.append(ImportRowError(row=int(item["row"]), message=msg))
    row_errors.sort(key=lambda e: e.row)
    return out, row_errors


# ---------------------------------------------------------------------------
# Exam CRUD
# ---------------------------------------------------------------------------
@router.post("/exams", status_code=status.HTTP_201_CREATED, response_model=ExamOut)
async def create_exam(body: ExamCreateIn, ctx: HrCtxDep, db: DbSessionDep) -> ExamOut:
    hr_uid, company_id = ctx
    now = datetime.now(tz=UTC)
    exam = Exam(
        id=uuid.uuid4(),
        company_id=company_id,
        created_by_user_id=hr_uid,
        title=body.title.strip(),
        description=body.description,
        target_job_title=body.target_job_title,
        pass_threshold=body.pass_threshold,
        time_limit_seconds=body.time_limit_seconds,
        allow_retake=body.allow_retake,
        status="draft",
        kind=body.kind,
        auto_advance_on_pass=body.auto_advance_on_pass,
        created_at=now,
        updated_at=now,
    )
    db.add(exam)
    await db.flush()  # exam.id available for the default round/section FKs
    await _create_default_round_section(db, exam, kind=body.kind)
    await db.commit()
    log.info("hr.exam.created", exam_id=str(exam.id), company_id=str(company_id), kind=body.kind)
    return _exam_out(exam)


@router.get("/exams", response_model=list[ExamSummaryOut])
async def list_exams(
    ctx: HrCtxDep,
    db: DbSessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[ExamSummaryOut]:
    _hr_uid, company_id = ctx
    stmt = select(Exam).where(Exam.company_id == company_id, Exam.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(Exam.status == status_filter)
    stmt = stmt.order_by(Exam.created_at.desc())
    exams = (await db.execute(stmt)).scalars().all()

    out: list[ExamSummaryOut] = []
    for e in exams:
        qn = await _question_count(db, e)
        an = await _attempt_count(db, company_id, e.id)
        out.append(
            ExamSummaryOut(**_exam_out(e).model_dump(), question_count=qn, attempt_count=an)
        )
    return out


@router.get("/exams/{exam_id}", response_model=ExamDetailOut)
async def get_exam(exam_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> ExamDetailOut:
    _hr_uid, company_id = ctx
    exam = await _get_owned_exam(db, company_id, exam_id)
    questions = await _live_questions(db, company_id, exam_id)
    an = await _attempt_count(db, company_id, exam_id)
    return ExamDetailOut(
        **_exam_out(exam).model_dump(),
        questions=[_question_out(q) for q in questions],
        attempt_count=an,
    )


@router.patch("/exams/{exam_id}", response_model=ExamOut)
async def update_exam(
    exam_id: uuid.UUID, body: ExamUpdateIn, ctx: HrCtxDep, db: DbSessionDep
) -> ExamOut:
    _hr_uid, company_id = ctx
    exam = await _get_owned_exam(db, company_id, exam_id)

    if body.status == "published" and await _question_count(db, exam) < 1:
        raise HTTPException(
            status_code=400, detail="Add at least one question before publishing."
        )

    if body.title is not None:
        exam.title = body.title.strip()
    if body.description is not None:
        exam.description = body.description
    if body.target_job_title is not None:
        exam.target_job_title = body.target_job_title
    if body.pass_threshold is not None:
        exam.pass_threshold = body.pass_threshold
    if body.time_limit_seconds is not None:
        exam.time_limit_seconds = body.time_limit_seconds
    if body.allow_retake is not None:
        exam.allow_retake = body.allow_retake
    if body.auto_advance_on_pass is not None:
        exam.auto_advance_on_pass = body.auto_advance_on_pass
    if body.status is not None:
        exam.status = body.status
    exam.updated_at = datetime.now(tz=UTC)
    await db.commit()
    return _exam_out(exam)


# ---------------------------------------------------------------------------
# Questions (locked once attempts exist)
# ---------------------------------------------------------------------------
@router.post(
    "/exams/{exam_id}/questions", status_code=status.HTTP_201_CREATED, response_model=QuestionOut
)
async def add_question(
    exam_id: uuid.UUID, body: QuestionIn, ctx: HrCtxDep, db: DbSessionDep
) -> QuestionOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await exam_locks.assert_editable_by_exam(db, company_id, exam_id)
    # Back-compat: target the exam's default MCQ section.
    section = await _default_section(db, company_id, exam_id, "mcq")

    max_pos = await db.scalar(
        select(func.max(ExamQuestion.position)).where(
            ExamQuestion.section_id == section.id, ExamQuestion.deleted_at.is_(None)
        )
    )
    now = datetime.now(tz=UTC)
    q = ExamQuestion(
        id=uuid.uuid4(),
        exam_id=exam_id,
        section_id=section.id,
        company_id=company_id,
        prompt=body.prompt.strip(),
        options=body.options,
        correct_index=body.correct_index,
        points=body.points,
        position=(int(max_pos) + 1) if max_pos is not None else 0,
        created_at=now,
        updated_at=now,
    )
    db.add(q)
    await exam_locks.commit_or_conflict(db)
    return _question_out(q)


@router.patch("/exams/{exam_id}/questions/{qid}", response_model=QuestionOut)
async def update_question(
    exam_id: uuid.UUID, qid: uuid.UUID, body: QuestionUpdateIn, ctx: HrCtxDep, db: DbSessionDep
) -> QuestionOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await exam_locks.assert_editable_by_exam(db, company_id, exam_id)
    q = await _get_owned_question(db, company_id, exam_id, qid)

    new_options = body.options if body.options is not None else list(q.options or [])
    new_correct = body.correct_index if body.correct_index is not None else q.correct_index
    if new_correct >= len(new_options):
        raise HTTPException(status_code=400, detail="correct_index out of range for options.")

    if body.prompt is not None:
        q.prompt = body.prompt.strip()
    q.options = new_options
    q.correct_index = new_correct
    if body.points is not None:
        q.points = body.points
    q.updated_at = datetime.now(tz=UTC)
    await exam_locks.commit_or_conflict(db)
    return _question_out(q)


@router.delete("/exams/{exam_id}/questions/{qid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    exam_id: uuid.UUID, qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> Response:
    _hr_uid, company_id = ctx
    exam = await _get_owned_exam(db, company_id, exam_id)
    await exam_locks.assert_editable_by_exam(db, company_id, exam_id)
    q = await _get_owned_question(db, company_id, exam_id, qid)

    live = await _live_questions(db, company_id, exam_id)
    if exam.status == "published" and len(live) <= 1:
        raise HTTPException(
            status_code=400,
            detail="A published exam must keep at least one question. Unpublish first.",
        )
    q.deleted_at = datetime.now(tz=UTC)
    await exam_locks.commit_or_conflict(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/exams/{exam_id}/questions/order", response_model=list[QuestionOut])
async def reorder_questions(
    exam_id: uuid.UUID, body: ReorderIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await exam_locks.assert_editable_by_exam(db, company_id, exam_id)

    live = await _live_questions(db, company_id, exam_id)
    if {q.id for q in live} != set(body.question_ids):
        raise HTTPException(
            status_code=400, detail="question_ids must be exactly the exam's live questions."
        )
    by_id = {q.id: q for q in live}
    # Two-phase to dodge the live (exam_id, position) partial-unique index: park
    # all rows at a high offset, flush, then assign the final 0..n-1 positions.
    for idx, qid in enumerate(body.question_ids):
        by_id[qid].position = 10_000 + idx
    await db.flush()
    now = datetime.now(tz=UTC)
    for idx, qid in enumerate(body.question_ids):
        by_id[qid].position = idx
        by_id[qid].updated_at = now
    await exam_locks.commit_or_conflict(db)
    return [_question_out(by_id[qid]) for qid in body.question_ids]


# ---------------------------------------------------------------------------
# Bulk add / AI generate / Excel import (locked once attempts exist)
# ---------------------------------------------------------------------------
@router.post(
    "/exams/{exam_id}/questions/bulk",
    status_code=status.HTTP_201_CREATED,
    response_model=list[QuestionOut],
)
async def bulk_add_questions(
    exam_id: uuid.UUID, body: BulkQuestionsIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    """Append many questions at once (AI-generate 'add all', or a reviewed import)."""
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await exam_locks.assert_editable_by_exam(db, company_id, exam_id)
    section = await _default_section(db, company_id, exam_id, "mcq")
    created = await _bulk_insert_questions(db, company_id, section, body.questions)
    log.info(
        "hr.exam.questions.bulk_added",
        exam_id=str(exam_id), company_id=str(company_id), count=len(created),
    )
    return [_question_out(q) for q in created]


@router.post("/exams/{exam_id}/questions/generate", response_model=GenerateQuestionsOut)
async def generate_questions(
    exam_id: uuid.UUID, body: GenerateQuestionsIn, ctx: HrCtxDep, db: DbSessionDep
) -> GenerateQuestionsOut:
    """Draft MCQs with Gemini (via feedback_billing). Returned for PREVIEW — not saved.
    HR reviews them, then persists the wanted ones via the bulk endpoint."""
    hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)

    try:
        raw = await generate_exam_questions_remote(
            topic=body.topic,
            num_questions=body.num_questions,
            difficulty=body.difficulty,
            language=body.language,
            acting_user_id=str(hr_uid),
            job_title=body.job_title,
            experience_level=body.experience_level,
            round_rubric=(
                await round_rubric_for_generation(
                    db, company_id=company_id, workflow_round_id=body.workflow_round_id
                )
                if body.workflow_round_id
                # Not named: use the round this exam belongs to, when exactly
                # one does. The authoring screen does not know it; the data does.
                else await rubric_for_exam(db, company_id=company_id, exam_id=exam_id)
            ),
        )
    except ExamGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI generation failed: {exc}"
        ) from exc

    out: list[GeneratedQuestionOut] = []
    for q in raw:
        opts = [str(o) for o in (q.get("options") or [])]
        try:
            ci = int(q.get("correct_index", 0))
        except (TypeError, ValueError):
            continue
        prompt = str(q.get("prompt") or "").strip()
        if prompt and opts and 0 <= ci < len(opts):
            out.append(
                GeneratedQuestionOut(prompt=prompt, options=opts, correct_index=ci, points=1)
            )
    if not out:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI returned no usable questions. Try again or refine the topic.",
        )
    log.info(
        "hr.exam.questions.generated",
        exam_id=str(exam_id), company_id=str(company_id), count=len(out),
    )
    return GenerateQuestionsOut(questions=out)


@router.post(
    "/exams/{exam_id}/questions/import",
    status_code=status.HTTP_201_CREATED,
    response_model=ImportQuestionsOut,
)
async def import_questions(
    exam_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
    file: Annotated[UploadFile, File(description=".xlsx or .csv in the template layout")],
) -> ImportQuestionsOut:
    """Bulk-import questions from an uploaded Excel/CSV in the template layout.

    Valid rows are inserted; malformed rows are reported (partial success).

    CAPPED AT ``question_import.MAX_ROWS`` (1000) SINCE 2026-10-03, and stated
    here because it was not: this route was previously uncapped, and sharing the
    parser with the bank importer brought the cap with it. A larger sheet is not
    silently truncated — the row it stopped at comes back in ``errors`` with
    "split the file and import the rest" — but the limit is new, so a 1200-row
    sheet that used to import whole now needs two passes.
    """
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await exam_locks.assert_editable_by_exam(db, company_id, exam_id)

    # BOUNDED READ: read(limit + 1), not read(). An unbounded read pulls the
    # entire spooled body into memory and only THEN measures it, so the cap
    # described the request without limiting it. One byte past the limit is all
    # it takes to know it is over. Same pattern as the pre-auth paths in
    # public_apply.py and the apply door's `read(_MAX_RESUME_BYTES + 1)`
    # (review 2026-10-06).
    #
    # WHAT IT DOES NOT DO, because the first version of this comment claimed
    # it did. FastAPI resolves an `UploadFile` parameter during dependency
    # solving, which runs starlette's multipart parser to completion BEFORE
    # the first line of this function — into a `SpooledTemporaryFile` that
    # rolls to disk past 1 MB. So the service still receives and spools the
    # whole body; measured at 20 MB with `file.size == 20971520` and the spool
    # rolled. This bounds memory in the handler, not what arrives.
    #
    # The half that actually bounds arrival is an edge cap, and `handle /hr/*`
    # NOW HAS ONE — 12MB, added by the upload-hardening change on `main`
    # (2026-10-06) and sized from the largest single document behind the prefix.
    # This comment said it had none, which was true when it was written and was
    # false within a day; `ops/ci/check_routing_contract.py`'s BODY_CAPS is the
    # machine-checked copy of that list, and it is the thing that caught this on
    # merge. Prose about which prefixes are capped goes stale — read BODY_CAPS.
    content = await file.read(_MAX_IMPORT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 2 MB).")

    rows = await asyncio.to_thread(_read_spreadsheet, file.filename or "", content)
    items, errors = _parse_question_rows(rows)
    if not items and not errors:
        raise HTTPException(
            status_code=400, detail="No question rows found. Use the template layout."
        )
    section = await _default_section(db, company_id, exam_id, "mcq")
    created = (
        await _bulk_insert_questions(db, company_id, section, items) if items else []
    )
    log.info(
        "hr.exam.questions.imported",
        exam_id=str(exam_id), company_id=str(company_id),
        added=len(created), errors=len(errors),
    )
    return ImportQuestionsOut(
        added=len(created),
        errors=errors,
        questions=[_question_out(q) for q in created],
    )


@router.get("/exam-question-template")
async def download_question_template(ctx: HrCtxDep) -> Response:
    """Download the .xlsx bulk-upload template (header + two example rows).

    Distinct top-level path (NOT /exams/...) so it never collides with the
    /exams/{exam_id} UUID route.
    """
    try:
        import openpyxl  # lazy import — only this route needs it
    except ImportError as exc:  # pragma: no cover - dep is in requirements
        raise HTTPException(
            status_code=500, detail="Excel support is not installed on the server."
        ) from exc
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(_TEMPLATE_HEADER)
    ws.append(["What is 2 + 2?", "3", "4", "5", "6", "B", "1"])
    ws.append(["Capital of France?", "Paris", "Rome", "Berlin", "Madrid", "A", "1"])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": 'attachment; filename="exam-questions-template.xlsx"'
        },
    )


# ---------------------------------------------------------------------------
# Assignments (magic-link mint / list / revoke)
# ---------------------------------------------------------------------------
@router.post(
    "/exams/{exam_id}/assignments", status_code=status.HTTP_201_CREATED, response_model=list[AssignOut]
)
async def assign_exam(
    exam_id: uuid.UUID, body: AssignIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[AssignOut]:
    hr_uid, company_id = ctx
    exam = await _get_owned_exam(db, company_id, exam_id)

    # Resolve the target round (specified or the exam's first round). The token
    # grants exactly ONE round, and the ROUND is the unit that gets published +
    # scheduled separately — so the publish gate is on the round, not the exam.
    if body.round_id is not None:
        rnd = await db.scalar(
            select(ExamRound).where(
                ExamRound.id == body.round_id,
                ExamRound.exam_id == exam_id,
                ExamRound.company_id == company_id,
                ExamRound.deleted_at.is_(None),
            )
        )
        if rnd is None:
            raise HTTPException(status_code=404, detail="Round not found for this exam.")
    else:
        rnd = await _default_round(db, company_id, exam_id)

    if rnd.status != "published":
        raise HTTPException(status_code=409, detail="Publish this round before assigning it.")

    now = datetime.now(tz=UTC)
    if body.scheduled_at is not None and body.scheduled_at < now:
        raise HTTPException(status_code=422, detail="scheduled_at cannot be in the past.")

    ttl_hours = body.ttl_hours or settings.exam_link_ttl_hours
    base_expires_at = now + timedelta(hours=ttl_hours)
    base = settings.exam_link_base_url.rstrip("/")

    if not body.applicant_ids and not body.enrolment_ids:
        raise HTTPException(status_code=422, detail="Choose at least one candidate.")
    # (person, application or None). A named application resolves to its person
    # inside this company only, so a foreign enrolment id is skipped like a
    # foreign applicant id.
    targets: list[tuple[uuid.UUID, uuid.UUID | None]] = [(a, None) for a in body.applicant_ids]
    if body.enrolment_ids:
        owned = (
            await db.execute(
                text(
                    "SELECT id, applicant_id FROM enrolments"
                    " WHERE id = ANY(:ids) AND company_id = :c AND deleted_at IS NULL"
                ),
                {"ids": list(body.enrolment_ids), "c": company_id},
            )
        ).all()
        by_id = {r[0]: r[1] for r in owned}
        targets += [(by_id[e], e) for e in body.enrolment_ids if e in by_id]

    out: list[AssignOut] = []
    for applicant_id, named_enrolment in targets:
        applicant = await db.scalar(
            select(Applicant).where(
                Applicant.id == applicant_id,
                Applicant.company_id == company_id,  # tenant scope: skip foreign applicants
                Applicant.deleted_at.is_(None),
            )
        )
        if applicant is None:
            continue  # not this company's applicant — silently skip

        # Rotate: revoke any existing active (invited) assignment for this
        # (round, applicant) so only one live link exists per round (the active
        # partial-unique index is now scoped to round_id).
        prior = await db.scalar(
            select(ExamAssignment).where(
                ExamAssignment.round_id == rnd.id,
                ExamAssignment.applicant_id == applicant_id,
                ExamAssignment.company_id == company_id,
                ExamAssignment.status == "invited",
                ExamAssignment.deleted_at.is_(None),
            )
        )
        if prior is not None:
            prior.status = "revoked"
            prior.updated_at = now
            await db.flush()

        resolved_enrolment_id = named_enrolment or await enrolment_for_exam_round(
            db, applicant_id=applicant_id, company_id=company_id, exam_round_id=rnd.id
        )
        # PH4-D2: any recorded deadline extension applies to THIS applicant's
        # link — the exam's own time limit is scaled separately, at /exam/start.
        adj_row = await accommodations.effective_for(
            db, company_id=company_id, applicant_id=applicant_id,
            enrolment_id=resolved_enrolment_id, exam_round_id=rnd.id,
        )
        extra_days = adj_row.deadline_extension_days if adj_row else None
        expires_at = base_expires_at + timedelta(days=extra_days or 0)

        raw_token = mint_exam_token()
        asn = ExamAssignment(
            id=uuid.uuid4(),
            company_id=company_id,
            exam_id=exam_id,
            round_id=rnd.id,
            applicant_id=applicant_id,
            # B5: which application this exam is for, when that can be known
            # without guessing — so its result shows against that opening.
            enrolment_id=resolved_enrolment_id,
            created_by_user_id=hr_uid,
            token_hash=hash_exam_token(raw_token, settings.exam_link_secret),
            expires_at=expires_at,
            scheduled_at=body.scheduled_at,
            accommodation_id=adj_row.id if adj_row else None,
            status="invited",
            created_at=now,
            updated_at=now,
        )
        db.add(asn)
        await db.flush()
        if adj_row is not None:
            await accommodations.record_applied(
                db, company_id=company_id, accommodation_id=adj_row.id,
                target_kind="exam_assignment", target_id=asn.id,
            )
        magic_link = f"{base}/exam#{raw_token}"  # raw token returned ONCE
        # Email the candidate their exam link (staged on this transaction →
        # atomic with the assignment, then delivered by the outbox worker). HR
        # still gets the link in the response to share manually if needed.
        await enqueue_email(
            db,
            to=applicant.email,
            template="exam_link",
            lang=await candidate_language(db, applicant.id),
            ctx={
                "name": applicant.full_name,
                "exam_title": exam.title,
                "exam_url": magic_link,
                "when": (
                    body.scheduled_at.strftime("%d %b %Y, %H:%M UTC")
                    if body.scheduled_at else None
                ),
                "expires": expires_at.strftime("%d %b %Y, %H:%M UTC"),
            },
            company_id=company_id,
            related_kind="exam_assignment",
            related_id=asn.id,
        )
        out.append(
            AssignOut(
                assignment_id=str(asn.id),
                applicant_id=str(applicant_id),
                enrolment_id=str(asn.enrolment_id) if asn.enrolment_id else None,
                applicant_name=applicant.full_name,
                magic_link=magic_link,
                expires_at=expires_at.isoformat(),
                status=asn.status,
                round_id=str(rnd.id),
                scheduled_at=body.scheduled_at.isoformat() if body.scheduled_at else None,
            )
        )

    if not out:
        raise HTTPException(status_code=400, detail="No valid applicants in your company.")
    await db.commit()
    log.info(
        "hr.exam.assigned", exam_id=str(exam_id), company_id=str(company_id), count=len(out)
    )
    return out


@router.get("/exams/{exam_id}/assignments", response_model=list[AssignmentOut])
async def list_assignments(
    exam_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[AssignmentOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    rows = (
        await db.execute(
            select(ExamAssignment, Applicant.full_name)
            .join(Applicant, Applicant.id == ExamAssignment.applicant_id)
            .where(
                ExamAssignment.exam_id == exam_id,
                ExamAssignment.company_id == company_id,
                ExamAssignment.deleted_at.is_(None),
            )
            .order_by(ExamAssignment.created_at.desc())
        )
    ).all()
    # An assignment past its expiry that was never completed is effectively dead
    # (the take endpoint already 404s it) — surface it as 'expired' rather than a
    # stale 'invited'/'started' so HR sees real link state.
    now = datetime.now(tz=UTC)
    return [
        AssignmentOut(
            assignment_id=str(a.id),
            applicant_id=str(a.applicant_id),
            applicant_name=name,
            status=(
                "expired"
                if a.status in ("invited", "started") and a.expires_at <= now
                else a.status
            ),
            expires_at=a.expires_at.isoformat(),
            consumed_at=a.consumed_at.isoformat() if a.consumed_at else None,
            created_at=a.created_at.isoformat(),
        )
        for a, name in rows
    ]


@router.post("/exams/{exam_id}/assignments/{aid}/revoke", response_model=AssignmentOut)
async def revoke_assignment(
    exam_id: uuid.UUID, aid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> AssignmentOut:
    _hr_uid, company_id = ctx
    asn = await db.scalar(
        select(ExamAssignment).where(
            ExamAssignment.id == aid,
            ExamAssignment.exam_id == exam_id,
            ExamAssignment.company_id == company_id,
            ExamAssignment.deleted_at.is_(None),
        )
    )
    if asn is None:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    asn.status = "revoked"  # the token dies immediately on the take path
    asn.updated_at = datetime.now(tz=UTC)
    await db.commit()
    applicant_name = await db.scalar(
        select(Applicant.full_name).where(Applicant.id == asn.applicant_id)
    )
    return AssignmentOut(
        assignment_id=str(asn.id),
        applicant_id=str(asn.applicant_id),
        applicant_name=applicant_name or "",
        status=asn.status,
        expires_at=asn.expires_at.isoformat(),
        consumed_at=asn.consumed_at.isoformat() if asn.consumed_at else None,
        created_at=asn.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@router.get("/exams/{exam_id}/attempts", response_model=list[AttemptResultOut])
async def list_attempts(
    exam_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
    passed: Annotated[bool | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[AttemptResultOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    stmt = (
        select(ExamAttempt, Applicant.full_name)
        .join(Applicant, Applicant.id == ExamAttempt.applicant_id)
        .where(
            ExamAttempt.exam_id == exam_id,
            ExamAttempt.company_id == company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    if passed is not None:
        stmt = stmt.where(ExamAttempt.passed.is_(passed))
    if status_filter:
        stmt = stmt.where(ExamAttempt.status == status_filter)
    stmt = stmt.order_by(ExamAttempt.score_percent.desc().nullslast(), ExamAttempt.submitted_at.desc())
    rows = (await db.execute(stmt)).all()
    return [
        AttemptResultOut(
            attempt_id=str(at.id),
            applicant_id=str(at.applicant_id),
            applicant_name=name,
            score_raw=at.score_raw,
            score_max=at.score_max,
            score_percent=at.score_percent,
            passed=at.passed,
            status=at.status,
            submitted_at=at.submitted_at.isoformat() if at.submitted_at else None,
            attempt_no=at.attempt_no,
            integrity_score=at.integrity_score,
            camera_in_use=at.camera_in_use,
        )
        for at, name in rows
    ]


@router.get(
    "/exams/{exam_id}/attempts/{aid}/proctoring", response_model=AttemptProctoringOut
)
async def attempt_proctoring(
    exam_id: uuid.UUID, aid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> AttemptProctoringOut:
    """The attempt's full proctoring picture — score, per-type counts, and the
    time-ordered events, each carrying its duration when it is a ranged
    (camera) event. Camera-proctoring contract §7. Flags inform HR; nothing
    here sets a stage, a status or a decision (CLAUDE.md hard constraint 9) —
    HR reads this and decides."""
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    at = await db.scalar(
        select(ExamAttempt).where(
            ExamAttempt.id == aid,
            ExamAttempt.exam_id == exam_id,
            ExamAttempt.company_id == company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    if at is None:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    rows = (
        await db.execute(
            select(
                ExamIntegrityEvent.event_type,
                ExamIntegrityEvent.started_at,
                ExamIntegrityEvent.ended_at,
            )
            .where(ExamIntegrityEvent.attempt_id == at.id)
            .order_by(ExamIntegrityEvent.started_at.asc())
        )
    ).all()
    # Counts are derived from the rows just read, NOT from the frozen
    # exam_attempts.proctoring_summary JSON. The two agree today — the ingest
    # recomputes that column with this same aggregation on every post, and
    # exam_integrity_events sits in the erasure executor's EXCLUDED_TABLES with
    # no retention clock of its own, so nothing deletes a row. But "nothing
    # deletes a row" is a project-wide convention, not something this endpoint
    # can enforce, and the failure mode if it ever stops holding is a screen
    # that lies to a reviewer: a count with no event in the timeline to back it
    # up. Deriving both numbers from one read makes the panel honest by
    # construction rather than by coupling.
    counts: dict[str, int] = dict(Counter(etype for etype, _started, _ended in rows))
    events = [
        ProctoringEventOut(
            event_type=etype,
            started_at=started.isoformat(),
            ended_at=ended.isoformat() if ended else None,
            duration_seconds=(
                round((ended - started).total_seconds(), 1) if ended is not None else None
            ),
        )
        for etype, started, ended in rows
    ]
    summary = at.proctoring_summary if isinstance(at.proctoring_summary, dict) else {}
    return AttemptProctoringOut(
        camera_in_use=at.camera_in_use,
        integrity_score=at.integrity_score,
        counts=counts,
        events=events,
        events_dropped=summary.get("events_dropped") is True,
    )


@router.get("/exams/{exam_id}/attempts/{aid}/breakdown")
async def attempt_breakdown(
    exam_id: uuid.UUID, aid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    """HR-ONLY per-question correctness, from the frozen graded_snapshot.

    This is the ONLY endpoint that exposes which answers were right — never the
    applicant take/submit path.
    """
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    at = await db.scalar(
        select(ExamAttempt).where(
            ExamAttempt.id == aid,
            ExamAttempt.exam_id == exam_id,
            ExamAttempt.company_id == company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    if at is None:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    snapshot: dict[str, Any] = at.graded_snapshot or {}
    raw_answers: dict[str, Any] = at.answers or {}
    # New (round) attempts nest under 'mcq'/'coding'; legacy flat attempts are
    # {question_id: meta} with answers {question_id: index}. Detect + normalize.
    is_nested = bool(snapshot) and set(snapshot.keys()) <= {"mcq", "coding"}
    if is_nested:
        mcq_snapshot: dict[str, Any] = snapshot.get("mcq", {}) or {}
        mcq_answers_src = raw_answers.get("mcq", {}) if isinstance(raw_answers, dict) else {}
        coding_snapshot: dict[str, Any] = snapshot.get("coding", {}) or {}
    else:
        mcq_snapshot = snapshot
        mcq_answers_src = raw_answers
        coding_snapshot = {}
    answers: dict[str, int] = {k: int(v) for k, v in dict(mcq_answers_src).items()}
    questions = [
        GradeQuestion(
            question_id=qid,
            correct_index=int(meta.get("correct_index", -1)),
            points=int(meta.get("points", 1)),
        )
        for qid, meta in mcq_snapshot.items()
    ]
    per_question = grade_breakdown(GradeInput(questions=questions, answers=answers))
    return {
        "attempt_id": str(at.id),
        "score_percent": at.score_percent,
        "passed": at.passed,
        "per_question": per_question,
        # Cut down exactly as the code-evidence tab is: scores, language,
        # submitted, error, and each test reduced to pass/fail. The attempt
        # page shows nothing more, and the raw snapshot carried every test's
        # stdout/stderr and the hidden cases' inputs and expected outputs
        # (security review, D3 M2).
        "coding": coding_results_for_screen(coding_snapshot),
        "adjustment": await _attempt_adjustment(db, company_id, at),
    }


async def _attempt_adjustment(
    db: AsyncSession, company_id: uuid.UUID, at: ExamAttempt
) -> dict[str, Any] | None:
    """What this attempt was actually given (PH4-D2), or None if nothing.

    Read off the attempt, which froze the allowance when it started -- never
    re-resolved from the applicant's history, which may have been revised or
    revoked since and would show the wrong adjustment for this attempt. The
    percentage comes from the exact accommodation row the attempt points at,
    which is the version that was applied.

    Facts only: no note, no basis, no recorder. This is what the attempt was
    given, not why.
    """
    if at.accommodation_id is None and not at.extra_time_seconds and not at.auto_submit_relaxed:
        return None
    pct: int | None = None
    if at.accommodation_id is not None:
        pct = await db.scalar(
            text("SELECT extra_time_percent FROM candidate_accommodations"
                 " WHERE id = :i AND company_id = :c"),
            {"i": at.accommodation_id, "c": company_id},
        )
    return {
        "extra_time_percent": pct,
        "extra_time_seconds": int(at.extra_time_seconds or 0),
        "auto_submit_relaxed": bool(at.auto_submit_relaxed),
    }
