"""PH4-A1: the scorecard guarantees hold AT THE DATABASE, not just in the app.

These run against a real, migrated Postgres (CI's pgvector service; locally any
loopback database — the conftest refuses a remote one). They exist because the
guarantees they check are enforced by constraints and triggers, and a unit test
that mocks the database cannot see a trigger at all.

EVERY REFUSAL IS CHECKED FOR ITS REASON, not merely for failing. The first
version of these checks passed "a duplicate live scorecard is refused" — because
the insert omitted the id and died on NOT NULL, never reaching the unique index
it was written to prove. A refusal for the wrong reason is a test that proves
nothing while reporting that it proved something.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

pytestmark = pytest.mark.integration


class Fixture:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.iv1 = uuid.uuid4()
        self.iv2 = uuid.uuid4()
        self.req = uuid.uuid4()
        self.wf = uuid.uuid4()
        self.round = uuid.uuid4()
        self.applicant = uuid.uuid4()
        self.enrolment = uuid.uuid4()
        self.card = uuid.uuid4()


async def _build(db: AsyncSession) -> Fixture:
    f = Fixture()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "i1": f.iv1, "i2": f.iv2, "r": f.req, "w": f.wf,
        "rd": f.round, "a": f.applicant, "e": f.enrolment, "card": f.card,
        "slug": f"ph4-trig-{tag}", "e1": f"iv1-{tag}@ph4.test", "e2": f"iv2-{tag}@ph4.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'PH4 trigger co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:i1, :e1, :c), (:i2, :e2, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " created_at, updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at) VALUES (:rd, :c, :w, 0, 'Tech', 'human_review', now(), now())",
        "INSERT INTO round_criteria (id, company_id, round_id, competency_id, competency_name,"
        " weight, created_at) VALUES"
        " (gen_random_uuid(), :c, :rd, 'problem_solving', 'Problem Solving', 0.5, now()),"
        " (gen_random_uuid(), :c, :rd, 'communication', 'Communication', 0.5, now())",
        "UPDATE workflows SET status = 'published', published_at = now() WHERE id = :w",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Candidate', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
        " target_job_title, workflow_id, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', :w, now(), now())",
        "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
        " interviewer_user_id, status) VALUES (:card, :c, :e, :rd, :i1, 'in_progress')",
    ):
        await db.execute(text(sql), p)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """One transaction per test, always rolled back — nothing is left behind."""
    # Its own engine: these tests do not boot the app, so the app's engine
    # singleton is never initialised.
    engine = build_engine(
        database_url=settings.database_url,
        database_ssl=settings.database_ssl,
        pool_size=2,
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


async def _refused(db: AsyncSession, sql: str, params: dict[str, Any], reason: str) -> None:
    """Assert ``sql`` is refused, and refused for ``reason``."""
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert reason in message, (
            f"refused for the WRONG reason — expected {reason!r}, got: {message[:200]}"
        )
        return
    await sp.rollback()
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:80]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


async def _submit(db: AsyncSession, f: Fixture) -> None:
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
            " competency_id, score, evidence) VALUES (:card, :c, :rd, 'problem_solving', 4, 'x')"
        ),
        {"card": f.card, "c": f.company, "rd": f.round},
    )
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET status = 'submitted', submitted_at = now(),"
            " summary = 'strong' WHERE id = :card"
        ),
        {"card": f.card},
    )


# ===========================================================================
# Criteria come from the frozen round_criteria — structurally
# ===========================================================================
@pytest.mark.asyncio
async def test_a_criterion_outside_the_frozen_rubric_cannot_be_scored(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
        " competency_id, score) VALUES (:card, :c, :rd, 'made_up_competency', 5)",
        {"card": f.card, "c": f.company, "rd": f.round},
        "fk_scorecard_scores_frozen_criterion",
    )


@pytest.mark.asyncio
async def test_scores_are_one_to_five(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
        " competency_id, score) VALUES (:card, :c, :rd, 'communication', 9)",
        {"card": f.card, "c": f.company, "rd": f.round},
        "ck_scorecard_scores_range",
    )


@pytest.mark.asyncio
async def test_a_score_cannot_claim_a_different_round_than_its_scorecard(
    db: AsyncSession,
) -> None:
    """Without this, a score could name another round's criterion and still pass
    the frozen-criterion foreign key."""
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
        " competency_id, score) VALUES (:card, :c, :other, 'communication', 3)",
        {"card": f.card, "c": f.company, "other": uuid.uuid4()},
        "fk_scorecard_scores",
    )


# ===========================================================================
# One live scorecard per interviewer / candidate / round
# ===========================================================================
@pytest.mark.asyncio
async def test_the_same_interviewer_cannot_hold_two_live_scorecards(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
        " interviewer_user_id) VALUES (:id, :c, :e, :rd, :i1)",
        {"id": uuid.uuid4(), "c": f.company, "e": f.enrolment, "rd": f.round, "i1": f.iv1},
        "uq_interviewer_scorecards_live",
    )


@pytest.mark.asyncio
async def test_each_interviewer_gets_an_independent_scorecard(db: AsyncSession) -> None:
    f = await _build(db)
    await _allowed(
        db,
        "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
        " interviewer_user_id) VALUES (:id, :c, :e, :rd, :i2)",
        {"id": uuid.uuid4(), "c": f.company, "e": f.enrolment, "rd": f.round, "i2": f.iv2},
    )


# ===========================================================================
# A submitted scorecard cannot be silently modified
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "sql", "reason"),
    [
        ("change a score",
         "UPDATE interviewer_scorecard_scores SET score = 1 WHERE scorecard_id = :card",
         "its scores cannot change"),
        ("rewrite evidence",
         "UPDATE interviewer_scorecard_scores SET evidence = 'changed' WHERE scorecard_id = :card",
         "can only be redacted"),
        ("delete a score",
         "DELETE FROM interviewer_scorecard_scores WHERE scorecard_id = :card",
         "its scores cannot change"),
        ("rewrite the summary",
         "UPDATE interviewer_scorecards SET summary = 'weak' WHERE id = :card",
         "can only be redacted"),
        ("reopen to draft",
         "UPDATE interviewer_scorecards SET status = 'in_progress', submitted_at = NULL"
         " WHERE id = :card",
         "cannot be edited"),
        ("delete the scorecard",
         "DELETE FROM interviewer_scorecards WHERE id = :card",
         "cannot be deleted"),
        ("clear the summary without marking a redaction",
         "UPDATE interviewer_scorecards SET summary = NULL WHERE id = :card",
         "can only be redacted"),
    ],
)
async def test_submitted_evidence_is_protected(
    db: AsyncSession, label: str, sql: str, reason: str
) -> None:
    f = await _build(db)
    await _submit(db, f)
    await _refused(db, sql, {"card": f.card}, reason)


@pytest.mark.asyncio
async def test_a_score_cannot_be_added_after_submission(db: AsyncSession) -> None:
    f = await _build(db)
    await _submit(db, f)
    await _refused(
        db,
        "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
        " competency_id, score) VALUES (:card, :c, :rd, 'communication', 2)",
        {"card": f.card, "c": f.company, "rd": f.round},
        "its scores cannot change",
    )


# ===========================================================================
# The two permitted changes: DPDP redaction, and the audited correction
# ===========================================================================
@pytest.mark.asyncio
async def test_dpdp_redaction_is_permitted_and_irreversible(db: AsyncSession) -> None:
    f = await _build(db)
    await _submit(db, f)
    await _allowed(
        db,
        "UPDATE interviewer_scorecard_scores SET evidence = NULL WHERE scorecard_id = :card",
        {"card": f.card},
    )
    await _allowed(
        db,
        "UPDATE interviewer_scorecards SET summary = NULL, redacted_at = now() WHERE id = :card",
        {"card": f.card},
    )
    await _refused(
        db,
        "UPDATE interviewer_scorecards SET redacted_at = NULL WHERE id = :card",
        {"card": f.card},
        "redaction cannot be reversed",
    )


@pytest.mark.asyncio
async def test_the_correction_path_works_in_its_real_order(db: AsyncSession) -> None:
    """Supersede the old row BY a new draft, then insert that draft.

    The live unique index refuses the new row while the old one is live, and the
    superseded_by foreign key refuses pointing at a row that does not exist yet.
    Only a DEFERRED foreign key lets both be true; this proves it.
    """
    f = await _build(db)
    await _submit(db, f)
    new_id = uuid.uuid4()
    sp = await db.begin_nested()
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET superseded_at = now(), superseded_by_id = :n"
            " WHERE id = :card"
        ),
        {"n": new_id, "card": f.card},
    )
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, corrects_id, correction_reason)"
            " VALUES (:n, :c, :e, :rd, :i1, 'in_progress', :card, 'Scored the wrong criterion')"
        ),
        {"n": new_id, "c": f.company, "e": f.enrolment, "rd": f.round, "i1": f.iv1,
         "card": f.card},
    )
    # Force the deferred check now rather than at a commit this test never makes.
    await db.execute(text("SET CONSTRAINTS fk_interviewer_scorecards_superseded_by IMMEDIATE"))
    await sp.commit()

    rows = (
        await db.execute(
            text("SELECT id, superseded_by_id, corrects_id FROM interviewer_scorecards"
                 " WHERE enrolment_id = :e AND interviewer_user_id = :i1"),
            {"e": f.enrolment, "i1": f.iv1},
        )
    ).mappings().all()
    by_id = {r["id"]: r for r in rows}
    assert by_id[f.card]["superseded_by_id"] == new_id
    assert by_id[new_id]["corrects_id"] == f.card
    # And the original scores survived untouched.
    assert await db.scalar(
        text("SELECT score FROM interviewer_scorecard_scores WHERE scorecard_id = :card"),
        {"card": f.card},
    ) == 4


@pytest.mark.asyncio
async def test_a_supersession_cannot_be_rewritten(db: AsyncSession) -> None:
    f = await _build(db)
    await _submit(db, f)
    await _allowed(
        db,
        "UPDATE interviewer_scorecards SET superseded_at = now(), superseded_by_id = :card"
        " WHERE id = :card",
        {"card": f.card},
    )
    await _refused(
        db,
        "UPDATE interviewer_scorecards SET superseded_by_id = :other WHERE id = :card",
        {"card": f.card, "other": uuid.uuid4()},
        "already superseded",
    )


@pytest.mark.asyncio
async def test_the_cascade_still_removes_evidence_when_the_application_goes(
    db: AsyncSession,
) -> None:
    """The delete guard must not make an application undeletable."""
    f = await _build(db)
    await _submit(db, f)
    await _allowed(db, "DELETE FROM enrolments WHERE id = :e", {"e": f.enrolment})
    assert await db.scalar(
        text("SELECT count(*) FROM interviewer_scorecards WHERE id = :card"), {"card": f.card}
    ) == 0


# ===========================================================================
# Review hardening — withdrawn rows, moved scores, every text column redacted
# ===========================================================================
async def _withdraw_card(db: AsyncSession, f: Fixture, reason: str | None) -> None:
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET status = 'withdrawn', withdrawn_at = now(),"
            " withdrawn_reason = :why WHERE id = :card"
        ),
        {"card": f.card, "why": reason},
    )


@pytest.mark.asyncio
async def test_a_withdrawn_assignment_cannot_be_deleted_on_its_own(db: AsyncSession) -> None:
    f = await _build(db)
    await _withdraw_card(db, f, "Conflict of interest")
    await _refused(
        db, "DELETE FROM interviewer_scorecards WHERE id = :card", {"card": f.card},
        "withdrawn assignment kept on record and cannot be deleted",
    )


@pytest.mark.asyncio
async def test_a_score_cannot_be_moved_off_a_submitted_scorecard(db: AsyncSession) -> None:
    """L1: the trigger used to consult only the NEW parent, so moving a score
    from submitted evidence onto a draft passed."""
    f = await _build(db)
    other = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status) VALUES (:o, :c, :e, :rd, :i2, 'in_progress')"
        ),
        {"o": other, "c": f.company, "e": f.enrolment, "rd": f.round, "i2": f.iv2},
    )
    await _submit(db, f)
    await _refused(
        db,
        "UPDATE interviewer_scorecard_scores SET scorecard_id = :o WHERE scorecard_id = :card",
        {"o": other, "card": f.card},
        "cannot be moved to another scorecard or criterion",
    )


@pytest.mark.asyncio
async def test_a_draft_score_cannot_change_criterion_either(db: AsyncSession) -> None:
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
            " competency_id, score) VALUES (:card, :c, :rd, 'problem_solving', 3)"
        ),
        {"card": f.card, "c": f.company, "rd": f.round},
    )
    await _refused(
        db,
        "UPDATE interviewer_scorecard_scores SET competency_id = 'communication'"
        " WHERE scorecard_id = :card",
        {"card": f.card},
        "cannot be moved to another scorecard or criterion",
    )


@pytest.mark.asyncio
async def test_a_withdrawal_reason_can_be_redacted_and_only_redacted(db: AsyncSession) -> None:
    """M4(a): the trigger used to refuse erasure touching the reason at all."""
    f = await _build(db)
    await _withdraw_card(db, f, "Knows the candidate from a previous job")
    await _refused(
        db, "UPDATE interviewer_scorecards SET withdrawn_reason = 'other words' WHERE id = :card",
        {"card": f.card}, "withdrawal reason can only be redacted",
    )
    await _allowed(
        db,
        "UPDATE interviewer_scorecards SET withdrawn_reason = '[redacted]', summary = NULL,"
        " redacted_at = now() WHERE id = :card",
        {"card": f.card},
    )


@pytest.mark.asyncio
async def test_a_correction_reason_can_be_redacted_and_only_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _submit(db, f)
    new_id = uuid.uuid4()
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET superseded_at = now(), superseded_by_id = :n"
            " WHERE id = :card"
        ),
        {"n": new_id, "card": f.card},
    )
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, submitted_at, corrects_id, correction_reason)"
            " VALUES (:n, :c, :e, :rd, :i1, 'submitted', now(), :card,"
            " 'The candidate named their employer, I misheard')"
        ),
        {"n": new_id, "c": f.company, "e": f.enrolment, "rd": f.round, "i1": f.iv1,
         "card": f.card},
    )
    await _refused(
        db,
        "UPDATE interviewer_scorecards SET correction_reason = 'Rewritten reason' WHERE id = :n",
        {"n": new_id}, "correction reason can only be redacted",
    )
    await _allowed(
        db,
        "UPDATE interviewer_scorecards SET correction_reason = '[redacted]', summary = NULL,"
        " redacted_at = now() WHERE id = :n",
        {"n": new_id},
    )


@pytest.mark.asyncio
async def test_once_redacted_no_text_can_be_written_again_in_any_state(db: AsyncSession) -> None:
    """M4(c): an editable row with redacted_at set used to accept a new summary."""
    f = await _build(db)
    await _allowed(
        db, "UPDATE interviewer_scorecards SET redacted_at = now() WHERE id = :card",
        {"card": f.card},
    )
    await _refused(
        db, "UPDATE interviewer_scorecards SET summary = 'about them' WHERE id = :card",
        {"card": f.card}, "its text cannot be written again",
    )


@pytest.mark.asyncio
async def test_a_redacted_open_scorecard_takes_no_new_evidence(db: AsyncSession) -> None:
    """Re-audit L1: the scores trigger only guarded submitted/withdrawn parents,
    so evidence could still be inserted onto a redacted draft."""
    f = await _build(db)
    await _allowed(
        db, "UPDATE interviewer_scorecards SET redacted_at = now() WHERE id = :card",
        {"card": f.card},
    )
    await _refused(
        db,
        "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
        " competency_id, score, evidence) VALUES (:card, :c, :rd, 'communication', 3,"
        " 'about them')",
        {"card": f.card, "c": f.company, "rd": f.round},
        "its text cannot be written again",
    )
    # A score with no text is still just a number.
    await _allowed(
        db,
        "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
        " competency_id, score) VALUES (:card, :c, :rd, 'communication', 3)",
        {"card": f.card, "c": f.company, "rd": f.round},
    )


@pytest.mark.asyncio
async def test_the_erasure_order_withdraw_then_redact_is_accepted(db: AsyncSession) -> None:
    """The statements erasure step 5f runs, in its order, on an open assignment
    with a draft: the triggers must let every one of them through."""
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
            " competency_id, score, evidence) VALUES (:card, :c, :rd, 'communication', 3, 'e')"
        ),
        {"card": f.card, "c": f.company, "rd": f.round},
    )
    await db.execute(
        text("UPDATE interviewer_scorecards SET summary = 'draft prose' WHERE id = :card"),
        {"card": f.card},
    )
    for sql in (
        "UPDATE interviewer_scorecards SET status = 'withdrawn', withdrawn_at = now(),"
        " withdrawn_reason = NULL, updated_at = now()"
        " WHERE status IN ('assigned', 'in_progress') AND enrolment_id = :e",
        "UPDATE interviewer_scorecard_scores SET evidence = NULL, updated_at = now()"
        " WHERE evidence IS NOT NULL AND scorecard_id = :card",
        "UPDATE interviewer_scorecards SET summary = NULL,"
        " correction_reason = CASE WHEN correction_reason IS NULL THEN NULL"
        "                          ELSE '[redacted]' END,"
        " withdrawn_reason = CASE WHEN withdrawn_reason IS NULL THEN NULL"
        "                         ELSE '[redacted]' END,"
        " redacted_at = now(), updated_at = now()"
        " WHERE redacted_at IS NULL AND enrolment_id = :e",
    ):
        await _allowed(db, sql, {"e": f.enrolment, "card": f.card})
    row = (
        await db.execute(
            text(
                "SELECT status, summary, redacted_at FROM interviewer_scorecards WHERE id = :card"
            ),
            {"card": f.card},
        )
    ).mappings().one()
    assert row["status"] == "withdrawn" and row["summary"] is None and row["redacted_at"]


@pytest.mark.asyncio
async def test_a_company_with_interview_evidence_can_still_be_deleted(db: AsyncSession) -> None:
    """The delete guards key on the enrolment still existing. A company delete
    cascades to scorecards directly AND through enrolments; whichever order
    Postgres takes, the tenant must still be removable."""
    f = await _build(db)
    other = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, withdrawn_at) VALUES"
            " (:o, :c, :e, :rd, :i2, 'withdrawn', now())"
        ),
        {"o": other, "c": f.company, "e": f.enrolment, "rd": f.round, "i2": f.iv2},
    )
    await _submit(db, f)
    await _allowed(db, "DELETE FROM companies WHERE id = :c", {"c": f.company})
    assert await db.scalar(
        text("SELECT count(*) FROM interviewer_scorecards WHERE company_id = :c"),
        {"c": f.company},
    ) == 0


# ===========================================================================
# PH4-O4 — the ledger refuses a final decision with no reason code
# ===========================================================================
_LEDGER_INSERT = (
    "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
    " actor_user_id, automated, reason, occurred_at, reason_code, reason_label)"
    " VALUES (:c, :e, :frm, :to, NULL, false, 'test', now(), :code, :label)"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("to_status", ["hired", "rejected"])
async def test_a_decision_without_a_reason_code_is_refused_by_the_ledger(
    db: AsyncSession, to_status: str
) -> None:
    f = await _build(db)
    await _refused(
        db, _LEDGER_INSERT,
        {"c": f.company, "e": f.enrolment, "frm": "held", "to": to_status, "code": None,
         "label": None},
        "needs a reason_code",
    )
    await _allowed(
        db, _LEDGER_INSERT,
        {"c": f.company, "e": f.enrolment, "frm": "held", "to": to_status,
         "code": "skills_fit", "label": "Skills / competency fit"},
    )


@pytest.mark.asyncio
async def test_the_ledger_still_takes_moves_and_notes_that_are_not_decisions(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    for frm, to in (("new", "shortlisted"), ("shortlisted", "held"), ("rejected", "rejected")):
        await _allowed(
            db, _LEDGER_INSERT,
            {"c": f.company, "e": f.enrolment, "frm": frm, "to": to, "code": None,
             "label": None},
        )
