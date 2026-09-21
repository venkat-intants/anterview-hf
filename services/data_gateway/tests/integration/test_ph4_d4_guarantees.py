"""PH4-D4: the job-simulation/portfolio guarantees that hold AT THE DATABASE.

The kind CHECK on workflow_rounds; round_tasks/materials frozen while their
workflow is published, archived, in review or approved
(workflow_children_immutable, reused unchanged); every task_submissions
lifecycle transition, with its reason; task_responses frozen once the parent
submission is no longer open, redaction the one exception; task_events
append-only; one live submission per (enrolment, round); composite FKs
refusing a cross-company reference; and enrolment_awaits_human true for a
task round.

Against a real, migrated Postgres. Every refusal is checked for its REASON.
"""

from __future__ import annotations

import hashlib
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
        self.applicant = uuid.uuid4()
        self.req = uuid.uuid4()
        self.workflow = uuid.uuid4()
        self.round = uuid.uuid4()  # job_simulation
        self.portfolio_round = uuid.uuid4()
        self.enrolment = uuid.uuid4()
        self.other_applicant = uuid.uuid4()
        self.other_req = uuid.uuid4()
        self.other_enrolment = uuid.uuid4()
        self.other_round = uuid.uuid4()


DIGEST = hashlib.sha256(b"cfg").hexdigest()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    otag = f.other_company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "oc": f.other_company, "h": f.hr, "a": f.applicant, "r": f.req,
        "w": f.workflow, "rnd": f.round, "prnd": f.portfolio_round, "e": f.enrolment,
        "oa": f.other_applicant, "or_": f.other_req, "oe": f.other_enrolment,
        "ornd": f.other_round,
        "slug": f"d4-{tag}", "oslug": f"d4o-{otag}",
        "m1": f"h-{tag}@d4.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'D4 co', :slug),"
        " (:oc, 'D4 other', :oslug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now()), (:or_, :oc, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " review_status, created_at, updated_at)"
        " VALUES (:w, :c, :r, 1, 'draft', 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " deadline_days, created_at, updated_at)"
        " VALUES (:rnd, :c, :w, 0, 'Sim round', 'job_simulation', 7, now(), now()),"
        " (:prnd, :c, :w, 1, 'Portfolio round', 'portfolio', 7, now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer'), (:oa, :oc, 'Bea', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " current_round_id, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', :rnd, now(), now()),"
        " (:oe, :oc, :or_, :oa, 'Engineer', NULL, now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " deadline_days, created_at, updated_at)"
        " SELECT :ornd, :oc, w2.id, 0, 'Other round', 'job_simulation', 7, now(), now()"
        "  FROM workflows w2 WHERE w2.company_id = :oc LIMIT 1",
    ):
        await db.execute(text(sql), p)
    # a second workflow (for the other company) so the other-round insert above has one
    await db.execute(
        text(
            "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
            " review_status, created_at, updated_at)"
            " VALUES (:w2, :oc, :or_, 1, 'draft', 'draft', now(), now())"
        ),
        {"w2": uuid.uuid4(), "oc": f.other_company, "or_": f.other_req},
    )
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
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:160]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


# ===========================================================================
# The kind CHECK
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_round_kind_refused(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at) VALUES (:i, :c, :w, 9, 'x', 'file_upload', now(), now())",
        {"i": uuid.uuid4(), "c": f.company, "w": f.workflow},
        "ck_workflow_rounds_kind",
    )


@pytest.mark.asyncio
async def test_job_simulation_and_portfolio_are_legal_kinds(db: AsyncSession) -> None:
    f = await _build(db)
    for position, kind in enumerate(("job_simulation", "portfolio"), start=9):
        await _allowed(
            db,
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " created_at, updated_at) VALUES (:i, :c, :w, :p, 'x', :k, now(), now())",
            {"i": uuid.uuid4(), "c": f.company, "w": f.workflow, "p": position, "k": kind},
        )


