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


def test_the_reply_model_carries_nothing_that_depends_on_stored_state() -> None:
    """Four fields, and no room for a fifth with an opinion in it.

    Both of the last two disclosures were a FIELD: `awaiting_confirmation`,
    then `already_applied`. Each was added in good faith, each was true, and
    each let anyone who typed an address into an anonymous form learn that a
    named person had applied here and been turned down. The shape of the reply
    is therefore the invariant, not the spelling of any one leak.
    """
    from app.routers.public_apply import ApplicationOut

    assert set(ApplicationOut.model_fields) == {
        "applicant_id",
        "enrolment_id",
        "full_name",
        "message",
    }


def test_every_reply_from_both_doors_is_the_same_object() -> None:
    """No call site builds its own.

    The four-case indistinguishability property is held down properly by
    `tests/integration/test_ph3_cooldown_indistinguishable.py`, which drives
    the real app and compares whole bodies. This is the cheap structural
    companion: a reply assembled inline is how a branch grows its own opinion
    again, and it is the one thing a source read genuinely can see.
    """
    from app.routers import public_apply

    src = inspect.getsource(public_apply)
    # One construction, inside `_received` itself.
    assert src.count("ApplicationOut(") == 2, (
        "ApplicationOut is constructed somewhere other than `_received` — "
        "every door must answer with the one reply"
    )
    for door in (public_apply.submit_application, public_apply.submit_draft):
        body = inspect.getsource(door)
        assert "ApplicationOut(" not in body, f"{door.__name__} builds its own reply"
        # Through `_reply`, which is `_received` held to the common deadline.
        # A door returning `_received` directly would answer with the right
        # bytes at the wrong time, which is the channel round 6 found.
        assert "_reply(name, floor_from=floor_from)" in body, (
            f"{door.__name__} answers without holding the reply floor"
        )
        assert "_received(name)" not in body


def test_nothing_stored_is_echoed_to_a_repeat_or_racing_submission() -> None:
    src = _submit()
    assert 'existing["full_name"]' not in src
    assert 'existing["enrolment_id"])' not in src


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
