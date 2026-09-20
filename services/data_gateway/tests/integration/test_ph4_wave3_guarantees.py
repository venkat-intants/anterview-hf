"""PH4 Wave 3: the scheduling guarantees that hold AT THE DATABASE.

No interviewer is ever in two live sessions at once, across every candidate and
loop; no candidate has two scheduled sessions closer than their loop's buffer;
availability windows do not overlap; the interviewer rows follow their session
(time and live-ness) by trigger, so a cancelled session frees its panel; and a
session's three instants always agree with its duration.

Against a real, migrated Postgres (CI's service; locally any loopback database
— the conftest refuses a remote one). Every refusal is checked for its REASON.
"""

from __future__ import annotations

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

pytestmark = pytest.mark.integration

T0 = datetime.now(tz=UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.iv1, self.iv2 = uuid.uuid4(), uuid.uuid4()
        self.req, self.wf, self.round = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.a1, self.a2 = uuid.uuid4(), uuid.uuid4()
        self.e1, self.e2 = uuid.uuid4(), uuid.uuid4()
        self.l1, self.l2 = uuid.uuid4(), uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "i1": f.iv1, "i2": f.iv2, "r": f.req, "w": f.wf, "rd": f.round,
        "a1": f.a1, "a2": f.a2, "e1": f.e1, "e2": f.e2, "l1": f.l1, "l2": f.l2,
        "slug": f"w3-{tag}", "m1": f"iv1-{tag}@w3.test", "m2": f"iv2-{tag}@w3.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'W3 co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:i1, :m1, :c), (:i2, :m2, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status, created_at,"
        " updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at) VALUES (:rd, :c, :w, 0, 'Panel', 'human_review', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a1, :c, 'Asha', 'Engineer'), (:a2, :c, 'Ravi', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " workflow_id, created_at, updated_at) VALUES"
        " (:e1, :c, :r, :a1, 'Engineer', :w, now(), now()),"
        " (:e2, :c, :r, :a2, 'Engineer', :w, now(), now())",
        "INSERT INTO interview_loops (id, company_id, enrolment_id, applicant_id, title,"
        " buffer_minutes) VALUES (:l1, :c, :e1, :a1, 'Asha loop', 15),"
        " (:l2, :c, :e2, :a2, 'Ravi loop', 15)",
    ):
        await db.execute(text(sql), p)
    return f


async def _session(
    db: AsyncSession, f: F, *, loop: str = "l1", start: float | None = 0, minutes: int = 60,
    buffer: int = 15, status: str = "scheduled",
) -> uuid.UUID:
    sid = uuid.uuid4()
    lp = {"l1": (f.l1, f.e1, f.a1), "l2": (f.l2, f.e2, f.a2)}[loop]
    s = at(start) if start is not None else None
    e = s + timedelta(minutes=minutes) if s else None
    b = e + timedelta(minutes=buffer) if e else None
    await db.execute(
        text(
            "INSERT INTO interview_sessions (id, company_id, loop_id, enrolment_id, applicant_id,"
            " round_id, title, duration_minutes, starts_at, ends_at, blocked_until, status)"
            " VALUES (:i, :c, :l, :e, :a, :r, 'Panel', :d, :s, :en, :b, :st)"
        ),
        {"i": sid, "c": f.company, "l": lp[0], "e": lp[1], "a": lp[2], "r": f.round,
         "d": minutes, "s": s, "en": e, "b": b, "st": status},
    )
    return sid


async def _sit(db: AsyncSession, f: F, session: uuid.UUID, who: uuid.UUID) -> None:
    await db.execute(
        text("INSERT INTO interview_session_interviewers (session_id, company_id,"
             " interviewer_user_id) VALUES (:s, :c, :u)"),
        {"s": session, "c": f.company, "u": who},
    )


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2
    )
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


async def _refused(db: AsyncSession, coro: Any, reason: str) -> None:
    sp = await db.begin_nested()
    try:
        await coro
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert reason in message, f"refused for the WRONG reason — expected {reason!r}: {message[:240]}"
        return
    await sp.rollback()
    pytest.fail(f"was allowed, expected refusal ({reason})")


async def _allowed(db: AsyncSession, coro: Any) -> Any:
    sp = await db.begin_nested()
    out = await coro
    await sp.commit()
    return out


