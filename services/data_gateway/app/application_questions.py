"""Application questions: authoring rules, and validating what comes back.

HR writes questions against an opening; the public form renders them; the
answers arrive with the application. Everything that decides whether a write is
allowed lives here rather than in a router, for the same reason the workflow
rules do: the copilot's commit path and any future import must hit the same
guard, and a rule that only exists in a handler is a rule with one caller.

Two ideas do most of the work.

AN ANSWERED QUESTION IS FROZEN
    Its ``prompt`` and ``kind`` cannot change once anybody has answered it.
    Editing "Do you have a work visa?" into "Do you need visa sponsorship?"
    silently inverts every stored "yes", and nothing in the data would record
    that it happened — the old answers would still read as answers to the new
    question. Order, requiredness, help text and retirement are all still
    editable, because none of them changes what an existing answer means.

VALIDATION IS SERVER-SIDE AND SHAPED BY THE KIND
    The form is the thing being validated, so the form cannot be the validator.
    A single-choice answer must be one of the options as they are *today*, a
    number must be a number, and a required question must actually be answered
    — where "answered" excludes an empty string and an empty list, because a
    browser will happily submit both for an untouched field.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Kinds the form can render and this module can validate. Mirrors the database
# check constraint — the constraint is the authority, this is the friendly
# refusal in front of it.
QUESTION_KINDS: frozenset[str] = frozenset(
    {"short_text", "long_text", "number", "single_choice", "multi_choice", "yes_no"}
)

CHOICE_KINDS: frozenset[str] = frozenset({"single_choice", "multi_choice"})

# What may still be edited after somebody has answered. Everything absent from
# this set is frozen — see the module docstring.
EDITABLE_AFTER_ANSWERS: frozenset[str] = frozenset(
    {"position", "required", "help_text", "options"}
)

MAX_QUESTIONS_PER_OPENING = 20
MAX_OPTIONS = 12
MAX_ANSWER_CHARS = 4000


class QuestionError(Exception):
    """A question could not be written as asked. Reported as 4xx, not 500."""


class AnswerError(Exception):
    """An application's answers do not satisfy the opening's questions."""

    def __init__(self, message: str, *, question_id: str | None = None) -> None:
        super().__init__(message)
        self.question_id = question_id


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
async def list_questions(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """This opening's live questions, in the order HR put them in.

    ``company_id`` is optional because the public form reads this too, and it
    has already proved the opening is open to applications — a second tenant
    check there would be reassurance rather than a control.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id, position, prompt, kind, help_text, required, options"
                "  FROM application_questions"
                " WHERE requisition_id = :r AND deleted_at IS NULL"
                + (" AND company_id = :c" if company_id else "")
                + " ORDER BY position"
            ),
            {"r": requisition_id, **({"c": company_id} if company_id else {})},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def answers_for(
    db: AsyncSession, *, enrolment_id: uuid.UUID
) -> list[dict[str, Any]]:
    """What one applicant said, with the question text as it was asked.

    Joined rather than stored alongside: the prompt is frozen once answered, so
    reading it live cannot drift from what the person was shown. Retired
    questions are included — an answer somebody gave does not stop existing
    because the question is no longer asked.
    """
    rows = (
        await db.execute(
            text(
                "SELECT q.id AS question_id, q.prompt, q.kind, q.position,"
                "       q.deleted_at IS NOT NULL AS retired, a.answer"
                "  FROM application_answers a"
                "  JOIN application_questions q ON q.id = a.question_id"
                " WHERE a.enrolment_id = :e"
                " ORDER BY q.position"
            ),
            {"e": enrolment_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------
async def _answer_count(db: AsyncSession, question_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            text("SELECT count(*) FROM application_answers WHERE question_id = :q"),
            {"q": question_id},
        )
        or 0
    )


def validate_shape(*, kind: str, options: list[str] | None) -> list[str]:
    """Check a question is answerable, and return its cleaned options."""
    if kind not in QUESTION_KINDS:
        raise QuestionError(f"kind must be one of {sorted(QUESTION_KINDS)}")
    cleaned = [" ".join(str(o).split()) for o in (options or [])]
    cleaned = [o for o in cleaned if o][:MAX_OPTIONS]
    if kind in CHOICE_KINDS:
        if len(cleaned) < 2:
            raise QuestionError("A choice question needs at least two options.")
        if len(set(cleaned)) != len(cleaned):
            # Two identical options are indistinguishable in the answer, so the
            # stored value cannot say which was picked.
            raise QuestionError("Options must be distinct.")
    elif cleaned:
        raise QuestionError(f"A {kind} question does not take options.")
    return cleaned


async def add_question(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    prompt: str,
    kind: str,
    required: bool = False,
    help_text: str | None = None,
    options: list[str] | None = None,
) -> uuid.UUID:
    """Append a question to an opening. Caller commits."""
    cleaned = validate_shape(kind=kind, options=options)
    live = await list_questions(db, requisition_id=requisition_id, company_id=company_id)
    if len(live) >= MAX_QUESTIONS_PER_OPENING:
        raise QuestionError(
            f"An opening can ask at most {MAX_QUESTIONS_PER_OPENING} questions."
        )

    question_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO application_questions (id, company_id, requisition_id, position,"
            " prompt, kind, help_text, required, options, created_at, updated_at)"
            " VALUES (:i,:c,:r,:p,:pr,:k,:h,:req, CAST(:o AS jsonb), now(), now())"
        ),
        {
            "i": question_id, "c": company_id, "r": requisition_id,
            # Appended after the last LIVE question. A retired question keeps
            # its position for the answers that reference it, and the partial
            # unique index only constrains live rows.
            "p": (max((q["position"] for q in live), default=-1) + 1),
            "pr": " ".join(prompt.split()), "k": kind, "h": help_text,
            "req": required, "o": _json(cleaned),
        },
    )
    return question_id


async def update_question(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    question_id: uuid.UUID,
    fields: dict[str, Any],
) -> None:
    """Edit a question. Caller commits.

    Refuses to reword or retype a question anybody has answered — see the
    module docstring. The refusal names the field, because "you cannot edit
    this" without saying which part is not actionable.
    """
    current = (
        await db.execute(
            text(
                "SELECT kind, options FROM application_questions"
                " WHERE id = :q AND company_id = :c AND deleted_at IS NULL"
            ),
            {"q": question_id, "c": company_id},
        )
    ).mappings().first()
    if current is None:
        raise QuestionError("Question not found.")

    if await _answer_count(db, question_id):
        frozen = sorted(set(fields) - EDITABLE_AFTER_ANSWERS)
        if frozen:
            raise QuestionError(
                "Candidates have already answered this question, so its "
                + " and ".join(frozen)
                + " cannot change. Retire it and add a new one instead — that "
                "keeps their answers attached to what they were actually asked."
            )

    if "options" in fields:
        fields["options"] = _json(
            validate_shape(kind=current["kind"], options=fields["options"])
        )
    if "prompt" in fields:
        fields["prompt"] = " ".join(str(fields["prompt"]).split())

    sets = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k == "options" else f"{k} = :{k}"
        for k in fields
    )
    await db.execute(
        text(
            f"UPDATE application_questions SET {sets}, updated_at = now()"
            " WHERE id = :q AND company_id = :c"
        ),
        {**fields, "q": question_id, "c": company_id},
    )


