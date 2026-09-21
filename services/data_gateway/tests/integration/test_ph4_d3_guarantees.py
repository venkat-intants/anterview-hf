"""PH4-D3: the code-evidence guarantees that hold AT THE DATABASE.

``code_quality_reports`` / ``code_fingerprints`` / ``code_similarity_signals``
are SYSTEM evidence: born once, immutable forever (UPDATE refused), DELETE
kept legal for retention and erasure. ``code_integrity_findings`` is the only
one of the four a PERSON writes to, and it can change only by a controlled
redaction, a signal being cleared, or a one-time supersede — never a verdict
rewrite. A submitted/expired ``exam_attempts`` row is frozen except for the
one redaction shape retention/erasure use. Composite FKs make a cross-tenant
signal unrepresentable.

Against a real, migrated Postgres. Every refusal is checked for its REASON.
"""

from __future__ import annotations

import json
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
        self.hr = uuid.uuid4()
        self.applicant_a = uuid.uuid4()
        self.applicant_b = uuid.uuid4()
        self.other_applicant = uuid.uuid4()
        self.exam = uuid.uuid4()
        self.round = uuid.uuid4()
        self.section = uuid.uuid4()
        self.question = uuid.uuid4()
        self.other_exam = uuid.uuid4()
        self.other_round = uuid.uuid4()
        self.other_section = uuid.uuid4()
        self.other_question = uuid.uuid4()
        self.attempt_a = uuid.uuid4()
        self.attempt_b = uuid.uuid4()
        self.other_attempt = uuid.uuid4()
        # ck_code_similarity_signals_pair_order needs attempt_low_id <
        # attempt_high_id -- a REAL ordering on two random UUIDs, not a coin
        # flip. Sorted once here so every signal fixture gets a deterministic
        # pair instead of failing the CHECK about half the time.
        self.attempt_low, self.attempt_high = sorted([self.attempt_a, self.attempt_b])
        self.cross_low, self.cross_high = sorted([self.attempt_a, self.other_attempt])


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    otag = f.other_company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "oc": f.other_company, "h": f.hr,
        "a": f.applicant_a, "b": f.applicant_b, "oa": f.other_applicant,
        "x": f.exam, "r": f.round, "s": f.section, "q": f.question,
        "ox": f.other_exam, "or_": f.other_round, "os": f.other_section, "oq": f.other_question,
        "ata": f.attempt_a, "atb": f.attempt_b, "oat": f.other_attempt,
        "slug": f"d3-{tag}", "oslug": f"d3o-{otag}", "m1": f"h-{tag}@d3.test",
        "langs": json.dumps(["python"]),
        "tc": json.dumps([{"stdin": "1 2", "expected_output": "3", "is_sample": True, "weight": 1}]),
        "otc": json.dumps([{"stdin": "1", "expected_output": "1", "is_sample": True, "weight": 1}]),
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'D3 co', :slug),"
        " (:oc, 'D3 other', :oslug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c)",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer'), (:b, :c, 'Bilal', 'Engineer'),"
        " (:oa, :oc, 'Chetan', 'Engineer')",
        "INSERT INTO exams (id, company_id, title, kind)"
        " VALUES (:x, :c, 'Coding exam', 'coding'), (:ox, :oc, 'Other exam', 'coding')",
        # 'draft', not 'published': D1's exam_round_content_frozen trigger
        # refuses inserting sections/questions into a published round, and
        # these fixtures only need the round to exist for the FK chain.
        "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, status, position)"
        " VALUES (:r, :x, :c, 1, 'Round', 'draft', 0),"
        "        (:or_, :ox, :oc, 1, 'Other round', 'draft', 0)",
        "INSERT INTO exam_sections (id, round_id, exam_id, company_id, title, kind, position)"
        " VALUES (:s, :r, :x, :c, 'Coding', 'coding', 0),"
        "        (:os, :or_, :ox, :oc, 'Coding', 'coding', 0)",
        # Bound params for the JSONB payloads, not inline literals: a literal
        # containing `"is_sample":true` has a bare colon that SQLAlchemy's
        # text() reads as a (nonexistent) bind parameter named `true`.
        "INSERT INTO coding_questions (id, exam_id, section_id, company_id, prompt,"
        " allowed_languages, test_cases, time_limit_ms, points, position)"
        " VALUES (:q, :x, :s, :c, 'Add two numbers', CAST(:langs AS jsonb),"
        "         CAST(:tc AS jsonb), 2000, 100, 0),"
        "        (:oq, :ox, :os, :oc, 'Other question', CAST(:langs AS jsonb),"
        "         CAST(:otc AS jsonb), 2000, 100, 0)",
        "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id, attempt_no,"
        " status, started_at, submitted_at, score_raw, score_max, score_percent, passed,"
        " created_at, updated_at)"
        " VALUES"
        " (:ata, :c, :x, :r, :a, 1, 'submitted', now(), now(), 100, 100, 100, true, now(), now()),"
        " (:atb, :c, :x, :r, :b, 1, 'submitted', now(), now(), 100, 100, 100, true, now(), now()),"
        " (:oat, :oc, :ox, :or_, :oa, 1, 'submitted', now(), now(), 100, 100, 100, true, now(), now())",
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
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:120]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


