"""Application questions — the authoring rules and the answer validation.

Two things carry the weight here.

THE FROZEN QUESTION. Rewording "Do you have a work visa?" into "Do you need
visa sponsorship?" inverts the meaning of every stored yes, and nothing in the
data would record it. The stored answers would still read as answers to the new
question — worse than losing them, because nobody would know to doubt them.

VALIDATION IS THE SERVER'S. The form is the thing being validated, so a
required question satisfied by an empty string, or a choice answered with
something the question never offered, has to be refused here rather than
prevented in a component.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application_questions import (
    EDITABLE_AFTER_ANSWERS,
    QUESTION_KINDS,
    AnswerError,
    QuestionError,
    coerce_answer,
    validate_answers,
    validate_shape,
)


def q(**kw: object) -> dict:
    base = {
        "id": uuid.uuid4(),
        "position": 0,
        "prompt": "Why do you want this role?",
        "kind": "long_text",
        "help_text": None,
        "required": False,
        "options": [],
    }
    return {**base, **kw}


# ===========================================================================
# Shape — a question that cannot be answered must not be storable
# ===========================================================================
def test_a_choice_question_needs_options() -> None:
    """A required single-choice with nothing to choose is an application
    nobody can submit."""
    with pytest.raises(QuestionError, match="at least two options"):
        validate_shape(kind="single_choice", options=[])


def test_one_option_is_not_a_choice() -> None:
    with pytest.raises(QuestionError, match="at least two options"):
        validate_shape(kind="single_choice", options=["Yes"])


def test_duplicate_options_are_refused() -> None:
    """Two identical options are indistinguishable in the stored answer, so it
    cannot say which one was picked."""
    with pytest.raises(QuestionError, match="distinct"):
        validate_shape(kind="multi_choice", options=["Python", "Python"])


def test_a_text_question_takes_no_options() -> None:
    with pytest.raises(QuestionError, match="does not take options"):
        validate_shape(kind="short_text", options=["a", "b"])


def test_options_are_tidied() -> None:
    assert validate_shape(kind="single_choice", options=["  Yes ", "", "  No  "]) == [
        "Yes",
        "No",
    ]


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(QuestionError, match="kind must be one of"):
        validate_shape(kind="file_upload", options=None)


def test_file_upload_is_not_a_kind_yet() -> None:
    """Deliberately absent: it is a second upload path with its own storage,
    erasure and consent story, not a dropdown value."""
    assert "file" not in QUESTION_KINDS
    assert "file_upload" not in QUESTION_KINDS


# ===========================================================================
# What may change after somebody has answered
# ===========================================================================
def test_the_wording_and_the_type_are_frozen() -> None:
    assert "prompt" not in EDITABLE_AFTER_ANSWERS
    assert "kind" not in EDITABLE_AFTER_ANSWERS


def test_order_and_requiredness_stay_editable() -> None:
    """None of these changes what an existing answer means."""
    assert {"position", "required", "help_text"} <= EDITABLE_AFTER_ANSWERS


@pytest.mark.asyncio
async def test_editing_an_answered_prompt_is_refused_and_says_why() -> None:
    from app.application_questions import update_question

    db = AsyncMock()
    result = MagicMock()
    mapped = MagicMock()
    mapped.first = MagicMock(return_value={"kind": "yes_no", "options": []})
    result.mappings = MagicMock(return_value=mapped)
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=3)  # three people have answered

    with pytest.raises(QuestionError) as exc:
        await update_question(
            db,
            company_id=uuid.uuid4(),
            question_id=uuid.uuid4(),
            fields={"prompt": "Do you need visa sponsorship?"},
        )
    message = str(exc.value)
    assert "already answered" in message
    assert "prompt" in message
    # And says what to do instead, rather than only refusing.
    assert "Retire it" in message


@pytest.mark.asyncio
async def test_an_unanswered_question_can_be_reworded() -> None:
    from app.application_questions import update_question

    db = AsyncMock()
    result = MagicMock()
    mapped = MagicMock()
    mapped.first = MagicMock(return_value={"kind": "short_text", "options": []})
    result.mappings = MagicMock(return_value=mapped)
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=0)

    await update_question(
        db, company_id=uuid.uuid4(), question_id=uuid.uuid4(), fields={"prompt": "New wording"}
    )
    assert db.execute.await_count >= 2


@pytest.mark.asyncio
async def test_reordering_an_answered_question_is_allowed() -> None:
    """Order is not meaning."""
    from app.application_questions import update_question

    db = AsyncMock()
    result = MagicMock()
    mapped = MagicMock()
    mapped.first = MagicMock(return_value={"kind": "short_text", "options": []})
    result.mappings = MagicMock(return_value=mapped)
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=5)

    await update_question(
        db, company_id=uuid.uuid4(), question_id=uuid.uuid4(), fields={"position": 2}
    )


# ===========================================================================
# Answers — per kind
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("yes", True), ("no", False), ("true", True)],
)
def test_yes_no_accepts_what_a_form_actually_sends(raw: object, expected: bool) -> None:
    assert coerce_answer(q(kind="yes_no"), raw) is expected


def test_yes_no_refuses_anything_else() -> None:
    with pytest.raises(AnswerError, match="needs a yes or a no"):
        coerce_answer(q(kind="yes_no"), "maybe")


def test_a_number_question_needs_a_number() -> None:
    assert coerce_answer(q(kind="number"), " 6 ") == 6.0
    with pytest.raises(AnswerError, match="needs a number"):
        coerce_answer(q(kind="number"), "six")


def test_a_choice_must_be_one_that_was_offered() -> None:
    """Checked against the options as they are today — a value the form can
    never render back is not an answer to the question being asked."""
    question = q(kind="single_choice", options=["Yes", "No"])
    assert coerce_answer(question, "Yes") == "Yes"
    with pytest.raises(AnswerError, match="does not offer"):
        coerce_answer(question, "Maybe")


def test_a_single_choice_takes_one_answer() -> None:
    question = q(kind="single_choice", options=["A", "B"])
    with pytest.raises(AnswerError, match="takes one answer"):
        coerce_answer(question, ["A", "B"])


def test_a_multi_choice_keeps_every_valid_pick() -> None:
    question = q(kind="multi_choice", options=["Python", "Go", "Rust"])
    assert coerce_answer(question, ["Python", "Rust"]) == ["Python", "Rust"]


def test_long_text_is_capped_rather_than_refused() -> None:
    from app.application_questions import MAX_ANSWER_CHARS

    answer = coerce_answer(q(kind="long_text"), "x" * 10_000)
    assert isinstance(answer, str)
    assert len(answer) == MAX_ANSWER_CHARS


# ===========================================================================
# Answers — the whole submission
# ===========================================================================
def test_a_required_question_must_actually_be_answered() -> None:
    question = q(required=True)
    with pytest.raises(AnswerError, match="is required"):
        validate_answers([question], {})


@pytest.mark.parametrize("blank", ["", "   ", None, []])
def test_an_empty_answer_does_not_satisfy_a_required_question(blank: object) -> None:
    """A browser sends "" for an untouched input and nothing for an untouched
    checkbox group. Either would otherwise satisfy "required" by being ignored.
    """
    question = q(required=True)
    with pytest.raises(AnswerError, match="is required"):
        validate_answers([question], {str(question["id"]): blank})


def test_the_refusal_names_the_question() -> None:
    question = q(required=True, prompt="Are you willing to relocate?")
    with pytest.raises(AnswerError) as exc:
        validate_answers([question], {})
    assert "Are you willing to relocate?" in str(exc.value)
    assert exc.value.question_id == str(question["id"])


def test_an_optional_question_may_be_skipped() -> None:
    question = q(required=False)
    assert validate_answers([question], {}) == []


def test_only_answers_to_live_questions_are_stored() -> None:
    """A form loaded before HR retired a question must not have the whole
    application refused for answering it — the change was the company's."""
    question = q()
    stale = str(uuid.uuid4())
    stored = validate_answers([question], {str(question["id"]): "Because", stale: "x"})
    assert [str(qid) for qid, _ in stored] == [str(question["id"])]


