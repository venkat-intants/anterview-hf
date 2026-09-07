"""Whose name is on an application — decision 2.

The defect this file exists to prevent was live and observed: an application
submitted through the public form as "Nadia Newbie" was in the database as
"Priya Sharma", the name inside the uploaded PDF, because the reconciler
overwrote ``full_name`` for any row flagged ``pending_enrichment``.

The comment beside the overwrite argued it was correct — "the CV is the
document the employer will read" — and for HR's bulk upload it is: the name
there is derived from a filename and the parsed name is strictly better. The
mistake was applying that to a form where a person types their own name.

So these tests are mostly about one distinction: a name a PERSON supplied is
never replaced, a PLACEHOLDER always may be. The parsed name is kept either
way, which is what makes the confirmation step possible.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest


class _Applicant:
    """Just enough of the model for the identity backfill to act on."""

    def __init__(self, **kw: object) -> None:
        self.full_name = kw.get("full_name", "cv_2024_final")
        self.full_name_source = kw.get("full_name_source")
        self.parsed_full_name = kw.get("parsed_full_name")
        self.email = kw.get("email")
        self.pending_enrichment = kw.get("pending_enrichment", True)
        self.updated_at = datetime(2020, 1, 1, tzinfo=UTC)


def _apply(a: _Applicant, **score: object) -> bool:
    from app.applicant_enrichment import apply_extracted_identity

    return apply_extracted_identity(a, dict(score))  # type: ignore[arg-type]


# ===========================================================================
# The regression
# ===========================================================================
def test_a_candidates_typed_name_is_not_replaced_by_the_pdf() -> None:
    """The exact case that was broken. "Nadia Newbie" stays "Nadia Newbie"."""
    a = _Applicant(full_name="Nadia Newbie", full_name_source="candidate")
    _apply(a, candidate_name="Priya Sharma")
    assert a.full_name == "Nadia Newbie"


def test_a_recruiters_typed_name_is_not_replaced_either() -> None:
    """Same rule. A person typed it; a parser did not."""
    a = _Applicant(full_name="Asha Rao", full_name_source="hr")
    _apply(a, candidate_name="A. Rao (CV)")
    assert a.full_name == "Asha Rao"


def test_a_filename_placeholder_is_still_replaced() -> None:
    """The behaviour that was right all along, and must survive the fix —
    otherwise bulk upload leaves everyone named after their file."""
    a = _Applicant(full_name="asha_rao_resume_v2", full_name_source="filename")
    assert _apply(a, candidate_name="Asha Rao") is True
    assert a.full_name == "Asha Rao"
    assert a.full_name_source == "resume"


def test_a_row_from_before_the_column_behaves_as_it_did() -> None:
    """NULL source means a row predating this change. Every one of them came in
    through bulk upload, so treating it as a placeholder preserves exactly
    today's behaviour rather than freezing a name nobody typed."""
    a = _Applicant(full_name="cv_final_2", full_name_source=None)
    _apply(a, candidate_name="Rahul Verma")
    assert a.full_name == "Rahul Verma"


# ===========================================================================
# The parsed name is kept regardless — it is the confirmation material
# ===========================================================================
def test_the_parsed_name_is_recorded_even_when_it_is_not_used() -> None:
    """Without this there is nothing to show the candidate, and HR can only
    ever see one of the two names."""
    a = _Applicant(full_name="Nadia Newbie", full_name_source="candidate")
    assert _apply(a, candidate_name="Priya Sharma") is True
    assert a.parsed_full_name == "Priya Sharma"
    assert a.full_name == "Nadia Newbie"


def test_the_parsed_name_is_recorded_when_it_is_used_too() -> None:
    a = _Applicant(full_name="cv.pdf", full_name_source="filename")
    _apply(a, candidate_name="Asha Rao")
    assert a.parsed_full_name == "Asha Rao"
    assert a.full_name == "Asha Rao"


def test_a_matching_parsed_name_reports_no_change() -> None:
    """Re-running the reconciler over an unchanged row must not look like an
    edit — 'changed' drives a log line about a name moving."""
    a = _Applicant(
        full_name="Asha Rao", full_name_source="candidate", parsed_full_name="Asha Rao"
    )
    assert _apply(a, candidate_name="Asha Rao") is False


