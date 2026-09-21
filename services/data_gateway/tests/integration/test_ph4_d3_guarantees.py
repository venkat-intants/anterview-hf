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

from app import code_evidence as svc
from app.config import settings
from app.interviewer_scorecards import RequestMeta

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
    signal_id: uuid.UUID | None = None, supersedes_id: uuid.UUID | None = None,
) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO code_integrity_findings (id, company_id, attempt_id, coding_question_id,"
        " signal_id, outcome, rationale, recorded_by_user_id, supersedes_id, superseded_at,"
        " redacted_at, created_at)"
        " VALUES (:i, :c, :a, :q, :sig, 'follow_up', :r, :rec, :sup, :sat, :rdat, now())"
    )
    return sql, {
        "i": fid, "c": company_id or f.company, "a": attempt_id or f.attempt_a, "q": f.question,
        "sig": signal_id, "r": rationale, "rec": recorder or f.hr, "sup": supersedes_id,
        "sat": superseded_at, "rdat": redacted_at,
    }


async def _extra_attempt(db: AsyncSession, f: F) -> uuid.UUID:
    """A THIRD same-company, same-question attempt outside the (attempt_a,
    attempt_b) pair — applicant_a's retake — for testing that a signal or a
    supersede reference must name an attempt actually involved."""
    aid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id, attempt_no,"
            " status, started_at, submitted_at, score_raw, score_max, score_percent, passed,"
            " created_at, updated_at)"
            " VALUES (:i, :c, :x, :r, :a, 2, 'submitted', now(), now(), 100, 100, 100, true, now(), now())"
        ),
        {"i": aid, "c": f.company, "x": f.exam, "r": f.round, "a": f.applicant_a},
    )
    return aid


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


# ===========================================================================
# MEDIUM-1(a): a BEFORE INSERT trigger refuses new evidence once an attempt's
# code is redacted, on all four evidence tables.
# ===========================================================================
async def _redact(db: AsyncSession, attempt_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE exam_attempts SET code_redacted_at = now() WHERE id = :i"), {"i": attempt_id},
    )


@pytest.mark.asyncio
async def test_a_report_insert_is_refused_once_its_attempt_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_a)
    sql, params = _report_insert(f, uuid.uuid4())
    await _refused(db, sql, params, "has redacted code")


@pytest.mark.asyncio
async def test_a_fingerprint_insert_is_refused_once_its_attempt_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_a)
    sql, params = _fingerprint_insert(f, uuid.uuid4())
    await _refused(db, sql, params, "has redacted code")


@pytest.mark.asyncio
async def test_a_signal_insert_is_refused_when_the_low_attempt_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_low)
    sql, params = _signal_insert(f, uuid.uuid4())
    await _refused(db, sql, params, "has redacted code")


@pytest.mark.asyncio
async def test_a_signal_insert_is_refused_when_the_high_attempt_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_high)
    sql, params = _signal_insert(f, uuid.uuid4())
    await _refused(db, sql, params, "has redacted code")


@pytest.mark.asyncio
async def test_a_finding_insert_is_refused_once_its_attempt_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_a)
    sql, params = _finding_insert(f, uuid.uuid4())
    await _refused(db, sql, params, "has redacted code")


# ===========================================================================
# MEDIUM-2: a finding's references are checked against its own attempt, both
# in the service (below) and, belt and braces, at the database.
# ===========================================================================
@pytest.mark.asyncio
async def test_a_finding_signal_must_be_on_the_same_attempt_and_question(db: AsyncSession) -> None:
    f = await _build(db)
    other_attempt = await _extra_attempt(db, f)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)  # pairs attempt_low/attempt_high, not `other_attempt`
    await db.execute(text(sql), params)
    sql2, params2 = _finding_insert(f, uuid.uuid4(), attempt_id=other_attempt, signal_id=sid)
    await _refused(db, sql2, params2, "is not a signal on attempt")