# ===========================================================================
# round_tasks / round_task_materials frozen with the workflow
# ===========================================================================
async def _put_task_config(db: AsyncSession, f: F, round_id: uuid.UUID) -> uuid.UUID:
    rt_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
            " created_at, updated_at) VALUES (:i, :c, :r, 'job_simulation', 'Do the thing',"
            " CAST(:it AS jsonb), now(), now())"
        ),
        {"i": rt_id, "c": f.company, "r": round_id,
         "it": '[{"key":"q1","prompt":"x","response_type":"text","required":true}]'},
    )
    return rt_id


@pytest.mark.asyncio
async def test_round_tasks_frozen_while_workflow_in_review(db: AsyncSession) -> None:
    f = await _build(db)
    rt_id = await _put_task_config(db, f, f.round)
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'in_review', submitted_by_user_id = :h,"
            " submitted_for_review_at = now(), review_fingerprint = 'x' WHERE id = :w"
        ),
        {"w": f.workflow, "h": f.hr},
    )
    await _refused(
        db, "UPDATE round_tasks SET brief = 'changed' WHERE id = :i", {"i": rt_id},
        "its rounds and criteria cannot change",
    )


@pytest.mark.asyncio
async def test_round_tasks_editable_while_draft(db: AsyncSession) -> None:
    f = await _build(db)
    rt_id = await _put_task_config(db, f, f.round)
    await _allowed(db, "UPDATE round_tasks SET brief = 'changed' WHERE id = :i", {"i": rt_id})


@pytest.mark.asyncio
async def test_round_task_materials_frozen_while_published(db: AsyncSession) -> None:
    f = await _build(db)
    await _put_task_config(db, f, f.round)
    mat_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO round_task_materials (id, company_id, round_id, title, storage_key,"
            " original_name, content_type, size_bytes, sha256, created_at, updated_at)"
            " VALUES (:i, :c, :r, 'Brief.pdf', :k, 'brief.pdf', 'application/pdf', 100,"
            " :sha, now(), now())"
        ),
        {"i": mat_id, "c": f.company, "r": f.round, "k": f"task_materials/{f.company}/{f.round}/{mat_id}",
         "sha": "a" * 64},
    )
    # in_review is enough to trip workflow_children_immutable() (it checks
    # status IN ('published','archived') OR review_status IN
    # ('in_review','approved')) without needing the full submit-then-approve
    # dance a real 'published' row requires.
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'in_review', submitted_by_user_id = :h,"
            " submitted_for_review_at = now(), review_fingerprint = 'x' WHERE id = :w"
        ),
        {"w": f.workflow, "h": f.hr},
    )
    await _refused(
        db, "DELETE FROM round_task_materials WHERE id = :i", {"i": mat_id},
        "cannot change",
    )


# ===========================================================================
# enrolment_awaits_human is true for a task round
# ===========================================================================
@pytest.mark.asyncio
async def test_enrolment_awaits_human_true_for_task_round(db: AsyncSession) -> None:
    f = await _build(db)
    row = await db.execute(
        text("SELECT enrolment_awaits_human('shortlisted', :r)"), {"r": f.round}
    )
    assert row.scalar() is True
    row2 = await db.execute(
        text("SELECT enrolment_awaits_human('shortlisted', :r)"), {"r": f.portfolio_round}
    )
    assert row2.scalar() is True


