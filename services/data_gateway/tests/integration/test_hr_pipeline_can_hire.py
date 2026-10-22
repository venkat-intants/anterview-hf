"""HR-H1: the pipeline board's ``can_hire`` against a real, migrated Postgres.

The board offers Hire from ``can_hire``, which ``_PIPELINE_SQL`` computes in
SQL. The decision endpoint refuses a hire by ``final_decision.refusal``, fed by
``enrolment_awaits_human``. If the two drift, the board either offers a Hire
the server refuses or hides one it would accept. The unit test only checks
that the SQL text contains the right fragments; this runs the real query and
the real function, one case per state, and asks both sides.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.final_decision import _STATE_SQL, refusal
from app.routers.hr_pipeline import get_pipeline

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


# name -> (status, has workflow, sits in which round, expected can_hire)
#   "mcq"  : an automatically scored round, so the workflow is still running
#   "human": a human_review round, waiting on a person
#   None   : no current round (finished every round, or on no workflow)
CASES: dict[str, tuple[str, bool, str | None, bool]] = {
    "held": ("held", True, "mcq", True),
    "in_round_one": ("shortlisted", True, "mcq", False),
    "at_human_review": ("shortlisted", True, "human", True),
    "finished_all_rounds": ("interviewed", True, None, True),
    "no_workflow_shortlisted": ("shortlisted", False, None, True),
    "no_workflow_new": ("new", False, None, False),
    "already_hired": ("hired", True, None, False),
    "rejected": ("rejected", True, None, False),
}


async def _seed(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID], uuid.UUID]:
    company, hr, req, wf = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mcq, human = uuid.uuid4(), uuid.uuid4()
    tag = company.hex[:10]
    p: dict[str, Any] = {
        "c": company, "h": hr, "r": req, "w": wf, "mcq": mcq, "human": human,
        "slug": f"canhire-{tag}", "m": f"h-{tag}@canhire.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'Can hire co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " review_status, created_at, updated_at)"
        " VALUES (:w, :c, :r, 1, 'draft', 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " deadline_days, created_at, updated_at)"
        " VALUES (:mcq, :c, :w, 0, 'Fundamentals', 'mcq', 7, now(), now()),"
        " (:human, :c, :w, 1, 'Panel', 'human_review', 7, now(), now())",
    ):
        await db.execute(text(sql), p)

    rounds = {"mcq": mcq, "human": human, None: None}
    enrolments: dict[str, uuid.UUID] = {}
    for name, (status, on_workflow, rnd, _expected) in CASES.items():
        applicant, enrolment = uuid.uuid4(), uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
                " VALUES (:a, :c, :n, 'Engineer')"
            ),
            {"a": applicant, "c": company, "n": f"Case {name}"},
        )
        await db.execute(
            text(
                "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
                " target_job_title, status, workflow_id, current_round_id, created_at,"
                " updated_at)"
                " VALUES (:e, :c, :r, :a, 'Engineer', :s, :w, :rnd, now(), now())"
            ),
            {
                "e": enrolment, "c": company, "r": req, "a": applicant, "s": status,
                "w": wf if on_workflow else None, "rnd": rounds[rnd],
            },
        )
        enrolments[name] = enrolment

    # Someone filed under no opening: no enrolment, so their own status decides.
    unfiled = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title, status)"
            " VALUES (:a, :c, 'Unfiled', 'Engineer', 'interviewed')"
        ),
        {"a": unfiled, "c": company},
    )
    return company, hr, enrolments, unfiled


@pytest.mark.asyncio
async def test_board_can_hire_matches_the_decision_rule_in_every_state(db: AsyncSession) -> None:
    company, hr, enrolments, unfiled = await _seed(db)

    page = await get_pipeline((hr, company), db, stage=None, status_f=None, limit=50, offset=0)
    by_enrolment = {row.enrolment_id: row for row in page.items if row.enrolment_id}

    for name, enrolment in enrolments.items():
        expected = CASES[name][3]
        row = by_enrolment[str(enrolment)]
        assert row.can_hire is expected, f"{name}: board says can_hire={row.can_hire}"

        state = (
            await db.execute(text(_STATE_SQL), {"e": enrolment, "c": company})
        ).mappings().one()
        server_accepts = (
            refusal("hired", state["status"], bool(state["awaits_human"]), state["workflow_id"])
            is None
        )
        assert row.can_hire is server_accepts, (
            f"{name}: board offers Hire={row.can_hire} but the decision rule says {server_accepts}"
        )

    unfiled_row = next(r for r in page.items if r.applicant_id == str(unfiled))
    assert unfiled_row.enrolment_id is None
    assert unfiled_row.can_hire is True
