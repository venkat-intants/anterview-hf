"""The candidate's offers and interview loops name the application they belong to.

``application_id`` is the row's ``enrolment_id`` - the same id
``/users/me/applications`` returns as ``id`` - so a client can attach each offer
and loop to the right application card. It is added only to the signed-in
payloads; the unauthenticated token page uses ``candidate_out`` and must not
gain it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


def _db_returning(rows: list[dict]) -> AsyncMock:
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _offer_row(**kw: object) -> dict:
    row: dict = {
        "id": uuid.uuid4(), "enrolment_id": uuid.uuid4(), "status": "sent",
        "company_name": "Acme", "job_title": "Engineer", "employment_type": "full_time",
        "start_date": None, "location": "Pune", "base_salary": Decimal("1"),
        "currency": "INR", "pay_period": "annual", "bonus": None, "equity": None,
        "benefits": None, "terms": None, "probation_months": 6, "notice_period_days": 30,
        "expires_at": datetime(2026, 10, 1, tzinfo=UTC), "responded_at": None,
        "preboarding_completed_at": None,
    }
    row.update(kw)
    return row


async def test_candidate_offers_carry_the_application_id() -> None:
    from app.offers import candidate_offers

    eid = uuid.uuid4()
    out = await candidate_offers(_db_returning([_offer_row(enrolment_id=eid)]),
                                 user_id=uuid.uuid4())
    assert out[0]["application_id"] == str(eid)


async def test_candidate_offer_without_an_enrolment_has_a_null_application_id() -> None:
    from app.offers import candidate_offers

    out = await candidate_offers(_db_returning([_offer_row(enrolment_id=None)]),
                                 user_id=uuid.uuid4())
    assert out[0]["application_id"] is None


async def test_candidate_offers_are_scoped_to_the_caller() -> None:
    from app.offers import candidate_offers

    uid = uuid.uuid4()
    db = _db_returning([])
    assert await candidate_offers(db, user_id=uid) == []
    sql = str(db.execute.await_args.args[0])
    assert "a.user_id = :u" in sql
    assert db.execute.await_args.args[1] == {"u": uid}


def test_the_shared_candidate_view_does_not_gain_the_field() -> None:
    """``candidate_out`` also serves the unauthenticated token page."""
    from app.offers import candidate_out

    out = candidate_out(_offer_row(), "Acme")
    assert "application_id" not in out
    assert "enrolment_id" not in out


async def test_candidate_loops_carry_the_application_id() -> None:
    import app.interview_scheduling as sched

    eid = uuid.uuid4()
    loop = {
        "id": uuid.uuid4(), "title": "Onsite", "job_title": "Engineer", "status": "sent",
        "candidate_timezone": "Asia/Kolkata", "self_schedule": False, "enrolment_id": eid,
    }
    db = _db_returning([loop, {**loop, "id": uuid.uuid4(), "enrolment_id": None}])
    orig = sched._sessions
    sched._sessions = AsyncMock(return_value=[])  # type: ignore[assignment]
    try:
        out = await sched.candidate_loops(db, user_id=uuid.uuid4())
    finally:
        sched._sessions = orig  # type: ignore[assignment]
    assert out[0]["application_id"] == str(eid)
    assert out[1]["application_id"] is None


def test_candidate_loops_query_is_scoped_and_selects_the_enrolment() -> None:
    from app.interview_scheduling import _CANDIDATE_LOOPS_SQL

    assert "a.user_id = :u" in _CANDIDATE_LOOPS_SQL
    assert "SELECT l.*" in _CANDIDATE_LOOPS_SQL  # includes l.enrolment_id


@pytest.mark.parametrize("module", ["app.routers.offers", "app.routers.interview_scheduling"])
def test_the_candidate_routes_still_take_no_identity_parameter(module: str) -> None:
    import importlib
    import inspect

    mod = importlib.import_module(module)
    fn = mod.my_offers if module.endswith("offers") else mod.me_loops
    params = set(inspect.signature(fn).parameters)
    assert not params & {"user_id", "applicant_id", "company_id", "enrolment_id"}