async def _fk_violated(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        assert "foreign key" in str(exc.orig).lower(), str(exc.orig)[:300]
        return
    await sp.rollback()
    pytest.fail("expected a foreign key violation")


def _report_insert(f: F, rid: uuid.UUID, *, attempt_id: uuid.UUID | None = None,
                    coding_question_id: uuid.UUID | None = None,
                    company_id: uuid.UUID | None = None) -> tuple[str, dict[str, Any]]:
    # coverage is a bound param, not an inline literal: a JSON value with a
    # bare `:` followed by a word (`"available":false`) is read by
    # SQLAlchemy's text() as a (nonexistent) bind parameter named `false`.
    sql = (
        "INSERT INTO code_quality_reports (id, company_id, attempt_id, coding_question_id, exam_id,"
        " language, analyser, analyser_version, status, metrics, findings, coverage, source_sha256,"
        " created_at)"
        " VALUES (:i, :c, :a, :q, :x, 'python', 'python-ast', 'v1', 'complete', '{}'::jsonb,"
        " '[]'::jsonb, CAST(:cov AS jsonb), 'deadbeef', now())"
    )
    return sql, {
        "i": rid, "c": company_id or f.company, "a": attempt_id or f.attempt_a,
        "q": coding_question_id or f.question, "x": f.exam,
        "cov": json.dumps({"available": False}),
    }


def _fingerprint_insert(f: F, fid: uuid.UUID, *, attempt_id: uuid.UUID | None = None,
                        company_id: uuid.UUID | None = None) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO code_fingerprints (id, company_id, attempt_id, coding_question_id, hashes,"
        " lines, token_count, algorithm_version, created_at)"
        " VALUES (:i, :c, :a, :q, ARRAY[1,2,3]::bigint[], ARRAY[1,2,3]::int[], 60, 'v1', now())"
    )
    return sql, {
        "i": fid, "c": company_id or f.company, "a": attempt_id or f.attempt_a, "q": f.question,
    }


def _signal_insert(
    f: F, sid: uuid.UUID, *, low: uuid.UUID | None = None, high: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None, reference_kind: str = "submission",
) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO code_similarity_signals (id, company_id, coding_question_id, exam_id,"
        " attempt_low_id, attempt_high_id, reference_kind, containment_low, containment_high,"
        " jaccard, shared_fingerprints, tokens_low, tokens_high, matched_regions, thresholds,"
        " algorithm_version, created_at)"
        " VALUES (:i, :c, :q, :x, :low, :high, :rk, 0.9, :ch, 0.8, 12, 60, :th, '[]'::jsonb,"
        " '{}'::jsonb, 'v1', now())"
    )
    low_id = low if low is not None else f.attempt_low
    high_id = high if high is not None else (f.attempt_high if reference_kind == "submission" else None)
    return sql, {
        "i": sid, "c": company_id or f.company, "q": f.question, "x": f.exam,
        "low": low_id, "high": high_id, "rk": reference_kind,
        "ch": 0.7 if high_id is not None else None, "th": 60 if high_id is not None else None,
    }


def _finding_insert(
    f: F, fid: uuid.UUID, *, attempt_id: uuid.UUID | None = None, rationale: str = "x" * 30,
    company_id: uuid.UUID | None = None, recorder: uuid.UUID | None = None,
    superseded_at: str | None = None, redacted_at: str | None = None,
    signal_id: uuid.UUID | None = None,
) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO code_integrity_findings (id, company_id, attempt_id, coding_question_id,"
        " signal_id, outcome, rationale, recorded_by_user_id, superseded_at, redacted_at,"
        " created_at)"
        " VALUES (:i, :c, :a, :q, :sig, 'follow_up', :r, :rec, :sat, :rdat, now())"
    )
    return sql, {
        "i": fid, "c": company_id or f.company, "a": attempt_id or f.attempt_a, "q": f.question,
        "sig": signal_id, "r": rationale, "rec": recorder or f.hr, "sat": superseded_at,
        "rdat": redacted_at,
    }


async def _new_finding(db: AsyncSession, f: F, **kw: Any) -> uuid.UUID:
    fid = uuid.uuid4()
    sql, params = _finding_insert(f, fid, **kw)
    await db.execute(text(sql), params)
    return fid


# ===========================================================================
# code_quality_reports / code_fingerprints / code_similarity_signals:
# immutable once written; DELETE stays legal
# ===========================================================================
@pytest.mark.asyncio
async def test_a_report_is_allowed_once(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _report_insert(f, uuid.uuid4())
    await _allowed(db, sql, params)


@pytest.mark.asyncio
async def test_a_report_cannot_be_updated(db: AsyncSession) -> None:
    f = await _build(db)
    rid = uuid.uuid4()
    sql, params = _report_insert(f, rid)
    await db.execute(text(sql), params)
    await _refused(
        db, "UPDATE code_quality_reports SET status = 'failed' WHERE id = :i", {"i": rid},
        "immutable once written",
    )


@pytest.mark.asyncio
async def test_a_report_delete_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    rid = uuid.uuid4()
    sql, params = _report_insert(f, rid)
    await db.execute(text(sql), params)
    await _allowed(db, "DELETE FROM code_quality_reports WHERE id = :i", {"i": rid})


@pytest.mark.asyncio
async def test_a_fingerprint_cannot_be_updated(db: AsyncSession) -> None:
    f = await _build(db)
    fid = uuid.uuid4()
    sql, params = _fingerprint_insert(f, fid)
    await db.execute(text(sql), params)
    await _refused(
        db, "UPDATE code_fingerprints SET token_count = 1 WHERE id = :i", {"i": fid},
        "immutable once written",
    )


@pytest.mark.asyncio
async def test_a_fingerprint_delete_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    fid = uuid.uuid4()
    sql, params = _fingerprint_insert(f, fid)
    await db.execute(text(sql), params)
    await _allowed(db, "DELETE FROM code_fingerprints WHERE id = :i", {"i": fid})


@pytest.mark.asyncio
async def test_a_signal_cannot_be_updated(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)
    await db.execute(text(sql), params)
    await _refused(
        db, "UPDATE code_similarity_signals SET jaccard = 0.1 WHERE id = :i", {"i": sid},
        "immutable once written",
    )


@pytest.mark.asyncio
async def test_a_signal_delete_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)
    await db.execute(text(sql), params)
    await _allowed(db, "DELETE FROM code_similarity_signals WHERE id = :i", {"i": sid})


# ===========================================================================
# code_similarity_signals: pair ordering, reference-solution shape, the
# unique-per-pair index
# ===========================================================================
@pytest.mark.asyncio
async def test_a_submission_pair_needs_low_before_high(db: AsyncSession) -> None:
    f = await _build(db)
    # Deliberately swapped from the deterministic (sorted) order, so this is
    # guaranteed invalid regardless of which random UUID either attempt got.
    sql, params = _signal_insert(f, uuid.uuid4(), low=f.attempt_high, high=f.attempt_low)
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        assert "ck_code_similarity_signals_pair_order" in str(exc.orig)
        return
    await sp.rollback()
    pytest.fail("expected the pair-order CHECK to fire")


@pytest.mark.asyncio
async def test_a_reference_solution_signal_has_no_high_attempt(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _signal_insert(f, uuid.uuid4(), reference_kind="reference_solution")
    await _allowed(db, sql, params)


@pytest.mark.asyncio
async def test_the_same_pair_cannot_be_recorded_twice(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _signal_insert(f, uuid.uuid4())
    await db.execute(text(sql), params)
    sql2, params2 = _signal_insert(f, uuid.uuid4())
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql2), params2)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        assert "uq_code_similarity_signals_pair" in str(exc.orig)
        return
    await sp.rollback()
    pytest.fail("expected the duplicate-pair unique index to fire")


@pytest.mark.asyncio
async def test_a_cross_tenant_signal_pair_is_unrepresentable(db: AsyncSession) -> None:
    """Both attempt FKs resolve through the SAME company_id column on this
    row — a cross-tenant pair cannot exist, not merely get filtered out."""
    f = await _build(db)
    # Deterministically ordered (cross_low < cross_high) so the pair-order
    # CHECK passes and only the cross-tenant FK is exercised.
    sql, params = _signal_insert(f, uuid.uuid4(), low=f.cross_low, high=f.cross_high)
    await _fk_violated(db, sql, params)


@pytest.mark.asyncio
async def test_a_cross_tenant_question_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _report_insert(f, uuid.uuid4(), coding_question_id=f.other_question)
    await _fk_violated(db, sql, params)


# ===========================================================================
# code_integrity_findings — HUMAN evidence
# ===========================================================================
@pytest.mark.asyncio
async def test_a_finding_must_arrive_not_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _finding_insert(f, uuid.uuid4(), redacted_at="now()")
    # redacted_at is passed as a literal string 'now()' via bind param, which
    # Postgres treats as a plain (non-NULL) text-cast timestamp value here --
    # what matters is only that it is NOT NULL on arrival.
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(
                "INSERT INTO code_integrity_findings (id, company_id, attempt_id,"
                " coding_question_id, outcome, rationale, recorded_by_user_id, redacted_at,"
                " created_at)"
                " VALUES (:i, :c, :a, :q, 'follow_up', :r, :rec, now(), now())"
            ),
            {"i": uuid.uuid4(), "c": f.company, "a": f.attempt_a, "q": f.question,
             "r": "x" * 30, "rec": f.hr},
        )
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        assert "arrives neither redacted nor superseded" in str(exc.orig)
        return
    await sp.rollback()
    pytest.fail("expected the arrival guard to fire")


