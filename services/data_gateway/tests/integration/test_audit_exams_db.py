"""Audit wave 2, exams area: the server-side fixes, against a real Postgres.

Each test names the audit finding it pins. The handlers are called directly
with a session whose ``commit()`` is a savepoint release inside one outer
transaction that is always rolled back, so nothing is left behind and the
real triggers (``exam_rounds_frozen`` and friends) still run.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from shared.db.engine import build_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import exam_locks
from app import question_banks as bank_svc
from app.config import settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            session = AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                await outer.rollback()
    finally:
        await engine.dispose()


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.other_company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.exam = uuid.uuid4()
        self.r1 = uuid.uuid4()
        self.r2 = uuid.uuid4()
        self.s1 = uuid.uuid4()
        self.s2 = uuid.uuid4()
        self.applicant = uuid.uuid4()

    @property
    def ctx(self) -> tuple[uuid.UUID, uuid.UUID]:
        return self.hr, self.company


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "oc": f.other_company, "h": f.hr, "x": f.exam, "r1": f.r1,
        "r2": f.r2, "s1": f.s1, "s2": f.s2, "a": f.applicant,
        "slug": f"ax-{tag}", "oslug": f"axo-{tag}", "m": f"h-{tag}@ax.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'AX co', :slug),"
        " (:oc, 'AX other', :oslug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m, :c)",
        "INSERT INTO exams (id, company_id, title, status) VALUES (:x, :c, 'Exam', 'draft')",
        "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, status,"
        " position, pass_threshold) VALUES (:r1, :x, :c, 1, 'Round 1', 'draft', 1, 60),"
        " (:r2, :x, :c, 2, 'Round 2', 'draft', 2, 75)",
        "INSERT INTO exam_sections (id, round_id, exam_id, company_id, title, kind, position)"
        " VALUES (:s1, :r1, :x, :c, 'MCQ 1', 'mcq', 1), (:s2, :r2, :x, :c, 'MCQ 2', 'mcq', 1)",
        "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
        " correct_index, points, position) VALUES"
        " (gen_random_uuid(), :x, :s1, :c, 'Q1', '[\"a\",\"b\"]'::jsonb, 0, 1, 0)",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer')",
    ):
        await db.execute(text(sql), p)
    return f


async def _publish(db: AsyncSession, f: F, round_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE exam_rounds SET status = 'published' WHERE id = :r"), {"r": round_id}
    )
    await db.execute(text("UPDATE exams SET status = 'published' WHERE id = :x"), {"x": f.exam})


async def _attempt(db: AsyncSession, f: F, round_id: uuid.UUID, **over: Any) -> uuid.UUID:
    aid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " attempt_no, status, started_at, created_at, updated_at, score_percent, passed)"
            " VALUES (:i, :c, :x, :r, :a, 1, :st, now(), now(), now(), :sp, :p)"
        ),
        {"i": aid, "c": f.company, "x": f.exam, "r": round_id, "a": f.applicant,
         "st": over.get("status", "submitted"), "sp": over.get("score"),
         "p": over.get("passed")},
    )
    return aid


def _csv(*rows: str) -> UploadFile:
    body = "Question,Option A,Option B,Option C,Option D,Correct,Points\n" + "\n".join(rows)
    return UploadFile(file=io.BytesIO(body.encode()), filename="q.csv")


async def _count(db: AsyncSession, section: uuid.UUID) -> int:
    n = await db.scalar(
        text("SELECT count(*) FROM exam_questions WHERE section_id = :s AND deleted_at IS NULL"),
        {"s": section},
    )
    return int(n or 0)


# ===========================================================================
# E1: the import lands in the section it was made from
# ===========================================================================
@pytest.mark.asyncio
async def test_section_import_writes_into_that_section_even_when_round_one_is_published(
    db: AsyncSession,
) -> None:
    from app.routers.hr_rounds import import_section_questions

    f = await _build(db)
    await _publish(db, f, f.r1)  # the legacy route checked THIS lock and refused

    out = await import_section_questions(
        f.exam, f.s2, f.ctx, db, _csv("Two plus two?,3,4,5,6,B,1", "Capital?,Paris,Rome,x,y,A,2")
    )

    assert out.added == 2
    assert await _count(db, f.s2) == 2
    assert await _count(db, f.s1) == 1  # round 1 untouched


@pytest.mark.asyncio
async def test_section_import_checks_the_lock_of_its_own_round(db: AsyncSession) -> None:
    from app.routers.hr_rounds import import_section_questions

    f = await _build(db)
    await db.execute(text("UPDATE exam_rounds SET status = 'published' WHERE id = :r"),
                     {"r": f.r2})

    with pytest.raises(HTTPException) as exc:
        await import_section_questions(f.exam, f.s2, f.ctx, db, _csv("Q,a,b,c,d,A,1"))
    assert exc.value.status_code == 409
    assert exc.value.detail == exam_locks.PUBLISHED_REASON


@pytest.mark.asyncio
async def test_section_import_is_tenant_scoped(db: AsyncSession) -> None:
    from app.routers.hr_rounds import import_section_questions

    f = await _build(db)
    with pytest.raises(HTTPException) as exc:
        await import_section_questions(
            f.exam, f.s2, (uuid.uuid4(), f.other_company), db, _csv("Q,a,b,c,d,A,1")
        )
    assert exc.value.status_code == 404
    assert await _count(db, f.s2) == 0


# ===========================================================================
# E2 / E10: unpublishing
# ===========================================================================
@pytest.mark.asyncio
async def test_unpublishing_a_taken_round_says_why(db: AsyncSession) -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    f = await _build(db)
    await _publish(db, f, f.r1)
    await _attempt(db, f, f.r1)

    with pytest.raises(HTTPException) as exc:
        await update_round(f.exam, f.r1, RoundUpdateIn(status="draft"), f.ctx, db)
    assert exc.value.status_code == 409
    # Not RACE_REASON ("reload and try again"), which reloading never fixes.
    assert exc.value.detail == exam_locks.UNPUBLISH_TAKEN_REASON


@pytest.mark.asyncio
async def test_unpublishing_the_last_live_round_returns_the_exam_to_draft(
    db: AsyncSession,
) -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    f = await _build(db)
    await _publish(db, f, f.r1)

    out = await update_round(f.exam, f.r1, RoundUpdateIn(status="draft"), f.ctx, db)

    assert out.status == "draft"
    assert await db.scalar(text("SELECT status FROM exams WHERE id = :x"), {"x": f.exam}) == "draft"


@pytest.mark.asyncio
async def test_unpublishing_one_of_two_live_rounds_keeps_the_exam_live(db: AsyncSession) -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    f = await _build(db)
    await db.execute(
        text("INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
             " correct_index, points, position) VALUES (gen_random_uuid(), :x, :s, :c, 'Q2',"
             " '[\"a\",\"b\"]'::jsonb, 0, 1, 0)"),
        {"x": f.exam, "s": f.s2, "c": f.company},
    )
    await _publish(db, f, f.r1)
    await _publish(db, f, f.r2)

    await update_round(f.exam, f.r1, RoundUpdateIn(status="draft"), f.ctx, db)

    status = await db.scalar(text("SELECT status FROM exams WHERE id = :x"), {"x": f.exam})
    assert status == "published"


@pytest.mark.asyncio
async def test_unpublishing_never_reopens_a_closed_exam(db: AsyncSession) -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    f = await _build(db)
    await _publish(db, f, f.r1)
    await db.execute(text("UPDATE exams SET status = 'closed' WHERE id = :x"), {"x": f.exam})

    await update_round(f.exam, f.r1, RoundUpdateIn(status="draft"), f.ctx, db)

    assert await db.scalar(text("SELECT status FROM exams WHERE id = :x"), {"x": f.exam}) == "closed"


@pytest.mark.asyncio
async def test_the_structure_read_carries_each_rounds_attempt_count(db: AsyncSession) -> None:
    from app.routers.hr_rounds import get_structure

    f = await _build(db)
    await _publish(db, f, f.r1)
    await _attempt(db, f, f.r1)

    out = await get_structure(f.exam, f.ctx, db)

    counts = {r.id: r.attempt_count for r in out.rounds}
    assert counts == {str(f.r1): 1, str(f.r2): 0}


# ===========================================================================
# E11: a time limit can be cleared
# ===========================================================================
@pytest.mark.asyncio
async def test_a_round_time_limit_can_be_cleared(db: AsyncSession) -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    f = await _build(db)
    await db.execute(text("UPDATE exam_rounds SET time_limit_seconds = 600 WHERE id = :r"),
                     {"r": f.r2})

    out = await update_round(f.exam, f.r2, RoundUpdateIn(time_limit_seconds=None), f.ctx, db)
    assert out.time_limit_seconds is None

    # ...and an update that does not mention it leaves it alone.
    await update_round(f.exam, f.r2, RoundUpdateIn(time_limit_seconds=900), f.ctx, db)
    out = await update_round(f.exam, f.r2, RoundUpdateIn(title="Renamed"), f.ctx, db)
    assert out.time_limit_seconds == 900


@pytest.mark.asyncio
async def test_clearing_a_published_rounds_time_limit_is_refused(db: AsyncSession) -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    f = await _build(db)
    await db.execute(text("UPDATE exam_rounds SET time_limit_seconds = 600 WHERE id = :r"),
                     {"r": f.r1})
    await _publish(db, f, f.r1)

    with pytest.raises(HTTPException) as exc:
        await update_round(f.exam, f.r1, RoundUpdateIn(time_limit_seconds=None), f.ctx, db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_an_exam_time_limit_can_be_cleared_and_the_exam_closed(db: AsyncSession) -> None:
    from app.routers.hr_exams import ExamUpdateIn, update_exam

    f = await _build(db)
    await db.execute(text("UPDATE exams SET time_limit_seconds = 600 WHERE id = :x"),
                     {"x": f.exam})

    out = await update_exam(f.exam, ExamUpdateIn(time_limit_seconds=None), f.ctx, db)
    assert out.time_limit_seconds is None

    out = await update_exam(f.exam, ExamUpdateIn(status="closed", allow_retake=True), f.ctx, db)
    assert out.status == "closed"
    assert out.allow_retake is True


# ===========================================================================
# E8 / E12 / E25: results and assignments carry the round
# ===========================================================================
@pytest.mark.asyncio
async def test_attempts_carry_their_round_and_its_pass_mark(db: AsyncSession) -> None:
    from app.routers.hr_exams import list_attempts

    f = await _build(db)
    await _publish(db, f, f.r1)
    await _publish(db, f, f.r2)
    await _attempt(db, f, f.r2, score=70, passed=False)

    rows = await list_attempts(f.exam, f.ctx, db)

    assert len(rows) == 1
    assert rows[0].round_id == str(f.r2)
    assert rows[0].round_title == "Round 2"
    assert rows[0].round_number == 2
    assert rows[0].round_pass_threshold == 75


@pytest.mark.asyncio
async def test_assignments_carry_their_round(db: AsyncSession) -> None:
    from app.routers.hr_exams import list_assignments

    f = await _build(db)
    aid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
            " created_by_user_id, token_hash, expires_at, status, created_at, updated_at)"
            " VALUES (:i, :c, :x, :r, :a, :h, :t, now() + interval '1 day', 'invited',"
            " now(), now())"
        ),
        {"i": aid, "c": f.company, "x": f.exam, "r": f.r2, "a": f.applicant, "h": f.hr,
         "t": uuid.uuid4().hex},
    )

    rows = await list_assignments(f.exam, f.ctx, db)

    assert [(r.assignment_id, r.round_id) for r in rows] == [(str(aid), str(f.r2))]


@pytest.mark.asyncio
async def test_an_attempt_is_located_in_one_request_and_only_in_its_company(
    db: AsyncSession,
) -> None:
    from app.routers.hr_exams import locate_attempt

    f = await _build(db)
    aid = await _attempt(db, f, f.r1)

    out = await locate_attempt(aid, f.ctx, db)
    assert out.exam_id == str(f.exam)

    with pytest.raises(HTTPException) as exc:
        await locate_attempt(aid, (uuid.uuid4(), f.other_company), db)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await locate_attempt(uuid.uuid4(), f.ctx, db)
    assert exc.value.status_code == 404


# ===========================================================================
# E15: "Used in N exams" counts distinct live exams
# ===========================================================================
async def _approved_bank(db: AsyncSession, f: F, n: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A bank of *n* approved MCQ questions, each its own lineage, approved the
    way the two-person rule requires (author, then a different submitter, then
    a third reviewer)."""
    bank = uuid.uuid4()
    sub, rev = uuid.uuid4(), uuid.uuid4()
    tag = bank.hex[:10]
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:s, :ms, :c), (:r, :mr, :c)"),
        {"s": sub, "r": rev, "c": f.company, "ms": f"s-{tag}@ax.test", "mr": f"r-{tag}@ax.test"},
    )
    await db.execute(
        text("INSERT INTO question_banks (id, company_id, name, created_by_user_id, created_at,"
             " updated_at) VALUES (:b, :c, 'Bank', :h, now(), now())"),
        {"b": bank, "c": f.company, "h": f.hr},
    )
    ids: list[uuid.UUID] = []
    for i in range(n):
        q = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO bank_questions (id, company_id, bank_id, root_id, version, kind,"
                " prompt, points, options, correct_index, difficulty, language, status, origin,"
                " content_hash, created_by_user_id, created_at, updated_at)"
                " VALUES (:i, :c, :b, :i, 1, 'mcq', :p, 1, '[\"3\",\"4\"]'::jsonb, 1,"
                " 'easy', 'en', 'draft', 'authored', repeat(:h1, 64), :h, now(), now())"
            ),
            {"i": q, "c": f.company, "b": bank, "h": f.hr, "p": f"Question {i}",
             "h1": "abcdef0123"[i]},
        )
        await db.execute(
            text("UPDATE bank_questions SET status = 'in_review', submitted_by_user_id = :u,"
                 " submitted_at = now() WHERE id = :q"), {"q": q, "u": sub},
        )
        await db.execute(
            text("UPDATE bank_questions SET status = 'approved', reviewed_by_user_id = :u,"
                 " reviewed_at = now() WHERE id = :q"), {"q": q, "u": rev},
        )
        ids.append(q)
    return bank, ids


