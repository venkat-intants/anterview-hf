"""The roll-up numbers, and the two mistakes that produced 175% and 400%.

Both were about meaning rather than arithmetic, and both looked fine in the
model until real data went through them:

  1. WRONG SOURCE. Conversion was derived from ``HrFunnel``, which counts
     applicants by CURRENT status — somebody shortlisted and then hired has
     left "shortlisted", so a ratio between two of its fields is not a
     conversion rate.
  2. WRONG DENOMINATOR. Even off the ledger, "% of the previous stage" assumes
     a chain, and this product does not enforce one: HR can assign an exam by
     hand outside any workflow, so more people can sit an exam than were ever
     shortlisted. Every rate is a share of APPLICATIONS now, which cannot
     exceed 100% and stays true whichever route someone took.

PH5-C2 moved the fix a second time: conversion and velocity's hire figures now
come from ``app.metrics.compute.compute_funnel`` (the governed metric layer)
rather than the hand-written ``_CONVERSION_SQL``/``_VELOCITY_SQL`` this file
used to assert on directly — both are gone from ``app.routers.hr_pipeline``.
What used to be tested here as "the ledger, not the snapshot" and "a share of
applications, not the stage before" is now enforced at import by
``app.metrics.definitions.validate_registry`` (a rate's numerator is a
structural superset of its denominator) and is covered directly against the
registry in ``tests/unit/test_ph5_w1_metrics_definitions.py`` and against real
Postgres in ``tests/integration/test_ph5_w1_metrics.py``. What is still this
router's own to get right — that it asks the metric layer for the right
metrics and puts the numbers in the right fields, under the new small-cell
suppression rule — is what this file tests now.

The rest is about not stating things the data does not support: no median
time-to-hire before anybody has been hired, and no percentage out of zero.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metrics.compute import CohortWindow, FunnelFilters, FunnelGroup, FunnelResult


def _analytics_db() -> AsyncMock:
    """A session that answers the FOUR raw roll-up queries `get_analytics`
    still runs itself, in order: funnel, averages, openings, recent. The
    governed conversion/velocity figures no longer come from `db.execute` —
    they come from `compute_funnel`, mocked separately per test."""
    zeros = dict.fromkeys(
        (
            "total_applicants", "total_applications", "shortlisted", "exam_taken",
            "exam_passed", "interview_invited", "interview_completed", "hired", "rejected",
        ),
        0,
    )
    queue = [
        _one(zeros),
        _one({"avg_ats": None, "avg_exam_percent": None, "avg_interview_composite": None}),
        _many([]),
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


def _count(name: str, value: int) -> dict:
    return {"metric": name, "version": 1, "kind": "count", "value": value}


def _rate(name: str, num: int, den: int) -> dict:
    suppressed = den < 5
    return {
        "metric": name, "version": 1, "kind": "rate",
        "numerator": num, "denominator": den,
        "value": None if suppressed else (round(100.0 * num / den, 1) if den else None),
        "suppressed": suppressed,
    }


def _median(name: str, value: float | None, n: int) -> dict:
    suppressed = n < 5
    return {
        "metric": name, "version": 1, "kind": "median",
        "value": None if suppressed else value, "n": n, "suppressed": suppressed,
    }


def _pipeline_result(*, applied: int, shortlisted: int, sat_exam: int, interviewed: int,
                      hired: int) -> FunnelResult:
    metrics = {
        "applications": _count("applications", applied),
        "screened": _count("screened", shortlisted),
        "assessed": _count("assessed", sat_exam),
        "interviewed": _count("interviewed", interviewed),
        "hires": _count("hires", hired),
        "application_to_screen": _rate("application_to_screen", shortlisted, applied),
        "application_to_assess": _rate("application_to_assess", sat_exam, applied),
        "application_to_interview": _rate("application_to_interview", interviewed, applied),
        "application_to_hire": _rate("application_to_hire", hired, applied),
    }
    return FunnelResult(
        registry_hash="test-hash", cohort=CohortWindow(basis="application"),
        filters=FunnelFilters(), group_by=None,
        groups=[FunnelGroup(key=None, label="All", in_progress=0, metrics=metrics)],
    )


def _hire_result(*, median_days: float | None = None, n: int = 0) -> FunnelResult:
    metrics = {"time_to_hire_days": _median("time_to_hire_days", median_days, n)}
    return FunnelResult(
        registry_hash="test-hash", cohort=CohortWindow(basis="hire"),
        filters=FunnelFilters(), group_by=None,
        groups=[FunnelGroup(key=None, label="All", in_progress=0, metrics=metrics)],
    )


def _patch_compute_funnel(monkeypatch: pytest.MonkeyPatch, pipeline: FunnelResult,
                           hire: FunnelResult) -> None:
    mock = AsyncMock(side_effect=[pipeline, hire])
    monkeypatch.setattr("app.routers.hr_pipeline.compute_funnel", mock)


# ===========================================================================
# The denominator, and small-cell suppression (PH5-C2)
# ===========================================================================
@pytest.mark.asyncio
async def test_more_exams_than_shortlists_still_yields_a_sane_percentage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression, with the numbers that produced 175%.

    Seven people had sat an exam and four had ever been shortlisted, because HR
    can assign an exam by hand outside any workflow. Against the previous stage
    that is 175%; against applications it is 24%, which is both true and
    readable.
    """
    from app.routers.hr_pipeline import get_analytics

    _patch_compute_funnel(
        monkeypatch,
        _pipeline_result(applied=29, shortlisted=4, sat_exam=7, interviewed=0, hired=0),
        _hire_result(),
    )
    analytics = await get_analytics((uuid.uuid4(), uuid.uuid4()), _analytics_db())
    c = analytics.conversion
    assert c.pct_shortlisted == 13.8
    assert c.pct_sat_exam == 24.1
    for pct in (c.pct_shortlisted, c.pct_sat_exam, c.pct_interviewed, c.pct_hired):
        assert pct is None or pct <= 100.0


