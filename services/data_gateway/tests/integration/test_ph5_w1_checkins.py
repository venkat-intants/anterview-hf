"""PH5 Wave 1 / D5-2: the 90-day hire check-in guarantees that hold AT THE
DATABASE, plus the service-level rules layered on top of them.

Refused for a non-hired or reversed-offer application; one live row per
(enrolment, kind); supersede freezes the old row and can only ever point at
the SAME hire; DELETE is allowed (retention and erasure both rely on it,
including a whole correction chain in one statement); the iff rules; tenant
isolation (including a cross-tenant correct); the check-in window (day 80-180,
security review MEDIUM-1); the anonymised-applicant and pending-erasure
refusals; a requisition hard-delete cascades through; ``due()`` prefers the
accepted offer's ``start_date`` and excludes erased applicants; a double
submit is a 409, not a 500; and the 24-month, whole-chain retention purge,
dry-run and live.

Against a real, migrated Postgres. Every DB-level refusal is checked for its
REASON.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.hire_checkins import (
    CheckinError,
    correct,
    due,
    list_for_enrolment,
    purge,
    record,
)
from app.hire_checkins import RequestMeta as Meta

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.other_company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.other_hr = uuid.uuid4()
        # One applicant per enrolment: uq_enrolments_requisition_applicant is
        # one live enrolment per (requisition, applicant), so many scenarios
        # under one opening need many different people.
        self.applicant = uuid.uuid4()
        self.applicant_rev = uuid.uuid4()
        self.applicant_nh = uuid.uuid4()
        self.applicant_recent = uuid.uuid4()
        self.applicant_noo = uuid.uuid4()
        self.applicant_oow = uuid.uuid4()
        self.applicant_anon = uuid.uuid4()
        self.applicant_pending_erasure = uuid.uuid4()
        self.applicant_chain = uuid.uuid4()
        self.other_applicant = uuid.uuid4()
        self.req = uuid.uuid4()
        self.other_req = uuid.uuid4()
        # A hire that stands, started ~113 days ago (via an accepted offer's
        # start_date), no offer_outcome reversal. Within the 80-180 window.
        self.hired = uuid.uuid4()
        # Hired, but the offer was later declined -- a reversed "hire".
        self.reversed_hire = uuid.uuid4()
        # Not hired at all.
        self.not_hired = uuid.uuid4()
        # Hired 10 days ago (via the ledger only, no offer row) -- too soon
        # for 'employed' to be recorded, but within the window for 'left'.
        self.hired_recent = uuid.uuid4()
        # Hired 100 days ago via the ledger only -- no offer row at all, so
        # due()/employment_start must fall back to the ledger's hire date.
        self.hired_no_offer = uuid.uuid4()
        # Hired 200 days ago -- past CHECKIN_WINDOW_MAX_DAYS (180).
        self.hired_out_of_window = uuid.uuid4()
        # Hired 100 days ago, but the applicant is already anonymised.
        self.hired_anon = uuid.uuid4()
        # Hired 100 days ago, applicant has a PENDING erasure_requests row.
        self.hired_pending_erasure = uuid.uuid4()
        # Dedicated to the whole-chain erasure-delete test (MEDIUM-3): its
        # applicant carries a user_id, like a real candidate account.
        self.user_chain = uuid.uuid4()
        self.hired_chain = uuid.uuid4()
        # Accepted, but the job has not started: start_date is 30 days out
        # (a notice period) -- security re-review T1-A.
        self.applicant_future = uuid.uuid4()
        self.hired_future_start = uuid.uuid4()
        self.other_hired = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    otag = f.other_company.hex[:10]
    now = datetime.now(tz=UTC)

    def _days_ago(n: int) -> datetime:
        return now - timedelta(days=n)

    start_113 = (now - timedelta(days=113)).date()
    start_future = (now + timedelta(days=30)).date()
    p: dict[str, Any] = {
        "c": f.company, "oc": f.other_company, "h": f.hr, "oh": f.other_hr,
        "a": f.applicant, "arev": f.applicant_rev, "anh": f.applicant_nh,
        "arecent": f.applicant_recent, "anoo": f.applicant_noo, "aoow": f.applicant_oow,
        "aanon": f.applicant_anon, "ape": f.applicant_pending_erasure,
        "achain": f.applicant_chain, "afuture": f.applicant_future,
        "oa": f.other_applicant, "r": f.req, "or_": f.other_req,
        "hired": f.hired, "rev": f.reversed_hire, "nh": f.not_hired,
        "recent": f.hired_recent, "noo": f.hired_no_offer, "oow": f.hired_out_of_window,
        "anon": f.hired_anon, "pe": f.hired_pending_erasure, "chain": f.hired_chain,
        "future": f.hired_future_start,
        "ohired": f.other_hired,
        "uchain": f.user_chain,
        "slug": f"d5-{tag}", "oslug": f"d5o-{otag}",
        "hm": f"hr-{tag}@d5.test", "ohm": f"hr-{otag}@d5.test",
        "t113": _days_ago(113), "t100": _days_ago(100), "t10": _days_ago(10),
        "t200": _days_ago(200), "now": now,
        "start113": start_113, "start_future": start_future,
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'D5 co', :slug),"
        " (:oc, 'D5 other', :oslug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :hm, :c), (:oh, :ohm, :oc)",
        # A candidate account, for the whole-chain erasure-delete test only.
        "INSERT INTO users (id, email) VALUES (:uchain, 'candidate-chain@d5.test')",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now()), (:or_, :oc, 'Engineer', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, email, target_job_title, user_id)"
        " VALUES (:a, :c, 'Asha', NULL, 'Engineer', NULL),"
        " (:arev, :c, 'Amit', NULL, 'Engineer', NULL),"
        " (:anh, :c, 'Chetan', NULL, 'Engineer', NULL),"
        " (:arecent, :c, 'Divya', NULL, 'Engineer', NULL),"
        " (:anoo, :c, 'Esha', NULL, 'Engineer', NULL),"
        " (:aoow, :c, 'Farah', NULL, 'Engineer', NULL),"
        " (:aanon, :c, '[redacted]', NULL, 'Engineer', NULL),"
        " (:ape, :c, 'Gita', NULL, 'Engineer', NULL),"
        " (:achain, :c, 'Hema', NULL, 'Engineer', :uchain),"
        " (:afuture, :c, 'Ira', NULL, 'Engineer', NULL),"
        " (:oa, :oc, 'Bea', NULL, 'Engineer', NULL)",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
        " offer_outcome, target_job_title, created_at, updated_at)"
        " VALUES"
        " (:hired, :c, :r, :a, 'hired', NULL, 'Engineer', :t113, :t113),"
        " (:rev, :c, :r, :arev, 'hired', 'offer_declined', 'Engineer', :t100, :t100),"
        " (:nh, :c, :r, :anh, 'new', NULL, 'Engineer', :now, :now),"
        " (:recent, :c, :r, :arecent, 'hired', NULL, 'Engineer', :t10, :t10),"
        " (:noo, :c, :r, :anoo, 'hired', NULL, 'Engineer', :t100, :t100),"
        " (:oow, :c, :r, :aoow, 'hired', NULL, 'Engineer', :t200, :t200),"
        " (:anon, :c, :r, :aanon, 'hired', NULL, 'Engineer', :t100, :t100),"
        " (:pe, :c, :r, :ape, 'hired', NULL, 'Engineer', :t100, :t100),"
        " (:chain, :c, :r, :achain, 'hired', NULL, 'Engineer', :t100, :t100),"
        " (:future, :c, :r, :afuture, 'hired', NULL, 'Engineer', :now, :now),"
        " (:ohired, :oc, :or_, :oa, 'hired', NULL, 'Engineer', :t100, :t100)",
        # The ledger's first move into 'hired' -- the due()/employment_start
        # fallback when there is no accepted offer. PH4-O4 requires a
        # reason_code/reason_label pair on any move into hired or rejected.
        "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
        " automated, occurred_at, reason_code, reason_label)"
        " VALUES (:c, :hired, 'new', 'hired', true, :t113, 'offer_accepted', 'Offer accepted'),"
        " (:c, :rev, 'new', 'hired', true, :t100, 'offer_accepted', 'Offer accepted'),"
        " (:c, :recent, 'new', 'hired', true, :t10, 'offer_accepted', 'Offer accepted'),"
        " (:c, :noo, 'new', 'hired', true, :t100, 'offer_accepted', 'Offer accepted'),"
        " (:c, :oow, 'new', 'hired', true, :t200, 'offer_accepted', 'Offer accepted'),"
        " (:c, :anon, 'new', 'hired', true, :t100, 'offer_accepted', 'Offer accepted'),"
        " (:c, :pe, 'new', 'hired', true, :t100, 'offer_accepted', 'Offer accepted'),"
        " (:c, :chain, 'new', 'hired', true, :t100, 'offer_accepted', 'Offer accepted'),"
        " (:c, :future, 'new', 'hired', true, :now, 'offer_accepted', 'Offer accepted'),"
        " (:oc, :ohired, 'new', 'hired', true, :t100, 'offer_accepted', 'Offer accepted')",
    ):
        await db.execute(text(sql), p)

    # applicant_pending_erasure needs its OWN user (not :uchain, which is
    # reused for the dedicated chain-delete test and must stay untouched by
    # an erasure request). Fixed up here rather than above the applicants
    # INSERT, which runs before any users row for it would exist otherwise.
    user_pending_erasure = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (:u, 'candidate-pe@d5.test')"),
        {"u": user_pending_erasure},
    )
    await db.execute(
        text("UPDATE applicants SET user_id = :u WHERE id = :ape"),
        {"u": user_pending_erasure, "ape": f.applicant_pending_erasure},
    )
    await db.execute(
        text(
            "INSERT INTO erasure_requests (user_id, requested_by, status, scheduled_for)"
            " VALUES (:u, :u, 'pending', now())"
        ),
        {"u": user_pending_erasure},
    )

    # An accepted offer for :hired, with an explicit start_date -- the
    # employment_start clock's preferred source. offers_lifecycle only lets an
    # offer arrive 'draft' and walk its normal state machine to get there, so
    # -- the test_ph4_wave4_guarantees.py precedent -- the trigger is lifted
    # for this one INSERT, inside this test's own (always rolled back)
    # transaction.
    await db.execute(text("ALTER TABLE offers DISABLE TRIGGER offers_lifecycle"))
    await db.execute(
        text(
            "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, status,"
            " job_title, base_salary, start_date)"
            " VALUES (gen_random_uuid(), :c, :hired, :a, 'accepted', 'Engineer', 100000,"
            " :start113)"
        ),
        p,
    )
    await db.execute(
        text(
            "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, status,"
            " job_title, base_salary, start_date)"
            " VALUES (gen_random_uuid(), :c, :future, :afuture, 'accepted', 'Engineer',"
            " 100000, :start_future)"
        ),
        p,
    )
    await db.execute(text("ALTER TABLE offers ENABLE TRIGGER offers_lifecycle"))
    return f


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(database_url=settings.database_url, database_ssl=settings.database_ssl,
                          pool_size=2)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def _refused(db: AsyncSession, sql: str, params: dict[str, Any], reason: str) -> None:
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert reason in message, f"refused for the WRONG reason — expected {reason!r}: {message[:300]}"
        return
    await sp.rollback()
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:160]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


_INSERT = (
    "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
    " left_reason, performance, recorded_by_user_id, recorded_at, created_at, updated_at)"
    " VALUES (:id, :c, :e, '90_day', :emp, :lr, :perf, :by, now(), now(), now())"
)


# ===========================================================================
# INSERT is refused unless the hire stands
# ===========================================================================
async def test_refused_for_a_non_hired_application(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.not_hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
        "needs a hire that stands",
    )


async def test_refused_for_a_reversed_hire(db: AsyncSession) -> None:
    """status='hired' but the offer was later declined -- not a hire to check in on."""
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.reversed_hire, "emp": "left",
         "lr": "involuntary", "perf": None, "by": f.hr},
        "needs a hire that stands",
    )


async def test_allowed_for_a_hire_that_stands(db: AsyncSession) -> None:
    f = await _build(db)
    await _allowed(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )


# ===========================================================================
# The iff rules and vocabulary, at the database
# ===========================================================================
async def test_left_requires_a_reason_at_the_db(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "left",
         "lr": None, "perf": None, "by": f.hr},
        "ck_hire_checkins_left_reason_iff",
    )


async def test_employed_requires_performance_at_the_db(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": None, "by": f.hr},
        "ck_hire_checkins_performance_iff",
    )


async def test_employed_refuses_a_left_reason_at_the_db(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": "voluntary", "perf": "meets", "by": f.hr},
        "ck_hire_checkins_left_reason_iff",
    )


async def test_bad_employment_value_at_the_db(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "quit",
         "lr": None, "perf": None, "by": f.hr},
        "ck_hire_checkins_employment",
    )


async def test_bad_left_reason_vocabulary_at_the_db(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "left",
         "lr": "got_bored", "perf": None, "by": f.hr},
        "ck_hire_checkins_left_reason_vocab",
    )


# ===========================================================================
# One live check-in per (enrolment, kind)
# ===========================================================================
async def test_one_live_check_in_per_enrolment(db: AsyncSession) -> None:
    f = await _build(db)
    await _allowed(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "left",
         "lr": "voluntary", "perf": None, "by": f.hr},
        "uq_hire_checkins_live",
    )


# ===========================================================================
# Supersede freezes the old row, can only point at the SAME hire; DELETE
# ===========================================================================
async def test_supersede_works_and_the_old_row_freezes(db: AsyncSession) -> None:
    f = await _build(db)
    original = uuid.uuid4()
    await _allowed(
        db, _INSERT,
        {"id": original, "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    successor = uuid.uuid4()
    # Bound, DISTINCT timestamps rather than SQL now() twice: now()/
    # CURRENT_TIMESTAMP is fixed for the whole transaction in Postgres, so a
    # second now() inside this same (nested-savepoint) transaction would equal
    # the first, and the trigger would see no change to compare at all.
    superseded_at = datetime.now(tz=UTC)
    later = superseded_at + timedelta(seconds=1)
    await _allowed(
        db,
        "UPDATE hire_checkins SET superseded_at = :t, updated_at = :t WHERE id = :id",
        {"id": original, "t": superseded_at},
    )
    await _allowed(
        db,
        "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
        " left_reason, performance, recorded_by_user_id, recorded_at, supersedes_id,"
        " created_at, updated_at)"
        " VALUES (:new, :c, :e, '90_day', 'employed', NULL, 'exceeds', :by, now(), :old,"
        " now(), now())",
        {"new": successor, "c": f.company, "e": f.hired, "by": f.hr, "old": original},
    )
    # The old row is frozen -- changing its employment value is refused.
    await _refused(
        db,
        "UPDATE hire_checkins SET employment = 'left', left_reason = 'voluntary',"
        " performance = NULL WHERE id = :id",
        {"id": original},
        "is fixed",
    )
    # Superseding it a second time is refused.
    await _refused(
        db,
        "UPDATE hire_checkins SET superseded_at = :t WHERE id = :id",
        {"id": original, "t": later},
        "already superseded",
    )


async def test_supersedes_id_must_name_the_same_enrolment(db: AsyncSession) -> None:
    """Security review MEDIUM-3: the original shape let ``supersedes_id`` name
    any row in the same company; the composite FK now refuses a cross-hire
    pointer at the database."""
    f = await _build(db)
    own = uuid.uuid4()
    await _allowed(
        db, _INSERT,
        {"id": own, "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    other_hire_checkin = uuid.uuid4()
    await _allowed(
        db, _INSERT,
        {"id": other_hire_checkin, "c": f.company, "e": f.hired_no_offer, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    await _allowed(
        db,
        "UPDATE hire_checkins SET superseded_at = now() WHERE id = :id",
        {"id": other_hire_checkin},
    )
    # own's enrolment is f.hired; other_hire_checkin's enrolment is
    # f.hired_no_offer. Pointing own's correction at a DIFFERENT hire's
    # superseded check-in must be refused. The trigger's own enrolment_id/
    # company_id match (added to give a readable message) fires before the
    # composite foreign key would even be checked; either way, the row is
    # refused, which is the property this test exists to pin.
    await _refused(
        db,
        "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
        " left_reason, performance, recorded_by_user_id, recorded_at, supersedes_id,"
        " created_at, updated_at)"
        " VALUES (:new, :c, :e, '90_day', 'left', 'voluntary', NULL, :by, now(), :old,"
        " now(), now())",
        {"new": uuid.uuid4(), "c": f.company, "e": f.hired, "by": f.hr, "old": other_hire_checkin},
        "must name an already-superseded",
    )


async def test_supersedes_id_must_name_an_already_superseded_row(db: AsyncSession) -> None:
    """The trigger's own check, beyond what the foreign key can express."""
    f = await _build(db)
    live = uuid.uuid4()
    await _allowed(
        db, _INSERT,
        {"id": live, "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    # live is NOT superseded yet -- pointing a new row at it is refused, even
    # though it belongs to the same hire and passes the foreign key.
    await _refused(
        db,
        "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
        " left_reason, performance, recorded_by_user_id, recorded_at, supersedes_id,"
        " created_at, updated_at)"
        " VALUES (:new, :c, :e, '90_day', 'left', 'voluntary', NULL, :by, now(), :old,"
        " now(), now())",
        {"new": uuid.uuid4(), "c": f.company, "e": f.hired, "by": f.hr, "old": live},
        "must name an already-superseded",
    )


async def test_each_row_has_at_most_one_successor(db: AsyncSession) -> None:
    """A second row cannot also claim to supersede the same original.

    ``uq_hire_checkins_supersedes`` exists for this and to give the composite
    foreign key's target side an index (migration ``8f41cb18a299``), but with
    only one ``kind`` in the vocabulary today, any row that would violate it
    is ALSO, unavoidably, a second live row for the same (enrolment, kind) —
    every inserted row arrives not-superseded (the trigger refuses
    otherwise), so a duplicate successor is a duplicate live row too. Which
    named constraint Postgres reports is therefore an implementation detail;
    what this test pins is that the second row is refused at all.
    """
    f = await _build(db)
    original = uuid.uuid4()
    await _allowed(
        db, _INSERT,
        {"id": original, "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    await _allowed(
        db, "UPDATE hire_checkins SET superseded_at = now() WHERE id = :id", {"id": original},
    )
    await _allowed(
        db,
        "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
        " left_reason, performance, recorded_by_user_id, recorded_at, supersedes_id,"
        " created_at, updated_at)"
        " VALUES (:new, :c, :e, '90_day', 'left', 'voluntary', NULL, :by, now(), :old,"
        " now(), now())",
        {"new": uuid.uuid4(), "c": f.company, "e": f.hired, "by": f.hr, "old": original},
    )
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(
                "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
                " left_reason, performance, recorded_by_user_id, recorded_at, supersedes_id,"
                " created_at, updated_at)"
                " VALUES (:new, :c, :e, '90_day', 'left', 'unknown', NULL, :by, now(), :old,"
                " now(), now())"
            ),
            {"new": uuid.uuid4(), "c": f.company, "e": f.hired, "by": f.hr, "old": original},
        )
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert (
            "uq_hire_checkins_supersedes" in message or "uq_hire_checkins_live" in message
        ), message[:300]
        return
    await sp.rollback()
    pytest.fail("a second row was allowed to claim the same supersedes_id")


async def test_delete_is_allowed(db: AsyncSession) -> None:
    """Retention and erasure both delete outright on purpose -- unlike
    interviewer_scorecards, this table's DELETE is never refused."""
    f = await _build(db)
    row_id = uuid.uuid4()
    await _allowed(
        db, _INSERT,
        {"id": row_id, "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    await _allowed(db, "DELETE FROM hire_checkins WHERE id = :id", {"id": row_id})


# ===========================================================================
# A requisition hard-delete cascades through
# ===========================================================================
async def test_requisition_hard_delete_cascades_through(db: AsyncSession) -> None:
    f = await _build(db)
    await _allowed(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    # A flat "refuse DELETE" trigger would make this fail with a foreign-key
    # violation; the trigger places no restriction on DELETE at all.
    await _allowed(db, "DELETE FROM job_requisitions WHERE id = :r", {"r": f.req})
    remaining = await db.scalar(
        text("SELECT count(*) FROM hire_checkins WHERE enrolment_id = :e"), {"e": f.hired}
    )
    assert remaining == 0


# ===========================================================================
# Tenant isolation (service level), including a cross-tenant correct (IDOR)
# ===========================================================================
async def test_tenant_isolation_on_record(db: AsyncSession) -> None:
    f = await _build(db)
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.other_company, enrolment_id=f.hired, actor=f.other_hr,
            employment="employed", left_reason=None, performance="meets", meta=Meta(),
        )
    assert exc.value.status_code == 404


async def test_tenant_isolation_on_list(db: AsyncSession) -> None:
    f = await _build(db)
    with pytest.raises(CheckinError) as exc:
        await list_for_enrolment(db, company_id=f.other_company, enrolment_id=f.hired)
    assert exc.value.status_code == 404


async def test_tenant_isolation_on_correct_is_not_found(db: AsyncSession) -> None:
    """A company cannot correct another company's check-in by guessing its id
    (IDOR) — security review MEDIUM-4."""
    f = await _build(db)
    out = await record(
        db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
        employment="employed", left_reason=None, performance="meets", meta=Meta(),
    )
    with pytest.raises(CheckinError) as exc:
        await correct(
            db, company_id=f.other_company, checkin_id=uuid.UUID(out["checkin_id"]),
            actor=f.other_hr, employment="left", left_reason="voluntary", performance=None,
            meta=Meta(),
        )
    assert exc.value.status_code == 404


# ===========================================================================
# Service-level: record / correct
# ===========================================================================
async def test_record_then_correct_end_to_end(db: AsyncSession) -> None:
    f = await _build(db)
    out = await record(
        db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
        employment="employed", left_reason=None, performance="meets", meta=Meta(),
    )
    assert out["employment"] == "employed"
    assert out["performance"] == "meets"
    assert out["superseded"] is False
    # f.hr has no full_name in this fixture -- recorded_by_name falls back to
    # email, the same COALESCE(full_name, email) shape as other HR screens.
    assert out["recorded_by_name"] == f"hr-{f.company.hex[:10]}@d5.test"

    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
            employment="left", left_reason="voluntary", performance=None, meta=Meta(),
        )
    assert exc.value.status_code == 409

    corrected = await correct(
        db, company_id=f.company, checkin_id=uuid.UUID(out["checkin_id"]), actor=f.hr,
        employment="left", left_reason="voluntary", performance=None, meta=Meta(),
    )
    assert corrected["employment"] == "left"
    assert corrected["supersedes_id"] == out["checkin_id"]

    result = await list_for_enrolment(db, company_id=f.company, enrolment_id=f.hired)
    assert len(result["checkins"]) == 2
    assert any(h["superseded"] for h in result["checkins"])
    assert result["window"]["open"] is True
    assert result["window"]["start"] is not None


async def test_record_refuses_a_second_live_check_in(db: AsyncSession) -> None:
    f = await _build(db)
    await record(
        db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
        employment="left", left_reason="voluntary", performance=None, meta=Meta(),
    )
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
            employment="left", left_reason="involuntary", performance=None, meta=Meta(),
        )
    assert exc.value.status_code == 409


async def test_record_refuses_a_declined_offer_hire(db: AsyncSession) -> None:
    f = await _build(db)
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.reversed_hire, actor=f.hr,
            employment="left", left_reason="involuntary", performance=None, meta=Meta(),
        )
    assert exc.value.status_code == 409
    assert "reversed" in exc.value.detail.lower()


async def test_employed_before_day_80_is_refused_left_is_accepted(db: AsyncSession) -> None:
    """f.hired_recent started 10 days ago (ledger only, no offer row)."""
    f = await _build(db)
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.hired_recent, actor=f.hr,
            employment="employed", left_reason=None, performance="meets", meta=Meta(),
        )
    assert exc.value.status_code == 409
    assert "day 80" in exc.value.detail

    # 'left' has no lower timing gate.
    out = await record(
        db, company_id=f.company, enrolment_id=f.hired_recent, actor=f.hr,
        employment="left", left_reason="involuntary", performance=None, meta=Meta(),
    )
    assert out["employment"] == "left"