async def _exam_with_section(db: AsyncSession, f: F) -> tuple[uuid.UUID, uuid.UUID]:
    x, r, s = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db.execute(text("INSERT INTO exams (id, company_id, title) VALUES (:x, :c, 'E')"),
                     {"x": x, "c": f.company})
    await db.execute(
        text("INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, status,"
             " position) VALUES (:r, :x, :c, 1, 'R', 'draft', 1)"),
        {"r": r, "x": x, "c": f.company},
    )
    await db.execute(
        text("INSERT INTO exam_sections (id, round_id, exam_id, company_id, title, kind, position)"
             " VALUES (:s, :r, :x, :c, 'S', 'mcq', 1)"),
        {"s": s, "r": r, "x": x, "c": f.company},
    )
    return x, s


async def _copy(db: AsyncSession, f: F, bq: uuid.UUID, exam: uuid.UUID,
                section: uuid.UUID) -> None:
    await db.execute(
        text(
            "INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
            " correct_index, position, source_bank_question_id, source_bank_root_id,"
            " source_bank_version) VALUES (gen_random_uuid(), :x, :s, :c, 'copy',"
            " '[\"3\",\"4\"]'::jsonb, 1, 100 + (SELECT count(*) FROM exam_questions"
            " WHERE section_id = :s), :q, :q, 1)"
        ),
        {"x": exam, "s": section, "c": f.company, "q": bq},
    )


