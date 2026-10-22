"""Unit tests for B2 — every candidate move through the ledger, and the ledger
append-only.

The SQL and the trigger are exercised end to end by
``tests/integration/smoke_group_b_ledger.py``; these pin the rules: which
moves write an entry, who the entry says made them, and what is refused.
"""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.requisitions import ApplicationChoice


def _db() -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# Round moves are stage changes
# ===========================================================================
@pytest.mark.asyncio
async def test_a_round_move_updates_the_round_and_records_where_from_and_to() -> None:
    from app.requisitions import record_round_move

    r1, r2, eid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.side_effect = [MagicMock(first=MagicMock(return_value=("shortlisted", r1))),
                              MagicMock(), MagicMock()]

    moved = await record_round_move(db, enrolment_id=eid, company_id=uuid.uuid4(),
                                    to_round_id=r2, actor_user_id=None, automated=True,
                                    reason="assigned Panel")

    assert moved is True
    update, insert = (c.args for c in db.execute.call_args_list[1:])
    assert "UPDATE enrolments SET current_round_id = :r" in str(update[0])
    assert update[1]["r"] == r2
    assert "INSERT INTO stage_transitions" in str(insert[0])
    # The status does not change between rounds — which is exactly why this
    # used to leave no trace.
    assert insert[1]["s"] == "shortlisted"
    assert insert[1]["fr"] == r1 and insert[1]["tr"] == r2 and insert[1]["auto"] is True


@pytest.mark.asyncio
async def test_staying_on_the_same_round_writes_nothing() -> None:
    from app.requisitions import record_round_move

    r1 = uuid.uuid4()
    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=("shortlisted", r1)))
    assert await record_round_move(db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
                                   to_round_id=r1, actor_user_id=None, automated=True) is False
    assert db.execute.await_count == 1


def test_the_runner_moves_rounds_only_through_the_ledger() -> None:
    """current_round_id has one writer, like status."""
    import app.workflow_runner as wr

    src = inspect.getsource(wr)
    assert "UPDATE enrolments SET current_round_id" not in src
    assert "record_round_move(" in inspect.getsource(wr._move_to_round)
    assert "to_round_id=None" in inspect.getsource(wr._advance)


# ===========================================================================
# The person-shaped boards route decisions to the application
# ===========================================================================
@pytest.mark.asyncio
async def test_a_pipeline_decision_that_cannot_name_an_opening_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.routers.hr_pipeline as hrp
    from app.models import Applicant

    applicant = Applicant(id=uuid.uuid4(), status="interviewed", full_name="Bharat")

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _two(*_: object, **__: object) -> ApplicationChoice:
        return ApplicationChoice(None, None, None, [uuid.uuid4(), uuid.uuid4()])

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "choose_application", _two)
    with pytest.raises(HTTPException) as exc:
        await hrp.decide_applicant(uuid.uuid4(), hrp.DecisionIn(decision="rejected"), MagicMock(),
                                   (uuid.uuid4(), uuid.uuid4()), _db())
    assert exc.value.status_code == 409
    assert "2 openings" in str(exc.value.detail)
    assert applicant.status == "interviewed", "nothing was changed"