# ===========================================================================
# The check-in window (security review MEDIUM-1)
# ===========================================================================
async def test_out_of_window_hire_is_refused_for_both_employment_values(
    db: AsyncSession,
) -> None:
    """f.hired_out_of_window started 200 days ago -- past day 180 for BOTH
    'employed' and 'left', which previously had no upper bound at all."""
    f = await _build(db)
    for employment, left_reason, performance in (
        ("left", "voluntary", None), ("employed", None, "meets"),
    ):
        with pytest.raises(CheckinError) as exc:
            await record(
                db, company_id=f.company, enrolment_id=f.hired_out_of_window, actor=f.hr,
                employment=employment, left_reason=left_reason, performance=performance,
                meta=Meta(),
            )
        assert exc.value.status_code == 409
        assert "window" in exc.value.detail.lower()


async def test_a_job_that_has_not_started_yet_is_refused_for_both_values(
    db: AsyncSession,
) -> None:
    """Security re-review T1-A: f.hired_future_start has an accepted offer
    whose start_date is 30 days out (a notice period). 'left' previously had
    no lower bound at all and would have been accepted against a job nobody
    has started."""
    f = await _build(db)
    for employment, left_reason, performance in (
        ("left", "voluntary", None), ("employed", None, "meets"),
    ):
        with pytest.raises(CheckinError) as exc:
            await record(
                db, company_id=f.company, enrolment_id=f.hired_future_start, actor=f.hr,
                employment=employment, left_reason=left_reason, performance=performance,
                meta=Meta(),
            )
        assert exc.value.status_code == 409
        assert "has not started yet" in exc.value.detail