# ===========================================================================
# task_submissions lifecycle
# ===========================================================================
def _sub_insert(
    f: F, sid: uuid.UUID, *, round_id: uuid.UUID | None = None, enrolment_id: uuid.UUID | None = None,
    applicant_id: uuid.UUID | None = None, status: str = "assigned",
    started_at: str | None = None, submitted_at: str | None = None, closed_by: str | None = None,
    token_hash: str | None = "tok", superseded_at: str | None = None,
    superseded_by_id: uuid.UUID | None = None, redacted_at: str | None = None,
    due_at_delta_days: int = 1,
) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO task_submissions (id, company_id, enrolment_id, round_id, applicant_id,"
        " kind, status, token_hash, due_at, started_at, submitted_at, closed_by,"
        " config_digest, superseded_at, superseded_by_id, redacted_at, created_at, updated_at)"
        " VALUES (:i, :c, :e, :r, :a, 'job_simulation', :st, :th,"
        " now() + make_interval(days => :dd), :sa, :sub, :cb, :dig, :spat, :spby, :rdat, now(), now())"
    )
    return sql, {
        "i": sid, "c": f.company, "e": enrolment_id or f.enrolment, "r": round_id or f.round,
        "a": applicant_id or f.applicant, "st": status, "th": token_hash, "dd": due_at_delta_days,
        "sa": started_at, "sub": submitted_at, "cb": closed_by, "dig": DIGEST,
        "spat": superseded_at, "spby": superseded_by_id, "rdat": redacted_at,
    }


