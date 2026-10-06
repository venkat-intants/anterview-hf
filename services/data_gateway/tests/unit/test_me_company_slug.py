"""``GET /auth/me`` carries the company's careers slug, and the positional unpack
that makes that fragile is pinned here.

WHY THE FIELD EXISTS. The public careers board is at ``/careers/<company-slug>`` and
candidates apply there with no login. The slug reached the browser in exactly two
places — a signed-in CANDIDATE's own console (``/applications/open-roles``) and the
board's own response — plus the PLATFORM OWNER's company table, as a bare slug with
no link. So the company's own staff could not discover their own front door. ``/me``
is the carrier because its SELECT already LEFT JOINs ``companies`` for ``c.name``, and
both consoles that host the link already query it.

WHY THESE TESTS LOOK LIKE THIS. ``_build_me_response`` unpacks its row POSITIONALLY
through ``g(i)``. Appending a column is safe; inserting one silently shifts ``g(15)``
(``email_verified``) and ``g(16)`` (``notify_login_email``) — both booleans, so the
symptom is a wrong value rather than an error, and no existing test would catch it.
The order assertions below are the guard against that, and they are the reason this
file is not simply "the field is present".
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.routers import auth as auth_router


def _select_sql() -> str:
    """The SELECT text out of ``_build_me_response``'s source.

    Read from the source rather than executed: the alternative needs a live
    Postgres, and what is being asserted is the SHAPE of the query — which columns,
    in which order — not what a database returns for it.
    """
    return inspect.getsource(auth_router._build_me_response)


class TestTheFieldIsThere:
    def test_meresponse_declares_company_slug(self) -> None:
        assert "company_slug" in auth_router.MeResponse.model_fields

    def test_it_is_optional_because_a_platform_owner_has_no_company(self) -> None:
        """``company_id`` is NULL for ``platform_owner``, so the LEFT JOIN yields no
        slug. A required field here would make ``/auth/me`` fail for the one account
        that administers the platform."""
        field = auth_router.MeResponse.model_fields["company_slug"]

        assert field.default is None
        assert not field.is_required()

    def test_the_query_selects_the_slug(self) -> None:
        assert "c.slug AS company_slug" in _select_sql()

    def test_the_response_is_built_from_it(self) -> None:
        assert re.search(r"company_slug=g\(\d+\)", _select_sql())


class TestThePositionalUnpackStaysAligned:
    """The fragile part. Each ``g(i)`` is an index into the row, so the SELECT's
    column order and the constructor's indices are one contract spread across two
    places thirty lines apart.
    """

    def test_the_slug_is_the_last_column_selected(self) -> None:
        """Appended, not inserted. Inserting it mid-list would shift every later
        index by one, and the two it would shift are booleans — so the service would
        report the wrong ``email_verified`` and the wrong ``notify_login_email``
        rather than failing.

        Asserted as an ORDER over the column names, which survives however the SQL
        string happens to be split across source lines."""
        sql = _select_sql()
        order = [
            sql.index(token)
            for token in (
                "u.email_verified_at",
                "u.notify_login_email",
                "c.slug AS company_slug",
                " FROM users u LEFT JOIN companies c",
            )
        ]

        assert order == sorted(order), (
            "the SELECT list order changed — c.slug must be the LAST column, after "
            "notify_login_email and before FROM, or the g(i) indices have shifted"
        )

    def test_the_slug_takes_the_highest_index(self) -> None:
        """The arithmetic form of the same rule, and the one that actually fails if
        somebody renumbers by hand."""
        sql = _select_sql()
        indices = {
            name: int(num)
            for name, num in re.findall(r"(\w+)=(?:str\()?g\((\d+)\)", sql)
        }

        assert "company_slug" in indices, "company_slug is not built from a g(i)"
        assert indices["company_slug"] == max(indices.values()), (
            f"company_slug is at g({indices['company_slug']}) but the highest index "
            f"in use is g({max(indices.values())}) — a column was inserted rather "
            "than appended, and every index past it now reads the wrong column"
        )

    @pytest.mark.parametrize(
        ("field", "expected"),
        [("email_verified", 15), ("notify_login_email", 16), ("company_slug", 17)],
    )
    def test_the_three_trailing_indices_are_exactly_these(
        self, field: str, expected: int
    ) -> None:
        """Named individually so a shift says WHICH field moved. These are the two
        booleans a mid-list insert would corrupt, plus the new column itself."""
        sql = _select_sql()
        match = re.search(rf"{field}=(?:bool\()?g\((\d+)\)", sql)

        assert match is not None, f"{field} is no longer built from a g(i)"
        assert int(match.group(1)) == expected, (
            f"{field} moved from g({expected}) to g({match.group(1)}) — if that was "
            "deliberate, the SELECT order changed and every sibling index needs "
            "re-checking against it"
        )


def test_the_field_name_matches_the_two_responses_that_already_carry_it() -> None:
    """One name for one thing. ``candidate_applications.OpenRole`` and
    ``careers.CareersBoard`` both call it ``company_slug``; a third spelling here
    would mean the frontend needs to know which endpoint it asked."""
    from app.routers import candidate_applications, careers

    assert "company_slug" in candidate_applications.OpenRole.model_fields
    assert "company_slug" in careers.CareersBoard.model_fields