@pytest.mark.asyncio
async def test_a_pipeline_decision_on_one_application_goes_through_the_one_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HR-H1: the pipeline board's decision is recorded by ``record_final_decision``,
    the writer the decision queue and the applicant board use, so its rules, its
    reason, its ledger entry, its audit row and its email are theirs."""
    from datetime import UTC, datetime

    import app.routers.hr_pipeline as hrp
    from app.models import Applicant

    eid, hr, company = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    applicant = Applicant(id=uuid.uuid4(), status="interviewed", full_name="Asha",
                          target_job_title="Staff Nurse", target_level="mid",
                          pending_enrichment=False, created_at=datetime.now(tz=UTC))
    calls: list[dict] = []

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _one(*_: object, **__: object) -> ApplicationChoice:
        return ApplicationChoice(eid, "interviewed", "Staff Nurse", [eid])

    async def _decide(_db: object, **kw: object) -> dict:
        calls.append(kw)
        return {"status": kw["decision"]}

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "choose_application", _one)
    monkeypatch.setattr(hrp, "record_final_decision", _decide)
    db = _db()
    db.refresh = AsyncMock()
    await hrp.decide_applicant(uuid.uuid4(),
                               hrp.DecisionIn(decision="hired", rationale="strong panel",
                                              reason_code="skills_fit"),
                               MagicMock(), (hr, company), db)

    assert len(calls) == 1
    call = calls[0]
    assert call["enrolment_id"] == eid and call["company_id"] == company
    assert call["decision"] == "hired" and call["actor_user_id"] == hr
    assert call["reason"] == "strong panel" and call["reason_code"] == "skills_fit"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(applicant)


@pytest.mark.asyncio
async def test_a_refused_pipeline_decision_is_rolled_back_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.routers.hr_pipeline as hrp
    from app.final_decision import DecisionRefusedError
    from app.models import Applicant

    eid = uuid.uuid4()
    applicant = Applicant(id=uuid.uuid4(), status="shortlisted", full_name="Asha")

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _one(*_: object, **__: object) -> ApplicationChoice:
        return ApplicationChoice(eid, "shortlisted", "Welder", [eid])

    async def _refuse(_db: object, **_kw: object) -> dict:
        raise DecisionRefusedError(409, "They have not reached a decision yet.")

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "choose_application", _one)
    monkeypatch.setattr(hrp, "record_final_decision", _refuse)
    db = _db()
    with pytest.raises(HTTPException) as exc:
        await hrp.decide_applicant(
            uuid.uuid4(), hrp.DecisionIn(decision="hired", rationale="Looks great"),
            MagicMock(), (uuid.uuid4(), uuid.uuid4()), db,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "They have not reached a decision yet."
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


def _state_db(**row: object) -> AsyncMock:
    """A session whose decision-state read returns ``row`` (final_decision._STATE_SQL)."""
    db = _db()
    result = MagicMock()
    result.mappings.return_value.first.return_value = {
        "applicant_id": uuid.uuid4(), "current_round_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(), "requisition_id": uuid.uuid4(), "title": "Welder",
        "round_title": "Round 1", **row,
    }
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_the_pipeline_cannot_hire_someone_still_in_the_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HR-H1: shortlisted and sitting round 1 is not a decision yet. The pipeline
    board used to hire them anyway; the decision queue always refused."""
    import app.routers.hr_pipeline as hrp
    from app.models import Applicant

    eid = uuid.uuid4()
    applicant = Applicant(id=uuid.uuid4(), status="shortlisted", full_name="Asha")

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _one(*_: object, **__: object) -> ApplicationChoice:
        return ApplicationChoice(eid, "shortlisted", "Welder", [eid])

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "choose_application", _one)
    db = _state_db(status="shortlisted", awaits_human=False)
    with pytest.raises(HTTPException) as exc:
        await hrp.decide_applicant(
            uuid.uuid4(),
            hrp.DecisionIn(decision="hired", rationale="Strong CV", reason_code="skills_fit"),
            MagicMock(), (uuid.uuid4(), uuid.uuid4()), db,
        )
    assert exc.value.status_code == 409
    assert "still in the workflow" in str(exc.value.detail)
    assert not db.add.called and not db.commit.called


@pytest.mark.asyncio
@pytest.mark.parametrize("rationale", [None, "", "  ", "ok"])
async def test_the_pipeline_needs_a_reason_like_every_other_path(
    monkeypatch: pytest.MonkeyPatch, rationale: str | None,
) -> None:
    """HR-H1: the board said "rationale is optional"; every other decision path
    refuses one under three characters."""
    import app.routers.hr_pipeline as hrp
    from app.models import Applicant

    eid = uuid.uuid4()
    applicant = Applicant(id=uuid.uuid4(), status="held", full_name="Asha")

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _one(*_: object, **__: object) -> ApplicationChoice:
        return ApplicationChoice(eid, "held", "Welder", [eid])

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "choose_application", _one)
    db = _state_db(status="held", awaits_human=True)
    with pytest.raises(HTTPException) as exc:
        await hrp.decide_applicant(
            uuid.uuid4(),
            hrp.DecisionIn(decision="rejected", rationale=rationale, reason_code="other"),
            MagicMock(), (uuid.uuid4(), uuid.uuid4()), db,
        )
    assert exc.value.status_code == 422
    assert not db.add.called and not db.commit.called


