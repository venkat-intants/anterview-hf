"""PH4-D2: the candidate-accommodation guarantees that hold AT THE DATABASE.

A row is born active, not superseded, not redacted, with a named recorder;
its scope, parameters, basis and dates never change after insert; its two
notes change only as part of one redaction, after which the whole row is
frozen; ``active`` only ever becomes ``revoked``, and only with who and when;
a revision's supersede pointers are set exactly once. A started exam
attempt's allowance is frozen the same way. Events are append-only. A
cross-company reference is refused by the schema, not by application code.

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
        self.hr = uuid.uuid4()
        self.hr2 = uuid.uuid4()
        self.applicant = uuid.uuid4()
        self.req = uuid.uuid4()
        self.workflow = uuid.uuid4()
        self.round = uuid.uuid4()
        self.enrolment = uuid.uuid4()
        self.exam = uuid.uuid4()
        self.exam_round = uuid.uuid4()
        self.other_applicant = uuid.uuid4()
        self.other_req = uuid.uuid4()
        self.other_enrolment = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    otag = f.other_company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "oc": f.other_company, "h": f.hr, "h2": f.hr2, "a": f.applicant,
        "r": f.req, "w": f.workflow, "rnd": f.round, "e": f.enrolment, "x": f.exam,
        "xr": f.exam_round, "oa": f.other_applicant, "or_": f.other_req, "oe": f.other_enrolment,
        "slug": f"d2-{tag}", "oslug": f"d2o-{otag}",
        "m1": f"h-{tag}@d2.test", "m2": f"h2-{tag}@d2.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'D2 co', :slug),"
        " (:oc, 'D2 other', :oslug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c), (:h2, :m2, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now()), (:or_, :oc, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " created_at, updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at)"
        " VALUES (:rnd, :c, :w, 0, 'Interview', 'human_review', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer'), (:oa, :oc, 'Bea', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', now(), now()),"
        " (:oe, :oc, :or_, :oa, 'Engineer', now(), now())",
        "INSERT INTO exams (id, company_id, title) VALUES (:x, :c, 'Exam')",
        "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, status, position)"
        " VALUES (:xr, :x, :c, 1, 'Round', 'draft', 0)",
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


_UNSET: Any = object()


def _acc_insert(
    f: F, aid: uuid.UUID, *, applicant_id: uuid.UUID | None = None,
    enrolment_id: uuid.UUID | None = None, round_id: uuid.UUID | None = None,
    exam_round_id: uuid.UUID | None = None, extra_time_percent: int | None = 50,
    other_adjustment: str | None = None, relax_auto_submit: bool = False,
    recorder: uuid.UUID | None = _UNSET, status: str = "active",
    superseded_at: str | None = None, superseded_by_id: uuid.UUID | None = None,
    redacted_at: str | None = None,
) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO candidate_accommodations (id, company_id, applicant_id, enrolment_id,"
        " round_id, exam_round_id, extra_time_percent, relax_auto_submit, other_adjustment,"
        " basis, effective_from, status, recorded_by_user_id, superseded_at, superseded_by_id,"
        " redacted_at, created_at, updated_at)"
        " VALUES (:i, :c, :a, :e, :r, :er, :pct, :rel, :other, 'hr_initiated', now(), :st,"
        " :rec, :sat, :sbid, :rdat, now(), now())"
    )
    return sql, {
        "i": aid, "c": f.company, "a": applicant_id or f.applicant, "e": enrolment_id,
        "r": round_id, "er": exam_round_id, "pct": extra_time_percent, "rel": relax_auto_submit,
        "other": other_adjustment, "st": status, "rec": f.hr if recorder is _UNSET else recorder,
        "sat": superseded_at, "sbid": superseded_by_id, "rdat": redacted_at,
    }


async def _new_active(db: AsyncSession, f: F, **kw: Any) -> uuid.UUID:
    aid = uuid.uuid4()
    sql, params = _acc_insert(f, aid, **kw)
    await db.execute(text(sql), params)
    return aid


REVOKE = (
    "UPDATE candidate_accommodations SET status = 'revoked', revoked_by_user_id = :u,"
    " revoked_at = now() WHERE id = :i"
)
REDACT = (
    "UPDATE candidate_accommodations SET interviewer_note = NULL, internal_note = NULL,"
    " other_adjustment = '[redacted]', redacted_at = now() WHERE id = :i"
)


# ===========================================================================
# INSERT: active, not superseded, not redacted, a named recorder
# ===========================================================================
@pytest.mark.asyncio
async def test_a_row_must_arrive_active(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), status="revoked")
    await _refused(db, sql, params, "arrives active")


@pytest.mark.asyncio
async def test_a_row_must_arrive_not_superseded(db: AsyncSession) -> None:
    f = await _build(db)
    other_id = uuid.uuid4()
    sql, params = _acc_insert(f, uuid.uuid4(), superseded_by_id=other_id)
    await _refused(db, sql, params, "arrives not superseded")


@pytest.mark.asyncio
async def test_a_row_must_arrive_not_redacted(db: AsyncSession) -> None:
    f = await _build(db)
    aid = uuid.uuid4()
    sql = (
        "INSERT INTO candidate_accommodations (id, company_id, applicant_id, extra_time_percent,"
        " basis, effective_from, status, recorded_by_user_id, redacted_at, created_at, updated_at)"
        " VALUES (:i, :c, :a, 50, 'hr_initiated', now(), 'active', :rec, now(), now(), now())"
    )
    await _refused(db, sql, {"i": aid, "c": f.company, "a": f.applicant, "rec": f.hr},
                   "arrives not redacted")


@pytest.mark.asyncio
async def test_a_row_needs_a_recorder(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), recorder=None)
    await _refused(db, sql, params, "needs a recorder")


@pytest.mark.asyncio
async def test_a_well_formed_row_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4())
    await _allowed(db, sql, params)


# ===========================================================================
# UPDATE: content is frozen after insert
# ===========================================================================
@pytest.mark.asyncio
async def test_the_parameters_are_frozen_after_insert(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    await _refused(
        db, "UPDATE candidate_accommodations SET extra_time_percent = 75 WHERE id = :i",
        {"i": aid}, "keeps its scope, parameters, basis and dates",
    )


@pytest.mark.asyncio
async def test_the_scope_is_frozen_after_insert(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f, enrolment_id=f.enrolment)
    await _refused(
        db, "UPDATE candidate_accommodations SET enrolment_id = NULL WHERE id = :i",
        {"i": aid}, "keeps its scope, parameters, basis and dates",
    )


@pytest.mark.asyncio
async def test_notes_change_only_via_a_redaction(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    await _refused(
        db,
        "UPDATE candidate_accommodations SET interviewer_note = 'not a redaction' WHERE id = :i",
        {"i": aid}, "keeps its notes outside a redaction",
    )


@pytest.mark.asyncio
async def test_a_redaction_value_must_be_null_or_the_fixed_marker(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f, other_adjustment="quiet room")
    await _refused(
        db,
        "UPDATE candidate_accommodations SET other_adjustment = 'still readable',"
        " redacted_at = now() WHERE id = :i",
        {"i": aid}, "becomes NULL or [redacted]",
    )


@pytest.mark.asyncio
async def test_a_redaction_is_allowed_once(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f, other_adjustment="quiet room")
    await _allowed(db, REDACT, {"i": aid})


@pytest.mark.asyncio
async def test_once_redacted_the_row_is_frozen(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f, other_adjustment="quiet room")
    await db.execute(text(REDACT), {"i": aid})
    await _refused(
        db, "UPDATE candidate_accommodations SET internal_note = 'anything' WHERE id = :i",
        {"i": aid}, "is redacted and is fixed",
    )


# ===========================================================================
# Revoke: active -> revoked, who and when, never back
# ===========================================================================
@pytest.mark.asyncio
async def test_revoking_needs_who_and_when(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    await _refused(
        db, "UPDATE candidate_accommodations SET status = 'revoked' WHERE id = :i",
        {"i": aid}, "needs who and when",
    )


@pytest.mark.asyncio
async def test_revoking_is_allowed_with_who_and_when(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    await _allowed(db, REVOKE, {"i": aid, "u": f.hr2})


@pytest.mark.asyncio
async def test_a_revoked_row_never_becomes_active_again(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    await db.execute(text(REVOKE), {"i": aid, "u": f.hr2})
    await _refused(
        db, "UPDATE candidate_accommodations SET status = 'active' WHERE id = :i",
        {"i": aid}, "cannot go from revoked to active",
    )


@pytest.mark.asyncio
async def test_how_it_was_revoked_is_fixed_once_set(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    await db.execute(text(REVOKE), {"i": aid, "u": f.hr2})
    await _refused(
        db,
        "UPDATE candidate_accommodations SET revoke_reason = 'changed my mind' WHERE id = :i",
        {"i": aid}, "keeps how it was revoked",
    )


# ===========================================================================
# Supersede: a revision is a NEW row; the pointers are set exactly once
# ===========================================================================
@pytest.mark.asyncio
async def test_superseding_is_allowed_once(db: AsyncSession) -> None:
    f = await _build(db)
    old_id = await _new_active(db, f)
    new_sql, new_params = _acc_insert(f, uuid.uuid4(), extra_time_percent=75)
    await db.execute(text(new_sql), new_params)
    await _allowed(
        db,
        "UPDATE candidate_accommodations SET superseded_at = now(), superseded_by_id = :new"
        " WHERE id = :old",
        {"old": old_id, "new": new_params["i"]},
    )


@pytest.mark.asyncio
async def test_what_superseded_a_row_is_fixed_once_set(db: AsyncSession) -> None:
    f = await _build(db)
    old_id = await _new_active(db, f)
    new1_sql, new1_params = _acc_insert(f, uuid.uuid4(), extra_time_percent=75)
    new2_sql, new2_params = _acc_insert(f, uuid.uuid4(), extra_time_percent=90)
    await db.execute(text(new1_sql), new1_params)
    await db.execute(text(new2_sql), new2_params)
    await db.execute(
        text(
            "UPDATE candidate_accommodations SET superseded_at = now(), superseded_by_id = :new"
            " WHERE id = :old"
        ),
        {"old": old_id, "new": new1_params["i"]},
    )
    await _refused(
        db,
        "UPDATE candidate_accommodations SET superseded_by_id = :new WHERE id = :old",
        {"old": old_id, "new": new2_params["i"]},
        "keeps what superseded it",
    )


@pytest.mark.asyncio
async def test_superseded_at_and_by_id_are_set_together(db: AsyncSession) -> None:
    f = await _build(db)
    old_id = await _new_active(db, f)
    new_sql, new_params = _acc_insert(f, uuid.uuid4(), extra_time_percent=75)
    await db.execute(text(new_sql), new_params)
    await _refused(
        db,
        "UPDATE candidate_accommodations SET superseded_by_id = :new WHERE id = :old",
        {"old": old_id, "new": new_params["i"]},
        "must record what and when it was superseded together",
    )


# ===========================================================================
# CHECK constraints
# ===========================================================================
async def _check_violated(
    db: AsyncSession, sql: str, params: dict[str, Any], constraint: str,
) -> None:
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        assert constraint in str(exc.orig), str(exc.orig)[:300]
        return
    await sp.rollback()
    pytest.fail(f"expected {constraint} to be violated")


@pytest.mark.asyncio
async def test_a_round_scoped_row_needs_an_enrolment(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), round_id=f.round, enrolment_id=None)
    await _check_violated(db, sql, params, "ck_candidate_accommodations_round_needs_enrolment")


@pytest.mark.asyncio
async def test_round_and_exam_round_are_mutually_exclusive(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(
        f, uuid.uuid4(), round_id=f.round, enrolment_id=f.enrolment, exam_round_id=f.exam_round,
    )
    await _check_violated(db, sql, params, "ck_candidate_accommodations_one_round_scope")


@pytest.mark.asyncio
async def test_at_least_one_adjustment_is_required(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), extra_time_percent=None)
    await _check_violated(db, sql, params, "ck_candidate_accommodations_at_least_one")


@pytest.mark.asyncio
async def test_extra_time_percent_has_a_floor_and_a_ceiling(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), extra_time_percent=5)
    await _check_violated(db, sql, params, "ck_candidate_accommodations_extra_time_range")
    sql2, params2 = _acc_insert(f, uuid.uuid4(), extra_time_percent=250)
    await _check_violated(db, sql2, params2, "ck_candidate_accommodations_extra_time_range")


@pytest.mark.asyncio
async def test_the_effective_window_must_end_after_it_starts(db: AsyncSession) -> None:
    f = await _build(db)
    aid = uuid.uuid4()
    sql = (
        "INSERT INTO candidate_accommodations (id, company_id, applicant_id, extra_time_percent,"
        " basis, effective_from, effective_until, status, recorded_by_user_id, created_at,"
        " updated_at)"
        " VALUES (:i, :c, :a, 50, 'hr_initiated', now(), now() - interval '1 day', 'active',"
        " :rec, now(), now())"
    )
    await _check_violated(
        db, sql, {"i": aid, "c": f.company, "a": f.applicant, "rec": f.hr},
        "ck_candidate_accommodations_window",
    )


# ===========================================================================
# Cross-company references are refused by the schema
# ===========================================================================
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


@pytest.mark.asyncio
async def test_a_cross_company_enrolment_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), enrolment_id=f.other_enrolment)
    await _fk_violated(db, sql, params)


@pytest.mark.asyncio
async def test_a_cross_company_applicant_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _acc_insert(f, uuid.uuid4(), applicant_id=f.other_applicant)
    await _fk_violated(db, sql, params)


# ===========================================================================
# exam_attempts: a started attempt's allowance is fixed
# ===========================================================================
async def _new_attempt(
    db: AsyncSession, f: F, *, extra_time_seconds: int = 900, auto_submit_relaxed: bool = True,
    accommodation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    aid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " attempt_no, status, started_at, extra_time_seconds, auto_submit_relaxed,"
            " accommodation_id, created_at, updated_at)"
            " VALUES (:i, :c, :x, :xr, :a, 1, 'in_progress', now(), :ext, :relaxed, :acc,"
            " now(), now())"
        ),
        {"i": aid, "c": f.company, "x": f.exam, "xr": f.exam_round, "a": f.applicant,
         "ext": extra_time_seconds, "relaxed": auto_submit_relaxed, "acc": accommodation_id},
    )
    return aid


@pytest.mark.asyncio
async def test_extra_time_seconds_is_frozen_once_the_attempt_exists(db: AsyncSession) -> None:
    f = await _build(db)
    attempt_id = await _new_attempt(db, f)
    await _refused(
        db, "UPDATE exam_attempts SET extra_time_seconds = 0 WHERE id = :i", {"i": attempt_id},
        "keeps the extra time it started with",
    )


@pytest.mark.asyncio
async def test_auto_submit_relaxed_is_frozen_once_the_attempt_exists(db: AsyncSession) -> None:
    f = await _build(db)
    attempt_id = await _new_attempt(db, f)
    await _refused(
        db, "UPDATE exam_attempts SET auto_submit_relaxed = false WHERE id = :i",
        {"i": attempt_id}, "keeps whether auto-submit was relaxed",
    )


@pytest.mark.asyncio
async def test_accommodation_id_cannot_repoint_to_a_different_row(db: AsyncSession) -> None:
    f = await _build(db)
    acc_id = await _new_active(db, f)
    other_acc_id = await _new_active(db, f)
    attempt_id = await _new_attempt(db, f, accommodation_id=acc_id)
    await _refused(
        db, "UPDATE exam_attempts SET accommodation_id = :new WHERE id = :i",
        {"i": attempt_id, "new": other_acc_id},
        "keeps which accommodation applied, or clears it",
    )


@pytest.mark.asyncio
async def test_accommodation_id_may_become_null(db: AsyncSession) -> None:
    f = await _build(db)
    acc_id = await _new_active(db, f)
    attempt_id = await _new_attempt(db, f, accommodation_id=acc_id)
    await _allowed(
        db, "UPDATE exam_attempts SET accommodation_id = NULL WHERE id = :i", {"i": attempt_id},
    )


# ===========================================================================
# accommodation_events: append-only
# ===========================================================================
@pytest.mark.asyncio
async def test_accommodation_events_cannot_be_updated(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    eid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO accommodation_events (id, company_id, accommodation_id, action,"
            " actor_user_id, details, created_at)"
            " VALUES (:i, :c, :a, 'recorded', :u, '{}'::jsonb, now())"
        ),
        {"i": eid, "c": f.company, "a": aid, "u": f.hr},
    )
    await _refused(
        db, "UPDATE accommodation_events SET action = 'revoked' WHERE id = :i", {"i": eid},
        "append-only",
    )


@pytest.mark.asyncio
async def test_accommodation_events_cannot_be_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    aid = await _new_active(db, f)
    eid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO accommodation_events (id, company_id, accommodation_id, action,"
            " actor_user_id, details, created_at)"
            " VALUES (:i, :c, :a, 'recorded', :u, '{}'::jsonb, now())"
        ),
        {"i": eid, "c": f.company, "a": aid, "u": f.hr},
    )
    await _refused(db, "DELETE FROM accommodation_events WHERE id = :i", {"i": eid}, "append-only")
