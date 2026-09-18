"""PH4 Wave 2: the guarantees that hold AT THE DATABASE.

Only an approved workflow version publishes, and only by the steps a person
takes — submitted by one, approved by another, before it goes live (O6); what a
reviewer saw cannot be changed under them, not even through an exam it uses
(O6); review history and dry-run results are append-only (O6/O2); branches stay
inside their workflow and travel as pairs (O3); stage settings are one per
stage, the SLA clock ignores notes, and an erased candidate's exception stays
redacted (O1).

Against a real, migrated Postgres (CI's service; locally any loopback database
— the conftest refuses a remote one). Every refusal is checked for its REASON.
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
from app.stage_sla import sla_for_enrolments
from app.workflows import WorkflowError, publish, workflow_fingerprint

pytestmark = pytest.mark.integration


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.hr = uuid.uuid4()  # wrote the version
        self.submitter = uuid.uuid4()  # a second HR manager, who submitted it
        self.reviewer = uuid.uuid4()  # the super admin who reviews it
        self.req = uuid.uuid4()
        self.wf = uuid.uuid4()
        self.r1 = uuid.uuid4()
        self.r2 = uuid.uuid4()
        self.other_wf = uuid.uuid4()
        self.other_round = uuid.uuid4()
        self.applicant = uuid.uuid4()
        self.enrolment = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "hr": f.hr, "r": f.req, "w": f.wf, "r1": f.r1, "r2": f.r2,
        "ow": f.other_wf, "orr": f.other_round, "a": f.applicant, "e": f.enrolment,
        "slug": f"w2-{tag}", "em": f"hr-{tag}@w2.test",
        "sb": f.submitter, "sbm": f"sub-{tag}@w2.test",
        "rv": f.reviewer, "rvm": f"rev-{tag}@w2.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'W2 co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:hr, :em, :c), (:sb, :sbm, :c),"
        " (:rv, :rvm, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " created_by_user_id, created_at, updated_at)"
        " VALUES (:w, :c, :r, 1, 'draft', :hr, now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at) VALUES (:r1, :c, :w, 0, 'Screen', 'human_review', now(), now()),"
        " (:r2, :c, :w, 1, 'Panel', 'human_review', now(), now())",
        "UPDATE workflow_rounds SET on_pass_next_round_id = :r2 WHERE id = :r1",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
        " created_at, updated_at) VALUES (:ow, :c, :r, 2, 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " created_at, updated_at) VALUES (:orr, :c, :ow, 0, 'Elsewhere', 'human_review',"
        " now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Candidate', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
        " target_job_title, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', now(), now())",
    ):
        await db.execute(text(sql), p)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2
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
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert reason in message, f"refused for the WRONG reason — expected {reason!r}: {message[:240]}"
        return
    await sp.rollback()
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:90]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


_SUBMIT = (
    "UPDATE workflows SET review_status = 'in_review', submitted_by_user_id = :s,"
    " submitted_for_review_at = now(), review_fingerprint = :fp WHERE id = :w"
)
_DECIDE = (
    "UPDATE workflows SET review_status = :t, reviewed_by_user_id = :r, reviewed_at = now()"
    " WHERE id = :w"
)
_PUBLISH = "UPDATE workflows SET status = 'published', published_at = now() WHERE id = :w"


async def _move(db: AsyncSession, f: F, state: str, *, fingerprint: str = "fp") -> None:
    """Walk the version to ``state`` by the only route the database allows."""
    if state == "draft":
        return
    await db.execute(text(_SUBMIT), {"s": f.submitter, "fp": fingerprint, "w": f.wf})
    if state == "in_review":
        return
    target = "changes_requested" if state == "changes_requested" else "approved"
    await db.execute(text(_DECIDE), {"t": target, "r": f.reviewer, "w": f.wf})
    if state in ("published", "archived"):
        await db.execute(text(_PUBLISH), {"w": f.wf})
    if state == "archived":
        await db.execute(text("UPDATE workflows SET status = 'archived' WHERE id = :w"),
                         {"w": f.wf})


# ===========================================================================
# O6 — only an approved version publishes; a reviewed version is locked
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "in_review", "changes_requested"])
async def test_an_unapproved_version_cannot_be_published(db: AsyncSession, state: str) -> None:
    f = await _build(db)
    await _move(db, f, state)
    await _refused(db, _PUBLISH, {"w": f.wf}, "has not been approved")


@pytest.mark.asyncio
async def test_an_approved_version_publishes(db: AsyncSession) -> None:
    f = await _build(db)
    await _move(db, f, "approved")
    await _allowed(db, _PUBLISH, {"w": f.wf})


@pytest.mark.asyncio
async def test_approving_and_publishing_in_one_statement_is_refused(db: AsyncSession) -> None:
    """Security probe P1: the approval has to exist BEFORE the publish."""
    f = await _build(db)
    await _move(db, f, "in_review")
    await _refused(
        db, "UPDATE workflows SET review_status = 'approved', reviewed_by_user_id = :r,"
            " reviewed_at = now(), status = 'published', published_at = now() WHERE id = :w",
        {"r": f.reviewer, "w": f.wf}, "has not been approved",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "review"),
    [("published", "approved"), ("archived", "approved"), ("draft", "approved"),
     ("draft", "in_review")],
)
async def test_a_version_is_born_an_unreviewed_draft(
    db: AsyncSession, status: str, review: str
) -> None:
    """Security probe P2: no INSERT skips the review."""
    f = await _build(db)
    await _refused(
        db, "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
            " review_status, created_at, updated_at)"
            " VALUES (gen_random_uuid(), :c, :r, 9, :s, :rs, now(), now())",
        {"c": f.company, "r": f.req, "s": status, "rs": review},
        "must be created as an unreviewed draft",
    )


@pytest.mark.asyncio
async def test_an_archived_version_never_comes_back(db: AsyncSession) -> None:
    """Security probe P3: archived → published is refused, and archived is frozen."""
    f = await _build(db)
    await _move(db, f, "archived")
    await _refused(db, _PUBLISH, {"w": f.wf}, "only a draft can be published")
    await _refused(db, "UPDATE workflows SET auto_advance_rounds = NOT auto_advance_rounds"
                       " WHERE id = :w", {"w": f.wf}, "is archived and cannot change")
    await _refused(db, "UPDATE workflow_rounds SET title = 'Changed' WHERE id = :r",
                   {"r": f.r1}, "is archived")
    await _allowed(db, "UPDATE workflows SET deleted_at = now() WHERE id = :w", {"w": f.wf})


@pytest.mark.asyncio
async def test_approval_needs_a_version_in_review(db: AsyncSession) -> None:
    f = await _build(db)
    for frm in ("draft", "changes_requested"):
        if frm == "changes_requested":
            await _move(db, f, "changes_requested")
        await _refused(db, _DECIDE, {"t": "approved", "r": f.reviewer, "w": f.wf},
                       "cannot be approved")


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["submitter", "hr"])
async def test_nobody_approves_their_own_version(db: AsyncSession, who: str) -> None:
    """Separation of duties: not the person who submitted it, nor who wrote it."""
    f = await _build(db)
    await _move(db, f, "in_review")
    await _refused(db, _DECIDE, {"t": "approved", "r": getattr(f, who), "w": f.wf},
                   "cannot be approved")


@pytest.mark.asyncio
async def test_the_submitter_cannot_be_rewritten_to_approve_their_own_work(
    db: AsyncSession,
) -> None:
    """Security probe P4: approve as the submitter while renaming the submitter."""
    f = await _build(db)
    await _move(db, f, "in_review")
    await _refused(
        db, "UPDATE workflows SET review_status = 'approved', reviewed_by_user_id = :a,"
            " submitted_by_user_id = :b WHERE id = :w",
        {"a": f.submitter, "b": f.reviewer, "w": f.wf}, "cannot be changed",
    )
    for other in (f.reviewer, None):
        await _refused(db, "UPDATE workflows SET submitted_by_user_id = :b WHERE id = :w",
                       {"b": other, "w": f.wf}, "cannot be changed")


@pytest.mark.asyncio
async def test_approval_is_of_what_was_submitted(db: AsyncSession) -> None:
    f = await _build(db)
    await _move(db, f, "in_review")
    await _refused(
        db, "UPDATE workflows SET review_status = 'approved', reviewed_by_user_id = :r,"
            " review_fingerprint = 'something else' WHERE id = :w",
        {"r": f.reviewer, "w": f.wf}, "cannot be approved",
    )
    await _refused(
        db, "UPDATE workflows SET review_status = 'approved', reviewed_by_user_id = :r,"
            " review_fingerprint = NULL WHERE id = :w",
        {"r": f.reviewer, "w": f.wf}, "cannot be approved",
    )


@pytest.mark.asyncio
async def test_a_submission_names_who_and_what(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(db, "UPDATE workflows SET review_status = 'in_review', review_fingerprint = 'x'"
                       " WHERE id = :w", {"w": f.wf}, "cannot be submitted")
    await _refused(db, "UPDATE workflows SET review_status = 'in_review',"
                       " submitted_by_user_id = :s WHERE id = :w",
                   {"s": f.submitter, "w": f.wf}, "cannot be submitted")
    await _move(db, f, "approved")
    await _refused(db, _SUBMIT, {"s": f.submitter, "fp": "fp", "w": f.wf}, "cannot be submitted")


@pytest.mark.asyncio
async def test_sending_back_needs_a_reviewer_other_than_the_submitter(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(db, _DECIDE, {"t": "changes_requested", "r": f.reviewer, "w": f.wf},
                   "cannot be sent back")
    await _move(db, f, "in_review")
    await _refused(db, _DECIDE, {"t": "changes_requested", "r": f.submitter, "w": f.wf},
                   "cannot be sent back")
    await _allowed(db, _DECIDE, {"t": "changes_requested", "r": f.reviewer, "w": f.wf})


@pytest.mark.asyncio
async def test_a_live_versions_review_is_closed(db: AsyncSession) -> None:
    f = await _build(db)
    await _move(db, f, "published")
    await _refused(db, "UPDATE workflows SET review_status = 'draft' WHERE id = :w",
                   {"w": f.wf}, "its review is closed")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["in_review", "approved"])
async def test_what_the_reviewer_saw_cannot_change(db: AsyncSession, state: str) -> None:
    f = await _build(db)
    await _move(db, f, state)
    await _refused(db, "UPDATE workflows SET auto_advance_rounds = NOT auto_advance_rounds"
                       " WHERE id = :w", {"w": f.wf}, "cannot be edited")
    await _refused(db, "UPDATE workflow_rounds SET title = 'Changed' WHERE id = :r",
                   {"r": f.r1}, "rounds and criteria cannot change")
    await _refused(
        db,
        "INSERT INTO round_criteria (id, company_id, round_id, competency_id, competency_name,"
        " weight, created_at) VALUES (gen_random_uuid(), :c, :r, 'x', 'X', 1, now())",
        {"c": f.company, "r": f.r1}, "rounds and criteria cannot change",
    )
    await _refused(
        db,
        "UPDATE workflow_rounds SET on_fail_next_round_id = :r2 WHERE id = :r1",
        {"r1": f.r1, "r2": f.r2}, "rounds and criteria cannot change",
    )


@pytest.mark.asyncio
async def test_the_review_record_itself_can_move(db: AsyncSession) -> None:
    f = await _build(db)
    await _move(db, f, "in_review")
    await _allowed(db, "UPDATE workflows SET review_status = 'draft', review_note = 'x'"
                       " WHERE id = :w", {"w": f.wf})
    await _allowed(db, "UPDATE workflow_rounds SET title = 'Editable again' WHERE id = :r",
                   {"r": f.r1})


@pytest.mark.asyncio
async def test_changes_requested_is_editable(db: AsyncSession) -> None:
    f = await _build(db)
    await _move(db, f, "changes_requested")
    await _allowed(db, "UPDATE workflow_rounds SET title = 'Fixed' WHERE id = :r", {"r": f.r1})


@pytest.mark.asyncio
async def test_an_exam_edited_after_approval_stops_the_publish(db: AsyncSession) -> None:
    """Security L1: an exam round is its own object and stays editable, so the
    fingerprint covers its content and publishing re-checks it."""
    f = await _build(db)
    exam, er, sec, q = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    p = {"c": f.company, "x": exam, "er": er, "s": sec, "q": q, "r": f.r1}
    for sql in (
        "INSERT INTO exams (id, company_id, title) VALUES (:x, :c, 'Exam')",
        "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, status, position)"
        " VALUES (:er, :x, :c, 1, 'Written', 'published', 0)",
        "INSERT INTO exam_sections (id, round_id, exam_id, company_id, title, position)"
        " VALUES (:s, :er, :x, :c, 'MCQ', 0)",
        "INSERT INTO exam_questions (id, exam_id, company_id, prompt, options, correct_index,"
        " position, section_id) VALUES (:q, :x, :c, 'Two plus two?',"
        " '[\"3\", \"4\"]'::jsonb, 1, 0, :s)",
        "UPDATE workflow_rounds SET kind = 'mcq', exam_round_id = :er WHERE id = :r",
    ):
        await db.execute(text(sql), p)
    before = await workflow_fingerprint(db, f.wf)
    await _move(db, f, "approved", fingerprint=before)
    await db.execute(text("UPDATE exam_questions SET correct_index = 0 WHERE id = :q"), {"q": q})
    assert await workflow_fingerprint(db, f.wf) != before, "the exam's content is in the hash"
    with pytest.raises(WorkflowError, match="changed after it was approved"):
        await publish(db, company_id=f.company, workflow_id=f.wf)
    await db.execute(text("UPDATE exam_questions SET correct_index = 1 WHERE id = :q"), {"q": q})
    assert await workflow_fingerprint(db, f.wf) == before, "timestamps are not content"


@pytest.mark.asyncio
async def test_review_history_is_append_only(db: AsyncSession) -> None:
    f = await _build(db)
    ev = uuid.uuid4()
    await db.execute(
        text("INSERT INTO workflow_review_events (id, company_id, workflow_id, action,"
             " actor_user_id, note) VALUES (:i, :c, :w, 'submitted', :u, 'please')"),
        {"i": ev, "c": f.company, "w": f.wf, "u": f.hr},
    )
    await _refused(db, "UPDATE workflow_review_events SET note = 'rewritten' WHERE id = :i",
                   {"i": ev}, "append-only")
    await _refused(db, "DELETE FROM workflow_review_events WHERE id = :i", {"i": ev},
                   "append-only")
    await _refused(
        db, "INSERT INTO workflow_review_events (id, company_id, workflow_id, action)"
            " VALUES (gen_random_uuid(), :c, :w, 'rejected')",
        {"c": f.company, "w": f.wf}, "ck_workflow_review_events_action",
    )


# ===========================================================================
# O2 — dry-run results are evidence
# ===========================================================================
@pytest.mark.asyncio
async def test_a_dry_run_result_cannot_be_edited_or_misreported(db: AsyncSession) -> None:
    f = await _build(db)
    sim = uuid.uuid4()
    await db.execute(
        text("INSERT INTO workflow_simulations (id, company_id, workflow_id, run_by_user_id,"
             " fingerprint, status, errors, warnings, scenarios, result)"
             " VALUES (:i, :c, :w, :u, 'fp', 'warnings', 0, 2, 3, '{}'::jsonb)"),
        {"i": sim, "c": f.company, "w": f.wf, "u": f.hr},
    )
    await _refused(db, "UPDATE workflow_simulations SET status = 'passed' WHERE id = :i",
                   {"i": sim}, "append-only")
    await _refused(
        db, "INSERT INTO workflow_simulations (id, company_id, workflow_id, fingerprint, status,"
            " errors, warnings, scenarios, result)"
            " VALUES (gen_random_uuid(), :c, :w, 'fp', 'passed', 2, 0, 1, '{}'::jsonb)",
        {"c": f.company, "w": f.wf}, "ck_workflow_simulations_failed_iff_errors",
    )


# ===========================================================================
# O3 — branches stay inside their workflow
# ===========================================================================
@pytest.mark.asyncio
async def test_a_branch_cannot_point_into_another_workflow(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, "UPDATE workflow_rounds SET on_fail_next_round_id = :o WHERE id = :r",
        {"o": f.other_round, "r": f.r1}, "fk_workflow_rounds_on_fail",
    )


@pytest.mark.asyncio
async def test_a_fast_track_needs_both_halves_and_a_real_score(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(db, "UPDATE workflow_rounds SET on_fast_track_next_round_id = :r2"
                       " WHERE id = :r1", {"r1": f.r1, "r2": f.r2},
                   "ck_workflow_rounds_fast_track_pair")
    await _refused(db, "UPDATE workflow_rounds SET on_fast_track_next_round_id = :r2,"
                       " fast_track_min_percent = 101 WHERE id = :r1",
                   {"r1": f.r1, "r2": f.r2}, "ck_workflow_rounds_fast_track_range")
    await _refused(db, "UPDATE workflow_rounds SET on_fail_next_round_id = :r1 WHERE id = :r1",
                   {"r1": f.r1}, "ck_workflow_rounds_fail_not_self")


@pytest.mark.asyncio
async def test_a_published_versions_branches_are_frozen_too(db: AsyncSession) -> None:
    f = await _build(db)
    await _move(db, f, "published")
    await _refused(db, "UPDATE workflow_rounds SET on_fail_next_round_id = :r2 WHERE id = :r1",
                   {"r1": f.r1, "r2": f.r2}, "is published")


# ===========================================================================
# O1 — one setting per stage; the clock ignores notes
# ===========================================================================
@pytest.mark.asyncio
async def test_one_setting_per_stage(db: AsyncSession) -> None:
    f = await _build(db)
    for rid in (f.r1, None):
        await db.execute(
            text("INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
                 " sla_hours) VALUES (gen_random_uuid(), :c, :w, :r, 24)"),
            {"c": f.company, "w": f.wf, "r": rid},
        )
    await _refused(
        db, "INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
            " sla_hours) VALUES (gen_random_uuid(), :c, :w, :r, 48)",
        {"c": f.company, "w": f.wf, "r": f.r1}, "uq_stage_settings_round",
    )
    await _refused(
        db, "INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
            " sla_hours) VALUES (gen_random_uuid(), :c, :w, NULL, 48)",
        {"c": f.company, "w": f.wf}, "uq_stage_settings_decision",
    )
    await _refused(
        db, "INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
            " sla_hours) VALUES (gen_random_uuid(), :c, :w, :r, 24)",
        {"c": f.company, "w": f.wf, "r": f.other_round}, "fk_stage_settings_round",
    )


@pytest.mark.asyncio
async def test_stage_settings_stay_editable_on_a_live_version(db: AsyncSession) -> None:
    """Owners and SLAs are operational — changing them re-grades nobody."""
    f = await _build(db)
    await db.execute(
        text("INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
             " sla_hours) VALUES (gen_random_uuid(), :c, :w, :r, 24)"),
        {"c": f.company, "w": f.wf, "r": f.r1},
    )
    await _move(db, f, "published")
    await _allowed(db, "UPDATE workflow_stage_settings SET sla_hours = 48 WHERE round_id = :r",
                   {"r": f.r1})


@pytest.mark.asyncio
async def test_the_sla_clock_starts_at_the_last_real_move(db: AsyncSession) -> None:
    f = await _build(db)
    for frm, to, ago in (("new", "shortlisted", "5 hours"), ("shortlisted", "shortlisted", "1 hour")):
        await db.execute(
            text("INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
                 " automated, reason, occurred_at)"
                 f" VALUES (:c, :e, :f, :t, true, 'x', now() - interval '{ago}')"),
            {"c": f.company, "e": f.enrolment, "f": frm, "t": to},
        )
    hours = await db.scalar(
        text("SELECT extract(epoch FROM now() - enrolment_stage_entered_at(:e, now())) / 3600"),
        {"e": f.enrolment},
    )
    assert 4.9 < float(hours) < 5.1, "a note that moved nothing must not restart the clock"


@pytest.mark.asyncio
async def test_an_exception_needs_a_reason_and_a_consistent_status(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, "INSERT INTO stage_exceptions (id, company_id, enrolment_id, stage_label, reason)"
            " VALUES (gen_random_uuid(), :c, :e, 'Screen', 'short')",
        {"c": f.company, "e": f.enrolment}, "ck_stage_exceptions_reason_len",
    )
    await _refused(
        db, "INSERT INTO stage_exceptions (id, company_id, enrolment_id, stage_label, reason,"
            " status) VALUES (gen_random_uuid(), :c, :e, 'Screen', 'long enough reason', 'resolved')",
        {"c": f.company, "e": f.enrolment}, "ck_stage_exceptions_resolved_at",
    )


@pytest.mark.asyncio
async def test_a_redacted_exception_stays_redacted(db: AsyncSession) -> None:
    """Security L2: once erasure has redacted it, nothing is written back."""
    f = await _build(db)
    x = uuid.uuid4()
    await db.execute(
        text("INSERT INTO stage_exceptions (id, company_id, enrolment_id, stage_label, reason)"
             " VALUES (:x, :c, :e, 'Screen', 'Candidate asked to reschedule')"),
        {"x": x, "c": f.company, "e": f.enrolment},
    )
    await _allowed(db, "UPDATE stage_exceptions SET reason = '[redacted]', resolution_note = NULL,"
                       " redacted_at = now() WHERE id = :x", {"x": x})
    await _refused(db, "UPDATE stage_exceptions SET reason = 'Written back after erasure'"
                       " WHERE id = :x", {"x": x}, "ck_stage_exceptions_redacted")
    await _refused(db, "UPDATE stage_exceptions SET status = 'resolved', resolved_at = now(),"
                       " resolution_note = 'about them' WHERE id = :x", {"x": x},
                   "ck_stage_exceptions_redacted")
    await _refused(db, "UPDATE stage_exceptions SET redacted_at = NULL,"
                       " reason = 'Written back after erasure' WHERE id = :x", {"x": x},
                   "stays redacted")
    await _allowed(db, "UPDATE stage_exceptions SET status = 'resolved', resolved_at = now()"
                       " WHERE id = :x", {"x": x})


@pytest.mark.asyncio
async def test_an_exception_counts_on_a_stage_nobody_set_an_sla_for(db: AsyncSession) -> None:
    """Code review: the decision queue's count must not depend on SLA settings."""
    f = await _build(db)
    await db.execute(
        text("INSERT INTO stage_exceptions (id, company_id, enrolment_id, stage_label, reason)"
             " VALUES (gen_random_uuid(), :c, :e, 'Screen', 'Candidate asked to reschedule')"),
        {"c": f.company, "e": f.enrolment},
    )
    out = await sla_for_enrolments(db, company_id=f.company, enrolment_ids=[f.enrolment])
    info = out[str(f.enrolment)]
    assert info["open_exceptions"] == 1
    assert info["sla"] is None and info["owner_name"] is None