# ===========================================================================
# Unchanged guarantees
# ===========================================================================
def test_a_row_that_is_not_pending_is_left_alone_entirely() -> None:
    a = _Applicant(full_name="Asha Rao", pending_enrichment=False)
    assert _apply(a, candidate_name="Someone Else") is False
    assert a.full_name == "Asha Rao"
    assert a.parsed_full_name is None


def test_an_empty_parsed_name_changes_nothing() -> None:
    a = _Applicant(full_name="asha_cv", full_name_source="filename")
    _apply(a, candidate_name="   ")
    assert a.full_name == "asha_cv"
    assert a.parsed_full_name is None


def test_the_email_branch_still_only_fills_a_gap() -> None:
    """It was always right; the name branch has been brought into line with it,
    so a regression in either direction should fail here."""
    a = _Applicant(email="typed@example.com", full_name_source="candidate")
    _apply(a, candidate_email="off-the-cv@example.com")
    assert a.email == "typed@example.com"


def test_a_missing_email_is_still_filled_from_the_cv() -> None:
    a = _Applicant(email=None, full_name_source="candidate")
    assert _apply(a, candidate_email="asha@example.com") is True
    assert a.email == "asha@example.com"


def test_a_malformed_extracted_email_is_dropped() -> None:
    a = _Applicant(email=None, full_name_source="filename")
    _apply(a, candidate_email="Jane Doe | jane@")
    assert a.email is None


def test_the_pending_flag_is_always_cleared() -> None:
    """Otherwise the reconciler picks the row up forever."""
    a = _Applicant(full_name="Nadia", full_name_source="candidate")
    _apply(a, candidate_name="Priya")
    assert a.pending_enrichment is False


# ===========================================================================
# Every ingest path declares where the name came from
# ===========================================================================
@pytest.mark.parametrize(
    ("module", "function", "expected"),
    [
        ("app.routers.public_apply", "submit_application", '"candidate"'),
        ("app.routers.hr_applicants", "create_applicant", '"hr"'),
    ],
)
def test_typed_names_are_marked_as_authored(
    module: str, function: str, expected: str
) -> None:
    """A path that forgets this gets NULL, which is read as a placeholder — so
    the omission would silently restore the bug for that path only."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, function))
    assert f"full_name_source={expected}" in src


def test_bulk_upload_marks_its_names_as_placeholders() -> None:
    """The one path where replacement is an improvement."""
    from app.routers import hr_applicants

    src = inspect.getsource(hr_applicants)
    assert 'full_name_source="filename"' in src


def test_the_authored_sources_are_the_ones_a_person_typed() -> None:
    from app.applicant_enrichment import AUTHORED_NAME_SOURCES

    assert {"candidate", "hr"} == AUTHORED_NAME_SOURCES


def test_the_api_and_the_database_agree_on_name_sources() -> None:
    """A source the API writes but the constraint refuses is a 500 on ingest."""
    import pathlib

    migration = (
        pathlib.Path(__file__).parents[2]
        / "alembic/versions/20260906_0002_d5f7b9c1e3a6_applicant_details_and_name_source.py"
    ).read_text(encoding="utf-8")
    constraint = migration.split("ck_applicants_full_name_source")[1].split(")")[0]
    for value in ("candidate", "hr", "filename", "resume"):
        assert f"'{value}'" in constraint


# ===========================================================================
# A repeat application must not wipe what was said the first time
# ===========================================================================
def test_a_returning_applicant_keeps_details_they_did_not_repeat() -> None:
    """COALESCE, not assignment. Skipping "current company" on a second
    application does not mean somebody left their job."""
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    for column in ("phone", "current_company", "current_title", "linkedin_url"):
        assert f"{column} = COALESCE(" in src


def test_a_repeat_application_does_not_rename_the_person() -> None:
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    update = src[src.index("UPDATE applicants SET") : src.index("WHERE id = :i")]
    assert "full_name" not in update


def test_a_blank_optional_field_is_stored_as_absent() -> None:
    """Browsers submit an untouched input as "". Stored verbatim it would make
    "left blank" and "answered with nothing" the same value, and the COALESCE
    above would then overwrite last time's answer with an empty string."""
    from app.routers.public_apply import _clean

    assert _clean("", 40) is None
    assert _clean("   ", 40) is None
    assert _clean("  Acme   Corp  ", 40) == "Acme Corp"
    assert _clean(None, 40) is None
    assert _clean("x" * 100, 40) == "x" * 40