@pytest.mark.asyncio
async def test_submission_must_arrive_assigned(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _sub_insert(f, uuid.uuid4(), status="in_progress")
    await _refused(db, sql, params, "arrives assigned")


@pytest.mark.asyncio
async def test_submission_must_arrive_with_no_progress(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO task_submissions (id, company_id, enrolment_id, round_id, applicant_id,"
        " kind, status, token_hash, due_at, started_at, config_digest, created_at, updated_at)"
        " VALUES (:i, :c, :e, :r, :a, 'job_simulation', 'assigned', :th,"
        " now() + interval '1 day', now(), :dig, now(), now())",
        {"i": uuid.uuid4(), "c": f.company, "e": f.enrolment, "r": f.round, "a": f.applicant,
         "th": "tok", "dig": DIGEST},
        "arrives with no progress recorded",
    )


@pytest.mark.asyncio
async def test_one_live_submission_per_enrolment_round(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _sub_insert(f, uuid.uuid4())
    await _allowed(db, sql, params)
    sql2, params2 = _sub_insert(f, uuid.uuid4(), token_hash="tok2")
    await _refused(db, sql2, params2, "uq_task_submissions_live_per_round")


@pytest.mark.asyncio
async def test_reissue_supersedes_then_inserts(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    new_id = uuid.uuid4()
    await db.execute(
        text("UPDATE task_submissions SET superseded_at = now(), superseded_by_id = :n WHERE id = :i"),
        {"n": new_id, "i": sid},
    )
    sql2, params2 = _sub_insert(f, new_id, token_hash="tok2")
    await _allowed(db, sql2, params2)


@pytest.mark.asyncio
async def test_identity_and_allowance_frozen_once_started(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await db.execute(
        text("UPDATE task_submissions SET status = 'in_progress', started_at = now() WHERE id = :i"),
        {"i": sid},
    )
    await _refused(
        db, "UPDATE task_submissions SET round_id = :r WHERE id = :i",
        {"r": f.portfolio_round, "i": sid}, "keeps its identity, config and allowance once started",
    )


@pytest.mark.asyncio
async def test_due_at_only_moves_later_while_open(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await _refused(
        db, "UPDATE task_submissions SET due_at = now() - interval '1 hour' WHERE id = :i",
        {"i": sid}, "due date can only move later",
    )
    await _allowed(
        db, "UPDATE task_submissions SET due_at = now() + interval '2 days' WHERE id = :i", {"i": sid},
    )


@pytest.mark.asyncio
async def test_submit_needs_submitted_at_and_closed_by(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await _refused(
        db, "UPDATE task_submissions SET status = 'submitted' WHERE id = :i", {"i": sid},
        "needs submitted_at and closed_by",
    )
    await _allowed(
        db,
        "UPDATE task_submissions SET status = 'submitted', submitted_at = now(),"
        " closed_by = 'candidate' WHERE id = :i",
        {"i": sid},
    )


@pytest.mark.asyncio
async def test_terminal_submission_is_frozen(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await db.execute(
        text(
            "UPDATE task_submissions SET status = 'submitted', submitted_at = now(),"
            " closed_by = 'candidate' WHERE id = :i"
        ),
        {"i": sid},
    )
    # An illegal status transition is refused on its own terms...
    await _refused(
        db, "UPDATE task_submissions SET status = 'withdrawn' WHERE id = :i", {"i": sid},
        "cannot go from submitted to withdrawn",
    )
    # ...and so is any other change while the status itself stays put (the
    # generic terminal freeze, distinct from the field-specific due_at and
    # identity/allowance rules exercised elsewhere in this file).
    await _refused(
        db, "UPDATE task_submissions SET attempt_no = 2 WHERE id = :i", {"i": sid}, "it is fixed",
    )


@pytest.mark.asyncio
async def test_redaction_clears_token_and_stamps_redacted_at(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await _allowed(
        db, "UPDATE task_submissions SET token_hash = NULL, redacted_at = now() WHERE id = :i",
        {"i": sid},
    )
    await _refused(
        db, "UPDATE task_submissions SET status = 'withdrawn' WHERE id = :i", {"i": sid},
        "is redacted and is fixed",
    )


@pytest.mark.asyncio
async def test_submission_never_hard_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await _refused(
        db, "DELETE FROM task_submissions WHERE id = :i", {"i": sid}, "is kept, not deleted",
    )


# ===========================================================================
# task_responses frozen after submit; redaction allowed
# ===========================================================================
@pytest.mark.asyncio
async def test_response_frozen_after_submit(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    resp_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO task_responses (id, company_id, submission_id, item_key, response_type,"
            " text_value, created_at, updated_at)"
            " VALUES (:i, :c, :s, 'q1', 'text', 'my answer', now(), now())"
        ),
        {"i": resp_id, "c": f.company, "s": sid},
    )
    await db.execute(
        text(
            "UPDATE task_submissions SET status = 'submitted', submitted_at = now(),"
            " closed_by = 'candidate' WHERE id = :i"
        ),
        {"i": sid},
    )
    await _refused(
        db, "UPDATE task_responses SET text_value = 'changed' WHERE id = :i", {"i": resp_id},
        "its responses are fixed",
    )
    await _allowed(
        db,
        "UPDATE task_responses SET text_value = NULL, redacted_at = now() WHERE id = :i",
        {"i": resp_id},
    )


@pytest.mark.asyncio
async def test_response_file_field_never_changes(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    resp_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO task_responses (id, company_id, submission_id, response_type,"
            " storage_key, content_type, created_at, updated_at)"
            " VALUES (:i, :c, :s, 'file', :k, 'application/pdf', now(), now())"
        ),
        {"i": resp_id, "c": f.company, "s": sid, "k": f"tasks/{f.company}/{sid}/{resp_id}"},
    )
    await _refused(
        db, "UPDATE task_responses SET storage_key = :k WHERE id = :i",
        {"i": resp_id, "k": "somewhere/else"}, "file never changes",
    )


# ===========================================================================
# task_events append-only
# ===========================================================================
@pytest.mark.asyncio
async def test_task_events_append_only(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    event_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO task_events (id, company_id, submission_id, action, actor_type)"
            " VALUES (:i, :c, :s, 'issued', 'system')"
        ),
        {"i": event_id, "c": f.company, "s": sid},
    )
    await _refused(
        db, "UPDATE task_events SET action = 'submitted' WHERE id = :i", {"i": event_id},
        "append-only",
    )
    await _refused(db, "DELETE FROM task_events WHERE id = :i", {"i": event_id}, "append-only")


# ===========================================================================
# Composite FKs — a cross-company reference is refused
# ===========================================================================
@pytest.mark.asyncio
async def test_cross_company_round_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _sub_insert(f, uuid.uuid4(), round_id=f.other_round)
    await _refused(db, sql, params, "fk_task_submissions_round")


@pytest.mark.asyncio
async def test_cross_company_applicant_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _sub_insert(f, uuid.uuid4(), applicant_id=f.other_applicant)
    await _refused(db, sql, params, "fk_task_submissions_applicant")


@pytest.mark.asyncio
async def test_cross_company_enrolment_refused(db: AsyncSession) -> None:
    f = await _build(db)
    sql, params = _sub_insert(f, uuid.uuid4(), enrolment_id=f.other_enrolment)
    await _refused(db, sql, params, "fk_task_submissions_enrolment")


@pytest.mark.asyncio
async def test_round_tasks_cross_company_refused(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db,
        "INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items, created_at,"
        " updated_at) VALUES (:i, :c, :r, 'job_simulation', 'b', '[]'::jsonb, now(), now())",
        {"i": uuid.uuid4(), "c": f.other_company, "r": f.round},
        "fk_round_tasks_round",
    )


# ===========================================================================
# M2 (security review, PH4-D4 wave 5): a candidate may retract their own
# upload/answer while the submission is open; it is frozen the moment it
# closes. Before the fix, a DELETE was refused unconditionally (while the
# parent submission existed at all), so ``remove_artifact``'s python-level
# "open submission" check always reached a DB error it did not catch — a 500
# on every attempt, whatever the status.
# ===========================================================================
def _response_insert(f: F, sid: uuid.UUID, resp_id: uuid.UUID) -> tuple[str, dict[str, Any]]:
    sql = (
        "INSERT INTO task_responses (id, company_id, submission_id, item_key, response_type,"
        " text_value, created_at, updated_at)"
        " VALUES (:i, :c, :s, 'q1', 'text', 'my answer', now(), now())"
    )
    return sql, {"i": resp_id, "c": f.company, "s": sid}


@pytest.mark.asyncio
async def test_response_deletable_while_submission_open(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    await db.execute(
        text("UPDATE task_submissions SET status = 'in_progress', started_at = now() WHERE id = :i"),
        {"i": sid},
    )
    resp_id = uuid.uuid4()
    rsql, rparams = _response_insert(f, sid, resp_id)
    await db.execute(text(rsql), rparams)
    await _allowed(db, "DELETE FROM task_responses WHERE id = :i", {"i": resp_id})


@pytest.mark.asyncio
async def test_response_not_deletable_once_submitted(db: AsyncSession) -> None:
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    resp_id = uuid.uuid4()
    rsql, rparams = _response_insert(f, sid, resp_id)
    await db.execute(text(rsql), rparams)
    await db.execute(
        text(
            "UPDATE task_submissions SET status = 'submitted', submitted_at = now(),"
            " closed_by = 'candidate' WHERE id = :i"
        ),
        {"i": sid},
    )
    await _refused(
        db, "DELETE FROM task_responses WHERE id = :i", {"i": resp_id}, "is kept, not deleted",
    )


@pytest.mark.asyncio
async def test_response_not_deletable_once_redacted(db: AsyncSession) -> None:
    """A redacted row stays gone-in-substance but present-in-form for the
    retention audit trail — it is never additionally hard-deleted."""
    f = await _build(db)
    sid = uuid.uuid4()
    sql, params = _sub_insert(f, sid)
    await db.execute(text(sql), params)
    resp_id = uuid.uuid4()
    rsql, rparams = _response_insert(f, sid, resp_id)
    await db.execute(text(rsql), rparams)
    await db.execute(
        text(
            "UPDATE task_responses SET text_value = NULL, redacted_at = now() WHERE id = :i"
        ),
        {"i": resp_id},
    )
    await _refused(
        db, "DELETE FROM task_responses WHERE id = :i", {"i": resp_id}, "is kept, not deleted",
    )
