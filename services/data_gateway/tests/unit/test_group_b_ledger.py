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

    async def _two(*_: object, **__: object) -> list[uuid.UUID]:
        return [uuid.uuid4(), uuid.uuid4()]

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "live_enrolments", _two)
    with pytest.raises(HTTPException) as exc:
        await hrp.decide_applicant(uuid.uuid4(), hrp.DecisionIn(decision="rejected"), MagicMock(),
                                   (uuid.uuid4(), uuid.uuid4()), _db())
    assert exc.value.status_code == 409
    assert "2 openings" in str(exc.value.detail)
    assert applicant.status == "interviewed", "nothing was changed"


@pytest.mark.asyncio
async def test_a_pipeline_decision_on_one_application_goes_through_the_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    import app.routers.hr_pipeline as hrp
    from app.models import Applicant

    eid, hr = uuid.uuid4(), uuid.uuid4()
    applicant = Applicant(id=uuid.uuid4(), status="interviewed", full_name="Asha",
                          target_job_title="Staff Nurse", target_level="mid",
                          pending_enrichment=False, created_at=datetime.now(tz=UTC))
    moves: list[dict] = []

    async def _owned(*_: object) -> Applicant:
        return applicant

    async def _one(*_: object, **__: object) -> list[uuid.UUID]:
        return [eid]

    async def _transition(_db: object, **kw: object) -> str:
        moves.append(kw)
        return "interviewed"

    async def _no_email(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(hrp, "_get_owned", _owned)
    monkeypatch.setattr(hrp, "live_enrolments", _one)
    monkeypatch.setattr(hrp, "record_transition", _transition)
    monkeypatch.setattr(hrp, "email_applicant_decision", _no_email)
    db = _db()
    db.add = MagicMock()
    await hrp.decide_applicant(uuid.uuid4(),
                               hrp.DecisionIn(decision="hired", rationale="strong panel"),
                               MagicMock(), (hr, uuid.uuid4()), db)

    assert moves[0]["enrolment_id"] == eid and moves[0]["to_status"] == "hired"
    assert moves[0]["automated"] is False and moves[0]["actor_user_id"] == hr
    assert moves[0]["reason"] == "strong panel"


def test_the_applicant_board_records_every_change_not_just_a_shortlist() -> None:
    from app.routers.hr_applicants import update_applicant_status

    src = inspect.getsource(update_applicant_status)
    # Terminal decisions that cannot name an opening are refused.
    assert "len(enrolments) > 1 and body.status in TERMINAL_STATUSES" in src
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
        return {"id": uuid.uuid4(), "company_id": uuid.uuid4(), "status": "held"}

    async def _transition(_db: object, **kw: object) -> str:
        moves.append(kw)
        return "held"

    monkeypatch.setattr(wr, "_load_enrolment", _held)
    monkeypatch.setattr(wr, "record_transition", _transition)
    await wr.release_hold(_db(), enrolment_id=uuid.uuid4(), actor_user_id=uuid.uuid4(),
                          reason="references came back fine")
    assert "references came back fine" in moves[0]["reason"]
    assert moves[0]["automated"] is False


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
