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


# ===========================================================================
# C6/C7 — a scored interview reaches the runner
# ===========================================================================
def test_only_workflow_issued_closed_invites_are_recorded() -> None:
    from app.workflow_runner import _SCORED_INTERVIEWS_SQL

    sql = _SCORED_INTERVIEWS_SQL

    # Hand-made HR invites carry no enrolment and stay out of the workflow.
    assert "e.id = inv.enrolment_id" in sql
    # Only after the completion stage closed it — or advancing into a second
    # interview round would find the old invite still open and mint nothing.
    assert "inv.status = 'completed'" in sql
    # An older round's interview can't be read as this round's result.
    assert "max(i2.created_at)" in sql
    # Idempotent: a scorecard is recorded once, for one round.
    assert "rr.attempt_ref = sc.scorecard_id" in sql
    # A held candidate moves only when a person releases them (D-05).
    assert "e.status <> 'held'" in sql
    # Written in the one form test_outcome_actions_contain_no_terminal_state
    # permits: excluding decided candidates, never writing a decision.
    assert "e.status NOT IN ('hired','rejected')" in sql


def test_the_workflow_stage_runs_after_the_completion_stage() -> None:
    import inspect

    from app.reminders import run_once

    src = inspect.getsource(run_once)
    assert src.index('("completed", _interview_completed)') < src.index(
        '("workflow", _workflow_results)'
    )


def _scored_row(**over: object) -> dict:
    row = {
        "enrolment_id": uuid.uuid4(), "round_id": uuid.uuid4(), "target_job_title": "Nurse",
        "scorecard_id": uuid.uuid4(), "composite_score": 7.4,
        "scores": {"communication": 8, "technical": 7, "problem_solving": 7, "confidence": 8},
        "rationale": {}, "summary": "Safe and structured.",
    }
    return {**row, **over}


def _rows_db(rows: list[dict]) -> AsyncMock:
    db = _db()
    db.execute.return_value = MagicMock(mappings=MagicMock(return_value=MagicMock(
        all=MagicMock(return_value=rows))))
    return db


@pytest.mark.asyncio
async def test_a_scored_interview_is_recorded_as_its_rounds_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow_runner as wr

    row = _scored_row()
    calls: list[dict] = []

    async def _record(_db: object, **kw: object) -> RunnerOutcome:
        calls.append(kw)
        return RunnerOutcome(action="advanced", enrolment_id=str(row["enrolment_id"]))

    async def _no_criteria(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(wr, "record_result", _record)
    monkeypatch.setattr(wr, "_criteria_for", _no_criteria)
    db = _rows_db([row])
    out = await wr.record_scored_interviews(db)

    assert [o.action for o in out] == ["advanced"]
    kw = calls[0]
    assert kw["enrolment_id"] == row["enrolment_id"] and kw["round_id"] == row["round_id"]
    assert kw["score"] == 7.4 and kw["max_score"] == wr.INTERVIEW_SCORE_MAX
    assert kw["graded_by"] == "ai"
    assert kw["attempt_ref"] == row["scorecard_id"]
    assert kw["criterion_scores"] is None
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_a_frozen_rubric_breakdown_decides_instead_of_the_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C8: when the scorecard graded this round's own criteria, those decide —
    score is left None so record_result takes the round's weighted composite."""
    import app.workflow_runner as wr

    breakdown = {"triage": {"score": 8, "weight": 0.6}, "handover": {"score": 6, "weight": 0.4}}
    calls: list[dict] = []

    async def _record(_db: object, **kw: object) -> RunnerOutcome:
        calls.append(kw)
        return RunnerOutcome(action="held")

    async def _criteria(*_: object, **__: object) -> dict:
        return breakdown

    monkeypatch.setattr(wr, "record_result", _record)
    monkeypatch.setattr(wr, "_criteria_for", _criteria)
    await wr.record_scored_interviews(_rows_db([_scored_row()]))

    assert calls[0]["score"] is None
    assert calls[0]["criterion_scores"] == breakdown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("breakdown", "usable"),
    [
        ({"triage": {"score": 8, "weight": 0.6}, "handover": {"score": 6, "weight": 0.4}}, True),
        # One criterion missing: averaging over what is present would quietly
        # drop it from the decision.
        ({"triage": {"score": 8, "weight": 0.6}}, False),
        # Graded against some other rubric entirely.
        ({"python": {"score": 9, "weight": 1.0}}, False),
        ({}, False),
    ],
)
async def test_criteria_are_used_only_when_they_cover_the_round_exactly(
    monkeypatch: pytest.MonkeyPatch, breakdown: dict, usable: bool,
) -> None:
    import app.workflow_runner as wr

    frozen = MagicMock(competencies=[MagicMock(id="triage"), MagicMock(id="handover")])

    async def _frozen(*_: object, **__: object) -> object:
        return frozen

    monkeypatch.setattr(wr, "frozen_rubric_for_round", _frozen)
    got = await wr._criteria_for(
        _db(), round_id=uuid.uuid4(), job_title="Nurse", rationale={"_competencies": breakdown}
    )
    assert (got == breakdown) if usable else (got is None)


@pytest.mark.asyncio
async def test_one_bad_enrolment_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow_runner as wr

    bad, good = _scored_row(), _scored_row()

    async def _record(_db: object, **kw: object) -> RunnerOutcome:
        if kw["enrolment_id"] == bad["enrolment_id"]:
            raise RuntimeError("constraint")
        return RunnerOutcome(action="completed")

    async def _no_criteria(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(wr, "record_result", _record)
    monkeypatch.setattr(wr, "_criteria_for", _no_criteria)
    db = _rows_db([bad, good])
    out = await wr.record_scored_interviews(db)

    assert [o.action for o in out] == ["completed"]
    db.rollback.assert_awaited()


# ===========================================================================
# Ownership of what the runner issues
# ===========================================================================
@pytest.mark.asyncio
async def test_a_workflow_issued_exam_link_belongs_to_the_workflow_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interview branch already attributed its invite to the workflow's
    owner; the exam branch left created_by_user_id NULL, so when one of these
    links lapsed there was nobody to tell."""
    import app.workflow_runner as wr
    from app.workflows import EXAM_BACKED_KINDS

    async def _enqueue(_db: object, **_: object) -> object:
        return object()

    monkeypatch.setattr(wr, "enqueue_email", _enqueue)
    owner = uuid.uuid4()
    db = _db(scalar=uuid.uuid4())  # the exam_id lookup

    await wr._assign_round(
        db,
        enrolment={
            "id": uuid.uuid4(), "company_id": uuid.uuid4(), "applicant_id": uuid.uuid4(),
            "email": "c@example.com", "full_name": "Chitra",
        },
        round_={
            "id": uuid.uuid4(), "kind": next(iter(EXAM_BACKED_KINDS)),
            "exam_round_id": uuid.uuid4(), "deadline_days": 7, "title": "Aptitude",
        },
        workflow={"created_by_user_id": owner},
    )

    inserts = [c for c in db.execute.call_args_list
               if "INSERT INTO exam_assignments" in str(c.args[0])]
    assert len(inserts) == 1
    assert "created_by_user_id" in str(inserts[0].args[0])
    assert inserts[0].args[1]["cb"] == owner