@pytest.mark.asyncio
async def test_a_well_formed_finding_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _finding_insert(f, uuid.uuid4())
    await _allowed(db, sql, params)


@pytest.mark.asyncio
async def test_the_rationale_length_check_is_enforced(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _finding_insert(f, uuid.uuid4(), rationale="too short")
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        assert "ck_code_integrity_findings_rationale_len" in str(exc.orig)
        return
    await sp.rollback()
    pytest.fail("expected the rationale-length CHECK to fire")


@pytest.mark.asyncio
async def test_a_finding_cannot_change_its_rationale_outside_a_redaction(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await _refused(
        db, "UPDATE code_integrity_findings SET rationale = 'changed my mind, no reason'"
            " WHERE id = :i",
        {"i": fid}, "keeps its scope and content outside a redaction",
    )


@pytest.mark.asyncio
async def test_a_finding_cannot_change_its_outcome_outside_a_redaction(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await _refused(
        db, "UPDATE code_integrity_findings SET outcome = 'confirmed' WHERE id = :i",
        {"i": fid}, "keeps its scope and content outside a redaction",
    )


@pytest.mark.asyncio
async def test_a_redaction_is_allowed_once(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await _allowed(
        db,
        "UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
        " WHERE id = :i",
        {"i": fid},
    )


@pytest.mark.asyncio
async def test_a_redaction_value_must_be_the_fixed_marker(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await _refused(
        db,
        "UPDATE code_integrity_findings SET rationale = 'still readable', redacted_at = now()"
        " WHERE id = :i",
        {"i": fid}, "becomes the fixed marker",
    )


@pytest.mark.asyncio
async def test_once_redacted_a_finding_is_frozen(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await db.execute(
        text(
            "UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
            " WHERE id = :i"
        ),
        {"i": fid},
    )
    await _refused(
        db, "UPDATE code_integrity_findings SET outcome = 'confirmed' WHERE id = :i",
        {"i": fid}, "is redacted and is fixed",
    )


@pytest.mark.asyncio
async def test_signal_id_may_only_become_null(db: AsyncSession) -> None:
    """``signal_id`` is set at INSERT time (``record_finding``'s own
    parameter) — the only legal UPDATE to it afterwards is clearing it to
    NULL, never repointing it at a different signal."""
    f = await _build(db)
    sid1 = uuid.uuid4()
    sql, params = _signal_insert(f, sid1)
    await db.execute(text(sql), params)
    sid2 = uuid.uuid4()
    sql2, params2 = _signal_insert(f, sid2, reference_kind="reference_solution")
    await db.execute(text(sql2), params2)
    fid = await _new_finding(db, f, signal_id=sid1)
    await _refused(
        db, "UPDATE code_integrity_findings SET signal_id = :s WHERE id = :i",
        {"s": sid2, "i": fid}, "keeps which signal it followed up, or clears it",
    )
    await _allowed(
        db, "UPDATE code_integrity_findings SET signal_id = NULL WHERE id = :i", {"i": fid},
    )


@pytest.mark.asyncio
async def test_superseding_is_allowed_once(db: AsyncSession) -> None:
    f = await _build(db)
    old_id = await _new_finding(db, f)
    new_sql, new_params = _finding_insert(f, uuid.uuid4())
    await db.execute(text(new_sql), new_params)
    await _allowed(
        db, "UPDATE code_integrity_findings SET superseded_at = now() WHERE id = :i",
        {"i": old_id},
    )


@pytest.mark.asyncio
async def test_a_finding_cannot_be_superseded_twice(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await db.execute(
        text("UPDATE code_integrity_findings SET superseded_at = now() WHERE id = :i"), {"i": fid},
    )
    await _refused(
        db, "UPDATE code_integrity_findings SET superseded_at = now() WHERE id = :i",
        {"i": fid}, "is already superseded",
    )


@pytest.mark.asyncio
async def test_a_finding_cannot_be_deleted_while_its_attempt_exists(db: AsyncSession) -> None:
    f = await _build(db)
    fid = await _new_finding(db, f)
    await _refused(
        db, "DELETE FROM code_integrity_findings WHERE id = :i", {"i": fid},
        "is kept, not deleted",
    )


@pytest.mark.asyncio
async def test_a_finding_needs_a_named_recorder(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _finding_insert(f, uuid.uuid4())
    params["rec"] = None
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        # recorded_by_user_id is NOT NULL at the column level (a person always
        # records a finding), so this is a not-null violation, not an FK one.
        assert "recorded_by_user_id" in str(exc.orig) and "null" in str(exc.orig).lower()
        return
    await sp.rollback()
    pytest.fail("expected a not-null violation on recorded_by_user_id")


@pytest.mark.asyncio
async def test_a_cross_tenant_finding_attempt_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _finding_insert(f, uuid.uuid4(), attempt_id=f.other_attempt)
    await _fk_violated(db, sql, params)


# ===========================================================================
# exam_attempts: submitted/expired is frozen, except the one redaction shape
# ===========================================================================
@pytest.mark.asyncio
async def test_a_submitted_attempts_score_is_frozen(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, "UPDATE exam_attempts SET score_percent = 0 WHERE id = :i", {"i": f.attempt_a},
        "its graded result is fixed",
    )


@pytest.mark.asyncio
async def test_a_submitted_attempts_answers_are_frozen(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "UPDATE exam_attempts SET answers = '{}'::jsonb WHERE id = :i", {"i": f.attempt_a},
        "its graded result is fixed",
    )


@pytest.mark.asyncio
async def test_the_redaction_shape_is_allowed_once(db: AsyncSession) -> None:
    f = await _build(db)
    await _allowed(
        db,
        "UPDATE exam_attempts SET answers = '{\"coding\":{}}'::jsonb,"
        " graded_snapshot = '{\"coding\":{}}'::jsonb, code_redacted_at = now()"
        " WHERE id = :i",
        {"i": f.attempt_a},
    )


@pytest.mark.asyncio
async def test_a_redaction_cannot_also_touch_the_score(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "UPDATE exam_attempts SET answers = '{}'::jsonb, code_redacted_at = now(),"
        " score_percent = 0 WHERE id = :i",
        {"i": f.attempt_a}, "changes only answers, graded_snapshot and code_redacted_at",
    )


@pytest.mark.asyncio
async def test_an_in_progress_attempts_answers_are_not_yet_frozen(db: AsyncSession) -> None:
    f = await _build(db)
    aid = uuid.uuid4()
    # attempt_no=2: applicant_a already has attempt_no=1 on this round from
    # _build (uq_exam_attempts_round_applicant_no).
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " attempt_no, status, started_at, created_at, updated_at)"
            " VALUES (:i, :c, :x, :r, :a, 2, 'in_progress', now(), now(), now())"
        ),
        {"i": aid, "c": f.company, "x": f.exam, "r": f.round, "a": f.applicant_a},
    )
    await _allowed(
        db, "UPDATE exam_attempts SET answers = '{\"coding\":{}}'::jsonb WHERE id = :i", {"i": aid},
    )


# ===========================================================================
# Retention: only a DECIDED application's coding evidence is ever purged
# ===========================================================================
async def _attempt_on_an_application(
    db: AsyncSession, f: F, *, status: str, decided_days_ago: int | None,
) -> uuid.UUID:
    """An attempt reached the ordinary way -- through an exam assignment that
    belongs to an application -- submitted long ago, with coding answers.

    The fixture above only ever built attempts with NO assignment, which is
    why the broken branch was never exercised: those take the separate
    "no application" branch of the purge predicate.
    """
    req, enr, asg, att = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    p: dict[str, Any] = {
        "req": req, "enr": enr, "asg": asg, "att": att, "c": f.company, "a": f.applicant_a,
        "x": f.exam, "r": f.round, "st": status, "tok": uuid.uuid4().hex,
    }
    for sql in (
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:req, :c, 'Engineer', now(), now())",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " status, created_at, updated_at)"
        " VALUES (:enr, :c, :req, :a, 'Engineer', :st, now(), now())",
        "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
        " enrolment_id, token_hash, expires_at, status, created_at, updated_at)"
        " VALUES (:asg, :c, :x, :r, :a, :enr, :tok, now() + interval '7 days', 'completed',"
        "         now() - interval '400 days', now())",
        "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
        " assignment_id, attempt_no, status, started_at, submitted_at, score_raw, score_max,"
        " score_percent, passed, answers, created_at, updated_at)"
        # attempt 2: the fixture already gave this applicant attempt 1 on this round.
        " VALUES (:att, :c, :x, :r, :a, :asg, 2, 'submitted', now() - interval '400 days',"
        "         now() - interval '400 days', 100, 100, 100, true,"
        "         '{\"coding\": {}}'::jsonb, now() - interval '400 days', now())",
    ):
        await db.execute(text(sql), p)
    if decided_days_ago is not None:
        await db.execute(
            # id is a bigint sequence, not a uuid -- left to its default.
            # A decision carries a reason code (PH4-O4's trigger refuses one
            # without), so the fixture writes one, as the product would.
            text("INSERT INTO stage_transitions (company_id, enrolment_id, to_status,"
                 " automated, occurred_at, reason_code, reason_label)"
                 " VALUES (:c, :enr, :st, false, now() - make_interval(days => :d),"
                 "         'fixture', 'Fixture reason')"),
            {"c": f.company, "enr": enr, "st": status, "d": decided_days_ago},
        )
    return att


async def _purgeable_ids(db: AsyncSession, retention_days: int) -> set[uuid.UUID]:
    from datetime import UTC, datetime, timedelta

    from app.code_evidence import _PURGEABLE_ATTEMPTS_SQL

    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    rows = (await db.execute(text(_PURGEABLE_ATTEMPTS_SQL),
                             {"cutoff": cutoff, "lim": 10_000})).all()
    return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_retention_never_touches_an_application_still_waiting_on_hr(
    db: AsyncSession,
) -> None:
    """Security review HIGH-3. The predicate read COALESCE(decision, 'epoch')
    < cutoff, and 'epoch' < cutoff is TRUE -- so on the first real run every
    application with NO decision yet would have had its source stripped and
    its evidence deleted, before HR had decided anything. Irreversible."""
    f = await _build(db)
    undecided = await _attempt_on_an_application(
        db, f, status="shortlisted", decided_days_ago=None,
    )
    assert undecided not in await _purgeable_ids(db, retention_days=180)


@pytest.mark.asyncio
async def test_retention_purges_a_decision_older_than_the_cutoff(db: AsyncSession) -> None:
    f = await _build(db)
    old = await _attempt_on_an_application(db, f, status="rejected", decided_days_ago=200)
    assert old in await _purgeable_ids(db, retention_days=180)


@pytest.mark.asyncio
async def test_retention_keeps_a_decision_still_inside_the_cutoff(db: AsyncSession) -> None:
    f = await _build(db)
    recent = await _attempt_on_an_application(db, f, status="hired", decided_days_ago=30)
    assert recent not in await _purgeable_ids(db, retention_days=180)


@pytest.mark.asyncio
async def test_retention_ignores_a_decision_that_was_later_reversed(db: AsyncSession) -> None:
    """A rejection long ago, then moved back into the pipeline: the old
    transition exists but the application is live again, so it is not over."""
    f = await _build(db)
    reopened = await _attempt_on_an_application(db, f, status="shortlisted", decided_days_ago=200)
    # The stage_transitions row written above says 'shortlisted', not a
    # decision; add the historical rejection that was later undone.
    enr = await db.scalar(
        text("SELECT e.id FROM exam_attempts a JOIN exam_assignments s ON s.id = a.assignment_id"
             " JOIN enrolments e ON e.id = s.enrolment_id WHERE a.id = :a"),
        {"a": reopened},
    )
    await db.execute(
        text("INSERT INTO stage_transitions (company_id, enrolment_id, to_status, automated,"
             " occurred_at, reason_code, reason_label)"
             " VALUES (:c, :e, 'rejected', false, now() - interval '300 days',"
             "         'fixture', 'Fixture reason')"),
        {"c": f.company, "e": enr},
    )
    assert reopened not in await _purgeable_ids(db, retention_days=180)
