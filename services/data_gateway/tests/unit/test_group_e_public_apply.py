"""E4 — the public application link: what it refuses, reveals and rewrites.

The endpoint is exercised end to end against Postgres in
``smoke_group_e_intake``. These pin the properties that would quietly regress.
"""

from __future__ import annotations

import inspect


def _submit() -> str:
    from app.routers.public_apply import submit_application

    return inspect.getsource(submit_application)


def test_an_opening_without_a_published_workflow_takes_no_applications() -> None:
    """Since PH3-B0 the gate is one shared predicate, so this asserts against
    that rather than against this endpoint's own copy of it — the copy is what
    the careers board drifted away from."""
    from app.publishing import visible_sql
    from app.routers.public_apply import _open_posting

    gate = visible_sql("r")
    assert "w.status = 'published'" in gate
    assert "r.public_apply_enabled" in gate
    assert "visible_sql('r')" in inspect.getsource(_open_posting)


def test_already_applied_is_answered_only_after_a_readable_cv() -> None:
    """Checked first, it was a free oracle: type an email, learn whether that
    person applied and what their name is."""
    src = _submit()
    assert src.index("_extract_pdf_text(raw)") < src.index("already_applied=True")


def test_nothing_stored_is_echoed_to_a_repeat_or_racing_submission() -> None:
    src = _submit()
    assert 'existing["full_name"]' not in src
    assert 'existing["enrolment_id"])' not in src
    # Both "already applied" replies carry no ids.
    assert src.count('applicant_id="",') == 2


def test_a_returning_applicants_record_is_not_rewritten_by_the_form() -> None:
    """Anyone who knew an email address could replace that person's CV, role,
    contact details and scores."""
    src = " ".join(_submit().split())
    assert "UPDATE applicants SET resume_text" not in src
    assert "previous_key" not in src
    # Their CV is pinned to the new application instead.
    assert "resume_s3_key=s3_key," in src


def test_the_upload_read_is_bounded() -> None:
    assert "resume.read(_MAX_RESUME_BYTES + 1)" in _submit()


def test_the_applicant_chooses_the_language_of_their_emails() -> None:
    from pydantic import TypeAdapter, ValidationError

    from app.routers.public_apply import _ensure_guest_user, submit_application

    param = inspect.signature(submit_application).parameters["language"]
    assert param.default == "en"
    assert ":lang" in inspect.getsource(_ensure_guest_user)

    import typing

    hint = typing.get_type_hints(submit_application, include_extras=True)["language"]
    adapter = TypeAdapter(hint)
    for ok in ("en", "hi", "te"):
        assert adapter.validate_python(ok) == ok
    try:
        adapter.validate_python("fr")
    except ValidationError:
        pass
    else:  # pragma: no cover — the assertion is the point
        raise AssertionError("an unsupported language was accepted")
