"""The copilot's invite and shortlist drafts, against a real migrated Postgres.

The unit suite feeds the draft tools hand-built rows, so the SQL that decides
which application a proposal is about (and whether it may be drafted at all)
is only exercised here: the application_progress view, the applicant join that
supplies the email, the company filter, and the uuid[] cast.

Audit CP-F5: a draft used to carry only the applicant id, so anyone with two
live applications got a card whose commit could only answer "choose which
one", and invites were drafted for people the endpoint would refuse.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from shared.agents import ToolContext
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import registry
from app.config import settings

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


async def _company(db: AsyncSession) -> uuid.UUID:
    c = uuid.uuid4()
    await db.execute(text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Draft co', :s)"),
                     {"c": c, "s": f"draft-{c.hex[:10]}"})
    return c


async def _opening(db: AsyncSession, company: uuid.UUID, title: str) -> uuid.UUID:
    r = uuid.uuid4()
    await db.execute(
        text("INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
             " VALUES (:r, :c, :t, now(), now())"),
        {"r": r, "c": company, "t": title},
    )
    return r


async def _person(db: AsyncSession, company: uuid.UUID, name: str,
                  email: str | None) -> uuid.UUID:
    a = uuid.uuid4()
    await db.execute(
        text("INSERT INTO applicants (id, company_id, full_name, email, target_job_title)"
             " VALUES (:a, :c, :n, :e, 'Role')"),
        {"a": a, "c": company, "n": name, "e": email},
    )
    return a


async def _apply(db: AsyncSession, company: uuid.UUID, applicant: uuid.UUID,
                 opening: uuid.UUID, status: str) -> uuid.UUID:
    e = uuid.uuid4()
    await db.execute(
        text("INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
             " target_job_title, status, created_at, updated_at)"
             " VALUES (:e, :c, :r, :a, 'Role', 'new', clock_timestamp(), clock_timestamp())"),
        {"e": e, "c": company, "r": opening, "a": applicant},
    )
    if status != "new":
        await db.execute(text("UPDATE enrolments SET status = :s WHERE id = :e"),
                         {"s": status, "e": e})
    return e


def _ctx(db: AsyncSession, company: uuid.UUID) -> ToolContext:
    return ToolContext(actor_id=str(uuid.uuid4()), role="hr_manager",
                       company_id=str(company), resources={"db": db})


async def _invoke(db: AsyncSession, company: uuid.UUID, tool: str, ids: list[Any]) -> Any:
    return await registry.invoke(tool, {"applicant_ids": [str(i) for i in ids]},
                                 _ctx(db, company), call_id="c1")


async def test_invite_is_drafted_per_eligible_application_with_its_enrolment(
    db: AsyncSession,
) -> None:
    company = await _company(db)
    welder, fitter = await _opening(db, company, "Welder"), await _opening(db, company, "Fitter")
    asha = await _person(db, company, "Asha", "asha@draft.test")
    shortlisted = await _apply(db, company, asha, welder, "shortlisted")
    await _apply(db, company, asha, fitter, "new")

    result = await _invoke(db, company, "draft_interview_invites", [asha])

    assert result.ok, result.error
    assert [p.commit.body for p in result.proposals] == [
        {"applicant_id": str(asha), "enrolment_id": str(shortlisted), "language": "en"}
    ]
    # Two live applications, so the card names the one it is for.
    assert result.proposals[0].title == "Interview invite - Asha (Welder)"
    assert "Welder" in result.proposals[0].summary


async def test_an_ineligible_or_foreign_applicant_gets_no_invite_draft(db: AsyncSession) -> None:
    company, other = await _company(db), await _company(db)
    opening = await _opening(db, company, "Welder")
    bala = await _person(db, company, "Bala", "bala@draft.test")
    await _apply(db, company, bala, opening, "new")
    foreign = await _person(db, other, "Chitra", "chitra@draft.test")
    await _apply(db, other, foreign, await _opening(db, other, "Welder"), "shortlisted")

    result = await _invoke(db, company, "draft_interview_invites", [bala, foreign])

    assert result.ok, result.error
    assert result.proposals == []
    assert "not shortlisted and no exam passed" in result.content
    assert str(foreign) in result.content  # reported as not found in this company


async def test_a_person_filed_under_no_opening_is_drafted_without_an_enrolment(
    db: AsyncSession,
) -> None:
    company = await _company(db)
    uma = await _person(db, company, "Uma", "uma@draft.test")
    await db.execute(text("UPDATE applicants SET status = 'shortlisted' WHERE id = :a"),
                     {"a": uma})

    result = await _invoke(db, company, "draft_interview_invites", [uma])

    assert result.ok, result.error
    assert [p.commit.body for p in result.proposals] == [
        {"applicant_id": str(uma), "language": "en"}
    ]


async def test_shortlist_is_drafted_only_for_the_application_not_yet_shortlisted(
    db: AsyncSession,
) -> None:
    company = await _company(db)
    welder, fitter = await _opening(db, company, "Welder"), await _opening(db, company, "Fitter")
    asha = await _person(db, company, "Asha", "asha@draft.test")
    await _apply(db, company, asha, welder, "shortlisted")
    fresh = await _apply(db, company, asha, fitter, "new")

    result = await _invoke(db, company, "draft_shortlist", [asha])

    assert result.ok, result.error
    assert [p.commit.body for p in result.proposals] == [
        {"status": "shortlisted", "enrolment_id": str(fresh)}
    ]
    assert result.proposals[0].commit.path == f"/hr/applicants/{asha}"
    assert "already shortlisted" in result.content