async def test_out_of_window_hire_is_not_due(db: AsyncSession) -> None:
    f = await _build(db)
    items = await due(db, company_id=f.company)
    assert str(f.hired_out_of_window) not in {i["enrolment_id"] for i in items}


async def test_window_info_on_a_hire_with_no_start_date_evidence(db: AsyncSession) -> None:
    """f.not_hired has no offer and never reached the ledger's 'hired' status
    at all -- employment_start is None, and the window reads as closed."""
    f = await _build(db)
    result = await list_for_enrolment(db, company_id=f.company, enrolment_id=f.not_hired)
    assert result["window"] == {
        "start": None, "employed_from": None, "closes_at": None, "open": False,
    }


async def test_after_a_live_purge_the_hire_does_not_come_back_as_due(
    db: AsyncSession,
) -> None:
    """Before the window existed, deleting an old check-in let due() list the
    hire again and record() accept a fresh one at any age. f.hired_out_of_window
    (200 days in) already has no check-in and is excluded by the window alone;
    this additionally proves it for a hire whose ONLY check-in retention just
    removed."""
    f = await _build(db)
    old_checkin = uuid.uuid4()
    ancient = datetime.now(tz=UTC) - timedelta(days=800)
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at, created_at,"
            " updated_at) VALUES (:id, :c, :e, '90_day', 'left', 'voluntary', NULL, :by,"
            " :old, :old, :old)"
        ),
        {"id": old_checkin, "c": f.company, "e": f.hired_out_of_window, "by": f.hr, "old": ancient},
    )
    deleted = await purge(db, retention_days=730, dry_run=False)
    assert deleted >= 1
    assert await db.scalar(
        text("SELECT 1 FROM hire_checkins WHERE id = :id"), {"id": old_checkin}
    ) is None

    items = await due(db, company_id=f.company)
    assert str(f.hired_out_of_window) not in {i["enrolment_id"] for i in items}
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.hired_out_of_window, actor=f.hr,
            employment="left", left_reason="voluntary", performance=None, meta=Meta(),
        )
    assert exc.value.status_code == 409
    assert "window" in exc.value.detail.lower()


