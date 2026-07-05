"""HR coding-question authoring — HR workflow Phase 2 (coding round).

The coding analog of the MCQ authoring in hr_exams.py. An HR manager adds coding
questions to an exam of kind='coding'; the candidate solves them in an editor and
they are graded by running the code (Piston) against test cases. Reuses the exact
tenant-isolation, ownership-404, and attempt-lock patterns from the MCQ side.

SECURITY: reference_solution + hidden test cases are HR-only here; the candidate
take path (exam_take.py) strips them, exactly as it strips correct_index for MCQ.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.execution import SUPPORTED_LANGUAGES
from app.models import CodingQuestion, Exam
from app.routers.hr_applicants import DbSessionDep, HrCtxDep
from app.routers.hr_exams import _default_section, _get_owned_exam, _require_no_attempts

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-coding"])


def _normalize_languages(v: list[str]) -> list[str]:
    """Lowercase, dedupe, and reject any language Piston can't run."""
    seen: list[str] = []
    for lang in v:
        slug = (lang or "").lower().strip()
        if slug not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language '{lang}'")
        if slug not in seen:
            seen.append(slug)
    if not seen:
        raise ValueError("at least one supported language is required")
    return seen


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class TestCaseIn(BaseModel):
    stdin: str = Field(default="", max_length=20_000)
    expected_output: str = Field(default="", max_length=20_000)
    is_sample: bool = False
    weight: int = Field(default=1, ge=1, le=100)


class CodingQuestionIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    allowed_languages: list[str] = Field(min_length=1, max_length=len(SUPPORTED_LANGUAGES))
    starter_code: str | None = Field(default=None, max_length=40_000)
    reference_solution: str | None = Field(default=None, max_length=40_000)
    test_cases: list[TestCaseIn] = Field(min_length=1, max_length=settings.code_max_test_cases)
    time_limit_ms: int = Field(default=5000, ge=100, le=15_000)
    points: int = Field(default=100, ge=1, le=1000)

    @field_validator("allowed_languages")
    @classmethod
    def _validate_languages(cls, v: list[str]) -> list[str]:
        return _normalize_languages(v)

    @model_validator(mode="after")
    def _require_sample(self) -> CodingQuestionIn:
        if not any(tc.is_sample for tc in self.test_cases):
            raise ValueError("at least one test case must be a sample (shown to the candidate)")
        return self


