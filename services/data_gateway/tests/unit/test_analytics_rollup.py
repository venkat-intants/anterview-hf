"""The roll-up numbers, and the two mistakes that produced 175% and 400%.

Both were about meaning rather than arithmetic, and both looked fine in the
model until real data went through them:

  1. WRONG SOURCE. Conversion was derived from ``HrFunnel``, which counts
     applicants by CURRENT status — somebody shortlisted and then hired has
     left "shortlisted", so a ratio between two of its fields is not a
     conversion rate. It reads the transition ledger now.

  2. WRONG DENOMINATOR. Even off the ledger, "% of the previous stage" assumes
     a chain, and this product does not enforce one: HR can assign an exam by
     hand outside any workflow, so more people can sit an exam than were ever
     shortlisted. Every rate is a share of APPLICATIONS now, which cannot
     exceed 100% and stays true whichever route someone took.

The rest is about not stating things the data does not support: no median
time-to-hire before anybody has been hired, and no percentage out of zero.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _analytics_db(*, conversion: dict) -> AsyncMock:
    """A session that answers the five roll-up queries in the order the handler
    runs them: funnel, averages, openings, velocity, conversion, recent."""
    zeros = dict.fromkeys(
        (
            "total_applicants", "shortlisted", "exam_taken", "exam_passed",
            "interview_invited", "interview_completed", "hired", "rejected",
        ),
        0,
    )
    queue = [
        _one(zeros),
        _one({"avg_ats": None, "avg_exam_percent": None, "avg_interview_composite": None}),
        _many([]),
        _one({"median_days": None, "hires_measured": 0}),
        _one(conversion),
        _one({"last_7d": 0, "prev_7d": 0}),
    ]

    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        return queue.pop(0) if queue else _many([])

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _one(row: dict) -> MagicMock:
    res, mapped = MagicMock(), MagicMock()
    mapped.one = MagicMock(return_value=row)
    mapped.all = MagicMock(return_value=[row])
    res.mappings = MagicMock(return_value=mapped)
    return res


def _many(rows: list[dict]) -> MagicMock:
    res, mapped = MagicMock(), MagicMock()
    mapped.all = MagicMock(return_value=rows)
    mapped.one = MagicMock(return_value=rows[0] if rows else {})
    res.mappings = MagicMock(return_value=mapped)
    return res



def _rate_fn():
    """The percentage helper, lifted out of the handler it is defined in."""
    from app.routers.hr_pipeline import get_analytics

    src = inspect.getsource(get_analytics)
    namespace: dict = {}
    body = src[src.index("    def _rate(") :]
    body = body[: body.index("\n    return HrAnalytics(")]
    exec("\n".join(line[4:] for line in body.splitlines()), namespace)  # noqa: S102
    return namespace["_rate"]


# ===========================================================================
# The percentage helper
# ===========================================================================
def test_a_rate_out_of_nothing_is_not_zero_percent() -> None:
    """Zero is a claim — "nobody converted". An empty funnel has not made it."""
    assert _rate_fn()(0, 0) is None


def test_an_ordinary_rate() -> None:
    assert _rate_fn()(4, 29) == 13.8


def test_everybody_converting_is_a_hundred() -> None:
    assert _rate_fn()(7, 7) == 100.0


# ===========================================================================
# The source
# ===========================================================================
def test_conversion_reads_the_ledger_not_the_status_snapshot() -> None:
    from app.routers.hr_pipeline import _CONVERSION_SQL

    sql = str(_CONVERSION_SQL)
    assert "stage_transitions" in sql
    assert "to_status = 'shortlisted'" in sql


def test_conversion_is_scoped_to_enrolments() -> None:
    """A stage is a property of an application. An applicant HR uploaded by
    hand has no enrolment and no stages, so counting them would put people in
    the denominator who were never in the process being measured."""
    from app.routers.hr_pipeline import _CONVERSION_SQL

    sql = str(_CONVERSION_SQL)
    assert "FROM enrolments" in sql
    assert "company_id = :cid" in sql


def test_the_handler_does_not_derive_conversion_from_the_funnel() -> None:
    """The original bug, asserted at the seam: the funnel fields must not be
    the source of any rate."""
    from app.routers.hr_pipeline import get_analytics

    src = inspect.getsource(get_analytics)
    conversion = src[src.index("conversion=HrConversion(") :]
    conversion = conversion[: conversion.index("),\n    )")]
    # f is the funnel row. No rate may be computed from it.
    assert 'f["' not in conversion


# ===========================================================================
# The denominator
# ===========================================================================
@pytest.mark.asyncio
async def test_more_exams_than_shortlists_still_yields_a_sane_percentage() -> None:
    """The regression, with the numbers that produced 175%.

    Seven people had sat an exam and four had ever been shortlisted, because HR
    can assign an exam by hand outside any workflow. Against the previous stage
    that is 175%; against applications it is 24%, which is both true and
    readable.
    """
    from app.routers.hr_pipeline import get_analytics

    analytics = await get_analytics(
        (uuid.uuid4(), uuid.uuid4()),
        _analytics_db(
            conversion={
                "applied": 29, "shortlisted": 4, "sat_exam": 7,
                "interviewed": 0, "hired": 0,
            }
        ),
    )
    c = analytics.conversion
    assert c.pct_shortlisted == 13.8
    assert c.pct_sat_exam == 24.1
    for pct in (c.pct_shortlisted, c.pct_sat_exam, c.pct_interviewed, c.pct_hired):
        assert pct is None or pct <= 100.0


@pytest.mark.asyncio
async def test_every_rate_is_measured_against_applications() -> None:
    """Each stage divided by the same denominator, whatever route people took."""
    from app.routers.hr_pipeline import get_analytics

    analytics = await get_analytics(
        (uuid.uuid4(), uuid.uuid4()),
        _analytics_db(
            conversion={
                "applied": 200, "shortlisted": 100, "sat_exam": 50,
                "interviewed": 20, "hired": 10,
            }
        ),
    )
    c = analytics.conversion
    assert (c.pct_shortlisted, c.pct_sat_exam, c.pct_interviewed, c.pct_hired) == (
        50.0, 25.0, 10.0, 5.0,
    )


@pytest.mark.asyncio
async def test_a_company_with_no_applications_reports_no_rates() -> None:
    from app.routers.hr_pipeline import get_analytics

    analytics = await get_analytics(
        (uuid.uuid4(), uuid.uuid4()),
        _analytics_db(
            conversion={
                "applied": 0, "shortlisted": 0, "sat_exam": 0,
                "interviewed": 0, "hired": 0,
            }
        ),
    )
    assert analytics.conversion.pct_shortlisted is None
    assert analytics.conversion.applied == 0


def test_the_field_names_say_what_the_denominator_is() -> None:
    """`shortlist_to_exam` invited exactly the misreading that produced 175%."""
    from app.routers.hr_pipeline import HrConversion

    fields = set(HrConversion.model_fields)
    assert {"pct_shortlisted", "pct_sat_exam", "pct_interviewed", "pct_hired"} <= fields
    assert not [f for f in fields if "_to_" in f]


def test_the_counts_ship_with_the_percentages() -> None:
    """"50%" out of two candidates and out of two hundred are different facts,
    and a percentage alone cannot tell them apart."""
    from app.routers.hr_pipeline import HrConversion

    fields = set(HrConversion.model_fields)
    assert {"applied", "ever_shortlisted", "ever_sat_exam", "ever_hired"} <= fields


# ===========================================================================
# Velocity
# ===========================================================================
def test_time_to_hire_is_a_median() -> None:
    """One candidate who sat in a pipeline for eight months drags a mean
    somewhere no real hire ever was, and this number is read as "how long
    should I expect this to take"."""
    from app.routers.hr_pipeline import _VELOCITY_SQL

    assert "PERCENTILE_CONT(0.5)" in str(_VELOCITY_SQL)


