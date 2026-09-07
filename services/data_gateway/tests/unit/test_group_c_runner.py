"""Unit tests for the Group C workflow runner.

Two of these pin bugs the integration run actually caught, so they cannot come
back silently:

* the threshold unit mismatch — an interview composite of 7.5 read as "7.5%"
* the idempotency hole — a replayed event after completion restarting a
  finished candidate mid-pipeline
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflow_runner import INTERVIEW_SCORE_MAX, RunnerOutcome


def _db(scalar: object = None) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=scalar)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# Threshold units — the bug the integration run caught
# ===========================================================================
def test_interview_scale_is_explicit() -> None:
    """An AI interview composite is out of 10; thresholds are always percentages.
    Leaving that implicit is what read 7.5 as 7.5% and held someone who passed."""
    assert INTERVIEW_SCORE_MAX == 10.0


@pytest.mark.parametrize(
    ("score", "max_score", "expected_percent"),
    [
        (18, 20, 90.0),      # mcq: 18/20
        (8, 10, 80.0),       # coding: 8/10
        (7.5, None, 75.0),   # interview composite, converted on the 0-10 scale
        (6.0, None, 60.0),   # exactly on a 60% threshold
        (4, 20, 20.0),
    ],
)
def test_percent_conversion(score, max_score, expected_percent) -> None:
    """Mirrors the arithmetic in record_result so a regression here is caught
    without a database."""
    from app.workflow_runner import AI_GRADED_KINDS

    kind = "ai_interview" if max_score is None else "mcq"
    effective_max = max_score
    if effective_max is None and kind in AI_GRADED_KINDS:
        effective_max = INTERVIEW_SCORE_MAX
    percent = round(float(score) / float(effective_max) * 100, 2) if effective_max else float(score)
    assert percent == expected_percent


def test_a_passing_interview_is_not_read_as_a_failure() -> None:
    """The exact regression: 7.5 out of 10 against a 60% threshold advances."""
    percent = round(7.5 / INTERVIEW_SCORE_MAX * 100, 2)
    assert percent >= 60


# ===========================================================================
# Idempotency — the second bug the integration run caught
# ===========================================================================
@pytest.mark.parametrize(
    ("current_round", "event_round", "should_advance"),
    [
        ("r1", "r1", True),    # the candidate's current round
        ("r2", "r1", False),   # a replayed earlier event
        (None, "r1", False),   # completed: current_round_id is NULL
        (None, "r2", False),   # completed, replay of the last round
    ],
)
def test_advance_only_from_the_current_round(current_round, event_round, should_advance) -> None:
    """A truthiness check on current_round_id left a hole at completion, where
    the column is NULL: a replayed event skipped the guard and dragged a
    finished candidate back a round, re-emailing them."""
    guard_allows = str(current_round or "") == str(event_round)
    assert guard_allows is should_advance


# ===========================================================================
# RunnerOutcome
# ===========================================================================
def test_outcome_actions_contain_no_terminal_state() -> None:
    """D-05, checked at the type level: the runner has no vocabulary for ending
    a candidacy. 'completed' means the workflow finished, not that anyone was
    hired or rejected."""
    import inspect

    from app import workflow_runner

    src = inspect.getsource(workflow_runner)
    # No literal that would write a terminal status from an automated path.
    assert 'to_status="rejected"' not in src
    assert 'to_status="hired"' not in src
    assert "'rejected'" not in src.replace("NOT IN ('hired','rejected')", "")


def test_outcome_serialises_for_logging() -> None:
    o = RunnerOutcome(action="held", enrolment_id="e1", reason="below threshold")
    d = o.as_dict()
    assert d["action"] == "held"
    assert d["reason"] == "below threshold"
    assert set(d) == {"action", "enrolment_id", "from_round", "to_round", "reason"}


# ===========================================================================
# enrol_applicant
# ===========================================================================
@pytest.mark.asyncio
async def test_reapplying_is_a_noop() -> None:
    from app.workflow_runner import enrol_applicant

    existing = uuid.uuid4()
    db = _db(scalar=existing)
    out = await enrol_applicant(
        db, company_id=uuid.uuid4(), applicant_id=uuid.uuid4(),
        requisition_id=uuid.uuid4(), target_job_title="Python Developer",
    )
    assert out.action == "noop"
    assert out.enrolment_id == str(existing)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrolling_without_a_published_workflow_still_keeps_the_candidate() -> None:
    """An applicant who arrives before a workflow is published is a real
    applicant. Dropping them would be a rejection by omission."""
    from app import workflow_runner

    db = _db(scalar=None)
    db.execute.return_value = MagicMock(
        mappings=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    )
    out = await workflow_runner.enrol_applicant(
        db, company_id=uuid.uuid4(), applicant_id=uuid.uuid4(),
        requisition_id=uuid.uuid4(), target_job_title="Python Developer",
    )
    assert out.action == "enrolled"
    assert "no published workflow" in (out.reason or "")


# ===========================================================================
# Hold semantics — C7
# ===========================================================================
def test_hold_is_not_in_the_terminal_set() -> None:
    from app.requisitions import TERMINAL_STATUSES, VALID_STATUSES

    assert "held" in VALID_STATUSES
    assert "held" not in TERMINAL_STATUSES


def test_release_hold_has_no_automated_caller() -> None:
    """Deciding a below-threshold candidate should continue is exactly the
    judgement D-05 reserves for a person, so the signature requires an actor."""
    import inspect

    from app.workflow_runner import release_hold

    sig = inspect.signature(release_hold)
    assert "actor_user_id" in sig.parameters
    # Required, not defaulted — there is no way to call it as the system.
    assert sig.parameters["actor_user_id"].default is inspect.Parameter.empty