# ===========================================================================
# _candidate_erased (security review MEDIUM-4)
# ===========================================================================
async def test_record_refuses_an_anonymised_applicant(db: AsyncSession) -> None:
    f = await _build(db)
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.hired_anon, actor=f.hr,
            employment="left", left_reason="voluntary", performance=None, meta=Meta(),
        )
    assert exc.value.status_code == 409
    assert "erased" in exc.value.detail.lower()


async def test_record_refuses_a_pending_erasure_request(db: AsyncSession) -> None:
    f = await _build(db)
    with pytest.raises(CheckinError) as exc:
        await record(
            db, company_id=f.company, enrolment_id=f.hired_pending_erasure, actor=f.hr,
            employment="left", left_reason="voluntary", performance=None, meta=Meta(),
        )
    assert exc.value.status_code == 409
    assert "erased" in exc.value.detail.lower()


# ===========================================================================
# due() — the employment-start clock, and excludes erased applicants (LOW-7)
# ===========================================================================
async def test_due_uses_the_accepted_offers_start_date(db: AsyncSession) -> None:
    """f.hired has an accepted offer with start_date 113 days ago -- inside
    the 80-180 window (the ledger move agrees, but the offer's start_date is
    the preferred source, not just a fallback that happens to agree)."""
    f = await _build(db)
    items = await due(db, company_id=f.company)
    by_enrolment = {i["enrolment_id"]: i for i in items}
    assert str(f.hired) in by_enrolment
    # applicant_id: so the UI can open the shared CandidateDrawer from a due row.
    assert by_enrolment[str(f.hired)]["applicant_id"] == str(f.applicant)
    started = datetime.fromisoformat(by_enrolment[str(f.hired)]["employment_start"])
    expected = datetime.now(tz=UTC) - timedelta(days=113)
    assert abs((started - expected).total_seconds()) < 24 * 3600