def test_answers_come_back_paired_with_their_question() -> None:
    one, two = q(prompt="Why?"), q(kind="number", prompt="Years?")
    stored = dict(
        validate_answers(
            [one, two], {str(one["id"]): "Interesting work", str(two["id"]): "6"}
        )
    )
    assert stored[one["id"]] == "Interesting work"
    assert stored[two["id"]] == 6.0


def test_a_whitespace_only_optional_answer_is_not_stored() -> None:
    """Storing "   " would show HR a blank answer rather than no answer, which
    reads as somebody having replied."""
    question = q(required=False)
    assert validate_answers([question], {str(question["id"]): "   "}) == []


# ===========================================================================
# The public payload
# ===========================================================================
def test_the_candidate_is_not_told_how_many_others_answered() -> None:
    """Same rule as the posting's applicant count: that is the company's
    information, and it leaks out of a per-question count just as readily."""
    from app.routers.public_apply import PostingQuestion

    fields = set(PostingQuestion.model_fields)
    assert "answer_count" not in fields
    assert "position" not in fields


def test_the_questions_reach_the_public_posting() -> None:
    from app.routers.public_apply import PostingOut

    assert "questions" in PostingOut.model_fields


def test_answers_are_validated_before_anything_is_stored() -> None:
    """A required answer that is missing must refuse the application in the same
    breath as a missing consent. After the upload it would mean deleting a file
    just written, and leaving a half-application nobody asked for."""
    import inspect

    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("validate_answers(") < src.index("Read the resume")