def test_time_to_hire_is_measured_from_the_ledger() -> None:
    """updated_at moves for reasons that have nothing to do with a stage."""
    from app.routers.hr_pipeline import _VELOCITY_SQL

    sql = str(_VELOCITY_SQL)
    assert "stage_transitions" in sql
    assert "updated_at" not in sql


def test_no_hires_yet_is_null_not_zero() -> None:
    """Zero days is a claim about speed. "Nobody has been hired" is not one."""
    from app.routers.hr_pipeline import HrVelocity

    assert HrVelocity().median_time_to_hire_days is None
    assert HrVelocity().hires_measured == 0


def test_the_hire_count_ships_with_the_median() -> None:
    """A median of one is a number, not a trend."""
    from app.routers.hr_pipeline import HrVelocity

    assert "hires_measured" in HrVelocity.model_fields


# ===========================================================================
# Openings
# ===========================================================================
def test_openings_are_counted_by_state() -> None:
    """A closed opening is not hiring, and one "openings" number that includes
    it answers a question nobody asked."""
    from app.routers.hr_pipeline import HrOpenings

    assert {"open", "paused", "closed"} == set(HrOpenings.model_fields)


def test_an_empty_company_gets_zeros_rather_than_an_error() -> None:
    from app.routers.hr_pipeline import HrAnalytics, HrAverages, HrFunnel

    empty = HrAnalytics(
        funnel=HrFunnel(
            total_applicants=0, shortlisted=0, exam_taken=0, exam_passed=0,
            interview_invited=0, interview_completed=0, hired=0, rejected=0,
        ),
        averages=HrAverages(
            avg_ats=None, avg_exam_percent=None, avg_interview_composite=None
        ),
    )
    assert empty.openings.open == 0
    assert empty.velocity.median_time_to_hire_days is None
    assert empty.conversion.pct_hired is None


@pytest.mark.parametrize(
    "sql_name", ["_OPENINGS_SQL", "_VELOCITY_SQL", "_RECENT_SQL", "_CONVERSION_SQL"]
)
def test_every_rollup_query_is_company_scoped(sql_name: str) -> None:
    """These are read by an HR manager, and there is exactly one tenant whose
    hiring they may see."""
    import app.routers.hr_pipeline as mod

    assert ":cid" in str(getattr(mod, sql_name))
