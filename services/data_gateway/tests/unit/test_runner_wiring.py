"""The two places the workflow runner is called from, and the ones it isn't.

Until this change ``on_shortlisted`` and ``record_result`` had no caller outside
the test suite. Both were written, both were tested, and neither was connected —
so a shortlisted candidate never got round one and a submitted exam never moved
anybody. The runner logic itself is covered by ``test_group_c_runner.py``; what
is tested here is the wiring, because that is what was missing.

Three of these assert an ABSENCE, which is the unusual part. A regression here
would not raise or fail a request — it would silently go back to doing nothing,
which is exactly how the gap survived being written, reviewed and shipped.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _rows(*items: object) -> MagicMock:
    res = MagicMock()
    res.all = MagicMock(return_value=list(items))
    res.first = MagicMock(return_value=items[0] if items else None)
    return res


def _db(*results: object) -> AsyncMock:
    db = AsyncMock()
    queue = list(results)

    async def _execute(*_a: object, **_k: object) -> object:
        return queue.pop(0) if queue else _rows()

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _row(**kw: object) -> MagicMock:
    r = MagicMock()
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# ===========================================================================
# The connection itself
# ===========================================================================
def test_shortlisting_an_enrolment_calls_the_runner() -> None:
    from app.routers.hr_requisitions import set_enrolment_status

    src = inspect.getsource(set_enrolment_status)
    assert "on_shortlisted" in src, (
        "recording the status is half the gate — this is where round one starts"
    )


def test_shortlisting_from_the_applicant_board_calls_the_runner() -> None:
    from app.routers.hr_applicants import update_applicant_status

    src = inspect.getsource(update_applicant_status)
    assert "on_shortlisted" in src


def test_submitting_a_round_calls_the_runner() -> None:
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    assert "record_result" in src, (
        "a graded attempt that does not move the enrolment is the original defect"
    )


def test_the_exam_result_is_recorded_before_the_commit() -> None:
    """Atomic with the attempt, or the defect comes back in a narrower form.

    An attempt that commits without its round result leaves the candidate
    graded and stationary — the same end state, reached by a different route.
    """
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    runner_at = src.index("record_result(")
    commit_at = src.index("await db.commit()", runner_at)
    assert runner_at < commit_at


def test_the_exam_result_is_not_swallowed() -> None:
    """Deliberately unlike the auto-advance beside it, which IS best-effort.

    Failing the submit means the candidate retries and grading (idempotent)
    runs again. Swallowing it means they are graded and never advance, silently
    — the thing this whole change exists to stop.
    """
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    block = src[src.index("pair = await enrolment_awaiting_exam_round") : src.index("# Auto-advance")]
    assert "except" not in block


def test_percent_is_sent_with_an_explicit_max_of_100() -> None:
    """Without max_score=100 the runner infers a scale, and 67% is read as 67
    out of 10 — a passing candidate held at 670%, or worse, the reverse."""
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    call = src[src.index("record_result(") : src.index("# Auto-advance")]
    assert "max_score=100.0" in call
    assert "score=float(percent)" in call


# ===========================================================================
# A4 — HR hears that an exam was submitted
# ===========================================================================
def _submit_notice() -> str:
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    start = src.index('kind="exam_submitted"')
    end = src.index("\n", src.index("dedupe_key=", start))
    return src[src.rindex("create_notification(", 0, start) : end]


def test_an_exam_submission_notifies_whoever_sent_the_link() -> None:
    call = _submit_notice()
    # The assignment's sender first — for a workflow-issued link that is the
    # workflow owner — falling back to the exam's author for older rows.
    assert "user_id=ctx.assignment.created_by_user_id or ctx.exam.created_by_user_id" in call
    assert 'link=f"/hr/exams/{ctx.exam.id}/attempts/{fresh_attempt.id}"' in call


def test_the_submission_notice_is_atomic_and_announced_once() -> None:
    """Staged before the one commit, so it exists exactly when the attempt does;
    keyed on the attempt, so a retried submit cannot announce it twice."""
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    notice_at = src.index('kind="exam_submitted"')
    assert notice_at < src.index("await db.commit()", notice_at)
    assert 'dedupe_key=f"exam_submitted:{fresh_attempt.id}"' in _submit_notice()


def test_the_submission_notice_states_a_score_not_a_decision() -> None:
    """The pass mark is a fact about the score. Whether the candidate proceeds
    is HR's call (D-05), and the notice must not read as having made it."""
    call = _submit_notice().lower()
    assert "pass mark" in call
    for word in ("reject", "failed", "unsuccessful", "hire"):
        assert word not in call


# ===========================================================================
# enrolment_awaiting_exam_round — which result belongs to which round
# ===========================================================================
@pytest.mark.asyncio
async def test_finds_the_enrolment_sitting_on_this_exam_round() -> None:
    from app.workflow_runner import enrolment_awaiting_exam_round

    eid, rid = uuid.uuid4(), uuid.uuid4()
    db = _db(_rows(_row(enrolment_id=str(eid), round_id=str(rid))))
    assert await enrolment_awaiting_exam_round(
        db, applicant_id=uuid.uuid4(), exam_round_id=uuid.uuid4()
    ) == (eid, rid)


@pytest.mark.asyncio
async def test_an_exam_outside_any_workflow_is_a_quiet_no_op() -> None:
    """The manual path: HR assigns an exam directly, no workflow involved.

    This is common and correct, so it must skip rather than raise — an error
    here would break the standalone exam feature to serve the workflow one.
    """
    from app.workflow_runner import enrolment_awaiting_exam_round

    assert await enrolment_awaiting_exam_round(
        _db(_rows()), applicant_id=uuid.uuid4(), exam_round_id=uuid.uuid4()
    ) is None


