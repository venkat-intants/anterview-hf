"""Unit tests for Group B — requisitions, enrolments, transitions and merge.

DB is mocked, matching the house pattern. The behaviour under test is the logic
that decides *whether* to write: normalisation, no-op suppression, the legacy
column sync guard, and the two cases a merge must refuse. The SQL itself is
covered by ``tests/integration/smoke_group_b_requisitions.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _db(**kw: object) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=kw.get("scalar"))
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# normalise_title — must agree with the SQL index expression
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Python Developer", "python developer"),
        ("python developer", "python developer"),
        ("Python  Developer", "python developer"),
        ("  Python Developer  ", "python developer"),
        ("PYTHON\tDEVELOPER", "python developer"),
        ("Staff\nNurse", "staff nurse"),
        ("Senior  Full-Stack   Engineer", "senior full-stack engineer"),
        ("UI/UX Designer", "ui/ux designer"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalise_title(raw: str, expected: str) -> None:
    from app.requisitions import normalise_title

    assert normalise_title(raw) == expected


def test_normalisation_does_not_fuzzy_match() -> None:
    """A near-miss must stay a different opening.

    Fuzzy matching here would silently merge two genuinely different roles, and
    the cost of that is a candidate assessed against a job they did not apply
    for. Case and spacing only — deliberately nothing cleverer.
    """
    from app.requisitions import normalise_title

    assert normalise_title("Python Developer") != normalise_title("Python Developers")
    assert normalise_title("Senior Developer") != normalise_title("Developer")
    assert normalise_title("Nurse") != normalise_title("Staff Nurse")


# ===========================================================================
# record_transition
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_status_is_rejected() -> None:
    from app.requisitions import record_transition

    with pytest.raises(ValueError, match="unknown status"):
        await record_transition(
            _db(), enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
            to_status="banana", actor_user_id=None, automated=True,
        )


@pytest.mark.asyncio
async def test_missing_enrolment_returns_none() -> None:
    from app.requisitions import record_transition

    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=None))
    got = await record_transition(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        to_status="hired", actor_user_id=None, automated=True,
    )
    assert got is None


@pytest.mark.asyncio
async def test_no_op_move_writes_nothing() -> None:
    """Re-saving the same status must not reset time-in-stage."""
    from app.requisitions import record_transition

    db = _db()
    db.execute.return_value = MagicMock(
        first=MagicMock(return_value=("shortlisted", uuid.uuid4()))
    )
    got = await record_transition(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        to_status="shortlisted", actor_user_id=None, automated=True,
    )
    assert got is None
    # One SELECT and nothing else.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_transition_writes_status_and_ledger_together() -> None:
    """The ledger entry is not optional — omitting it is how time-in-stage rots."""
    from app.requisitions import record_transition

    db = _db(scalar=1)
    db.execute.return_value = MagicMock(first=MagicMock(return_value=("new", uuid.uuid4())))
    prev = await record_transition(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        to_status="shortlisted", actor_user_id=uuid.uuid4(), automated=False,
        reason="strong CV",
    )
    assert prev == "new"
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "UPDATE enrolments" in sql
    assert "INSERT INTO stage_transitions" in sql


@pytest.mark.asyncio
async def test_legacy_status_synced_for_a_single_enrolment() -> None:
    from app.requisitions import record_transition

    db = _db(scalar=1)  # exactly one live enrolment
    db.execute.return_value = MagicMock(first=MagicMock(return_value=("new", uuid.uuid4())))
    await record_transition(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        to_status="hired", actor_user_id=None, automated=True,
    )
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "UPDATE applicants SET status" in sql


@pytest.mark.asyncio
async def test_legacy_status_not_guessed_when_several_enrolments() -> None:
    """With two applications there is no single honest value for the legacy
    column, and writing one would show a candidate as hired for a role they
    never applied to."""
    from app.requisitions import record_transition

    db = _db(scalar=3)  # three live enrolments
    db.execute.return_value = MagicMock(first=MagicMock(return_value=("new", uuid.uuid4())))
    await record_transition(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        to_status="hired", actor_user_id=None, automated=True,
    )
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "UPDATE applicants SET status" not in sql


@pytest.mark.asyncio
async def test_automated_flag_is_recorded_verbatim() -> None:
    """'Did a person decide this?' is the question a DPDP audit asks."""
    from app.requisitions import record_transition

    for automated in (True, False):
        db = _db(scalar=1)
        db.execute.return_value = MagicMock(first=MagicMock(return_value=("new", uuid.uuid4())))
        await record_transition(
            db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
            to_status="shortlisted", actor_user_id=None, automated=automated,
        )
        params = [c.args[1] for c in db.execute.call_args_list if len(c.args) > 1]
        assert any(p.get("auto") is automated for p in params)


# ===========================================================================
# merge_applicants — the two refusals
# ===========================================================================
@pytest.mark.asyncio
async def test_survivor_cannot_be_absorbed() -> None:
    from app.requisitions import merge_applicants

    same = uuid.uuid4()
    with pytest.raises(ValueError, match="cannot also be absorbed"):
        await merge_applicants(
            _db(), company_id=uuid.uuid4(), survivor_id=same,
            absorbed_ids=[same], actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_empty_merge_is_a_no_op() -> None:
    from app.requisitions import merge_applicants

    db = _db()
    got = await merge_applicants(
        db, company_id=uuid.uuid4(), survivor_id=uuid.uuid4(),
        absorbed_ids=[], actor_user_id=uuid.uuid4(),
    )
    assert got == {"enrolments": 0, "assignments": 0, "attempts": 0, "invites": 0}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_refuses_rows_outside_the_company() -> None:
    """Ownership is checked by count, so a foreign id simply fails to match."""
    from app.requisitions import merge_applicants

    db = _db(scalar=1)  # asked for 2, only 1 owned
    with pytest.raises(ValueError, match="belong to this company"):
        await merge_applicants(
            db, company_id=uuid.uuid4(), survivor_id=uuid.uuid4(),
            absorbed_ids=[uuid.uuid4()], actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_merge_refuses_when_both_are_in_one_opening() -> None:
    """Choosing which of two applications survives is a judgement about a
    person's candidacy, not a tie-break the system may make."""
    from app.requisitions import merge_applicants

    db = _db(scalar=2)
    db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=["Python Developer"])))
    )
    with pytest.raises(ValueError, match="same opening"):
        await merge_applicants(
            db, company_id=uuid.uuid4(), survivor_id=uuid.uuid4(),
            absorbed_ids=[uuid.uuid4()], actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_merge_repoints_history_and_soft_deletes() -> None:
    from app.requisitions import merge_applicants

    db = _db(scalar=2)
    db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        rowcount=1,
    )
    got = await merge_applicants(
        db, company_id=uuid.uuid4(), survivor_id=uuid.uuid4(),
        absorbed_ids=[uuid.uuid4()], actor_user_id=uuid.uuid4(),
    )
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    for table in ("enrolments", "exam_assignments", "exam_attempts", "interview_invites"):
        assert f"UPDATE {table} SET applicant_id" in sql, f"{table} not repointed"
    assert "UPDATE applicants SET deleted_at" in sql, "absorbed rows not soft-deleted"
    # A merge is not an erasure: PII must not be blanked here.
    assert "full_name = " not in sql
    assert set(got) == {"enrolments", "assignments", "attempts", "invites"}


# ===========================================================================
# Status vocabulary
# ===========================================================================
def test_status_vocabulary_matches_the_legacy_column() -> None:
    """The backfill copies applicants.status verbatim, so every legacy value
    must be legal here or the migration would have failed."""
    from app.requisitions import VALID_STATUSES

    assert {"new", "shortlisted", "interviewed", "hired", "rejected"} <= VALID_STATUSES


def test_held_is_reserved_for_phase_two() -> None:
    from app.requisitions import TERMINAL_STATUSES, VALID_STATUSES

    assert "held" in VALID_STATUSES
    # D-05: being held is explicitly NOT an ending.
    assert "held" not in TERMINAL_STATUSES
    assert set(TERMINAL_STATUSES) == {"hired", "rejected"}