@pytest.mark.asyncio
async def test_a_finding_signal_on_its_own_attempt_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)
    await db.execute(text(sql), params)
    sql2, params2 = _finding_insert(f, uuid.uuid4(), attempt_id=f.attempt_low, signal_id=sid)
    await _allowed(db, sql2, params2)


@pytest.mark.asyncio
async def test_a_finding_cannot_supersede_a_finding_on_a_different_attempt(db: AsyncSession) -> None:
    f = await _build(db)
    other_attempt = await _extra_attempt(db, f)
    old_fid = await _new_finding(db, f, attempt_id=other_attempt)
    sql, params = _finding_insert(f, uuid.uuid4(), supersedes_id=old_fid)  # attempt_id defaults to attempt_a
    await _refused(db, sql, params, "does not supersede a live finding")


@pytest.mark.asyncio
async def test_a_finding_cannot_supersede_an_already_superseded_finding_at_the_database(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    old_fid = await _new_finding(db, f)
    await db.execute(
        text("UPDATE code_integrity_findings SET superseded_at = now() WHERE id = :i"), {"i": old_fid},
    )
    sql, params = _finding_insert(f, uuid.uuid4(), supersedes_id=old_fid)
    await _refused(db, sql, params, "does not supersede a live finding")


@pytest.mark.asyncio
async def test_a_finding_cannot_supersede_a_redacted_finding(db: AsyncSession) -> None:
    f = await _build(db)
    old_fid = await _new_finding(db, f)
    await db.execute(
        text("UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
             " WHERE id = :i"),
        {"i": old_fid},
    )
    sql, params = _finding_insert(f, uuid.uuid4(), supersedes_id=old_fid)
    await _refused(db, sql, params, "does not supersede a live finding")


@pytest.mark.asyncio
async def test_a_finding_may_supersede_a_live_finding_on_the_same_attempt_and_question(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    old_fid = await _new_finding(db, f)
    sql, params = _finding_insert(f, uuid.uuid4(), supersedes_id=old_fid)
    await _allowed(db, sql, params)


@pytest.mark.asyncio
async def test_signal_id_may_be_cleared_even_on_an_already_redacted_finding(db: AsyncSession) -> None:
    """MEDIUM-2. Without this exception, a purge/erasure pass clearing
    signal_id on a finding an EARLIER pass already redacted would raise 'is
    redacted and is fixed' and block the whole transaction — forever, since
    the next tick reaches the same row the same way."""
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)
    await db.execute(text(sql), params)
    fid = await _new_finding(db, f, signal_id=sid)
    await db.execute(
        text("UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
             " WHERE id = :i"),
        {"i": fid},
    )
    await _allowed(
        db, "UPDATE code_integrity_findings SET signal_id = NULL WHERE id = :i", {"i": fid},
    )


@pytest.mark.asyncio
async def test_a_redacted_finding_still_refuses_anything_other_than_clearing_signal_id(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)
    await db.execute(text(sql), params)
    fid = await _new_finding(db, f, signal_id=sid)
    await db.execute(
        text("UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
             " WHERE id = :i"),
        {"i": fid},
    )
    await _refused(
        db, "UPDATE code_integrity_findings SET signal_id = NULL, outcome = 'confirmed' WHERE id = :i",
        {"i": fid}, "is redacted and is fixed",
    )


@pytest.mark.asyncio
async def test_the_supersedes_fk_is_no_action_not_set_null(db: AsyncSession) -> None:
    """MEDIUM-2. A composite FK's ON DELETE SET NULL nulls EVERY column it
    names, including company_id (NOT NULL here) — the same defect already
    fixed on the signal FK in the same migration. Checked against
    information_schema so a future edit that reintroduces SET NULL fails
    this test, not a production incident."""
    rule = await db.scalar(
        text(
            "SELECT rc.delete_rule FROM information_schema.referential_constraints rc"
            " WHERE rc.constraint_name = 'fk_code_integrity_findings_supersedes'"
        )
    )
    assert rule == "NO ACTION", rule


# ===========================================================================
# LOW-3: code_redacted_at is frozen once set, and the redaction exception
# touches only the coding subtree of answers/graded_snapshot.
# ===========================================================================
@pytest.mark.asyncio
async def test_code_redacted_at_is_frozen_once_set(db: AsyncSession) -> None:
    f = await _build(db)
    await db.execute(
        text(
            "UPDATE exam_attempts SET answers = '{\"coding\":{}}'::jsonb,"
            " graded_snapshot = '{\"coding\":{}}'::jsonb, code_redacted_at = now()"
            " WHERE id = :i"
        ),
        {"i": f.attempt_a},
    )
    await _refused(
        db,
        "UPDATE exam_attempts SET code_redacted_at = '2031-01-01T00:00:00+00'::timestamptz"
        " WHERE id = :i",
        {"i": f.attempt_a}, "code_redacted_at is frozen once set",
    )


@pytest.mark.asyncio
async def test_the_redaction_cannot_introduce_a_non_coding_answer(db: AsyncSession) -> None:
    """LOW-3. The redaction exception only ever touches the CODING answer's
    source and the CODING test results' output — never an MCQ selection or
    any other non-coding entry.

    ``:9`` bound as a bind param (not a bind param, but SQLAlchemy's text()
    cannot tell), so the JSONB payload is a bound parameter, not an inline
    literal — the same reason every other JSONB payload in this file is.
    """
    f = await _build(db)
    await _refused(
        db,
        "UPDATE exam_attempts SET answers = CAST(:ans AS jsonb),"
        " code_redacted_at = now() WHERE id = :i",
        {"i": f.attempt_a, "ans": json.dumps({"coding": {}, "mcq": {"q1": 9}})},
        "changes only the coding source and output",
    )


@pytest.mark.asyncio
async def test_the_redaction_cannot_change_a_non_coding_test_result(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "UPDATE exam_attempts SET graded_snapshot = CAST(:snap AS jsonb),"
        " code_redacted_at = now() WHERE id = :i",
        {"i": f.attempt_a, "snap": json.dumps({"coding": {}, "mcq": {"raw": 1}})},
        "changes only the coding source and output",
    )


# ===========================================================================
# MEDIUM-1(b): the service refuses to write NEW evidence for an attempt
# whose code is already redacted.
# ===========================================================================
@pytest.mark.asyncio
async def test_record_finding_refuses_an_attempt_whose_code_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_a)
    with pytest.raises(svc.CodeEvidenceError) as exc_info:
        await svc.record_finding(
            db, company_id=f.company, attempt_id=f.attempt_a, coding_question_id=f.question,
            outcome="follow_up", rationale="x" * 30, actor=f.hr, meta=RequestMeta(),
        )
    assert exc_info.value.status_code == 409
    assert "redacted" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_analyse_attempt_refuses_an_attempt_whose_code_is_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    await _redact(db, f.attempt_a)
    with pytest.raises(svc.CodeEvidenceError) as exc_info:
        await svc.analyse_attempt(db, company_id=f.company, attempt_id=f.attempt_a)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_record_finding_refuses_a_signal_not_on_this_attempt(db: AsyncSession) -> None:
    f = await _build(db)
    other_attempt = await _extra_attempt(db, f)
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid)  # pairs attempt_low/attempt_high
    await db.execute(text(sql), params)
    with pytest.raises(svc.CodeEvidenceError) as exc_info:
        await svc.record_finding(
            db, company_id=f.company, attempt_id=other_attempt, coding_question_id=f.question,
            outcome="follow_up", rationale="x" * 30, actor=f.hr, meta=RequestMeta(), signal_id=sid,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_record_finding_refuses_superseding_a_finding_on_another_attempt(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    other_attempt = await _extra_attempt(db, f)
    old_fid = await _new_finding(db, f, attempt_id=other_attempt)
    with pytest.raises(svc.CodeEvidenceError) as exc_info:
        await svc.record_finding(
            db, company_id=f.company, attempt_id=f.attempt_a, coding_question_id=f.question,
            outcome="follow_up", rationale="x" * 30, actor=f.hr, meta=RequestMeta(),
            supersedes_id=old_fid,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_record_finding_refuses_superseding_an_already_superseded_finding(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    old_fid = await _new_finding(db, f)
    newer_fid = uuid.uuid4()
    sql, params = _finding_insert(f, newer_fid)
    await db.execute(text(sql), params)
    await db.execute(
        text("UPDATE code_integrity_findings SET superseded_at = now() WHERE id = :i"), {"i": old_fid},
    )
    with pytest.raises(svc.CodeEvidenceError) as exc_info:
        await svc.record_finding(
            db, company_id=f.company, attempt_id=f.attempt_a, coding_question_id=f.question,
            outcome="follow_up", rationale="x" * 30, actor=f.hr, meta=RequestMeta(),
            supersedes_id=old_fid,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_record_finding_supersedes_cleanly_end_to_end(db: AsyncSession) -> None:
    f = await _build(db)
    old_fid = await svc.record_finding(
        db, company_id=f.company, attempt_id=f.attempt_a, coding_question_id=f.question,
        outcome="follow_up", rationale="first look, seems fine so far", actor=f.hr,
        meta=RequestMeta(),
    )
    new_fid = await svc.record_finding(
        db, company_id=f.company, attempt_id=f.attempt_a, coding_question_id=f.question,
        outcome="confirmed", rationale="on a closer look, this is a clear match", actor=f.hr,
        meta=RequestMeta(), supersedes_id=old_fid,
    )
    row = (
        await db.execute(
            text("SELECT superseded_at FROM code_integrity_findings WHERE id = :i"), {"i": old_fid},
        )
    ).first()
    assert row.superseded_at is not None
    assert new_fid != old_fid


# ===========================================================================
# MEDIUM-1(c): purge() redacts a finding's rationale on the SURVIVING side of
# a pair too, not just its own attempt's findings.
# ===========================================================================
@pytest.mark.asyncio
async def test_purge_redacts_a_finding_recorded_against_the_surviving_side_of_a_pair(
    db: AsyncSession,
) -> None:
    """The ERASED attempt has no application (immediately eligible for
    retention); the SURVIVING attempt is tied to an application still
    waiting on HR (never eligible) — so this one ``purge()`` pass redacts
    only one side of the pair, and the finding on the OTHER, still-live side
    must still lose its rationale."""
    f = await _build(db)
    source = "\n".join(f"line_{i} = {i}" for i in range(1, 60))
    erased_attempt = await _attempt_with_answers(
        db, f, applicant_id=f.applicant_b, attempt_no=2, source=source,
    )
    surviving_attempt = await _attempt_on_an_application(
        db, f, status="shortlisted", decided_days_ago=None,
    )
    low_id, high_id = sorted((erased_attempt, surviving_attempt))
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid, low=low_id, high=high_id)
    await db.execute(text(sql), params)
    finding_on_survivor = await _new_finding(db, f, attempt_id=surviving_attempt, signal_id=sid)

    purged = await svc.purge(db, retention_days=0, dry_run=False)
    assert purged >= 1

    survivor_row = (
        await db.execute(
            text("SELECT code_redacted_at FROM exam_attempts WHERE id = :i"), {"i": surviving_attempt},
        )
    ).first()
    assert survivor_row.code_redacted_at is None, "the surviving attempt is not itself purged yet"

    row = (
        await db.execute(
            text("SELECT rationale, redacted_at, signal_id FROM code_integrity_findings WHERE id = :i"),
            {"i": finding_on_survivor},
        )
    ).first()
    assert row.rationale == "[redacted]"
    assert row.redacted_at is not None
    assert row.signal_id is None


# ===========================================================================
# MEDIUM-3: the compare view excerpts only the matched regions, and audits
# both attempt ids.
# ===========================================================================
async def _attempt_with_answers(
    db: AsyncSession, f: F, *, applicant_id: uuid.UUID, attempt_no: int, source: str,
) -> uuid.UUID:
    """A fresh attempt with a real coding answer set AT INSERT TIME. The
    submission-freeze trigger only fires on UPDATE, so this is the only way
    to give an attempt real ``answers`` without it already being 'submitted'
    (which ``_build()``'s attempt_a/attempt_b already are, and updating
    ``answers`` on a submitted attempt is exactly what that trigger refuses)."""
    aid = uuid.uuid4()
    answers = json.dumps({"coding": {str(f.question): {"language": "python", "source": source}}})
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id, attempt_no,"
            " status, started_at, submitted_at, score_raw, score_max, score_percent, passed,"
            " answers, created_at, updated_at)"
            " VALUES (:i, :c, :x, :r, :a, :n, 'submitted', now(), now(), 100, 100, 100, true,"
            "         CAST(:ans AS jsonb), now(), now())"
        ),
        {
            "i": aid, "c": f.company, "x": f.exam, "r": f.round, "a": applicant_id, "n": attempt_no,
            "ans": answers,
        },
    )
    return aid


@pytest.mark.asyncio
async def test_compare_view_excerpts_only_the_matched_region(db: AsyncSession) -> None:
    from app.code_similarity import Fingerprint

    f = await _build(db)
    source = "\n".join(f"line_{i} = {i}" for i in range(1, 300))
    attempt_x = await _attempt_with_answers(db, f, applicant_id=f.applicant_a, attempt_no=2, source=source)
    attempt_y = await _attempt_with_answers(db, f, applicant_id=f.applicant_b, attempt_no=2, source=source)
    low_id, high_id = sorted((attempt_x, attempt_y))

    # A fingerprint pointing at line 5 on each side, so regions() computes a
    # match there — nowhere near the rest of the 300-line file.
    fp = Fingerprint(hashes=[42], lines=[5], token_count=60)
    for attempt_id in (low_id, high_id):
        await db.execute(
            text(
                "INSERT INTO code_fingerprints (id, company_id, attempt_id, coding_question_id,"
                " hashes, lines, token_count, algorithm_version, created_at)"
                " VALUES (:i, :c, :a, :q, CAST(:h AS bigint[]), CAST(:ln AS int[]), :tc, 'sim-winnow-1.0',"
                " now())"
            ),
            {
                "i": uuid.uuid4(), "c": f.company, "a": attempt_id, "q": f.question,
                "h": fp.hashes, "ln": fp.lines, "tc": fp.token_count,
            },
        )
    sid = uuid.uuid4()
    sql, params = _signal_insert(f, sid, low=low_id, high=high_id)
    await db.execute(text(sql), params)

    result = await svc.compare_view(db, company_id=f.company, signal_id=sid, actor=f.hr, meta=RequestMeta())
    for side in ("low", "high"):
        excerpt = result[side]["excerpt"]
        assert "line_5 " in excerpt, excerpt
        assert "line_290" not in excerpt, excerpt
        assert len(excerpt.splitlines()) < 20, excerpt

    audit_details = (
        await db.execute(
            text(
                "SELECT details FROM audit_log WHERE action = 'code_similarity.viewed'"
                " AND resource_id = :i ORDER BY event_ts DESC LIMIT 1"
            ),
            {"i": sid},
        )
    ).scalar()
    assert audit_details["attempt_low_id"] == str(low_id)
    assert audit_details["attempt_high_id"] == str(high_id)


@pytest.mark.asyncio
async def test_evidence_for_attempt_audits_the_read(db: AsyncSession) -> None:
    f = await _build(db)
    await svc.evidence_for_attempt(
        db, company_id=f.company, exam_id=f.exam, attempt_id=f.attempt_a, actor=f.hr,
        meta=RequestMeta(),
    )
    count = await db.scalar(
        text(
            "SELECT count(*) FROM audit_log WHERE action = 'code_evidence.viewed'"
            " AND resource_id = :i"
        ),
        {"i": f.attempt_a},
    )
    assert int(count or 0) >= 1