async def test_due_falls_back_to_the_ledger_hire_date(db: AsyncSession) -> None:
    """f.hired_no_offer has no offer row at all -- employment_start (and
    due()'s reading of it) must fall back to the ledger's first move into
    'hired', 100 days ago in this fixture."""
    f = await _build(db)
    items = await due(db, company_id=f.company)
    by_enrolment = {i["enrolment_id"]: i for i in items}
    assert str(f.hired_no_offer) in by_enrolment
    started = datetime.fromisoformat(by_enrolment[str(f.hired_no_offer)]["employment_start"])
    expected = datetime.now(tz=UTC) - timedelta(days=100)
    assert abs((started - expected).total_seconds()) < 24 * 3600


async def test_due_excludes_a_reversed_hire_and_a_non_hire(db: AsyncSession) -> None:
    f = await _build(db)
    items = await due(db, company_id=f.company)
    ids = {i["enrolment_id"] for i in items}
    assert str(f.reversed_hire) not in ids
    assert str(f.not_hired) not in ids


async def test_due_excludes_a_hire_with_a_live_check_in(db: AsyncSession) -> None:
    f = await _build(db)
    await record(
        db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
        employment="employed", left_reason=None, performance="meets", meta=Meta(),
    )
    items = await due(db, company_id=f.company)
    assert str(f.hired) not in {i["enrolment_id"] for i in items}