class CodingQuestionUpdateIn(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    allowed_languages: list[str] | None = Field(default=None, min_length=1)
    starter_code: str | None = Field(default=None, max_length=40_000)
    reference_solution: str | None = Field(default=None, max_length=40_000)
    test_cases: list[TestCaseIn] | None = Field(
        default=None, min_length=1, max_length=settings.code_max_test_cases
    )
    time_limit_ms: int | None = Field(default=None, ge=100, le=15_000)
    points: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("allowed_languages")
    @classmethod
    def _validate_languages(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _normalize_languages(v)

    @model_validator(mode="after")
    def _require_sample(self) -> CodingQuestionUpdateIn:
        # A PATCH that REPLACES test_cases must still include a sample.
        if self.test_cases is not None and not any(tc.is_sample for tc in self.test_cases):
            raise ValueError("at least one test case must be a sample (shown to the candidate)")
        return self


class CodingQuestionOut(BaseModel):
    """HR-facing — INCLUDES reference_solution + ALL test cases (never on take path)."""

    id: str
    prompt: str
    allowed_languages: list[str]
    starter_code: str | None
    reference_solution: str | None
    test_cases: list[dict[str, Any]]
    time_limit_ms: int
    points: int
    position: int


# ---------------------------------------------------------------------------
# Helpers (tenant isolation mirrors hr_exams)
# ---------------------------------------------------------------------------
async def _get_coding_exam(db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID) -> Exam:
    """Owned exam that must be a coding exam (400 if it's an MCQ exam)."""
    exam = await _get_owned_exam(db, company_id, exam_id)
    if exam.kind != "coding":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This is not a coding exam.",
        )
    return exam


async def _get_owned_coding_question(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID, qid: uuid.UUID
) -> CodingQuestion:
    q = await db.scalar(
        select(CodingQuestion).where(
            CodingQuestion.id == qid,
            CodingQuestion.exam_id == exam_id,
            CodingQuestion.company_id == company_id,
            CodingQuestion.deleted_at.is_(None),
        )
    )
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    return q


def _test_cases_payload(items: list[TestCaseIn]) -> list[dict[str, Any]]:
    return [
        {
            "stdin": tc.stdin,
            "expected_output": tc.expected_output,
            "is_sample": tc.is_sample,
            "weight": tc.weight,
        }
        for tc in items
    ]


def _coding_out(q: CodingQuestion) -> CodingQuestionOut:
    return CodingQuestionOut(
        id=str(q.id),
        prompt=q.prompt,
        allowed_languages=list(q.allowed_languages or []),
        starter_code=q.starter_code,
        reference_solution=q.reference_solution,
        test_cases=list(q.test_cases or []),
        time_limit_ms=q.time_limit_ms,
        points=q.points,
        position=q.position,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/exams/{exam_id}/coding-questions", response_model=list[CodingQuestionOut])
async def list_coding_questions(
    exam_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[CodingQuestionOut]:
    _hr_uid, company_id = ctx
    await _get_coding_exam(db, company_id, exam_id)
    rows = (
        await db.execute(
            select(CodingQuestion)
            .where(
                CodingQuestion.exam_id == exam_id,
                CodingQuestion.company_id == company_id,
                CodingQuestion.deleted_at.is_(None),
            )
            .order_by(CodingQuestion.position.asc())
        )
    ).scalars().all()
    return [_coding_out(q) for q in rows]


@router.post(
    "/exams/{exam_id}/coding-questions",
    status_code=status.HTTP_201_CREATED,
    response_model=CodingQuestionOut,
)
async def add_coding_question(
    exam_id: uuid.UUID, body: CodingQuestionIn, ctx: HrCtxDep, db: DbSessionDep
) -> CodingQuestionOut:
    _hr_uid, company_id = ctx
    await _get_coding_exam(db, company_id, exam_id)
    await _require_no_attempts(db, company_id, exam_id)
    # Back-compat: target the exam's default coding section.
    section = await _default_section(db, company_id, exam_id, "coding")

    max_pos = await db.scalar(
        select(func.max(CodingQuestion.position)).where(
            CodingQuestion.section_id == section.id, CodingQuestion.deleted_at.is_(None)
        )
    )
    now = datetime.now(tz=UTC)
    q = CodingQuestion(
        id=uuid.uuid4(),
        exam_id=exam_id,
        section_id=section.id,
        company_id=company_id,
        prompt=body.prompt.strip(),
        starter_code=body.starter_code,
        reference_solution=body.reference_solution,
        allowed_languages=body.allowed_languages,
        test_cases=_test_cases_payload(body.test_cases),
        time_limit_ms=body.time_limit_ms,
        points=body.points,
        position=(int(max_pos) + 1) if max_pos is not None else 0,
        created_at=now,
        updated_at=now,
    )
    db.add(q)
    await db.commit()
    log.info("hr.coding_question.created", exam_id=str(exam_id), company_id=str(company_id))
    return _coding_out(q)


@router.patch(
    "/exams/{exam_id}/coding-questions/{qid}", response_model=CodingQuestionOut
)
async def update_coding_question(
    exam_id: uuid.UUID,
    qid: uuid.UUID,
    body: CodingQuestionUpdateIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> CodingQuestionOut:
    _hr_uid, company_id = ctx
    await _get_coding_exam(db, company_id, exam_id)
    await _require_no_attempts(db, company_id, exam_id)
    q = await _get_owned_coding_question(db, company_id, exam_id, qid)

    if body.prompt is not None:
        q.prompt = body.prompt.strip()
    if body.allowed_languages is not None:
        q.allowed_languages = body.allowed_languages
    if body.starter_code is not None:
        q.starter_code = body.starter_code
    if body.reference_solution is not None:
        q.reference_solution = body.reference_solution
    if body.test_cases is not None:
        q.test_cases = _test_cases_payload(body.test_cases)
    if body.time_limit_ms is not None:
        q.time_limit_ms = body.time_limit_ms
    if body.points is not None:
        q.points = body.points
    q.updated_at = datetime.now(tz=UTC)
    await db.commit()
    return _coding_out(q)


@router.delete(
    "/exams/{exam_id}/coding-questions/{qid}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_coding_question(
    exam_id: uuid.UUID, qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> Response:
    _hr_uid, company_id = ctx
    exam = await _get_coding_exam(db, company_id, exam_id)
    await _require_no_attempts(db, company_id, exam_id)
    q = await _get_owned_coding_question(db, company_id, exam_id, qid)

    live = await db.scalar(
        select(func.count()).select_from(CodingQuestion).where(
            CodingQuestion.exam_id == exam_id,
            CodingQuestion.company_id == company_id,
            CodingQuestion.deleted_at.is_(None),
        )
    )
    if exam.status == "published" and int(live or 0) <= 1:
        raise HTTPException(
            status_code=400,
            detail="A published exam must keep at least one question. Unpublish first.",
        )
    # Soft-delete: the partial-unique index is WHERE deleted_at IS NULL, so a
    # deleted row leaves its (exam_id, position) slot free for re-use.
    q.deleted_at = datetime.now(tz=UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