# ===========================================================================
# Interviewers are never double-booked
# ===========================================================================
@pytest.mark.asyncio
async def test_an_interviewer_cannot_sit_two_overlapping_sessions_even_for_different_candidates(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    s1 = await _session(db, f, loop="l1", start=0)
    await _sit(db, f, s1, f.iv1)
    s2 = await _session(db, f, loop="l2", start=0.5)
    await _refused(db, _sit(db, f, s2, f.iv1), "ex_session_interviewers_overlap")
    await _allowed(db, _sit(db, f, s2, f.iv2))  # somebody else is free


@pytest.mark.asyncio
async def test_back_to_back_is_not_a_conflict(db: AsyncSession) -> None:
    f = await _build(db)
    s1 = await _session(db, f, loop="l1", start=0)
    await _sit(db, f, s1, f.iv1)
    s2 = await _session(db, f, loop="l2", start=1)  # starts as the other ends
    await _allowed(db, _sit(db, f, s2, f.iv1))


@pytest.mark.asyncio
async def test_moving_a_session_onto_its_interviewer_s_other_session_is_refused(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    s1 = await _session(db, f, loop="l1", start=0)
    await _sit(db, f, s1, f.iv1)
    s2 = await _session(db, f, loop="l2", start=5)
    await _sit(db, f, s2, f.iv1)
    new_s, new_e = at(0.25), at(1.25)
    await _refused(
        db,
        db.execute(text("UPDATE interview_sessions SET starts_at = :s, ends_at = :e,"
                        " blocked_until = :b WHERE id = :i"),
                   {"s": new_s, "e": new_e, "b": new_e + timedelta(minutes=15), "i": s2}),
        "ex_session_interviewers_overlap",
    )


@pytest.mark.asyncio
async def test_a_cancelled_session_frees_its_panel(db: AsyncSession) -> None:
    f = await _build(db)
    s1 = await _session(db, f, loop="l1", start=0)
    await _sit(db, f, s1, f.iv1)
    await db.execute(text("UPDATE interview_sessions SET status = 'cancelled', cancelled_at = now()"
                          " WHERE id = :i"), {"i": s1})
    live = await db.scalar(text("SELECT live FROM interview_session_interviewers"
                                " WHERE session_id = :s"), {"s": s1})
    assert live is False
    s2 = await _session(db, f, loop="l2", start=0)
    await _allowed(db, _sit(db, f, s2, f.iv1))


@pytest.mark.asyncio
async def test_interviewer_rows_take_their_times_from_the_session_not_the_writer(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    s1 = await _session(db, f, start=2)
    await db.execute(
        text("INSERT INTO interview_session_interviewers (session_id, company_id,"
             " interviewer_user_id, starts_at, ends_at, live)"
             " VALUES (:s, :c, :u, :x, :x, false)"),
        {"s": s1, "c": f.company, "u": f.iv1, "x": at(-100)},
    )
    row = (await db.execute(text("SELECT starts_at, ends_at, live FROM"
                                 " interview_session_interviewers WHERE session_id = :s"),
                            {"s": s1})).one()
    assert (row[0], row[1], row[2]) == (at(2), at(3), True)


@pytest.mark.asyncio
async def test_an_interviewer_row_cannot_belong_to_another_company(db: AsyncSession) -> None:
    f = await _build(db)
    s1 = await _session(db, f, start=0)
    other = uuid.uuid4()
    await db.execute(text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Other', :s)"),
                     {"c": other, "s": f"w3o-{other.hex[:10]}"})
    await _refused(
        db,
        db.execute(text("INSERT INTO interview_session_interviewers (session_id, company_id,"
                        " interviewer_user_id) VALUES (:s, :c, :u)"),
                   {"s": s1, "c": other, "u": f.iv1}),
        # The sync trigger refuses it before the composite foreign key would.
        "belongs to another company",
    )


# ===========================================================================
# A candidate's sessions keep their gap
# ===========================================================================
@pytest.mark.asyncio
async def test_a_candidate_cannot_have_two_sessions_inside_the_buffer(db: AsyncSession) -> None:
    f = await _build(db)
    await _session(db, f, start=0, buffer=15)  # 0:00–1:00, blocked to 1:15
    await _refused(db, _session(db, f, start=1, buffer=15), "ex_interview_sessions_candidate_overlap")
    await _allowed(db, _session(db, f, start=1.25, buffer=15))  # after the gap: fine


@pytest.mark.asyncio
async def test_the_same_day_holds_several_sessions(db: AsyncSession) -> None:
    f = await _build(db)
    for start in (0, 1.5, 3, 4.5):
        await _allowed(db, _session(db, f, start=start, minutes=60, buffer=15))
    n = await db.scalar(text("SELECT count(*) FROM interview_sessions WHERE applicant_id = :a"
                             " AND status = 'scheduled'"), {"a": f.a1})
    assert n == 4


@pytest.mark.asyncio
async def test_different_candidates_may_share_a_time(db: AsyncSession) -> None:
    f = await _build(db)
    await _session(db, f, loop="l1", start=0)
    await _allowed(db, _session(db, f, loop="l2", start=0))


@pytest.mark.asyncio
async def test_a_session_awaiting_a_slot_blocks_nothing(db: AsyncSession) -> None:
    f = await _build(db)
    await _session(db, f, start=None, status="awaiting_slot")
    await _allowed(db, _session(db, f, start=0))


# ===========================================================================
# Shapes
# ===========================================================================
@pytest.mark.asyncio
async def test_a_session_s_instants_agree_with_its_duration(db: AsyncSession) -> None:
    f = await _build(db)
    base = {"c": f.company, "l": f.l1, "e": f.e1, "a": f.a1, "r": f.round}
    sql = ("INSERT INTO interview_sessions (id, company_id, loop_id, enrolment_id, applicant_id,"
           " round_id, title, duration_minutes, starts_at, ends_at, blocked_until, status)"
           " VALUES (gen_random_uuid(), :c, :l, :e, :a, :r, 'x', :d, :s, :en, :b, :st)")
    await _refused(db, db.execute(text(sql), {**base, "d": 60, "s": at(0), "en": at(2),
                                              "b": at(2), "st": "scheduled"}),
                   "ck_interview_sessions_time_shape")
    await _refused(db, db.execute(text(sql), {**base, "d": 60, "s": None, "en": None, "b": None,
                                              "st": "scheduled"}),
                   "ck_interview_sessions_time_present")
    await _refused(db, db.execute(text(sql), {**base, "d": 60, "s": at(0), "en": at(1),
                                              "b": at(6), "st": "scheduled"}),
                   "ck_interview_sessions_time_shape")  # a buffer beyond 4 hours


# ===========================================================================
# Availability
# ===========================================================================
@pytest.mark.asyncio
async def test_availability_windows_do_not_overlap_until_one_is_removed(db: AsyncSession) -> None:
    f = await _build(db)
    ins = ("INSERT INTO interviewer_availability (id, company_id, user_id, starts_at, ends_at)"
           " VALUES (:i, :c, :u, :s, :e)")
    w1 = uuid.uuid4()
    await db.execute(text(ins), {"i": w1, "c": f.company, "u": f.iv1, "s": at(0), "e": at(4)})
    await _refused(db, db.execute(text(ins), {"i": uuid.uuid4(), "c": f.company, "u": f.iv1,
                                              "s": at(3), "e": at(6)}),
                   "ex_interviewer_availability_overlap")
    await db.execute(text("UPDATE interviewer_availability SET deleted_at = now() WHERE id = :i"),
                     {"i": w1})
    await _allowed(db, db.execute(text(ins), {"i": uuid.uuid4(), "c": f.company, "u": f.iv1,
                                              "s": at(3), "e": at(6)}))


@pytest.mark.asyncio
async def test_capacity_limits_are_sane(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        db.execute(text("INSERT INTO interviewer_capacity (user_id, company_id,"
                        " max_sessions_per_day) VALUES (:u, :c, 0)"),
                   {"u": f.iv1, "c": f.company}),
        "ck_interviewer_capacity_day",
    )


@pytest.mark.asyncio
async def test_a_session_cancelled_while_awaiting_its_slot_needs_no_time(db: AsyncSession) -> None:
    """A self-schedule session never booked can still be cancelled."""
    f = await _build(db)
    sid = await _session(db, f, start=None, status="awaiting_slot")
    await _allowed(db, db.execute(text("UPDATE interview_sessions SET status = 'cancelled',"
                                       " cancelled_at = now() WHERE id = :i"), {"i": sid}))
    await _refused(
        db,
        db.execute(text("UPDATE interview_sessions SET status = 'completed', cancelled_at = NULL"
                        " WHERE id = :i"), {"i": sid}),
        "ck_interview_sessions_time_present",
    )


@pytest.mark.asyncio
async def test_only_the_company_s_own_staff_can_sit_on_a_session(db: AsyncSession) -> None:
    f = await _build(db)
    stranger = uuid.uuid4()
    await db.execute(text("INSERT INTO users (id, email) VALUES (:u, :e)"),
                     {"u": stranger, "e": f"stranger-{stranger.hex[:10]}@w3.test"})
    s1 = await _session(db, f, start=0)
    await _refused(db, _sit(db, f, s1, stranger), "is not staff of this company")