async def test_due_is_company_scoped(db: AsyncSession) -> None:
    f = await _build(db)
    items = await due(db, company_id=f.company)
    assert str(f.other_hired) not in {i["enrolment_id"] for i in items}


async def test_due_excludes_an_anonymised_applicant(db: AsyncSession) -> None:
    f = await _build(db)
    items = await due(db, company_id=f.company)
    assert str(f.hired_anon) not in {i["enrolment_id"] for i in items}


async def test_due_excludes_a_pending_erasure_request(db: AsyncSession) -> None:
    f = await _build(db)
    items = await due(db, company_id=f.company)
    assert str(f.hired_pending_erasure) not in {i["enrolment_id"] for i in items}


# ===========================================================================
# LOW-1: a double submit is a 409, not a 500
# ===========================================================================
async def test_double_submit_at_the_db_gives_a_named_constraint(db: AsyncSession) -> None:
    """The unit-level test (test_ph5_w1_checkins.py) mocks the session to
    prove record()/correct() translate this into CheckinError(409, ...); this
    proves the constraint they catch by name really exists and really fires
    on a genuine double-insert."""
    f = await _build(db)
    await _allowed(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
    )
    await _refused(
        db, _INSERT,
        {"id": uuid.uuid4(), "c": f.company, "e": f.hired, "emp": "employed",
         "lr": None, "perf": "meets", "by": f.hr},
        "uq_hire_checkins_live",
    )