@pytest.mark.asyncio
async def test_bank_usage_counts_distinct_live_exams_not_copied_rows(db: AsyncSession) -> None:
    f = await _build(db)
    bank, qs = await _approved_bank(db, f, 3)
    # Three questions from the bank copied into ONE exam, over two sections.
    await _copy(db, f, qs[0], f.exam, f.s1)
    await _copy(db, f, qs[1], f.exam, f.s1)
    await _copy(db, f, qs[2], f.exam, f.s2)
    # A copy in an exam that is later deleted, and one under a section that is
    # later deleted: neither exam uses the bank any more.
    gone_exam, gone_sec = await _exam_with_section(db, f)
    await _copy(db, f, qs[0], gone_exam, gone_sec)
    await db.execute(text("UPDATE exams SET deleted_at = now() WHERE id = :x"), {"x": gone_exam})
    other_exam, dead_sec = await _exam_with_section(db, f)
    await _copy(db, f, qs[1], other_exam, dead_sec)
    await db.execute(text("UPDATE exam_sections SET deleted_at = now() WHERE id = :s"),
                     {"s": dead_sec})

    one = await bank_svc.bank_summary(db, company_id=f.company, bank_id=bank)
    listed = {b["id"]: b for b in await bank_svc.list_banks(db, company_id=f.company)}

    # Was 5 (copied rows, deleted exam and section included).
    assert one["used_in_exams"] == 1
    assert listed[str(bank)]["used_in_exams"] == 1


@pytest.mark.asyncio
async def test_deleting_a_round_retires_its_questions_too(db: AsyncSession) -> None:
    """Deleting a round soft-deleted its sections but left their questions live,
    so the exam list's question count and a bank's usage still counted them."""
    from app.routers.hr_rounds import delete_round

    f = await _build(db)
    await db.execute(
        text("INSERT INTO exam_questions (id, exam_id, section_id, company_id, prompt, options,"
             " correct_index, points, position) VALUES (gen_random_uuid(), :x, :s, :c, 'Q2',"
             " '[\"a\",\"b\"]'::jsonb, 0, 1, 0)"),
        {"x": f.exam, "s": f.s2, "c": f.company},
    )

    await delete_round(f.exam, f.r2, f.ctx, db)

    assert await _count(db, f.s2) == 0
    assert await _count(db, f.s1) == 1
