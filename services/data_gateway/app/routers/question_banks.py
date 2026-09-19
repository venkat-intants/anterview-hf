"""Reusable question banks — PH4-D1.

``hr_router`` (``/hr``, HR managers): banks, authoring, review (any HR manager
other than the author/submitter), search, and copying into an exam section.
``admin_router`` (``/admin``, the company super admin): the same review
actions, so a single-HR company is never blocked on a second approver.

Every route is company-scoped through ``HrCtxDep`` / ``SuperAdminCtxDep`` —
the caller's company from the authenticated session, never from the request —
so another company's bank or question answers 404, and an interviewer or
candidate session cannot reach any of this at all (the role gate is upstream
of these routers).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app import exam_locks
from app import question_banks as svc
from app.database import DbSessionDep
from app.dependencies import HrCtxDep, SuperAdminCtxDep
from app.routers.hr_coding import CodingQuestionIn, TestCaseIn
from app.routers.hr_exams import QuestionIn

hr_router = APIRouter(prefix="/hr", tags=["question-banks"])
admin_router = APIRouter(prefix="/admin", tags=["question-banks"])

DIFFICULTIES = {"easy", "medium", "hard"}
LANGUAGES = {"en", "hi", "te"}


async def _fail(db: AsyncSession, exc: svc.QuestionBankError) -> HTTPException:
    await db.rollback()
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class CompetencyIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)


class BankCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class BankUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class BankQuestionIn(BaseModel):
    """One bank question, either kind. Shape is validated by constructing the
    SAME Pydantic models the exam-authoring routes use (below) — a bank
    question and an exam question can never silently drift apart in what
    counts as well-formed.
    """

    kind: str
    prompt: str = Field(min_length=1, max_length=20_000)
    points: int = Field(default=1, ge=1, le=1000)
    difficulty: str = "medium"
    language: str = "en"
    competencies: list[CompetencyIn] = Field(default_factory=list, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=20)
    options: list[str] | None = None
    correct_index: int | None = None
    starter_code: str | None = None
    reference_solution: str | None = None
    allowed_languages: list[str] | None = None
    test_cases: list[TestCaseIn] | None = None
    time_limit_ms: int | None = Field(default=None, ge=100, le=15_000)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        v = (v or "").lower()
        if v not in {"mcq", "coding"}:
            raise ValueError("kind must be mcq | coding")
        return v

    @field_validator("difficulty")
    @classmethod
    def _difficulty(cls, v: str) -> str:
        v = (v or "medium").lower()
        if v not in DIFFICULTIES:
            raise ValueError("difficulty must be easy | medium | hard")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        v = (v or "en").lower()
        if v not in LANGUAGES:
            raise ValueError("language must be en | hi | te")
        return v

    @model_validator(mode="after")
    def _validate_shape(self) -> BankQuestionIn:
        try:
            if self.kind == "mcq":
                if self.correct_index is None:
                    raise ValueError("correct_index is required for an mcq question")
                QuestionIn(prompt=self.prompt, options=self.options or [],
                          correct_index=self.correct_index, points=self.points)
            else:
                CodingQuestionIn(
                    prompt=self.prompt, allowed_languages=self.allowed_languages or [],
                    starter_code=self.starter_code, reference_solution=self.reference_solution,
                    test_cases=self.test_cases or [], time_limit_ms=self.time_limit_ms or 5000,
                    points=self.points,
                )
        except ValidationError as exc:
            errs = exc.errors()
            msg = str(errs[0].get("msg", "invalid question")) if errs else "invalid question"
            raise ValueError(msg) from exc
        return self


class BankQuestionUpdateIn(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    points: int | None = Field(default=None, ge=1, le=1000)
    difficulty: str | None = None
    language: str | None = None
    competencies: list[CompetencyIn] | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    options: list[str] | None = None
    correct_index: int | None = None
    starter_code: str | None = None
    reference_solution: str | None = None
    allowed_languages: list[str] | None = None
    test_cases: list[TestCaseIn] | None = None
    time_limit_ms: int | None = Field(default=None, ge=100, le=15_000)

    @field_validator("difficulty")
    @classmethod
    def _difficulty(cls, v: str | None) -> str | None:
        if v is not None and v.lower() not in DIFFICULTIES:
            raise ValueError("difficulty must be easy | medium | hard")
        return v.lower() if v else v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str | None) -> str | None:
        if v is not None and v.lower() not in LANGUAGES:
            raise ValueError("language must be en | hi | te")
        return v.lower() if v else v

    def as_fields(self) -> dict[str, Any]:
        data = self.model_dump(exclude_unset=True)
        if "competencies" in data and data["competencies"] is not None:
            data["competencies"] = [c if isinstance(c, dict) else c.model_dump()
                                    for c in self.competencies or []]
        if "test_cases" in data and data["test_cases"] is not None:
            data["test_cases"] = [tc.model_dump() for tc in (self.test_cases or [])]
        return data


class BulkQuestionsIn(BaseModel):
    questions: list[BankQuestionIn] = Field(min_length=1, max_length=200)


class ReviewActionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class AddToSectionIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class SaveToBankIn(BaseModel):
    bank_id: uuid.UUID
    difficulty: str = "medium"
    language: str = "en"
    competencies: list[CompetencyIn] = Field(default_factory=list, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("difficulty")
    @classmethod
    def _difficulty(cls, v: str) -> str:
        v = (v or "medium").lower()
        if v not in DIFFICULTIES:
            raise ValueError("difficulty must be easy | medium | hard")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        v = (v or "en").lower()
        if v not in LANGUAGES:
            raise ValueError("language must be en | hi | te")
        return v


def _competency_dicts(items: list[CompetencyIn]) -> list[dict[str, str]]:
    return [c.model_dump() for c in items]


def _question_kwargs(body: BankQuestionIn) -> dict[str, Any]:
    return {
        "kind": body.kind, "prompt": body.prompt, "points": body.points,
        "difficulty": body.difficulty, "language": body.language,
        "competencies": _competency_dicts(body.competencies), "tags": body.tags,
        "options": body.options, "correct_index": body.correct_index,
        "starter_code": body.starter_code, "reference_solution": body.reference_solution,
        "allowed_languages": body.allowed_languages,
        "test_cases": [tc.model_dump() for tc in (body.test_cases or [])] or None,
        "time_limit_ms": body.time_limit_ms,
    }


# ---------------------------------------------------------------------------
# HR — banks
# ---------------------------------------------------------------------------
@hr_router.get("/question-banks")
async def list_question_banks(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.list_banks(db, company_id=company_id)


@hr_router.post("/question-banks", status_code=201)
async def create_question_bank(body: BankCreateIn, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        bank = await svc.create_bank(db, company_id=company_id, actor=uid, name=body.name,
                                     description=body.description)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    bank_id = bank.id
    await exam_locks.commit_or_conflict(db)
    return await svc.bank_summary(db, company_id=company_id, bank_id=bank_id)


@hr_router.patch("/question-banks/{bank_id}")
async def update_question_bank(
    bank_id: uuid.UUID, body: BankUpdateIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        await svc.update_bank(db, company_id=company_id, bank_id=bank_id, name=body.name,
                              description=body.description)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return await svc.bank_summary(db, company_id=company_id, bank_id=bank_id)


@hr_router.post("/question-banks/{bank_id}/archive")
async def archive_question_bank(bank_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        await svc.archive_bank(db, company_id=company_id, bank_id=bank_id)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return {"id": str(bank_id), "archived": True}


# ---------------------------------------------------------------------------
# HR — questions within a bank
# ---------------------------------------------------------------------------
@hr_router.get("/question-banks/{bank_id}/questions")
async def list_bank_questions(
    bank_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
    q: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    difficulty: Annotated[str | None, Query()] = None,
    language: Annotated[str | None, Query()] = None,
    competency: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.search(
        db, company_id=company_id, bank_id=bank_id, q=q, kind=kind, difficulty=difficulty,
        language=language, competency=competency, tag=tag, status=status,
    )


@hr_router.post("/question-banks/{bank_id}/questions", status_code=201)
async def create_bank_question(
    bank_id: uuid.UUID, body: BankQuestionIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.create_question(db, company_id=company_id, bank_id=bank_id, actor=uid,
                                      **_question_kwargs(body))
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/question-banks/{bank_id}/questions/bulk", status_code=201)
async def create_bank_questions_bulk(
    bank_id: uuid.UUID, body: BulkQuestionsIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[dict[str, Any]]:
    """The AI-preview 'add all' path: every question lands as ``origin='ai_draft'``."""
    uid, company_id = ctx
    try:
        items = [_question_kwargs(item) for item in body.questions]
        created = await svc.create_drafts_bulk(db, company_id=company_id, bank_id=bank_id, actor=uid,
                                               items=items)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return [svc.question_out(q) for q in created]


# ---------------------------------------------------------------------------
# HR — cross-bank search, the competency catalogue, the review queue
# ---------------------------------------------------------------------------
@hr_router.get("/bank-questions")
async def search_bank_questions(
    ctx: HrCtxDep, db: DbSessionDep,
    q: Annotated[str | None, Query()] = None,
    bank_id: Annotated[uuid.UUID | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    difficulty: Annotated[str | None, Query()] = None,
    language: Annotated[str | None, Query()] = None,
    competency: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    status: Annotated[str, Query()] = "approved",
    exclude_exam_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.search(
        db, company_id=company_id, bank_id=bank_id, q=q, kind=kind, difficulty=difficulty,
        language=language, competency=competency, tag=tag, status=status,
        exclude_exam_id=exclude_exam_id,
    )


@hr_router.get("/bank-questions/competencies")
async def bank_question_competencies(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, str]]:
    _uid, company_id = ctx
    return await svc.competency_catalog(db, company_id=company_id)


@hr_router.get("/bank-questions/review-queue")
async def bank_question_review_queue(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    uid, company_id = ctx
    return await svc.review_queue(db, company_id=company_id, actor=uid)


# ---------------------------------------------------------------------------
# HR — one question
# ---------------------------------------------------------------------------
@hr_router.get("/bank-questions/{qid}")
async def get_bank_question(qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        return await svc.get_question(db, company_id=company_id, qid=qid)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc


@hr_router.patch("/bank-questions/{qid}")
async def update_bank_question(
    qid: uuid.UUID, body: BankQuestionUpdateIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.update_question(db, company_id=company_id, qid=qid, actor=uid,
                                      **body.as_fields())
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.delete("/bank-questions/{qid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bank_question(qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> Response:
    _uid, company_id = ctx
    try:
        await svc.delete_question(db, company_id=company_id, qid=qid)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@hr_router.post("/bank-questions/{qid}/submit")
async def submit_bank_question(qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.submit(db, company_id=company_id, qid=qid, actor=uid)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/bank-questions/{qid}/withdraw")
async def withdraw_bank_question(qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.withdraw(db, company_id=company_id, qid=qid, actor=uid)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/bank-questions/{qid}/approve")
async def approve_bank_question_hr(
    qid: uuid.UUID, body: ReviewActionIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.review(db, company_id=company_id, qid=qid, actor=uid, role="hr_manager",
                             action="approve", note=body.note)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/bank-questions/{qid}/request-changes")
async def request_changes_bank_question_hr(
    qid: uuid.UUID, body: ReviewActionIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.review(db, company_id=company_id, qid=qid, actor=uid, role="hr_manager",
                             action="request_changes", note=body.note)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/bank-questions/{qid}/new-version")
async def new_version_bank_question(
    qid: uuid.UUID, body: BankQuestionUpdateIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.new_version(db, company_id=company_id, qid=qid, actor=uid, **body.as_fields())
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/bank-questions/{qid}/retire")
async def retire_bank_question(qid: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.retire(db, company_id=company_id, qid=qid, actor=uid)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


# ---------------------------------------------------------------------------
# HR — copying into (or out of) an exam
# ---------------------------------------------------------------------------
@hr_router.post("/exams/{exam_id}/sections/{section_id}/bank-questions")
async def add_bank_questions_to_section(
    exam_id: uuid.UUID, section_id: uuid.UUID, body: AddToSectionIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await svc.add_to_section(
            db, company_id=company_id, exam_id=exam_id, section_id=section_id, ids=body.ids, actor=uid,
        )
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return out


@hr_router.post("/exams/{exam_id}/questions/{qid}/save-to-bank", status_code=201)
async def save_question_to_bank(
    exam_id: uuid.UUID, qid: uuid.UUID, body: SaveToBankIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.save_exam_question_to_bank(
            db, company_id=company_id, exam_id=exam_id, question_id=qid, bank_id=body.bank_id,
            actor=uid, kind="mcq", difficulty=body.difficulty, language=body.language,
            competencies=_competency_dicts(body.competencies), tags=body.tags,
        )
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@hr_router.post("/exams/{exam_id}/coding-questions/{qid}/save-to-bank", status_code=201)
async def save_coding_question_to_bank(
    exam_id: uuid.UUID, qid: uuid.UUID, body: SaveToBankIn, ctx: HrCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.save_exam_question_to_bank(
            db, company_id=company_id, exam_id=exam_id, question_id=qid, bank_id=body.bank_id,
            actor=uid, kind="coding", difficulty=body.difficulty, language=body.language,
            competencies=_competency_dicts(body.competencies), tags=body.tags,
        )
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


# ---------------------------------------------------------------------------
# Super admin — the same review actions, so a single-HR company is never
# blocked on a second HR approver.
# ---------------------------------------------------------------------------
@admin_router.get("/question-reviews")
async def list_question_reviews(ctx: SuperAdminCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    uid, company_id = ctx
    return await svc.review_queue(db, company_id=company_id, actor=uid)


@admin_router.post("/bank-questions/{qid}/approve")
async def approve_bank_question_admin(
    qid: uuid.UUID, body: ReviewActionIn, ctx: SuperAdminCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.review(db, company_id=company_id, qid=qid, actor=uid, role="super_admin",
                             action="approve", note=body.note)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)


@admin_router.post("/bank-questions/{qid}/request-changes")
async def request_changes_bank_question_admin(
    qid: uuid.UUID, body: ReviewActionIn, ctx: SuperAdminCtxDep, db: DbSessionDep
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        q = await svc.review(db, company_id=company_id, qid=qid, actor=uid, role="super_admin",
                             action="request_changes", note=body.note)
    except svc.QuestionBankError as exc:
        raise await _fail(db, exc) from exc
    await exam_locks.commit_or_conflict(db)
    return svc.question_out(q)