async def retire_question(
    db: AsyncSession, *, company_id: uuid.UUID, question_id: uuid.UUID
) -> None:
    """Stop asking a question without losing the answers to it. Caller commits.

    Soft delete, always — even with no answers yet — so the two cases behave
    identically and nobody discovers the difference on the day it matters.
    """
    await db.execute(
        text(
            "UPDATE application_questions SET deleted_at = now(), updated_at = now()"
            " WHERE id = :q AND company_id = :c AND deleted_at IS NULL"
        ),
        {"q": question_id, "c": company_id},
    )


async def reorder_questions(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    ordered_ids: list[uuid.UUID],
) -> None:
    """Set the order questions are asked in. Caller commits."""
    live = await list_questions(db, requisition_id=requisition_id, company_id=company_id)
    if {str(q["id"]) for q in live} != {str(i) for i in ordered_ids}:
        raise QuestionError(
            "The new order must list every live question exactly once."
        )
    # Parked out of the way first: the partial unique index on
    # (requisition_id, position) rejects the intermediate states of any
    # reordering that is not a pure append. Same trick, same reason, as the
    # workflow round reorder.
    await db.execute(
        text(
            "UPDATE application_questions SET position = position + 1000"
            " WHERE requisition_id = :r AND deleted_at IS NULL"
        ),
        {"r": requisition_id},
    )
    for index, question_id in enumerate(ordered_ids):
        await db.execute(
            text(
                "UPDATE application_questions SET position = :p, updated_at = now()"
                " WHERE id = :q AND company_id = :c"
            ),
            {"p": index, "q": question_id, "c": company_id},
        )


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------
def _is_blank(value: Any) -> bool:
    """Whether an answer counts as not given.

    A browser submits an untouched text input as "" and an untouched checkbox
    group as nothing at all, so both have to read as unanswered — otherwise a
    required question is satisfied by ignoring it.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not [v for v in value if str(v).strip()]
    return False


def coerce_answer(question: dict[str, Any], raw: Any) -> Any:
    """Validate one answer against its question, returning what to store."""
    kind = question["kind"]
    prompt = question["prompt"]

    if kind == "yes_no":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in {"true", "false", "yes", "no"}:
            return raw.strip().lower() in {"true", "yes"}
        raise AnswerError(f"'{prompt}' needs a yes or a no.", question_id=str(question["id"]))

    if kind == "number":
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise AnswerError(
                f"'{prompt}' needs a number.", question_id=str(question["id"])
            ) from exc

    if kind in CHOICE_KINDS:
        options = list(question["options"] or [])
        chosen = raw if isinstance(raw, list) else [raw]
        chosen = [str(c) for c in chosen if str(c).strip()]
        # Checked against the options as they are TODAY. A choice that is no
        # longer offered is not an answer to the question being asked, and
        # accepting it would put a value in the column that the form can never
        # render back.
        unknown = [c for c in chosen if c not in options]
        if unknown:
            raise AnswerError(
                f"'{prompt}' does not offer {unknown[0]!r}.",
                question_id=str(question["id"]),
            )
        if kind == "single_choice":
            if len(chosen) > 1:
                raise AnswerError(
                    f"'{prompt}' takes one answer.", question_id=str(question["id"])
                )
            return chosen[0] if chosen else None
        return chosen

    text_value = str(raw).strip()[:MAX_ANSWER_CHARS]
    return text_value or None


def validate_answers(
    questions: list[dict[str, Any]], submitted: dict[str, Any]
) -> list[tuple[uuid.UUID, Any]]:
    """Check a whole submission and return the (question_id, value) to store.

    Unknown keys are IGNORED rather than rejected: a candidate whose form was
    loaded before HR retired a question should not have their application
    refused for answering it. The question no longer being asked is the
    company's change, not the applicant's mistake.
    """
    out: list[tuple[uuid.UUID, Any]] = []
    for question in questions:
        raw = submitted.get(str(question["id"]))
        if _is_blank(raw):
            if question["required"]:
                raise AnswerError(
                    f"'{question['prompt']}' is required.",
                    question_id=str(question["id"]),
                )
            continue
        value = coerce_answer(question, raw)
        if value is None or (isinstance(value, list) and not value):
            if question["required"]:
                raise AnswerError(
                    f"'{question['prompt']}' is required.",
                    question_id=str(question["id"]),
                )
            continue
        out.append((question["id"], value))
    return out


async def store_answers(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    answers: list[tuple[uuid.UUID, Any]],
) -> None:
    """Write an application's answers. Caller commits.

    Upsert on (enrolment, question) so a resubmission replaces rather than
    accumulating — the unique constraint would otherwise turn a retry into a
    500 on a form the candidate had already filled in once.
    """
    for question_id, value in answers:
        await db.execute(
            text(
                "INSERT INTO application_answers"
                " (id, company_id, enrolment_id, question_id, answer, created_at)"
                " VALUES (:i,:c,:e,:q, CAST(:a AS jsonb), now())"
                " ON CONFLICT (enrolment_id, question_id)"
                " DO UPDATE SET answer = EXCLUDED.answer"
            ),
            {
                "i": uuid.uuid4(), "c": company_id, "e": enrolment_id,
                "q": question_id, "a": _json(value),
            },
        )


def _json(value: Any) -> str:
    return json.dumps(value)
