"""HR exam STRUCTURE authoring — rounds, sections, section-scoped questions.

The flexible (multi-round / multi-section / mixed-type) layer on top of the flat
exam authoring in hr_exams.py + hr_coding.py:

    Exam ── ExamRound* ── ExamSection* (kind: mcq|coding) ── questions

Reuses the EXACT tenant-isolation (company_id scope → 404), ownership, and
attempt-lock discipline of hr_exams.py. Questions lock (409) once any attempt
exists on the ROUND. Rounds are the unit HR schedules/assigns separately (the
per-round magic link lives in hr_exams.assign_exam, which accepts a round_id).

This is the PRIMARY authoring API the round-aware HR UI uses; the legacy
exam-scoped routes in hr_exams.py / hr_coding.py remain as back-compat shims onto
the exam's default round/section.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CodingQuestion,
    ExamAttempt,
    ExamQuestion,
    ExamRound,
    ExamSection,
)
from app.routers.hr_applicants import DbSessionDep, HrCtxDep
from app.routers.hr_coding import (
    CodingQuestionIn,
    CodingQuestionOut,
    _coding_out,
    _test_cases_payload,
)
from app.routers.hr_exams import (
    QuestionIn,
    QuestionOut,
    _bulk_insert_questions,
    _get_owned_exam,
    _question_out,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-rounds"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RoundCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    pass_threshold: int = Field(default=60, ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86_400)
    advances_to_interview: bool = False


class RoundUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pass_threshold: int | None = Field(default=None, ge=0, le=100)
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86_400)
    advances_to_interview: bool | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in {"draft", "published"}:
            raise ValueError("status must be draft | published")
        return v


class SectionCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="mcq")
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86_400)

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        v = (v or "mcq").lower()
        if v not in {"mcq", "coding"}:
            raise ValueError("kind must be mcq | coding")
        return v


class SectionUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86_400)


class ReorderIdsIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1)


class SectionOut(BaseModel):
    id: str
    round_id: str
    title: str
    kind: str
    time_limit_seconds: int | None
    position: int
    question_count: int


class RoundOut(BaseModel):
    id: str
    title: str
    round_number: int
    pass_threshold: int
    time_limit_seconds: int | None
    advances_to_interview: bool
    status: str
    position: int
    sections: list[SectionOut]


class ExamStructureOut(BaseModel):
    exam_id: str
    rounds: list[RoundOut]


# ---------------------------------------------------------------------------
# Helpers (tenant isolation mirrors hr_exams)
# ---------------------------------------------------------------------------
async def _get_owned_round(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID, round_id: uuid.UUID
) -> ExamRound:
    rnd = await db.scalar(
        select(ExamRound).where(
            ExamRound.id == round_id,
            ExamRound.exam_id == exam_id,
            ExamRound.company_id == company_id,
            ExamRound.deleted_at.is_(None),
        )
    )
    if rnd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found.")
    return rnd


async def _get_owned_section(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID, section_id: uuid.UUID
) -> ExamSection:
    sec = await db.scalar(
        select(ExamSection).where(
            ExamSection.id == section_id,
            ExamSection.exam_id == exam_id,
            ExamSection.company_id == company_id,
            ExamSection.deleted_at.is_(None),
        )
    )
    if sec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found.")
    return sec


async def _require_no_round_attempts(
    db: AsyncSession, company_id: uuid.UUID, round_id: uuid.UUID
) -> None:
    """A round's structure/questions are immutable once any attempt exists on it."""
    n = await db.scalar(
        select(func.count()).select_from(ExamAttempt).where(
            ExamAttempt.round_id == round_id,
            ExamAttempt.company_id == company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    if int(n or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This round already has attempts — its structure is locked.",
        )


async def _section_question_count(db: AsyncSession, section: ExamSection) -> int:
    model = CodingQuestion if section.kind == "coding" else ExamQuestion
    n = await db.scalar(
        select(func.count()).select_from(model).where(
            model.section_id == section.id, model.deleted_at.is_(None)
        )
    )
    return int(n or 0)


async def _section_out(db: AsyncSession, sec: ExamSection) -> SectionOut:
    return SectionOut(
        id=str(sec.id),
        round_id=str(sec.round_id),
        title=sec.title,
        kind=sec.kind,
        time_limit_seconds=sec.time_limit_seconds,
        position=sec.position,
        question_count=await _section_question_count(db, sec),
    )


async def _round_out(db: AsyncSession, rnd: ExamRound) -> RoundOut:
    secs = (
        await db.execute(
            select(ExamSection)
            .where(
                ExamSection.round_id == rnd.id,
                ExamSection.company_id == rnd.company_id,
                ExamSection.deleted_at.is_(None),
            )
            .order_by(ExamSection.position.asc())
        )
    ).scalars().all()
    return RoundOut(
        id=str(rnd.id),
        title=rnd.title,
        round_number=rnd.round_number,
        pass_threshold=rnd.pass_threshold,
        time_limit_seconds=rnd.time_limit_seconds,
        advances_to_interview=rnd.advances_to_interview,
        status=rnd.status,
        position=rnd.position,
        sections=[await _section_out(db, s) for s in secs],
    )


# ---------------------------------------------------------------------------
# Structure (nested read for the editor)
# ---------------------------------------------------------------------------
@router.get("/exams/{exam_id}/structure", response_model=ExamStructureOut)
async def get_structure(
    exam_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> ExamStructureOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    rounds = (
        await db.execute(
            select(ExamRound)
            .where(
                ExamRound.exam_id == exam_id,
                ExamRound.company_id == company_id,
                ExamRound.deleted_at.is_(None),
            )
            .order_by(ExamRound.position.asc())
        )
    ).scalars().all()
    return ExamStructureOut(
        exam_id=str(exam_id),
        rounds=[await _round_out(db, r) for r in rounds],
    )


# ---------------------------------------------------------------------------
# Round CRUD
# ---------------------------------------------------------------------------
@router.post(
    "/exams/{exam_id}/rounds", status_code=status.HTTP_201_CREATED, response_model=RoundOut
)
async def create_round(
    exam_id: uuid.UUID, body: RoundCreateIn, ctx: HrCtxDep, db: DbSessionDep
) -> RoundOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    max_num = await db.scalar(
        select(func.max(ExamRound.round_number)).where(
            ExamRound.exam_id == exam_id, ExamRound.deleted_at.is_(None)
        )
    )
    nxt = (int(max_num) + 1) if max_num is not None else 1
    now = datetime.now(tz=UTC)
    rnd = ExamRound(
        id=uuid.uuid4(),
        exam_id=exam_id,
        company_id=company_id,
        round_number=nxt,
        title=body.title.strip(),
        pass_threshold=body.pass_threshold,
        time_limit_seconds=body.time_limit_seconds,
        advances_to_interview=body.advances_to_interview,
        status="draft",
        position=nxt,
        created_at=now,
        updated_at=now,
    )
    db.add(rnd)
    await db.commit()
    log.info("hr.exam.round.created", exam_id=str(exam_id), round_id=str(rnd.id))
    return await _round_out(db, rnd)


@router.get("/exams/{exam_id}/rounds", response_model=list[RoundOut])
async def list_rounds(
    exam_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[RoundOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    rounds = (
        await db.execute(
            select(ExamRound)
            .where(
                ExamRound.exam_id == exam_id,
                ExamRound.company_id == company_id,
                ExamRound.deleted_at.is_(None),
            )
            .order_by(ExamRound.position.asc())
        )
    ).scalars().all()
    return [await _round_out(db, r) for r in rounds]


@router.patch("/exams/{exam_id}/rounds/{round_id}", response_model=RoundOut)
async def update_round(
    exam_id: uuid.UUID,
    round_id: uuid.UUID,
    body: RoundUpdateIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> RoundOut:
    _hr_uid, company_id = ctx
    exam = await _get_owned_exam(db, company_id, exam_id)
    rnd = await _get_owned_round(db, company_id, exam_id, round_id)

    if body.status == "published":
        if await _section_count(db, rnd) < 1:
            raise HTTPException(
                status_code=400, detail="Add at least one section before publishing the round."
            )
        if await _round_question_count(db, company_id, rnd) < 1:
            raise HTTPException(
                status_code=400,
                detail="Add at least one question before publishing the round.",
            )

    now = datetime.now(tz=UTC)
    if body.title is not None:
        rnd.title = body.title.strip()
    if body.pass_threshold is not None:
        rnd.pass_threshold = body.pass_threshold
    if body.time_limit_seconds is not None:
        rnd.time_limit_seconds = body.time_limit_seconds
    if body.advances_to_interview is not None:
        rnd.advances_to_interview = body.advances_to_interview
    if body.status is not None:
        rnd.status = body.status
        # Publishing ANY round makes the exam takeable: the candidate take path
        # gates on exam.status='published'. There is no separate exam-level publish
        # in the round model, so auto-publish the parent exam here.
        if body.status == "published" and exam.status != "published":
            exam.status = "published"
            exam.updated_at = now
    rnd.updated_at = now
    await db.commit()
    return await _round_out(db, rnd)


@router.delete("/exams/{exam_id}/rounds/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_round(
    exam_id: uuid.UUID, round_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> Response:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    rnd = await _get_owned_round(db, company_id, exam_id, round_id)
    await _require_no_round_attempts(db, company_id, round_id)

    live = await db.scalar(
        select(func.count()).select_from(ExamRound).where(
            ExamRound.exam_id == exam_id,
            ExamRound.company_id == company_id,
            ExamRound.deleted_at.is_(None),
        )
    )
    if int(live or 0) <= 1:
        raise HTTPException(
            status_code=400, detail="An exam must keep at least one round."
        )
    now = datetime.now(tz=UTC)
    rnd.deleted_at = now
    # Cascade soft-delete the round's sections (DB FK cascade only fires on hard
    # delete; soft-delete must be explicit so child queries stay consistent).
    secs = (
        await db.execute(
            select(ExamSection).where(
                ExamSection.round_id == round_id,
                ExamSection.company_id == company_id,
                ExamSection.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for s in secs:
        s.deleted_at = now
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/exams/{exam_id}/rounds/order", response_model=list[RoundOut])
async def reorder_rounds(
    exam_id: uuid.UUID, body: ReorderIdsIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[RoundOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    live = (
        await db.execute(
            select(ExamRound).where(
                ExamRound.exam_id == exam_id,
                ExamRound.company_id == company_id,
                ExamRound.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if {r.id for r in live} != set(body.ids):
        raise HTTPException(
            status_code=400, detail="ids must be exactly the exam's live rounds."
        )
    by_id = {r.id: r for r in live}
    # Two-phase to dodge the (exam_id, round_number) live partial-unique index.
    for idx, rid in enumerate(body.ids):
        by_id[rid].round_number = 10_000 + idx
        by_id[rid].position = 10_000 + idx
    await db.flush()
    now = datetime.now(tz=UTC)
    for idx, rid in enumerate(body.ids):
        by_id[rid].round_number = idx + 1
        by_id[rid].position = idx + 1
        by_id[rid].updated_at = now
    await db.commit()
    return [await _round_out(db, by_id[rid]) for rid in body.ids]


async def _section_count(db: AsyncSession, rnd: ExamRound) -> int:
    n = await db.scalar(
        select(func.count()).select_from(ExamSection).where(
            ExamSection.round_id == rnd.id, ExamSection.deleted_at.is_(None)
        )
    )
    return int(n or 0)


async def _round_question_count(
    db: AsyncSession, company_id: uuid.UUID, rnd: ExamRound
) -> int:
    """Total live MCQ + coding questions across the round's live sections."""
    section_ids = (
        await db.execute(
            select(ExamSection.id).where(
                ExamSection.round_id == rnd.id,
                ExamSection.company_id == company_id,
                ExamSection.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not section_ids:
        return 0
    mcq = await db.scalar(
        select(func.count()).select_from(ExamQuestion).where(
            ExamQuestion.section_id.in_(section_ids), ExamQuestion.deleted_at.is_(None)
        )
    )
    coding = await db.scalar(
        select(func.count()).select_from(CodingQuestion).where(
            CodingQuestion.section_id.in_(section_ids), CodingQuestion.deleted_at.is_(None)
        )
    )
    return int(mcq or 0) + int(coding or 0)


# ---------------------------------------------------------------------------
# Section CRUD (nested under a round)
# ---------------------------------------------------------------------------
@router.post(
    "/exams/{exam_id}/rounds/{round_id}/sections",
    status_code=status.HTTP_201_CREATED,
    response_model=SectionOut,
)
async def create_section(
    exam_id: uuid.UUID,
    round_id: uuid.UUID,
    body: SectionCreateIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> SectionOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await _get_owned_round(db, company_id, exam_id, round_id)
    await _require_no_round_attempts(db, company_id, round_id)

    max_pos = await db.scalar(
        select(func.max(ExamSection.position)).where(
            ExamSection.round_id == round_id, ExamSection.deleted_at.is_(None)
        )
    )
    now = datetime.now(tz=UTC)
    sec = ExamSection(
        id=uuid.uuid4(),
        round_id=round_id,
        exam_id=exam_id,
        company_id=company_id,
        title=body.title.strip(),
        kind=body.kind,
        time_limit_seconds=body.time_limit_seconds,
        position=(int(max_pos) + 1) if max_pos is not None else 1,
        created_at=now,
        updated_at=now,
    )
    db.add(sec)
    await db.commit()
    log.info("hr.exam.section.created", round_id=str(round_id), section_id=str(sec.id))
    return await _section_out(db, sec)


@router.patch(
    "/exams/{exam_id}/rounds/{round_id}/sections/{section_id}", response_model=SectionOut
)
async def update_section(
    exam_id: uuid.UUID,
    round_id: uuid.UUID,
    section_id: uuid.UUID,
    body: SectionUpdateIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> SectionOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await _get_owned_round(db, company_id, exam_id, round_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    # kind is immutable after creation (it'd orphan the section's questions).
    if body.title is not None:
        sec.title = body.title.strip()
    if body.time_limit_seconds is not None:
        sec.time_limit_seconds = body.time_limit_seconds
    sec.updated_at = datetime.now(tz=UTC)
    await db.commit()
    return await _section_out(db, sec)


@router.delete(
    "/exams/{exam_id}/rounds/{round_id}/sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_section(
    exam_id: uuid.UUID,
    round_id: uuid.UUID,
    section_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> Response:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    await _get_owned_round(db, company_id, exam_id, round_id)
    await _require_no_round_attempts(db, company_id, round_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    now = datetime.now(tz=UTC)
    sec.deleted_at = now
    # Cascade soft-delete the section's questions (DB FK cascade only fires on a
    # hard delete) so they don't linger as live rows (e.g. inflating exam-wide
    # question counts or holding their position slots).
    mcq_rows = (
        await db.execute(
            select(ExamQuestion).where(
                ExamQuestion.section_id == section_id,
                ExamQuestion.company_id == company_id,
                ExamQuestion.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    coding_rows = (
        await db.execute(
            select(CodingQuestion).where(
                CodingQuestion.section_id == section_id,
                CodingQuestion.company_id == company_id,
                CodingQuestion.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for mq in mcq_rows:
        mq.deleted_at = now
    for cq in coding_rows:
        cq.deleted_at = now
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Section-scoped question deletion
# ---------------------------------------------------------------------------
@router.delete(
    "/exams/{exam_id}/sections/{section_id}/questions/{qid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_section_question(
    exam_id: uuid.UUID,
    section_id: uuid.UUID,
    qid: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> Response:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    _require_section_kind(sec, "mcq")
    await _require_no_round_attempts(db, company_id, sec.round_id)
    q = await db.scalar(
        select(ExamQuestion).where(
            ExamQuestion.id == qid,
            ExamQuestion.section_id == section_id,
            ExamQuestion.company_id == company_id,
            ExamQuestion.deleted_at.is_(None),
        )
    )
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    q.deleted_at = datetime.now(tz=UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/exams/{exam_id}/sections/{section_id}/coding-questions/{qid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_section_coding_question(
    exam_id: uuid.UUID,
    section_id: uuid.UUID,
    qid: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> Response:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    _require_section_kind(sec, "coding")
    await _require_no_round_attempts(db, company_id, sec.round_id)
    q = await db.scalar(
        select(CodingQuestion).where(
            CodingQuestion.id == qid,
            CodingQuestion.section_id == section_id,
            CodingQuestion.company_id == company_id,
            CodingQuestion.deleted_at.is_(None),
        )
    )
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    q.deleted_at = datetime.now(tz=UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Section-scoped question authoring
# ---------------------------------------------------------------------------
def _require_section_kind(sec: ExamSection, expected: str) -> None:
    if sec.kind != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This is a {sec.kind} section — expected {expected}.",
        )


@router.get(
    "/exams/{exam_id}/sections/{section_id}/questions", response_model=list[QuestionOut]
)
async def list_section_questions(
    exam_id: uuid.UUID, section_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    _require_section_kind(sec, "mcq")
    rows = (
        await db.execute(
            select(ExamQuestion)
            .where(
                ExamQuestion.section_id == section_id,
                ExamQuestion.company_id == company_id,
                ExamQuestion.deleted_at.is_(None),
            )
            .order_by(ExamQuestion.position.asc())
        )
    ).scalars().all()
    return [_question_out(q) for q in rows]


@router.post(
    "/exams/{exam_id}/sections/{section_id}/questions",
    status_code=status.HTTP_201_CREATED,
    response_model=QuestionOut,
)
async def add_section_question(
    exam_id: uuid.UUID,
    section_id: uuid.UUID,
    body: QuestionIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> QuestionOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    _require_section_kind(sec, "mcq")
    await _require_no_round_attempts(db, company_id, sec.round_id)
    created = await _bulk_insert_questions(db, company_id, sec, [body])
    return _question_out(created[0])


@router.get(
    "/exams/{exam_id}/sections/{section_id}/coding-questions",
    response_model=list[CodingQuestionOut],
)
async def list_section_coding_questions(
    exam_id: uuid.UUID, section_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[CodingQuestionOut]:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    _require_section_kind(sec, "coding")
    rows = (
        await db.execute(
            select(CodingQuestion)
            .where(
                CodingQuestion.section_id == section_id,
                CodingQuestion.company_id == company_id,
                CodingQuestion.deleted_at.is_(None),
            )
            .order_by(CodingQuestion.position.asc())
        )
    ).scalars().all()
    return [_coding_out(q) for q in rows]


@router.post(
    "/exams/{exam_id}/sections/{section_id}/coding-questions",
    status_code=status.HTTP_201_CREATED,
    response_model=CodingQuestionOut,
)
async def add_section_coding_question(
    exam_id: uuid.UUID,
    section_id: uuid.UUID,
    body: CodingQuestionIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
) -> CodingQuestionOut:
    _hr_uid, company_id = ctx
    await _get_owned_exam(db, company_id, exam_id)
    sec = await _get_owned_section(db, company_id, exam_id, section_id)
    _require_section_kind(sec, "coding")
    await _require_no_round_attempts(db, company_id, sec.round_id)

    max_pos = await db.scalar(
        select(func.max(CodingQuestion.position)).where(
            CodingQuestion.section_id == section_id, CodingQuestion.deleted_at.is_(None)
        )
    )
    now = datetime.now(tz=UTC)
    q = CodingQuestion(
        id=uuid.uuid4(),
        exam_id=exam_id,
        section_id=section_id,
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
    log.info("hr.section.coding_question.created", section_id=str(section_id))
    return _coding_out(q)