@pytest.mark.asyncio
async def test_every_rate_is_measured_against_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each stage divided by the same denominator, whatever route people took."""
    from app.routers.hr_pipeline import get_analytics

    _patch_compute_funnel(
        monkeypatch,
        _pipeline_result(applied=200, shortlisted=100, sat_exam=50, interviewed=20, hired=10),
        _hire_result(),
    )
    analytics = await get_analytics((uuid.uuid4(), uuid.uuid4()), _analytics_db())
    c = analytics.conversion
    assert (c.pct_shortlisted, c.pct_sat_exam, c.pct_interviewed, c.pct_hired) == (
        50.0, 25.0, 10.0, 5.0,
    )


@pytest.mark.asyncio
async def test_a_company_with_no_applications_reports_no_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers.hr_pipeline import get_analytics

    _patch_compute_funnel(
        monkeypatch,
        _pipeline_result(applied=0, shortlisted=0, sat_exam=0, interviewed=0, hired=0),
        _hire_result(),
    )
    analytics = await get_analytics((uuid.uuid4(), uuid.uuid4()), _analytics_db())
    assert analytics.conversion.pct_shortlisted is None
    assert analytics.conversion.applied == 0


@pytest.mark.asyncio
async def test_a_small_cohort_is_suppressed_not_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """PH5-C2 (DPDP ruling): below 5, a rate's VALUE is null — not shown with a
    caveat, and not a raw percentage a small denominator makes noisy."""
    from app.routers.hr_pipeline import get_analytics

    _patch_compute_funnel(
        monkeypatch,
        _pipeline_result(applied=4, shortlisted=3, sat_exam=0, interviewed=0, hired=0),
        _hire_result(),
    )
    analytics = await get_analytics((uuid.uuid4(), uuid.uuid4()), _analytics_db())
    assert analytics.conversion.applied == 4
    assert analytics.conversion.pct_shortlisted is None  # suppressed, not 75.0


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


def test_the_response_names_the_metric_behind_every_field() -> None:
    """PH5-C2: `/hr/analytics` states, per field, which governed metric@version
    computed it — the "How is this calculated?" contract."""
    from app.routers.hr_pipeline import HrAnalytics

    fields = set(HrAnalytics.model_fields)
    assert {"definitions", "registry_hash"} <= fields


# ===========================================================================
# Velocity
# ===========================================================================
@pytest.mark.asyncio
async def test_time_to_hire_comes_from_the_hire_cohort_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PH5-C2: `time_to_hire_days@1`, over the `hire` cohort — not the ledger
    query this replaces, which counted a reversed hire the governed `hired`
    flag would exclude."""
    from app.routers.hr_pipeline import get_analytics

    _patch_compute_funnel(
        monkeypatch,
        _pipeline_result(applied=10, shortlisted=5, sat_exam=5, interviewed=5, hired=5),
        _hire_result(median_days=12.3, n=5),
    )
    analytics = await get_analytics((uuid.uuid4(), uuid.uuid4()), _analytics_db())
    assert analytics.velocity.median_time_to_hire_days == 12.3
    assert analytics.velocity.hires_measured == 5


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


@pytest.mark.parametrize("sql_name", ["_OPENINGS_SQL", "_RECENT_SQL"])
def test_every_rollup_query_is_company_scoped(sql_name: str) -> None:
    """These are read by an HR manager, and there is exactly one tenant whose
    hiring they may see. (`_VELOCITY_SQL`/`_CONVERSION_SQL` are gone — the
    governed queries they were replaced by are scoped by `app.metrics.compute`,
    covered by tests/integration/test_ph5_w1_metrics.py's tenant-isolation
    tests instead, since that scoping is exercised against real Postgres.)"""
    import app.routers.hr_pipeline as mod

    assert ":cid" in str(getattr(mod, sql_name))
