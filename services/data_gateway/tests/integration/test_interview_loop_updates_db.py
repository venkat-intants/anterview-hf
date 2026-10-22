"""Human interview loops: a change after sending reaches the candidate (IV-M8),
and an empty calendar file is refused (IV-L8).

Against a real, migrated Postgres, calling the scheduling service the routers
call. The candidate is told through the email outbox (``email_events``), so the
assertions count outbox rows for the loop rather than trusting a mock.
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
from sqlalchemy.ext.asyncio import AsyncSession

from app import interview_scheduling as sched
from app.config import settings
from app.interviewer_scorecards import RequestMeta

pytestmark = pytest.mark.integration

META = RequestMeta()
T0 = datetime.now(tz=UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def seed(db: AsyncSession) -> AsyncIterator[dict[str, uuid.UUID]]:
    ids = {k: uuid.uuid4() for k in ("c", "hr", "iv", "r", "w", "rd", "a", "e")}
    tag = ids["c"].hex[:10]
    p: dict[str, Any] = {**ids, "slug": f"loopupd-{tag}", "mhr": f"hr-{tag}@lu.test",
                         "miv": f"iv-{tag}@lu.test", "ma": f"asha-{tag}@lu.test"}
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'Loop co', :slug)",
        "INSERT INTO users (id, email, full_name, company_id) VALUES"
        " (:hr, :mhr, 'Hema HR', :c), (:iv, :miv, 'Ivan Interviewer', :c)",
        "INSERT INTO user_roles (user_id, role_id, assigned_at)"
        " SELECT :hr, id, now() FROM roles WHERE name = 'hr_manager'",
        "INSERT INTO user_roles (user_id, role_id, assigned_at)"
        " SELECT :iv, id, now() FROM roles WHERE name = 'interviewer'",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status, created_at,"
        " updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at) VALUES (:rd, :c, :w, 0, 'Panel', 'human_review', now(), now())",
        "INSERT INTO round_criteria (id, company_id, round_id, competency_id, competency_name,"
        " competency_kind, weight, created_at)"
        " VALUES (gen_random_uuid(), :c, :rd, 'communication', 'Communication', 'technical',"
        " 1.0, now())",
        "INSERT INTO applicants (id, company_id, full_name, email, target_job_title)"
        " VALUES (:a, :c, 'Asha', :ma, 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " status, workflow_id, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', 'shortlisted', :w, now(), now())",
    ):
        await db.execute(text(sql), p)
    await db.commit()
    try:
        yield ids
    finally:
        await db.rollback()
        # Children first: the session-interviewer link holds a composite FK to
        # the scorecard, which the company cascade would otherwise reach first.
        for table in ("interview_session_interviewers", "interview_sessions", "interview_loops",
                      "interviewer_scorecards"):
            await db.execute(text(f"DELETE FROM {table} WHERE company_id = :c"), {"c": ids["c"]})
        await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": ids["c"]})
        await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"),
                         {"u": [ids["hr"], ids["iv"]]})
        await db.commit()


async def _loop(db: AsyncSession, s: dict[str, uuid.UUID]) -> uuid.UUID:
    out = await sched.create_loop(
        db, company_id=s["c"], enrolment_id=s["e"], title="Interviews",
        candidate_timezone="Asia/Kolkata", buffer_minutes=15, self_schedule=False,
        actor=s["hr"], meta=META,
    )
    return uuid.UUID(out["loop_id"])


async def _add(db: AsyncSession, s: dict[str, uuid.UUID], loop_id: uuid.UUID, hours: int) -> None:
    await sched.add_session(
        db, company_id=s["c"], loop_id=loop_id, round_id=s["rd"], title=None,
        duration_minutes=45, interviewer_ids=[s["iv"]], starts_at=T0 + timedelta(hours=hours),
        location=None, allow_outside_availability=True, actor=s["hr"], meta=META,
    )


async def _candidate_emails(db: AsyncSession, loop_id: uuid.UUID) -> int:
    return int(await db.scalar(text(
        "SELECT count(*) FROM email_events WHERE related_kind = 'interview_loop'"
        " AND related_id = :l"), {"l": loop_id}) or 0)


async def test_an_interview_added_after_sending_is_sent_to_the_candidate(
    db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    loop_id = await _loop(db, seed)
    await _add(db, seed, loop_id, 0)
    await sched.send_loop(db, company_id=seed["c"], loop_id=loop_id, actor=seed["hr"], meta=META)
    await db.commit()
    assert await _candidate_emails(db, loop_id) == 1
    version = await db.scalar(
        text("SELECT itinerary_version FROM interview_loops WHERE id = :l"), {"l": loop_id})

    await _add(db, seed, loop_id, 3)
    await db.commit()

    # The candidate gets the updated itinerary, at a new calendar version.
    assert await _candidate_emails(db, loop_id) == 2
    assert await db.scalar(
        text("SELECT itinerary_version FROM interview_loops WHERE id = :l"), {"l": loop_id}
    ) == (version or 0) + 1


async def test_an_interview_added_before_sending_tells_nobody_yet(
    db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    loop_id = await _loop(db, seed)
    await _add(db, seed, loop_id, 0)
    await db.commit()
    assert await _candidate_emails(db, loop_id) == 0


def test_the_availability_refusal_is_grammatical_for_any_count() -> None:
    assert sched._outside_message(1, 1).startswith("The interviewer has not marked")
    assert sched._outside_message(1, 3).startswith("1 of the interviewers has not marked")
    assert sched._outside_message(2, 3).startswith("2 of the interviewers have not marked")
    # The frontend offers "Schedule anyway" on this phrase.
    for args in ((1, 1), (1, 3), (2, 3)):
        assert "not marked that time as available" in sched._outside_message(*args)


async def test_a_loop_with_no_timed_interview_has_no_calendar_file(
    db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    loop_id = await _loop(db, seed)
    await db.commit()
    with pytest.raises(sched.SchedulingError) as err:
        await sched.hr_ics(db, company_id=seed["c"], loop_id=loop_id)
    assert err.value.status_code == 409
    await db.rollback()

    await _add(db, seed, loop_id, 0)
    await db.commit()
    assert "BEGIN:VEVENT" in await sched.hr_ics(db, company_id=seed["c"], loop_id=loop_id)
