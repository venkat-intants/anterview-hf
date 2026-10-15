"""GET /users/{id}/profile for HR viewing their own applicants — real Postgres.

QA ANTHIRE-005: claiming an applicant account clears users.company_id, so the
company-member test alone 404'd "View full profile" for every activated
applicant. The fix lets HR view a candidate account that has a live applicant
row in HR's own company. The unit tests mock the two lookups; these run the SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from shared.auth.base import User
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.routers.profile import get_user_profile

pytestmark = pytest.mark.integration


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


async def _setup(db: AsyncSession) -> dict[str, uuid.UUID]:
    ids = {k: uuid.uuid4() for k in ("co", "other", "hr", "cand", "applicant")}
    tag = ids["co"].hex[:10]
    for sql, params in (
        ("INSERT INTO companies (id, name, slug) VALUES (:a, 'Here', :s1), (:b, 'There', :s2)",
         {"a": ids["co"], "b": ids["other"], "s1": f"pv-{tag}", "s2": f"pvo-{tag}"}),
        ("INSERT INTO users (id, email, company_id) VALUES (:h, :mh, :c)",
         {"h": ids["hr"], "mh": f"hr-{tag}@pv.test", "c": ids["co"]}),
        # A claimed candidate account: no company of its own.
        ("INSERT INTO users (id, email, full_name) VALUES (:u, :mu, 'Babu Baddam')",
         {"u": ids["cand"], "mu": f"cand-{tag}@pv.test"}),
    ):
        await db.execute(text(sql), params)
    return ids


def _hr(ids: dict[str, uuid.UUID]) -> User:
    return User(user_id=str(ids["hr"]), full_name="", email="", roles=["hr_manager"])


async def _apply(db: AsyncSession, ids: dict[str, uuid.UUID], company: uuid.UUID,
                 *, deleted: bool = False, status: str = "new",
                 opening: str = "open") -> None:
    """An applicant row plus one enrolment (an application) at `company`."""
    a, r = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text("INSERT INTO applicants (id, company_id, user_id, full_name, target_job_title,"
             " deleted_at) VALUES (:a, :c, :u, 'Babu Baddam', 'Engineer',"
             " CASE WHEN :d THEN now() END)"),
        {"a": a, "c": company, "u": ids["cand"], "d": deleted},
    )
    await db.execute(
        text("INSERT INTO job_requisitions (id, company_id, title, status, created_at,"
             " updated_at) VALUES (:r, :c, 'Engineer', :st, now(), now())"),
        {"r": r, "c": company, "st": opening},
    )
    await db.execute(
        text("INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
             " target_job_title, status, created_at, updated_at)"
             " VALUES (:e, :c, :r, :a, 'Engineer', 'new', now(), now())"),
        {"e": uuid.uuid4(), "c": company, "r": r, "a": a},
    )
    if status != "new":
        # Bypass the transition ledger's reason trigger: a direct status write
        # is enough for a read-side test.
        await db.execute(
            text("UPDATE enrolments SET status = :s WHERE applicant_id = :a"),
            {"s": status, "a": a},
        )


@pytest.mark.asyncio
async def test_hr_sees_a_claimed_candidate_who_applied_to_their_company(db: AsyncSession) -> None:
    ids = await _setup(db)
    await _apply(db, ids, ids["co"])
    out = await get_user_profile(ids["cand"], _hr(ids), db)
    assert out.full_name == "Babu Baddam"
    assert out.official_email is None and out.roles == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["applied_elsewhere", "application_erased", "never_applied", "rejected_here",
     "opening_closed"],
)
async def test_hr_does_not_see_a_candidate_with_no_live_application_here(
    db: AsyncSession, case: str
) -> None:
    ids = await _setup(db)
    if case == "applied_elsewhere":
        await _apply(db, ids, ids["other"])
    elif case == "application_erased":
        await _apply(db, ids, ids["co"], deleted=True)
    elif case == "rejected_here":
        # Security review M2: a rejected candidate's live account profile is
        # not HR's to keep watching.
        await _apply(db, ids, ids["co"], status="rejected")
    elif case == "opening_closed":
        await _apply(db, ids, ids["co"], opening="closed")
    with pytest.raises(HTTPException) as exc:
        await get_user_profile(ids["cand"], _hr(ids), db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_hire_stays_viewable_after_the_opening_closes(db: AsyncSession) -> None:
    ids = await _setup(db)
    await _apply(db, ids, ids["co"], status="hired", opening="closed")
    out = await get_user_profile(ids["cand"], _hr(ids), db)
    assert out.full_name == "Babu Baddam"