# ===========================================================================
# Audit content (security review MEDIUM-4): never a recorded value
# ===========================================================================
async def test_audit_rows_never_carry_a_recorded_value(db: AsyncSession) -> None:
    import json

    f = await _build(db)
    out = await record(
        db, company_id=f.company, enrolment_id=f.hired, actor=f.hr,
        employment="employed", left_reason=None, performance="exceeds", meta=Meta(),
    )
    await correct(
        db, company_id=f.company, checkin_id=uuid.UUID(out["checkin_id"]), actor=f.hr,
        employment="left", left_reason="involuntary", performance=None, meta=Meta(),
    )
    await db.flush()

    rows = (
        await db.execute(
            text(
                "SELECT action, details FROM audit_log"
                " WHERE resource_type = 'hire_checkin'"
                "   AND details->>'enrolment_id' = :e"
                " ORDER BY event_ts"
            ),
            {"e": str(f.hired)},
        )
    ).all()
    assert len(rows) == 2
    allowed_keys = {"enrolment_id", "kind", "corrects"}
    forbidden_values = {"employed", "left", "exceeds", "involuntary", "meets",
                        "below", "voluntary", "unknown"}
    for action, details in rows:
        assert action in {"checkin.recorded", "checkin.corrected"}
        assert set(details.keys()) <= allowed_keys, details
        blob = json.dumps(details)
        for value in forbidden_values:
            assert value not in blob, (action, details, value)


# ===========================================================================
# MEDIUM-3: the erasure executor's exact step 5j DELETE, on a correction chain
# ===========================================================================
def _erasure_step_5j_sql() -> str:
    """Extracted (not retyped) from the real source via ``ast``, so this
    cannot silently drift from what step 5j actually runs — the same
    precedent as the migration/service HIRE_STANDS_SQL cross-checks."""
    path = REPO_ROOT / "services" / "admin_ops" / "app" / "erasure_executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("DELETE FROM hire_checkins")
        ):
            return node.args[0].value
    raise AssertionError("step 5j's DELETE FROM hire_checkins was not found")


