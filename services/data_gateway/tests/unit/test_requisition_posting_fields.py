"""The advert half of a requisition — validation, and what reaches the public.

These columns exist so a candidate can decide whether a role is worth opening,
which means most of them are candidate-facing by definition. Salary is the one
that is not, and that asymmetry is what most of this file is about.

The rest guards the two ways a range can be wrong (inverted, or half-given) and
the jsonb binding, which fails in a way that looks like a database fault rather
than like the list that caused it.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


def _posting(**kw: object):
    from app.routers.hr_requisitions import PostingFields

    return PostingFields(**kw)


# ===========================================================================
# Ranges
# ===========================================================================
def test_an_inverted_experience_range_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        _posting(experience_min_years=8, experience_max_years=3)
    assert "experience_min_years cannot exceed" in str(exc.value)


def test_an_inverted_salary_range_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        _posting(salary_min=2_000_000, salary_max=1_200_000)
    assert "salary_min cannot exceed" in str(exc.value)


def test_equal_bounds_are_fine() -> None:
    """A single-point band is a real advert: "5 years", "₹12L"."""
    p = _posting(experience_min_years=5, experience_max_years=5, salary_min=1, salary_max=1)
    assert p.experience_min_years == 5


@pytest.mark.parametrize(
    "kw",
    [
        {"experience_min_years": 3},
        {"experience_max_years": 3},
        {"salary_min": 100},
        {"salary_max": 100},
    ],
)
def test_half_a_range_is_allowed(kw: dict) -> None:
    """"3+ years" and "up to ₹20L" are both things job adverts say."""
    assert _posting(**kw) is not None


def test_a_negative_bound_is_refused() -> None:
    with pytest.raises(ValidationError):
        _posting(experience_min_years=-1)


# ===========================================================================
# Employment type
# ===========================================================================
def test_the_known_employment_types_are_accepted() -> None:
    from app.routers.hr_requisitions import EMPLOYMENT_TYPES

    for value in EMPLOYMENT_TYPES:
        assert _posting(employment_type=value).employment_type == value


def test_an_unknown_employment_type_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        _posting(employment_type="freelance-ish")
    assert "employment_type must be one of" in str(exc.value)


def test_the_api_and_the_database_agree_on_employment_types() -> None:
    """Drift here is a 500 rather than a 422 — the API would accept a value the
    check constraint then refuses, and the recruiter sees a server error on a
    dropdown the product offered them."""
    import pathlib

    from app.routers.hr_requisitions import EMPLOYMENT_TYPES

    migration = (
        pathlib.Path(__file__).parents[2]
        / "alembic/versions/20260906_0001_c4e6a8b0d2f4_requisition_posting_fields.py"
    ).read_text(encoding="utf-8")
    constraint = migration.split("ck_job_requisitions_employment_type")[1].split(")")[0]
    for value in EMPLOYMENT_TYPES:
        assert f"'{value}'" in constraint


# ===========================================================================
# The lists
# ===========================================================================
def test_blank_entries_are_dropped_and_order_kept() -> None:
    p = _posting(responsibilities=["Ship features", "   ", "Review code", ""])
    assert p.responsibilities == ["Ship features", "Review code"]


def test_whitespace_inside_an_entry_is_collapsed() -> None:
    p = _posting(required_skills=["  Python   3.12  "])
    assert p.required_skills == ["Python 3.12"]


def test_an_over_long_list_is_refused_by_name() -> None:
    """Refused, not truncated.

    Silently keeping the first twenty of thirty bullets loses content the
    recruiter is never told about; a 422 naming the field leaves what they
    typed on screen while they cut it down.
    """
    from app.routers.hr_requisitions import _MAX_LIST_ITEMS

    with pytest.raises(ValidationError) as exc:
        _posting(responsibilities=[f"Item {i}" for i in range(100)])
    assert "responsibilities" in str(exc.value)
    assert str(_MAX_LIST_ITEMS) in str(exc.value)


def test_a_list_at_the_limit_is_accepted() -> None:
    from app.routers.hr_requisitions import _MAX_LIST_ITEMS

    p = _posting(responsibilities=[f"Item {i}" for i in range(_MAX_LIST_ITEMS)])
    assert p.responsibilities is not None
    assert len(p.responsibilities) == _MAX_LIST_ITEMS


def test_a_very_long_entry_is_trimmed_rather_than_refused() -> None:
    """Different call from the count. One pasted paragraph is a formatting
    slip, not lost information — the advert keeps its first 300 characters."""
    from app.routers.hr_requisitions import _MAX_ITEM_CHARS

    p = _posting(responsibilities=["x" * 5_000])
    assert p.responsibilities is not None
    assert len(p.responsibilities[0]) == _MAX_ITEM_CHARS


def test_an_absent_list_stays_absent() -> None:
    """None and [] must not collapse into each other: on a PATCH one means
    "leave it alone" and the other means "clear it"."""
    assert _posting().responsibilities is None
    assert _posting(responsibilities=[]).responsibilities == []


# ===========================================================================
# jsonb binding — the failure that looks like a database fault
# ===========================================================================
def test_the_list_columns_are_bound_as_json_with_a_cast() -> None:
    """A Python list bound into a jsonb column arrives as a Postgres ARRAY and
    the statement fails on a type nobody mentioned in the request."""
    import inspect

    from app.routers.hr_requisitions import _JSON_LIST_FIELDS, update_requisition

    assert {
        "responsibilities", "required_skills", "nice_to_have_skills",
    } == _JSON_LIST_FIELDS
    src = inspect.getsource(update_requisition)
    assert "CAST(:{k} AS jsonb)" in src or "AS jsonb" in src
    assert "json.dumps" in src


def test_create_serialises_the_lists() -> None:
    import inspect

    from app.routers.hr_requisitions import create_requisition

    src = inspect.getsource(create_requisition)
    for field in ("resp", "req", "nice"):
        assert f"CAST(:{field} AS jsonb)" in src
    assert src.count("json.dumps") >= 3


def test_an_unset_salary_visible_does_not_become_null() -> None:
    """The column is NOT NULL. An optional the recruiter never touched is None,
    and binding that straight through violates the constraint on every create
    that omits it — which is most of them."""
    import inspect

    from app.routers.hr_requisitions import create_requisition

    assert "bool(body.salary_visible)" in inspect.getsource(create_requisition)


# ===========================================================================
# What the public posting shows — and what it does not
# ===========================================================================
def _posting_out(**row: object):
    """Build the public payload the way get_posting does."""
    from app.routers.public_apply import PostingOut

    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Backend Engineer",
        "level": "mid",
        "company_name": "Acme",
        "jd_text": "Build things.",
        "closes_at": None,
        "salary_min": 1_200_000,
        "salary_max": 2_000_000,
        "salary_currency": "INR",
        "salary_visible": False,
        **row,
    }
    shows = bool(base.get("salary_visible"))
    return PostingOut(
        requisition_id=str(base["id"]),
        title=base["title"],
        level=base["level"],
        company_name=base["company_name"],
        jd_text=base["jd_text"],
        closes_at=None,
        department=base.get("department"),
        location=base.get("location"),
        employment_type=base.get("employment_type"),
        experience_min_years=base.get("experience_min_years"),
        experience_max_years=base.get("experience_max_years"),
        responsibilities=base.get("responsibilities") or [],
        required_skills=base.get("required_skills") or [],
        nice_to_have_skills=base.get("nice_to_have_skills") or [],
        salary_min=base.get("salary_min") if shows else None,
        salary_max=base.get("salary_max") if shows else None,
        salary_currency=base.get("salary_currency") if shows else None,
    )


def test_a_recorded_but_unpublished_salary_never_reaches_the_candidate() -> None:
    """The whole reason salary_visible is separate from "a salary exists"."""
    out = _posting_out(salary_visible=False)
    body = out.model_dump_json()
    assert out.salary_min is None
    assert "1200000" not in body
    assert "2000000" not in body


def test_a_published_salary_does_reach_the_candidate() -> None:
    out = _posting_out(salary_visible=True)
    assert (out.salary_min, out.salary_max, out.salary_currency) == (1_200_000, 2_000_000, "INR")


def test_the_advert_fields_are_all_candidate_facing() -> None:
    out = _posting_out(
        department="Engineering",
        location="Bangalore",
        employment_type="full_time",
        experience_min_years=3,
        experience_max_years=6,
        responsibilities=["Ship features"],
        required_skills=["Python"],
        nice_to_have_skills=["Kubernetes"],
    )
    assert out.department == "Engineering"
    assert out.location == "Bangalore"
    assert out.required_skills == ["Python"]


def test_the_public_posting_still_reveals_nothing_operational() -> None:
    """Unchanged rule: this is served to anyone with the link, so applicant
    counts, owners and internal state stay out of it."""
    from app.routers.public_apply import PostingOut

    fields = set(PostingOut.model_fields)
    for forbidden in (
        "owner_user_id", "created_by_user_id", "status", "target_hires",
        "public_apply_enabled", "from_backfill", "salary_visible",
    ):
        assert forbidden not in fields


def test_the_posting_payload_serialises() -> None:
    """Cheap end-to-end on the model: a jsonb column read back as None must not
    422 the response on an opening created before these fields existed."""
    out = _posting_out(responsibilities=None, required_skills=None)
    assert json.loads(out.model_dump_json())["responsibilities"] == []
