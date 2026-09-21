"""PH4-D1: the question-bank and exam-lock guarantees that hold AT THE DATABASE.

A bank question is born a draft, moves to review, and is approved only by
someone other than its author and submitter — a new version follows an
approved-or-retired one in the same lineage, and at most one version per
lineage is ever approved. A published exam round's sections and questions are
fixed, whichever table or statement touches them, and copying a bank question
into one requires it to be approved, same company, same kind. A round's
grading fields freeze the same way; unpublishing needs zero attempts; title
stays editable. The same bank lineage cannot land in one exam twice.

Against a real, migrated Postgres. Every refusal is checked for its REASON.
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


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.other_company = uuid.uuid4()
        self.hr = uuid.uuid4()  # author
        self.hr2 = uuid.uuid4()  # submitter
        self.hr3 = uuid.uuid4()  # a third HR manager — the legitimate reviewer
        self.bank = uuid.uuid4()
        self.exam = uuid.uuid4()
        self.round = uuid.uuid4()
        self.other_round = uuid.uuid4()
        self.section = uuid.uuid4()
        self.other_section = uuid.uuid4()
        self.applicant = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "oc": f.other_company, "h": f.hr, "h2": f.hr2, "h3": f.hr3,
        "b": f.bank, "x": f.exam, "er": f.round, "or_": f.other_round, "s": f.section,
        "os": f.other_section, "a": f.applicant,
        "slug": f"d1-{tag}", "oslug": f"d1o-{tag}",
        "m1": f"h-{tag}@d1.test", "m2": f"h2-{tag}@d1.test", "m3": f"h3-{tag}@d1.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'D1 co', :slug),"
        " (:oc, 'D1 other', :oslug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c), (:h2, :m2, :c),"
        " (:h3, :m3, :c)",
        "INSERT INTO question_banks (id, company_id, name, created_by_user_id, created_at,"
        " updated_at) VALUES (:b, :c, 'Aptitude', :h, now(), now())",
        "INSERT INTO exams (id, company_id, title) VALUES (:x, :c, 'Exam')",
        "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, status, position)"
        " VALUES (:er, :x, :c, 1, 'Round 1', 'draft', 0),"
        " (:or_, :x, :c, 2, 'Round 2', 'draft', 1)",
        "INSERT INTO exam_sections (id, round_id, exam_id, company_id, title, kind, position)"
        " VALUES (:s, :er, :x, :c, 'MCQ', 'mcq', 0), (:os, :or_, :x, :c, 'MCQ 2', 'mcq', 0)",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer')",
    ):
        await db.execute(text(sql), p)
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
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:100]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


def _q_insert(f: F, qid: uuid.UUID, *, root: uuid.UUID | None = None, version: int = 1) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO bank_questions (id, company_id, bank_id, root_id, version, kind, prompt,"
        " points, options, correct_index, difficulty, language, status, origin, content_hash,"
        " created_by_user_id, created_at, updated_at)"
        " VALUES (:i, :c, :b, :r, :v, 'mcq', 'Two plus two?', 1, '[\"3\",\"4\"]'::jsonb, 1,"
        " 'easy', 'en', 'draft', 'authored', repeat('a', 64), :h, now(), now())"
    )
    return sql, {"i": qid, "c": f.company, "b": f.bank, "r": root or qid, "v": version, "h": f.hr}


async def _new_draft(db: AsyncSession, f: F, **kw: Any) -> uuid.UUID:
    qid = uuid.uuid4()
    sql, params = _q_insert(f, qid, **kw)
    await db.execute(text(sql), params)
    return qid


SUBMIT_Q = ("UPDATE bank_questions SET status = 'in_review', submitted_by_user_id = :u,"
           " submitted_at = now() WHERE id = :q")
APPROVE_Q = ("UPDATE bank_questions SET status = 'approved', reviewed_by_user_id = :u,"
            " reviewed_at = now() WHERE id = :q")
WITHDRAW_Q = "UPDATE bank_questions SET status = 'draft' WHERE id = :q"
CHANGES_Q = ("UPDATE bank_questions SET status = 'draft', reviewed_by_user_id = :u,"
            " reviewed_at = now() WHERE id = :q")
RETIRE_Q = "UPDATE bank_questions SET status = 'retired', retired_by_user_id = :u WHERE id = :q"


# ===========================================================================
# T1 — the bank question lifecycle
# ===========================================================================
@pytest.mark.asyncio
async def test_a_bank_question_is_born_a_draft(db: AsyncSession) -> None:
    f = await _build(db)
    qid = uuid.uuid4()
    sql, params = _q_insert(f, qid)
    await _refused(
        db, sql.replace("'draft', 'authored'", "'in_review', 'authored'"), params,
        "arrives as a draft",
    )


@pytest.mark.asyncio
async def test_version_one_is_its_own_root(db: AsyncSession) -> None:
    f = await _build(db)
    qid = uuid.uuid4()
    sql, params = _q_insert(f, qid, root=uuid.uuid4())  # a DIFFERENT root than its own id
    await _refused(db, sql, params, "its own root")


@pytest.mark.asyncio
async def test_no_step_can_be_skipped(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await _refused(db, APPROVE_Q, {"q": qid, "u": f.hr3}, "cannot go from draft to approved")


@pytest.mark.asyncio
async def test_submitting_needs_a_submitter(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await _refused(
        db, "UPDATE bank_questions SET status = 'in_review' WHERE id = :q", {"q": qid},
        "needs a submitter",
    )


@pytest.mark.asyncio
async def test_the_author_cannot_approve_their_own_question(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)  # authored by f.hr
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _refused(
        db, APPROVE_Q, {"q": qid, "u": f.hr},
        "approved by someone other than its author and submitter",
    )


@pytest.mark.asyncio
async def test_the_submitter_cannot_approve_it_either(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _refused(
        db, APPROVE_Q, {"q": qid, "u": f.hr2},
        "approved by someone other than its author and submitter",
    )


@pytest.mark.asyncio
async def test_a_third_hr_manager_approves_it(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _allowed(db, APPROVE_Q, {"q": qid, "u": f.hr3})


@pytest.mark.asyncio
async def test_withdrawing_needs_no_reviewer_named(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _allowed(db, WITHDRAW_Q, {"q": qid})


@pytest.mark.asyncio
async def test_changes_requested_by_the_submitter_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _refused(
        db, CHANGES_Q, {"q": qid, "u": f.hr2},
        "changes requested by someone other than its submitter",
    )


@pytest.mark.asyncio
async def test_changes_requested_by_a_third_party_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _allowed(db, CHANGES_Q, {"q": qid, "u": f.hr3})


@pytest.mark.asyncio
async def test_retired_is_terminal(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await db.execute(text(APPROVE_Q), {"q": qid, "u": f.hr3})
    await db.execute(text(RETIRE_Q), {"q": qid, "u": f.hr})
    await _refused(
        db, "UPDATE bank_questions SET status = 'draft' WHERE id = :q", {"q": qid},
        "cannot go from retired to draft",
    )


@pytest.mark.asyncio
async def test_content_is_frozen_once_submitted(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _refused(
        db, "UPDATE bank_questions SET prompt = 'Changed' WHERE id = :q", {"q": qid},
        "its content is fixed",
    )


@pytest.mark.asyncio
async def test_a_new_version_needs_an_approved_or_retired_root(db: AsyncSession) -> None:
    f = await _build(db)
    root = await _new_draft(db, f)  # still a draft
    sql, params = _q_insert(f, uuid.uuid4(), root=root, version=2)
    await _refused(db, sql, params, "must be approved or retired before a new version follows it")


@pytest.mark.asyncio
async def test_a_new_version_follows_an_approved_root(db: AsyncSession) -> None:
    f = await _build(db)
    root = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": root, "u": f.hr2})
    await db.execute(text(APPROVE_Q), {"q": root, "u": f.hr3})
    sql, params = _q_insert(f, uuid.uuid4(), root=root, version=2)
    await _allowed(db, sql, params)


@pytest.mark.asyncio
async def test_at_most_one_approved_version_per_lineage(db: AsyncSession) -> None:
    """The partial unique index — the backstop if the app forgets to retire the
    prior version in the same transaction it approves the next one."""
    f = await _build(db)
    root = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": root, "u": f.hr2})
    await db.execute(text(APPROVE_Q), {"q": root, "u": f.hr3})
    v2_sql, v2_params = _q_insert(f, uuid.uuid4(), root=root, version=2)
    await db.execute(text(v2_sql), v2_params)
    v2 = v2_params["i"]
    await db.execute(text(SUBMIT_Q), {"q": v2, "u": f.hr2})
    await _refused(
        db, APPROVE_Q, {"q": v2, "u": f.hr3}, "uq_bank_questions_one_approved_per_lineage",
    )


@pytest.mark.asyncio
async def test_a_draft_never_copied_can_be_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await _allowed(db, "DELETE FROM bank_questions WHERE id = :q", {"q": qid})


@pytest.mark.asyncio
async def test_a_non_draft_question_cannot_be_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await _refused(
        db, "DELETE FROM bank_questions WHERE id = :q", {"q": qid}, "is in_review and is kept",
    )


@pytest.mark.asyncio
async def test_an_approved_question_cannot_be_deleted_either(db: AsyncSession) -> None:
    """Reaching T1's "copied into an exam" check specifically needs a draft
    that has somehow been copied — unreachable through the normal state
    machine, since T2 only accepts a copy from an APPROVED question, and
    approved is already non-draft. The "not draft -> kept" rule this test
    exercises is what actually protects a copied question in practice."""
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await db.execute(text(APPROVE_Q), {"q": qid, "u": f.hr3})
    await db.execute(
        text(
            "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
            " correct_index, position, source_bank_question_id, source_bank_root_id,"
            " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'Two plus two?',"
            " '[\"3\",\"4\"]'::jsonb, 1, 0, :q, :q, 1)"
        ),
        {"x": f.exam, "s": f.section, "c": f.company, "q": qid},
    )
    await _refused(
        db, "DELETE FROM bank_questions WHERE id = :q", {"q": qid}, "is approved and is kept",
    )


# ===========================================================================
# T2 — a published (or taken) round's sections and questions are fixed
# ===========================================================================
async def _publish_round(db: AsyncSession, round_id: uuid.UUID) -> None:
    await db.execute(text("UPDATE exam_rounds SET status = 'published' WHERE id = :r"), {"r": round_id})


async def _seed_question(db: AsyncSession, f: F) -> uuid.UUID:
    qid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
            " correct_index, position) VALUES (:i, :x, :s, :c, 'Two plus two?',"
            " '[\"3\",\"4\"]'::jsonb, 1, 0)"
        ),
        {"i": qid, "x": f.exam, "s": f.section, "c": f.company},
    )
    return qid


@pytest.mark.asyncio
async def test_a_question_cannot_be_inserted_into_a_published_round(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await _refused(
        db,
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, position) VALUES (gen_random_uuid(), :x, :s, :c, 'New?',"
        " '[\"3\",\"4\"]'::jsonb, 0, 1)",
        {"x": f.exam, "s": f.section, "c": f.company},
        "is published; its content is fixed",
    )


@pytest.mark.asyncio
async def test_a_question_on_a_published_round_cannot_be_edited(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _seed_question(db, f)
    await _publish_round(db, f.round)
    await _refused(
        db, "UPDATE exam_questions SET correct_index = 0 WHERE id = :q", {"q": qid},
        "is published; its content is fixed",
    )


@pytest.mark.asyncio
async def test_a_question_on_a_published_round_cannot_be_soft_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _seed_question(db, f)
    await _publish_round(db, f.round)
    await _refused(
        db, "UPDATE exam_questions SET deleted_at = now() WHERE id = :q", {"q": qid},
        "is published; its content is fixed",
    )


@pytest.mark.asyncio
async def test_a_new_section_cannot_be_added_to_a_published_round(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await _refused(
        db,
        "INSERT INTO exam_sections (id, round_id, exam_id, company_id, title, kind, position)"
        " VALUES (gen_random_uuid(), :r, :x, :c, 'Coding', 'coding', 1)",
        {"r": f.round, "x": f.exam, "c": f.company},
        "is published; its content is fixed",
    )


@pytest.mark.asyncio
async def test_a_draft_round_with_attempts_is_also_fixed(db: AsyncSession) -> None:
    """A round can be draft (unpublished after being taken is impossible — see
    T3 — but a row could in principle be draft-with-attempts via a direct
    write) and content is fixed by attempts alone, not just by status."""
    f = await _build(db)
    qid = await _seed_question(db, f)
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " attempt_no, status, started_at, created_at, updated_at)"
            " VALUES (gen_random_uuid(), :c, :x, :r, :a, 1, 'submitted', now(), now(), now())"
        ),
        {"c": f.company, "x": f.exam, "r": f.round, "a": f.applicant},
    )
    await _refused(
        db, "UPDATE exam_questions SET correct_index = 0 WHERE id = :q", {"q": qid},
        "has been taken; its content is fixed",
    )


@pytest.mark.asyncio
async def test_copying_an_unapproved_bank_question_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)  # never submitted, still draft
    await _refused(
        db,
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, position, source_bank_question_id, source_bank_root_id,"
        " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'Two plus two?',"
        " '[\"3\",\"4\"]'::jsonb, 1, 1, :q, :q, 1)",
        {"x": f.exam, "s": f.section, "c": f.company, "q": qid},
        "is not approved and cannot be copied into an exam",
    )


@pytest.mark.asyncio
async def test_copying_a_bank_question_of_the_wrong_kind_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    qid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO bank_questions (id, company_id, bank_id, root_id, version, kind, prompt,"
            " points, test_cases, allowed_languages, time_limit_ms, difficulty, language, status,"
            " origin, content_hash, created_by_user_id, created_at, updated_at)"
            " VALUES (:i, :c, :b, :i, 1, 'coding', 'Reverse a list', 100,"
            " CAST(:tc AS jsonb), CAST(:al AS jsonb), 5000, 'easy', 'en', 'draft', 'authored',"
            " repeat('b', 64), :h, now(), now())"
        ),
        {"i": qid, "c": f.company, "b": f.bank, "h": f.hr,
         "tc": '[{"stdin": "", "expected_output": "", "is_sample": true}]', "al": '["python"]'},
    )
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await db.execute(text(APPROVE_Q), {"q": qid, "u": f.hr3})
    await _refused(
        db,
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, position, source_bank_question_id, source_bank_root_id,"
        " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'x', '[\"a\",\"b\"]'::jsonb,"
        " 0, 1, :q, :q, 1)",
        {"x": f.exam, "s": f.section, "c": f.company, "q": qid},
        "cannot become a exam_questions row",
    )


# ===========================================================================
# T3 — a published (or taken) round's own settings are fixed
# ===========================================================================
@pytest.mark.asyncio
async def test_grading_fields_are_frozen_once_published(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await _refused(
        db, "UPDATE exam_rounds SET pass_threshold = 90 WHERE id = :r", {"r": f.round},
        "its settings are fixed",
    )
    await _refused(
        db, "UPDATE exam_rounds SET time_limit_seconds = 600 WHERE id = :r", {"r": f.round},
        "its settings are fixed",
    )
    await _refused(
        db, "UPDATE exam_rounds SET advances_to_interview = true WHERE id = :r", {"r": f.round},
        "its settings are fixed",
    )


@pytest.mark.asyncio
async def test_title_stays_editable_on_a_published_round(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await _allowed(db, "UPDATE exam_rounds SET title = 'Renamed' WHERE id = :r", {"r": f.round})


@pytest.mark.asyncio
async def test_unpublish_is_allowed_with_no_attempts(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await _allowed(db, "UPDATE exam_rounds SET status = 'draft' WHERE id = :r", {"r": f.round})


@pytest.mark.asyncio
async def test_unpublish_is_refused_once_taken(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " attempt_no, status, started_at, created_at, updated_at)"
            " VALUES (gen_random_uuid(), :c, :x, :r, :a, 1, 'submitted', now(), now(), now())"
        ),
        {"c": f.company, "x": f.exam, "r": f.round, "a": f.applicant},
    )
    await _refused(
        db, "UPDATE exam_rounds SET status = 'draft' WHERE id = :r", {"r": f.round},
        "has been taken; it cannot be unpublished",
    )


@pytest.mark.asyncio
async def test_a_published_round_cannot_be_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    await _publish_round(db, f.round)
    await _refused(
        db, "DELETE FROM exam_rounds WHERE id = :r", {"r": f.round},
        "is published and cannot be deleted",
    )


# ===========================================================================
# The duplicate-lineage-in-one-exam index (#12)
# ===========================================================================
@pytest.mark.asyncio
async def test_the_same_bank_lineage_cannot_land_in_one_exam_twice(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"q": qid, "u": f.hr2})
    await db.execute(text(APPROVE_Q), {"q": qid, "u": f.hr3})
    p = {"x": f.exam, "s": f.section, "os": f.other_section, "c": f.company, "q": qid}
    await db.execute(
        text(
            "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
            " correct_index, position, source_bank_question_id, source_bank_root_id,"
            " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'Two plus two?',"
            " '[\"3\",\"4\"]'::jsonb, 1, 0, :q, :q, 1)"
        ),
        p,
    )
    # A DIFFERENT round of the SAME exam, same root — still refused: the index
    # is scoped to the exam, not the round.
    await _refused(
        db,
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, position, source_bank_question_id, source_bank_root_id,"
        " source_bank_version) VALUES (gen_random_uuid(), :x, :os, :c, 'Two plus two?',"
        " '[\"3\",\"4\"]'::jsonb, 1, 0, :q, :q, 1)",
        p,
        "uq_exam_questions_exam_source_root",
    )


# ===========================================================================
# Cross-company isolation
# ===========================================================================
@pytest.mark.asyncio
async def test_a_bank_question_cannot_reference_another_companys_bank(db: AsyncSession) -> None:
    f = await _build(db)
    other_bank = uuid.uuid4()
    await db.execute(
        text("INSERT INTO question_banks (id, company_id, name, created_at, updated_at)"
             " VALUES (:b, :oc, 'Other bank', now(), now())"),
        {"b": other_bank, "oc": f.other_company},
    )
    qid = uuid.uuid4()
    await _refused(
        db,
        "INSERT INTO bank_questions (id, company_id, bank_id, root_id, version, kind, prompt,"
        " points, options, correct_index, difficulty, language, status, origin, content_hash,"
        " created_at, updated_at) VALUES (:i, :c, :b, :i, 1, 'mcq', 'x', 1,"
        " '[\"a\",\"b\"]'::jsonb, 0, 'easy', 'en', 'draft', 'authored', repeat('a', 64),"
        " now(), now())",
        {"i": qid, "c": f.company, "b": other_bank},
        "violates foreign key constraint",
    )


@pytest.mark.asyncio
async def test_an_exam_question_cannot_source_another_companys_bank_question(db: AsyncSession) -> None:
    f = await _build(db)
    other_q = uuid.uuid4()
    other_bank = uuid.uuid4()
    other_hr, other_hr2, other_hr3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    otag = f.other_company.hex[:8]
    for sql in (
        "INSERT INTO users (id, email, company_id) VALUES (:h1, :m1, :oc), (:h2, :m2, :oc),"
        " (:h3, :m3, :oc)",
        "INSERT INTO question_banks (id, company_id, name, created_by_user_id, created_at,"
        " updated_at) VALUES (:b, :oc, 'Other bank', :h1, now(), now())",
    ):
        await db.execute(text(sql), {
            "h1": other_hr, "h2": other_hr2, "h3": other_hr3, "oc": f.other_company, "b": other_bank,
            "m1": f"oh1-{otag}@d1.test", "m2": f"oh2-{otag}@d1.test", "m3": f"oh3-{otag}@d1.test",
        })
    await db.execute(
        text(
            "INSERT INTO bank_questions (id, company_id, bank_id, root_id, version, kind, prompt,"
            " points, options, correct_index, difficulty, language, status, origin, content_hash,"
            " created_by_user_id, created_at, updated_at) VALUES (:i, :oc, :b, :i, 1, 'mcq', 'x', 1,"
            " '[\"a\",\"b\"]'::jsonb, 0, 'easy', 'en', 'draft', 'authored', repeat('c', 64),"
            " :h1, now(), now())"
        ),
        {"i": other_q, "oc": f.other_company, "b": other_bank, "h1": other_hr},
    )
    await db.execute(text(SUBMIT_Q), {"q": other_q, "u": other_hr2})
    await db.execute(text(APPROVE_Q), {"q": other_q, "u": other_hr3})
    # T2's own check catches this before the composite FK ever gets a chance to
    # (a BEFORE trigger runs before the FK's constraint trigger) — a clearer
    # message for the same guarantee: a cross-tenant reference is impossible.
    await _refused(
        db,
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, position, source_bank_question_id, source_bank_root_id,"
        " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'x', '[\"a\",\"b\"]'::jsonb,"
        " 0, 1, :q, :q, 1)",
        {"x": f.exam, "s": f.section, "c": f.company, "q": other_q},
        "belongs to another company",
    )
    # And if T2 were ever bypassed (a raw write with the trigger disabled), the
    # composite FK (source_bank_question_id, company_id) -> bank_questions(id,
    # company_id) is the backstop — proved directly, trigger off, in this same
    # transaction (rolled back either way, so no other session ever sees it).
    await db.execute(text("ALTER TABLE exam_questions DISABLE TRIGGER exam_round_content_frozen"))
    await _refused(
        db,
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, position, source_bank_question_id, source_bank_root_id,"
        " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'x', '[\"a\",\"b\"]'::jsonb,"
        " 0, 1, :q, :q, 1)",
        {"x": f.exam, "s": f.section, "c": f.company, "q": other_q},
        "violates foreign key constraint",
    )
    await db.execute(text("ALTER TABLE exam_questions ENABLE TRIGGER exam_round_content_frozen"))


# ===========================================================================
# Append-only events
# ===========================================================================
@pytest.mark.asyncio
async def test_bank_question_events_are_append_only(db: AsyncSession) -> None:
    f = await _build(db)
    qid = await _new_draft(db, f)
    ev = uuid.uuid4()
    await db.execute(
        text("INSERT INTO bank_question_events (id, company_id, bank_question_id, action,"
             " actor_user_id) VALUES (:i, :c, :q, 'created', :u)"),
        {"i": ev, "c": f.company, "q": qid, "u": f.hr},
    )
    await _refused(db, "UPDATE bank_question_events SET action = 'retired' WHERE id = :i",
                   {"i": ev}, "append-only")
    await _refused(db, "DELETE FROM bank_question_events WHERE id = :i", {"i": ev}, "append-only")


# ===========================================================================
# The review is judged against the row as it stands (security review W5-L1/L2)
# ===========================================================================
@pytest.mark.asyncio
async def test_a_submitter_cannot_rewrite_the_record_and_approve_in_one_breath(
    db: AsyncSession,
) -> None:
    """The check used to read the incoming row, so one statement could name
    somebody else as the submitter and approve at the same time."""
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"u": f.hr2, "q": qid})
    await _refused(
        db,
        "UPDATE bank_questions SET status = 'approved', reviewed_by_user_id = :me,"
        " submitted_by_user_id = :other, reviewed_at = now() WHERE id = :q",
        {"me": f.hr2, "other": f.hr, "q": qid},
        "keeps who submitted it",
    )
    # Nor quietly, without approving.
    await _refused(db, "UPDATE bank_questions SET submitted_by_user_id = :other WHERE id = :q",
                   {"other": f.hr, "q": qid}, "keeps who submitted it")
    # The author cannot approve either, however the row is dressed up.
    await _refused(db, APPROVE_Q, {"u": f.hr, "q": qid}, "someone other than its author")
    await _allowed(db, APPROVE_Q, {"u": f.hr3, "q": qid})


@pytest.mark.asyncio
async def test_a_copied_question_keeps_the_provenance_it_was_made_with(db: AsyncSession) -> None:
    """Where a copy came from is checked when it is made; repointing it later
    could only misstate it, so it is refused."""
    f = await _build(db)
    qid = await _new_draft(db, f)
    await db.execute(text(SUBMIT_Q), {"u": f.hr2, "q": qid})
    await db.execute(text(APPROVE_Q), {"u": f.hr3, "q": qid})
    copy = uuid.uuid4()
    await db.execute(
        text("INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
             " correct_index, position, source_bank_question_id, source_bank_root_id,"
             " source_bank_version) VALUES (:i, :x, :s, :c, 'x', '[\"a\",\"b\"]'::jsonb,"
             " 0, 1, :q, :q, 1)"),
        {"i": copy, "x": f.exam, "s": f.section, "c": f.company, "q": qid},
    )
    draft = await _new_draft(db, f)
    await _refused(db, "UPDATE exam_questions SET source_bank_question_id = :d WHERE id = :i",
                   {"d": draft, "i": copy}, "keeps the bank provenance")
    await _refused(db, "UPDATE exam_questions SET source_bank_version = 9 WHERE id = :i",
                   {"i": copy}, "keeps the bank provenance")