async def test_erasure_step_5j_deletes_a_whole_correction_chain(db: AsyncSession) -> None:
    """Before MEDIUM-3, a correction could point at another enrolment's row;
    if it had, this exact DELETE would fail forever with a foreign-key
    violation the moment it reached one enrolment's rows while the other's
    still referenced them. Runs the real step 5j SQL, not a re-typed copy."""
    f = await _build(db)
    out = await record(
        db, company_id=f.company, enrolment_id=f.hired_chain, actor=f.hr,
        employment="employed", left_reason=None, performance="meets", meta=Meta(),
    )
    await correct(
        db, company_id=f.company, checkin_id=uuid.UUID(out["checkin_id"]), actor=f.hr,
        employment="left", left_reason="voluntary", performance=None, meta=Meta(),
    )
    await db.flush()

    before = await db.scalar(
        text("SELECT count(*) FROM hire_checkins WHERE enrolment_id = :e"),
        {"e": f.hired_chain},
    )
    assert before == 2

    await db.execute(text(_erasure_step_5j_sql()), {"uid": f.user_chain})

    after = await db.scalar(
        text("SELECT count(*) FROM hire_checkins WHERE enrolment_id = :e"),
        {"e": f.hired_chain},
    )
    assert after == 0


# ===========================================================================
# Retention — whole-chain purge, dry-run and live (security review LOW-3)
# ===========================================================================
async def test_purge_dry_run_counts_without_deleting(db: AsyncSession) -> None:
    f = await _build(db)
    checkin_id = uuid.uuid4()
    old = datetime.now(tz=UTC) - timedelta(days=800)
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at, created_at,"
            " updated_at) VALUES (:id, :c, :e, '90_day', 'left', 'voluntary', NULL, :by,"
            " :old, :old, :old)"
        ),
        {"id": checkin_id, "c": f.company, "e": f.hired, "by": f.hr, "old": old},
    )
    counted = await purge(db, retention_days=730, dry_run=True)
    assert counted >= 1
    still_there = await db.scalar(
        text("SELECT 1 FROM hire_checkins WHERE id = :id"), {"id": checkin_id}
    )
    assert still_there == 1


async def test_purge_live_run_deletes_expired_rows(db: AsyncSession) -> None:
    f = await _build(db)
    checkin_id = uuid.uuid4()
    old = datetime.now(tz=UTC) - timedelta(days=800)
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at, created_at,"
            " updated_at) VALUES (:id, :c, :e, '90_day', 'left', 'voluntary', NULL, :by,"
            " :old, :old, :old)"
        ),
        {"id": checkin_id, "c": f.company, "e": f.hired, "by": f.hr, "old": old},
    )
    fresh_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at, created_at,"
            " updated_at) VALUES (:id, :c, :e, '90_day', 'employed', NULL, 'meets', :by,"
            " now(), now(), now())"
        ),
        {"id": fresh_id, "c": f.company, "e": f.hired_recent, "by": f.hr},
    )
    deleted = await purge(db, retention_days=730, dry_run=False)
    assert deleted >= 1
    assert await db.scalar(text("SELECT 1 FROM hire_checkins WHERE id = :id"), {"id": checkin_id}) is None
    assert await db.scalar(text("SELECT 1 FROM hire_checkins WHERE id = :id"), {"id": fresh_id}) == 1


async def test_purge_deletes_a_whole_chain_when_the_original_is_old_enough(
    db: AsyncSession,
) -> None:
    """Security review LOW-3: an 800-day-old original with a FRESH correction
    must still expire together, in one statement, keyed on the chain's own
    earliest recorded_at -- not skipped because the correction itself is
    recent. The dry-run count and the live delete count must agree."""
    f = await _build(db)
    original = uuid.uuid4()
    ancient = datetime.now(tz=UTC) - timedelta(days=800)
    # The trigger refuses an INSERT that arrives already superseded ("a hire
    # check-in arrives not superseded"), so the row is inserted live, at the
    # ancient recorded_at, and THEN superseded in a separate UPDATE.
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at,"
            " created_at, updated_at) VALUES (:id, :c, :e, '90_day', 'left', 'voluntary',"
            " NULL, :by, :old, :old, :old)"
        ),
        {"id": original, "c": f.company, "e": f.hired, "by": f.hr, "old": ancient},
    )
    await db.execute(
        text("UPDATE hire_checkins SET superseded_at = :old WHERE id = :id"),
        {"id": original, "old": ancient},
    )
    correction = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind, employment,"
            " left_reason, performance, recorded_by_user_id, recorded_at, supersedes_id,"
            " created_at, updated_at) VALUES (:id, :c, :e, '90_day', 'employed', NULL,"
            " 'meets', :by, now(), :old, now(), now())"
        ),
        {"id": correction, "c": f.company, "e": f.hired, "by": f.hr, "old": original},
    )

    dry_count = await purge(db, retention_days=730, dry_run=True)
    deleted = await purge(db, retention_days=730, dry_run=False)
    assert dry_count == deleted
    assert deleted >= 2

    assert await db.scalar(text("SELECT 1 FROM hire_checkins WHERE id = :id"), {"id": original}) is None
    assert await db.scalar(text("SELECT 1 FROM hire_checkins WHERE id = :id"), {"id": correction}) is None