@pytest.mark.asyncio
async def test_a_named_application_is_guarded_on_its_own_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B5: the person's row mirrors their LATEST application. Hiring someone
    for an older one that is still 'new' is refused on that application's own
    state, whatever the newer one says."""
    import app.routers.hr_pipeline as hrp
    from app.models import Applicant

    older, newer = uuid.uuid4(), uuid.uuid4()
    applicant = Applicant(id=uuid.uuid4(), status="shortlisted", full_name="Asha")
    asked: list[uuid.UUID] = []

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _older_is_new(*_: object, **__: object) -> ApplicationChoice:
        return ApplicationChoice(older, "new", "Welder", [older, newer])

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "choose_application", _older_is_new)
    db = _state_db(status="new", awaits_human=False, current_round_id=None)
    original = db.execute

    async def _execute(stmt: object, params: dict | None = None) -> object:
        if params and "e" in params:
            asked.append(params["e"])
        return await original(stmt, params)

    db.execute = _execute
    with pytest.raises(HTTPException) as exc:
        await hrp.decide_applicant(
            uuid.uuid4(),
            hrp.DecisionIn(decision="hired", rationale="Strong CV", enrolment_id=older),
            MagicMock(), (uuid.uuid4(), uuid.uuid4()), db,
        )
    assert exc.value.status_code == 409, "an application still 'new' cannot be hired"
    assert asked == [older], "the decision read the named application's own state"
    assert applicant.status == "shortlisted", "the mirror (latest application) is untouched"


def test_the_applicant_board_records_every_change_not_just_a_shortlist() -> None:
    from app.routers.hr_applicants import update_applicant_status

    src = inspect.getsource(update_applicant_status)
    # A change that cannot name an opening is refused — any status (B5): a
    # shortlist that lands on no application moves nothing anyone can see.
    assert "if app_.ambiguous:" in src
    # And the transition is written for any status, outside the shortlist branch.
    assert "to_status=body.status" in src
    assert 'to_status="shortlisted"' not in src


# ===========================================================================
# Holds and their release keep the human's reasons
# ===========================================================================
def test_a_manual_hold_sets_when_and_why() -> None:
    from app.routers.hr_requisitions import set_enrolment_status

    src = inspect.getsource(set_enrolment_status)
    assert "held_at = now(), held_reason = :r" in src
    assert "held_at = NULL, held_reason = NULL" in src


@pytest.mark.asyncio
async def test_releasing_a_hold_records_the_reviewers_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflow_runner as wr

    moves: list[dict] = []

    async def _held(*_: object) -> dict:
        # Every column _load_enrolment selects. It used to carry only three, so
        # release_hold could read nothing else without this test breaking —
        # which it did, the moment the release started moving candidates on.
        return {
            "id": uuid.uuid4(), "company_id": uuid.uuid4(), "status": "held",
            "applicant_id": uuid.uuid4(), "requisition_id": uuid.uuid4(),
            "workflow_id": uuid.uuid4(), "current_round_id": None,
            "target_job_title": "Fitter", "full_name": "A Candidate",
            "email": "candidate@example.test",
        }

    async def _transition(_db: object, **kw: object) -> str:
        moves.append(kw)
        return "held"

    monkeypatch.setattr(wr, "_load_enrolment", _held)
    monkeypatch.setattr(wr, "record_transition", _transition)
    await wr.release_hold(_db(), enrolment_id=uuid.uuid4(), actor_user_id=uuid.uuid4(),
                          reason="references came back fine")
    assert "references came back fine" in moves[0]["reason"]
    assert moves[0]["automated"] is False
    # current_round_id is None here: the candidate was held after their last
    # round, so there is nowhere to advance to and the release only lifts it.


def test_the_hold_release_endpoint_passes_the_reason_on() -> None:
    from app.routers.hr_workflows import post_release_hold

    assert "reason=body.reason" in inspect.getsource(post_release_hold)


# ===========================================================================
# Append-only, at the database
# ===========================================================================
def _migration() -> str:
    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    return next(versions.glob("*_c9e1a3b5d7f0_*.py")).read_text(encoding="utf-8")


def test_the_ledger_refuses_edits_and_deletes() -> None:
    src = _migration()
    assert "BEFORE UPDATE OR DELETE ON stage_transitions" in src
    assert "stage_transitions is append-only" in src


def test_the_only_deletes_allowed_are_the_applications_own() -> None:
    """History can leave with its subject; it cannot be pruned while the
    subject remains."""
    assert "NOT EXISTS (SELECT 1 FROM enrolments WHERE id = OLD.enrolment_id)" in _migration()


def test_the_only_edit_allowed_is_a_deleted_users_id_being_cleared() -> None:
    src = _migration()
    assert "NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL" in src
    # Every other column must be unchanged for the exception to apply.
    assert "IS NOT DISTINCT FROM" in src and "OLD.to_status" in src and "OLD.reason" in src


def test_round_references_are_not_foreign_keys() -> None:
    """A foreign key's ON DELETE SET NULL would rewrite history — and the
    trigger would refuse it."""
    src = _migration()
    assert 'add_column("stage_transitions", sa.Column("from_round_id", sa.UUID()' in src
    assert "ForeignKey" not in src


# ===========================================================================
# HR can read the history
# ===========================================================================
def test_the_history_endpoint_names_only_people() -> None:
    from app.routers.hr_requisitions import get_enrolment_history

    src = inspect.getsource(get_enrolment_history)
    assert '"actor": None if r["automated"] else r["actor"]' in src
    assert "t.company_id = :c" in src  # tenant-scoped


def test_the_history_endpoint_returns_the_ledger_row_id() -> None:
    """PH5-E5: the frontend's per-decision "Why?" link needs the
    ``stage_transitions`` row id to open ``/hr/decisions/{id}/trace`` on
    exactly that decision, rather than only the trail's picker."""
    from app.routers.hr_requisitions import get_enrolment_history

    src = inspect.getsource(get_enrolment_history)
    assert "SELECT t.id," in src
    assert '"id": r["id"]' in src
