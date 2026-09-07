"""The public careers board.

Unauthenticated and reachable by anyone, so most of what matters here is what
the board must NOT do: list a role that cannot be applied to, leak a withheld
salary, leak anything operational, or let a search term widen its own result
set. The filtering itself is ordinary.

The visibility predicate is asserted against ``public_apply``'s rather than
spelled out again — the two must agree, and a board that advertises a role the
apply endpoint then refuses is the one failure a job board really cannot have.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


def _mapping(**kw: object) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Platform Engineer",
        "level": "senior",
        "department": "Engineering",
        "location": "Hyderabad",
        "employment_type": "full_time",
        "experience_min_years": 4,
        "experience_max_years": 8,
        "required_skills": ["Kubernetes", "Terraform"],
        "salary_min": 1_800_000,
        "salary_max": 3_000_000,
        "salary_currency": "INR",
        "salary_visible": False,
        "created_at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    }
    return {**base, **kw}


def _result(rows: list[dict]) -> MagicMock:
    res = MagicMock()
    mapped = MagicMock()
    mapped.all = MagicMock(return_value=rows)
    mapped.first = MagicMock(return_value=rows[0] if rows else None)
    res.mappings = MagicMock(return_value=mapped)
    return res


def _db(company: dict | None, cards: list[dict], facets: list[dict] | None = None) -> AsyncMock:
    """Answers the company lookup, then the card page, then the facet query."""
    db = AsyncMock()
    queue = [
        _result([company] if company else []),
        _result(cards),
        _result(facets if facets is not None else cards),
    ]

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        return queue.pop(0) if queue else _result([])

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=len(cards))
    return db


COMPANY = {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "Acme Test Co",
    "slug": "acme-test",
}


# ===========================================================================
# Visibility — the board and the apply endpoint must agree
# ===========================================================================
def test_only_roles_that_can_be_applied_to_are_listed() -> None:
    from app.routers.careers import _VISIBLE

    for condition in ("deleted_at IS NULL", "status = 'open'", "public_apply_enabled"):
        assert condition in _VISIBLE


def test_a_closed_role_drops_off_the_board_without_anyone_editing_it() -> None:
    from app.routers.careers import _VISIBLE

    assert "closes_at IS NULL OR r.closes_at > :now" in _VISIBLE


def test_the_board_uses_the_same_predicate_as_the_apply_endpoint() -> None:
    """Drift here means advertising a role that 404s on click.

    Compared as conditions rather than as text: the two are written against
    different aliases, so the useful assertion is that neither has a gate the
    other lacks.
    """
    from app.routers.careers import _VISIBLE
    from app.routers.public_apply import _open_posting

    posting_sql = inspect.getsource(_open_posting)
    for gate in ("public_apply_enabled", "status = 'open'", "deleted_at IS NULL"):
        assert gate in _VISIBLE
        assert gate in posting_sql
    # Both enforce the closing date; the posting endpoint does it in Python.
    assert "closes_at" in _VISIBLE
    assert "closes_at" in posting_sql


# ===========================================================================
# Salary — the gate is enforced again, not trusted
# ===========================================================================
@pytest.mark.asyncio
async def test_a_withheld_salary_never_reaches_a_card() -> None:
    from app.routers.careers import get_board

    board = await get_board("acme-test", _db(COMPANY, [_mapping(salary_visible=False)]))
    card = board.items[0]
    assert card.salary_min is None and card.salary_max is None
    assert "1800000" not in board.model_dump_json()


@pytest.mark.asyncio
async def test_a_published_salary_does_reach_a_card() -> None:
    from app.routers.careers import get_board

    board = await get_board("acme-test", _db(COMPANY, [_mapping(salary_visible=True)]))
    assert board.items[0].salary_min == 1_800_000
    assert board.items[0].salary_currency == "INR"


def test_a_card_cannot_carry_the_visibility_flag_itself() -> None:
    """If salary_visible were on the model, a client could tell "withheld" from
    "not recorded" — which is the distinction the flag exists to hide."""
    from app.routers.careers import JobCard

    assert "salary_visible" not in JobCard.model_fields


# ===========================================================================
# What a public board must not expose
# ===========================================================================
def test_a_card_carries_nothing_operational() -> None:
    from app.routers.careers import JobCard

    fields = set(JobCard.model_fields)
    for forbidden in (
        "jd_text", "owner_user_id", "created_by_user_id", "status",
        "target_hires", "public_apply_enabled", "from_backfill",
        "applicant_count", "total_enrolments", "company_id",
    ):
        assert forbidden not in fields


def test_the_board_does_not_ship_every_job_description() -> None:
    """A list response whose size grows with the length of the prose rather
    than the number of roles. The detail page has the full advert."""
    from app.routers.careers import get_board

    assert "jd_text" not in inspect.getsource(get_board)


def test_the_board_is_scoped_to_one_company() -> None:
    from app.routers.careers import get_board

    src = inspect.getsource(get_board)
    assert "r.company_id = :c" in src


# ===========================================================================
# Filtering
# ===========================================================================
@pytest.mark.asyncio
async def test_a_search_term_cannot_widen_the_result_set() -> None:
    """'%' is a LIKE wildcard. Unescaped, searching for it matches every role —
    a filter that widens what it was asked to narrow."""
    from app.routers.careers import get_board

    db = _db(COMPANY, [_mapping()])
    await get_board("acme-test", db, q="%")
    params = db.execute.await_args_list[1].args[1]
    assert params["q"] == "%\\%%"
    assert params["esc"] == "\\"


@pytest.mark.asyncio
async def test_search_covers_skills_as_well_as_the_title() -> None:
    """"Kubernetes" is how people look for a platform role, and it is rarely
    in the title."""
    from app.routers.careers import get_board

    db = _db(COMPANY, [_mapping()])
    await get_board("acme-test", db, q="Kubernetes")
    sql = str(db.execute.await_args_list[1].args[0])
    assert "r.title ILIKE" in sql
    assert "required_skills::text ILIKE" in sql


@pytest.mark.asyncio
async def test_experience_filter_keeps_roles_with_no_stated_minimum() -> None:
    """A role that asks for nothing is open to everybody, so it must not be
    filtered out by someone reporting how much experience they have."""
    from app.routers.careers import get_board

    db = _db(COMPANY, [_mapping()])
    await get_board("acme-test", db, max_experience_years=4)
    sql = str(db.execute.await_args_list[1].args[0])
    assert "r.experience_min_years IS NULL OR r.experience_min_years <= :maxexp" in sql


@pytest.mark.asyncio
async def test_no_filters_means_no_extra_predicates() -> None:
    from app.routers.careers import get_board

    db = _db(COMPANY, [_mapping()])
    await get_board("acme-test", db)
    params = db.execute.await_args_list[1].args[1]
    assert "q" not in params and "dept" not in params and "maxexp" not in params


# ===========================================================================
# Facets
# ===========================================================================
@pytest.mark.asyncio
async def test_filter_options_do_not_shrink_as_you_filter() -> None:
    """Computed over every visible role, not over the filtered page.

    A department list that collapses to the one you already picked cannot be
    used to change your mind, which is most of what a filter is for.
    """
    from app.routers.careers import get_board

    db = _db(
        COMPANY,
        [_mapping(department="Engineering")],
        facets=[
            {"department": "Engineering", "location": "Hyderabad", "employment_type": "full_time"},
            {"department": "Design", "location": "Mumbai", "employment_type": "contract"},
        ],
    )
    board = await get_board("acme-test", db, department="Engineering")
    assert board.filters.departments == ["Design", "Engineering"]
    assert board.filters.locations == ["Hyderabad", "Mumbai"]
    # The facet query carries no user filter — only the company and the clock.
    facet_params = db.execute.await_args_list[2].args[1]
    assert set(facet_params) == {"c", "now"}


@pytest.mark.asyncio
async def test_facets_skip_the_openings_that_never_filled_them_in() -> None:
    from app.routers.careers import get_board

    db = _db(
        COMPANY,
        [_mapping()],
        facets=[
            {"department": None, "location": None, "employment_type": None},
            {"department": "Engineering", "location": None, "employment_type": None},
        ],
    )
    board = await get_board("acme-test", db)
    assert board.filters.departments == ["Engineering"]
    assert board.filters.locations == []


# ===========================================================================
# The company
# ===========================================================================
@pytest.mark.asyncio
async def test_an_unknown_slug_is_a_404() -> None:
    from fastapi import HTTPException

    from app.routers.careers import get_board

    with pytest.raises(HTTPException) as exc:
        await get_board("no-such-company", _db(None, []))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_deactivated_company_has_no_careers_page() -> None:
    from app.routers.careers import _company

    src = inspect.getsource(_company)
    assert "is_active" in src
    assert "deleted_at IS NULL" in src


@pytest.mark.asyncio
async def test_the_slug_is_matched_case_insensitively() -> None:
    """A careers link is typed, pasted and printed; case is not meaningful."""
    from app.routers.careers import _company

    assert "lower(slug) = lower(:s)" in inspect.getsource(_company)


@pytest.mark.asyncio
async def test_an_empty_board_is_a_board_not_an_error() -> None:
    """A company with nothing open still has a careers page."""
    from app.routers.careers import get_board

    board = await get_board("acme-test", _db(COMPANY, [], facets=[]))
    assert board.total == 0
    assert board.items == []
    assert board.company_name == "Acme Test Co"


# ===========================================================================
# Cards
# ===========================================================================
@pytest.mark.asyncio
async def test_a_card_shows_only_the_first_few_skills() -> None:
    from app.routers.careers import _CARD_SKILLS, get_board

    many = [f"Skill {i}" for i in range(20)]
    board = await get_board("acme-test", _db(COMPANY, [_mapping(required_skills=many)]))
    assert board.items[0].skills == many[:_CARD_SKILLS]


@pytest.mark.asyncio
async def test_an_opening_with_no_advert_still_renders_as_a_card() -> None:
    """Most existing openings predate the posting fields entirely."""
    from app.routers.careers import get_board

    bare = _mapping(
        department=None, location=None, employment_type=None,
        experience_min_years=None, experience_max_years=None,
        required_skills=None, salary_visible=False,
    )
    board = await get_board("acme-test", _db(COMPANY, [bare]))
    card = board.items[0]
    assert card.title == "Platform Engineer"
    assert card.skills == []
    assert card.department is None