@pytest.mark.asyncio
async def test_the_match_goes_through_the_round_the_candidate_was_sent_to() -> None:
    """Not through workflow membership.

    A workflow may use one exam round in more than one place, and an applicant
    may hold several enrolments. current_round_id is the only thing that says
    which round this particular result is for.
    """
    from app.workflow_runner import enrolment_awaiting_exam_round

    db = _db(_rows(_row(enrolment_id=str(uuid.uuid4()), round_id=str(uuid.uuid4()))))
    await enrolment_awaiting_exam_round(
        db, applicant_id=uuid.uuid4(), exam_round_id=uuid.uuid4()
    )
    sql = str(db.execute.await_args.args[0])
    assert "wr.id = e.current_round_id" in sql
    assert "wr.exam_round_id = :er" in sql


# ===========================================================================
# sole_live_enrolment — the ambiguity guard
# ===========================================================================
@pytest.mark.asyncio
async def test_one_open_application_is_started() -> None:
    from app.workflow_runner import sole_live_enrolment

    eid = uuid.uuid4()
    got = await sole_live_enrolment(
        _db(_rows(_row(id=str(eid)))), applicant_id=uuid.uuid4(), company_id=uuid.uuid4()
    )
    assert got == eid


@pytest.mark.asyncio
async def test_two_open_applications_start_neither() -> None:
    """Shortlisting on the applicant board says something about the person.

    It does not say which of their three applications should begin, and firing
    all three would email exam links for jobs nobody decided on.
    """
    from app.workflow_runner import sole_live_enrolment

    db = _db(_rows(_row(id=str(uuid.uuid4())), _row(id=str(uuid.uuid4()))))
    assert await sole_live_enrolment(
        db, applicant_id=uuid.uuid4(), company_id=uuid.uuid4()
    ) is None


@pytest.mark.asyncio
async def test_no_application_is_not_an_error() -> None:
    """An applicant HR uploaded by hand has no enrolment at all."""
    from app.workflow_runner import sole_live_enrolment

    assert await sole_live_enrolment(
        _db(_rows()), applicant_id=uuid.uuid4(), company_id=uuid.uuid4()
    ) is None


@pytest.mark.asyncio
async def test_decided_applications_do_not_count_as_open() -> None:
    """Someone hired for one role and applying for another has one LIVE
    application, so shortlisting them should still start it."""
    from app.workflow_runner import sole_live_enrolment

    db = _db(_rows(_row(id=str(uuid.uuid4()))))
    await sole_live_enrolment(db, applicant_id=uuid.uuid4(), company_id=uuid.uuid4())
    sql = str(db.execute.await_args.args[0])
    assert "status NOT IN ('hired','rejected')" in sql
    # LIMIT 2, not 1: the query has to be able to see that there are two.
    assert "LIMIT 2" in sql


# ===========================================================================
# Not on a re-save
# ===========================================================================
def test_the_enrolment_gate_ignores_a_re_save() -> None:
    """Re-saving 'shortlisted' must not re-fire the runner.

    on_shortlisted is idempotent anyway, but the guard keeps the ledger honest:
    without it every save writes another transition and the candidate's history
    fills with events that did not happen.
    """
    from app.routers.hr_requisitions import set_enrolment_status

    src = inspect.getsource(set_enrolment_status)
    assert 'previous != "shortlisted"' in src


def test_the_applicant_gate_ignores_a_re_save() -> None:
    from app.routers.hr_applicants import update_applicant_status

    src = inspect.getsource(update_applicant_status)
    gate = src.index("if body.status != prev_status:")
    fired = src.index("on_shortlisted")
    assert gate < fired, "the runner call must sit inside the changed-status branch"


def test_starting_from_the_applicant_board_records_a_transition() -> None:
    """Otherwise the enrolment moves with nothing in its history saying why,
    and the candidate's own timeline has a gap where the decision was."""
    from app.routers.hr_applicants import update_applicant_status

    src = inspect.getsource(update_applicant_status)
    assert "record_transition" in src
    assert "automated=False" in src


# ===========================================================================
# The invariant this must not break
# ===========================================================================
def test_nothing_in_the_wiring_can_reject_anybody() -> None:
    """D-05. The runner cannot produce 'rejected', and neither may its callers.

    Checked at the seam rather than only inside the runner: a caller that
    reacted to a failed round by setting 'rejected' itself would defeat the
    guarantee without touching the file that documents it.
    """
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    block = src[src.index("pair = await enrolment_awaiting_exam_round") : src.index("# Auto-advance")]
    assert "reject" not in block.lower()


def test_the_runner_still_has_no_path_to_rejected() -> None:
    import app.workflow_runner as runner

    src = inspect.getsource(runner)
    # Only the two places that READ the terminal set may mention it.
    assignments = [
        line for line in src.splitlines()
        if "'rejected'" in line or '"rejected"' in line
    ]
    for line in assignments:
        assert "NOT IN" in line or "#" in line, f"suspicious mention of rejected: {line.strip()}"


def test_the_runner_reports_what_it_did() -> None:
    """Both call sites log the outcome. Without it, "auto-assign disabled" and
    "no workflow attached" are invisible and look identical to nothing running
    at all — which is how long the original gap went unnoticed."""
    from app.routers.hr_applicants import update_applicant_status
    from app.routers.hr_requisitions import set_enrolment_status

    for fn in (set_enrolment_status, update_applicant_status):
        src = inspect.getsource(fn)
        assert "outcome.action" in src
        assert "outcome.reason" in src
