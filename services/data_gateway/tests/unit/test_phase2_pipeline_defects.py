"""Defects the whole-pipeline test found in a real run (2026-09-13).

* A workflow published with a DRAFT (or empty) exam round emailed candidates
  exam links that could never open ("This exam link isn't valid").
* Candidates who chose Hindi or Telugu got English shortlist and exam emails.
* A public application waited for the next scheduled reconciler pass (~8 min)
  to be scored; bulk uploads woke it at once.
* The application-received email put ``<strong>`` into its plain-text part and
  spliced an English " at <Company>" into the Hindi and Telugu sentences.

The stateful halves are covered by ``smoke_phase2_pipeline_defects``.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows import exam_round_errors, exam_round_problem


def _round(kind: str = "mcq", exam: object = "er-1", title: str = "Aptitude") -> dict[str, Any]:
    return {"id": "r1", "position": 0, "title": title, "kind": kind,
            "pass_threshold": 60, "exam_round_id": exam, "on_pass_next_round_id": None}


# ===========================================================================
# An exam round a candidate could not open blocks publishing
# ===========================================================================
def test_a_published_exam_round_with_questions_is_ready() -> None:
    assert exam_round_problem({"status": "published", "questions": 8}) is None


@pytest.mark.parametrize(
    ("readiness", "expected"),
    [
        ({"status": "draft", "questions": 8}, "draft"),
        ({"status": "published", "questions": 0}, "no questions"),
        (None, "no longer exists"),
    ],
)
def test_an_exam_round_candidates_cannot_open_is_named(
    readiness: dict[str, Any] | None, expected: str
) -> None:
    problem = exam_round_problem(readiness)
    assert problem is not None and expected in problem


def test_a_draft_exam_round_is_a_blocking_error() -> None:
    """The live defect: validation only checked that SOMETHING was attached."""
    errors = exam_round_errors([_round()], {"er-1": {"status": "draft", "questions": 8}})
    assert len(errors) == 1
    assert errors[0].startswith("Aptitude:") and "draft" in errors[0]


def test_an_empty_exam_round_is_a_blocking_error() -> None:
    errors = exam_round_errors([_round()], {"er-1": {"status": "published", "questions": 0}})
    assert len(errors) == 1 and "no questions" in errors[0]


def test_ready_rounds_and_non_exam_rounds_raise_nothing() -> None:
    rounds = [
        _round(),
        _round(kind="ai_interview", exam=None, title="Interview"),
        _round(kind="human_review", exam=None, title="Review"),
    ]
    assert exam_round_errors(rounds, {"er-1": {"status": "published", "questions": 3}}) == []


def test_a_round_with_nothing_attached_is_left_to_validate_chain() -> None:
    """validate_chain already says it needs questions; saying it twice helps nobody."""
    assert exam_round_errors([_round(exam=None)], {}) == []


def test_validate_checks_exam_round_readiness() -> None:
    from app.workflows import validate

    src = inspect.getsource(validate)
    assert "exam_round_readiness(" in src and "exam_round_errors(" in src


# ===========================================================================
# The runner never mints a link to an exam round candidates cannot open
# ===========================================================================
def _db(scalar: object = None) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=scalar)
    db.execute = AsyncMock()
    return db


def _enrolment() -> dict[str, Any]:
    return {"id": uuid.uuid4(), "company_id": uuid.uuid4(), "applicant_id": uuid.uuid4(),
            "email": "c@example.com", "full_name": "Arjun"}


def _exam_round() -> dict[str, Any]:
    return {"id": uuid.uuid4(), "kind": "mcq", "exam_round_id": uuid.uuid4(),
            "deadline_days": 5, "title": "Aptitude"}


@pytest.mark.asyncio
async def test_a_draft_exam_round_gets_no_link_and_the_owner_is_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow_runner as wr

    round_ = _exam_round()

    async def _readiness(_db: object, ids: list[Any]) -> dict[str, Any]:
        return {str(ids[0]): {"exam_id": uuid.uuid4(), "status": "draft", "questions": 8}}

    sent: list[dict[str, Any]] = []
    notified: list[dict[str, Any]] = []

    async def _enqueue(_db: object, **kw: Any) -> object:
        sent.append(kw)
        return object()

    async def _notify(_db: object, **kw: Any) -> None:
        notified.append(kw)

    monkeypatch.setattr(wr, "exam_round_readiness", _readiness)
    monkeypatch.setattr(wr, "enqueue_email", _enqueue)
    monkeypatch.setattr(wr, "create_notification", _notify)
    owner = uuid.uuid4()
    db = _db()

    await wr._assign_round(db, enrolment=_enrolment(), round_=round_,
                           workflow={"created_by_user_id": owner})

    inserts = [c for c in db.execute.call_args_list
               if "INSERT INTO exam_assignments" in str(c.args[0])]
    assert inserts == []
    assert sent == []
    assert len(notified) == 1
    assert notified[0]["user_id"] == owner
    assert "draft" in notified[0]["body"]


@pytest.mark.asyncio
async def test_the_exam_invite_is_sent_in_the_candidates_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow_runner as wr

    round_ = _exam_round()

    async def _readiness(_db: object, ids: list[Any]) -> dict[str, Any]:
        return {str(ids[0]): {"exam_id": uuid.uuid4(), "status": "published", "questions": 8}}

    async def _lang(_db: object, _applicant_id: object) -> str:
        return "hi"

    sent: list[dict[str, Any]] = []

    async def _enqueue(_db: object, **kw: Any) -> object:
        sent.append(kw)
        return object()

    monkeypatch.setattr(wr, "exam_round_readiness", _readiness)
    monkeypatch.setattr(wr, "candidate_language", _lang)
    monkeypatch.setattr(wr, "enqueue_email", _enqueue)

    db = _db()
    # PH4-D2: _assign_round also looks up an effective accommodation (none
    # here) before minting the assignment — a plain SELECT, never db.scalar.
    exec_result = MagicMock()
    exec_result.mappings.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=exec_result)

    await wr._assign_round(db, enrolment=_enrolment(), round_=round_,
                           workflow={"created_by_user_id": uuid.uuid4()})

    assert len(sent) == 1
    assert sent[0]["template"] == "exam_link" and sent[0]["lang"] == "hi"


def test_the_workflow_interview_invite_carries_the_candidates_language() -> None:
    import app.workflow_runner as wr

    src = inspect.getsource(wr._assign_round)
    assert "language=await candidate_language(db, applicant.id)" in src


# ===========================================================================
# Candidate emails use the candidate's language
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "expected"),
    [("hi", "hi"), ("te", "te"), ("en", "en"), ("TE ", "te"), ("fr", "en"), (None, "en")],
)
async def test_candidate_language_reads_the_linked_account(stored: object, expected: str) -> None:
    from app.mailer import candidate_language

    assert await candidate_language(_db(scalar=stored), uuid.uuid4()) == expected


@pytest.mark.asyncio
async def test_no_applicant_means_english_without_a_query() -> None:
    from app.mailer import candidate_language

    db = _db(scalar="hi")
    assert await candidate_language(db, None) == "en"
    db.scalar.assert_not_called()


@pytest.mark.parametrize(
    ("module", "function"),
    [
        ("app.routers.hr_applicants", "email_applicant_decision"),
        ("app.routers.hr_exams", "assign_exam"),
    ],
)
def test_candidate_emails_no_longer_hard_code_english(module: str, function: str) -> None:
    import importlib

    src = inspect.getsource(getattr(importlib.import_module(module), function))
    assert 'lang="en"' not in src
    assert "candidate_language(db, applicant.id)" in src


# ===========================================================================
# Public applications are scored promptly
# ===========================================================================
def test_a_public_application_wakes_the_reconciler_after_it_is_saved() -> None:
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert "wake_reconciler()" in src
    assert src.index("wake_reconciler()") > src.index("await db.commit()")


# ===========================================================================
# The application-received email reads correctly in every language
# ===========================================================================
_CTX = {"name": "Arjun Rao", "job_title": "Python Developer", "company": "Acme & Co",
        "set_url": "https://example.test/activate#t"}


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_the_plain_text_part_carries_no_html(lang: str) -> None:
    from app.email_templates import render

    out = render("application_received", lang, _CTX)
    assert "<strong>" not in out.text and "&amp;" not in out.text
    assert "Acme & Co" in out.text and "Python Developer" in out.text


@pytest.mark.parametrize("lang", ["hi", "te"])
def test_indian_language_copy_has_no_english_fragment(lang: str) -> None:
    from app.email_templates import render

    out = render("application_received", lang, _CTX)
    assert " at " not in out.text
    assert "<strong>Acme &amp; Co</strong>" in out.html


def test_english_copy_still_names_the_company() -> None:
    from app.email_templates import render

    out = render("application_received", "en", _CTX)
    assert "Thanks — your application for Python Developer at Acme & Co is in." in out.text
    assert "<strong>Acme &amp; Co</strong>" in out.html


def test_without_a_company_no_dangling_words_remain() -> None:
    from app.email_templates import render

    ctx = {**_CTX, "company": None}
    assert "Thanks — your application for Python Developer is in." in render(
        "application_received", "en", ctx).text
    assert "धन्यवाद — Python Developer के लिए आपका आवेदन मिल गया है।" in render(
        "application_received", "hi", ctx).text


# ===========================================================================
# An exam round a live (or reviewed) workflow uses cannot be unpublished
# ===========================================================================
def test_unpublishing_a_round_used_by_a_live_workflow_is_refused() -> None:
    """PH4-D1 widened this guard: an ARCHIVED workflow may still have a
    candidate finishing on it, and an IN-REVIEW or APPROVED version was
    reviewed against this round's exact content (the O6 fingerprint) — both
    are now refused too, not just a currently PUBLISHED workflow."""
    from app.routers.hr_rounds import update_round

    src = " ".join(inspect.getsource(update_round).split())
    assert 'body.status == "draft" and rnd.status == "published"' in src
    assert "w.status IN ('published', 'archived')" in src
    assert "w.review_status IN ('in_review', 'approved')" in src
    assert "HTTP_409_CONFLICT" in src


# ===========================================================================
# The builder reads a round's criteria in the shape it sends them back
# ===========================================================================
def test_a_rounds_criteria_carry_the_ids_the_builder_keys_on() -> None:
    """Found while checking the fixed builder: the workflow response carried only
    competency_id/_name/_kind, so no competency ever showed as ticked, and
    ticking one sent the others back with no id (CriterionIn refused it)."""
    from app.routers.hr_workflows import CriterionIn, _criterion_out

    stored = {"competency_id": "python", "competency_name": "Python proficiency",
              "competency_kind": "technical", "weight": 0.4,
              "anchors": {"low": "l", "mid": "m", "high": "h"}, "probes": ["p"]}
    out = _criterion_out(stored)
    assert (out["id"], out["name"], out["kind"]) == ("python", "Python proficiency", "technical")
    assert out["competency_id"] == "python"  # the other readers' names stay
    # What the builder sends back verbatim is accepted.
    sent = {k: out[k] for k in ("id", "name", "kind", "weight", "anchors", "probes")}
    assert CriterionIn.model_validate(sent).id == "python"
