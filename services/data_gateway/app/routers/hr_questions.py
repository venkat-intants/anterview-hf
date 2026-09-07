"""Authoring the questions an opening asks — Builder step 4.

    GET    /hr/requisitions/{id}/questions        what this opening asks
    POST   /hr/requisitions/{id}/questions        add one
    PUT    /hr/requisitions/{id}/questions/order   reorder
    PATCH  /hr/questions/{qid}                     edit one
    DELETE /hr/questions/{qid}                     stop asking it

A thin router: every rule that decides whether a write is allowed lives in
``app.application_questions`` so the copilot's commit path and any future
import hit the same guard. What is here is HTTP — status codes, and turning a
service refusal into a message a recruiter can act on.

The refusals are 409 rather than 422 on purpose. A frozen question and an
already-answered edit are not malformed requests; they are well-formed requests
that conflict with the state of the opening, and the difference matters to a
client deciding whether to show a field error or a dialogue.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.application_questions import (
    MAX_OPTIONS,
    QUESTION_KINDS,
    QuestionError,
    add_question,
    list_questions,
    reorder_questions,
    retire_question,
    update_question,
)
from app.database import DbSessionDep
from app.dependencies import HrCtxDep

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-questions"])


class QuestionIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    kind: str
    required: bool = False
    help_text: str | None = Field(default=None, max_length=500)
    options: list[str] = Field(default_factory=list, max_length=MAX_OPTIONS)

    @field_validator("kind")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in QUESTION_KINDS:
            raise ValueError(f"kind must be one of {sorted(QUESTION_KINDS)}")
        return v


class QuestionPatch(BaseModel):
    """Only what may change. ``kind`` is absent entirely — a question's type is
    fixed from creation, answered or not, because changing it would reinterpret
    every answer already given and there is no version of that which is not a
    silent rewrite."""

    prompt: str | None = Field(default=None, min_length=3, max_length=500)
    required: bool | None = None
    help_text: str | None = Field(default=None, max_length=500)
    options: list[str] | None = Field(default=None, max_length=MAX_OPTIONS)


class OrderIn(BaseModel):
    question_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class QuestionOut(BaseModel):
    id: str
    position: int
    prompt: str
    kind: str
    help_text: str | None = None
    required: bool
    options: list[str] = Field(default_factory=list)
    # How many people have answered — what the console needs to explain, before
    # somebody starts typing, why the prompt is not editable.
    answer_count: int = 0


async def _owned(db: DbSessionDep, company_id: uuid.UUID, requisition_id: uuid.UUID) -> None:
    found = await db.scalar(
        text(
            "SELECT 1 FROM job_requisitions"
            " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
        ),
        {"i": requisition_id, "c": company_id},
    )
    if not found:
        raise HTTPException(status_code=404, detail="Requisition not found.")


async def _with_counts(
    db: DbSessionDep, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> list[QuestionOut]:
    questions = await list_questions(
        db, requisition_id=requisition_id, company_id=company_id
    )
    counts = {
        str(r.question_id): int(r.n)
        for r in (
            await db.execute(
                text(
                    "SELECT a.question_id, count(*) AS n FROM application_answers a"
                    "  JOIN application_questions q ON q.id = a.question_id"
                    " WHERE q.requisition_id = :r GROUP BY a.question_id"
                ),
                {"r": requisition_id},
            )
        ).all()
    }
    return [
        QuestionOut(
            id=str(q["id"]),
            position=q["position"],
            prompt=q["prompt"],
            kind=q["kind"],
            help_text=q["help_text"],
            required=q["required"],
            options=list(q["options"] or []),
            answer_count=counts.get(str(q["id"]), 0),
        )
        for q in questions
    ]


def _conflict(exc: QuestionError) -> HTTPException:
    """A service refusal, as HTTP.

    409 for "already answered", 422 for "this question could never work" —
    a client showing a field error and a client showing a dialogue want to tell
    those apart, and the message alone does not let them.
    """
    text_ = str(exc)
    already = "already answered" in text_
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT if already else 422, detail=text_
    )


@router.get(
    "/requisitions/{requisition_id}/questions", response_model=list[QuestionOut]
)
async def get_questions(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    _hr_uid, company_id = ctx
    await _owned(db, company_id, requisition_id)
    return await _with_counts(db, requisition_id, company_id)


@router.post(
    "/requisitions/{requisition_id}/questions",
    response_model=list[QuestionOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_question(
    requisition_id: uuid.UUID, body: QuestionIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    """Add a question, and return the whole list.

    The list rather than the one row: adding changes positions, and a client
    that has to re-fetch to find out what it now looks like will sometimes
    forget to.
    """
    hr_uid, company_id = ctx
    await _owned(db, company_id, requisition_id)
    try:
        question_id = await add_question(
            db,
            company_id=company_id,
            requisition_id=requisition_id,
            prompt=body.prompt,
            kind=body.kind,
            required=body.required,
            help_text=body.help_text,
            options=body.options,
        )
        await db.commit()
    except QuestionError as exc:
        await db.rollback()
        raise _conflict(exc) from exc
    log.info(
        "hr.question.added",
        requisition_id=str(requisition_id), question_id=str(question_id),
        kind=body.kind, actor=str(hr_uid),
    )
    return await _with_counts(db, requisition_id, company_id)


@router.put(
    "/requisitions/{requisition_id}/questions/order", response_model=list[QuestionOut]
)
async def reorder(
    requisition_id: uuid.UUID, body: OrderIn, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    _hr_uid, company_id = ctx
    await _owned(db, company_id, requisition_id)
    try:
        await reorder_questions(
            db,
            company_id=company_id,
            requisition_id=requisition_id,
            ordered_ids=body.question_ids,
        )
        await db.commit()
    except QuestionError as exc:
        await db.rollback()
        raise _conflict(exc) from exc
    return await _with_counts(db, requisition_id, company_id)


async def _requisition_of(db: DbSessionDep, company_id: uuid.UUID, qid: uuid.UUID) -> uuid.UUID:
    row = await db.scalar(
        text(
            "SELECT requisition_id FROM application_questions"
            " WHERE id = :q AND company_id = :c"
        ),
        {"q": qid, "c": company_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found.")
    return uuid.UUID(str(row))


@router.patch("/questions/{question_id}", response_model=list[QuestionOut])
async def edit_question(
    question_id: uuid.UUID, body: QuestionPatch, ctx: HrCtxDep, db: DbSessionDep
) -> list[QuestionOut]:
    _hr_uid, company_id = ctx
    requisition_id = await _requisition_of(db, company_id, question_id)
    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    if not fields:
        return await _with_counts(db, requisition_id, company_id)
    try:
        await update_question(
            db, company_id=company_id, question_id=question_id, fields=fields
        )
        await db.commit()
    except QuestionError as exc:
        await db.rollback()
        raise _conflict(exc) from exc
    return await _with_counts(db, requisition_id, company_id)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    question_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> Response:
    """Stop asking a question. The answers already given stay readable."""
    _hr_uid, company_id = ctx
    await _requisition_of(db, company_id, question_id)
    await retire_question(db, company_id=company_id, question_id=question_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
