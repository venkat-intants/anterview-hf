"""Unit tests for Group B — requisitions, enrolments, transitions and merge.

DB is mocked, matching the house pattern. The behaviour under test is the logic
that decides *whether* to write: normalisation, no-op suppression, the legacy
column sync guard, and the two cases a merge must refuse. The SQL itself is
covered by ``tests/integration/smoke_group_b_requisitions.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
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


# ===========================================================================
# E3 — delivery risk. Pure arithmetic, so tested as arithmetic.
# ===========================================================================
_T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _risk(**kw: object) -> str | None:
    from app.requisitions import delivery_risk

    base: dict[str, object] = {
        "target_hires": 10,
        "hired": 0,
        "created_at": _T0,
        "closes_at": _T0 + timedelta(days=100),
        "now": _T0 + timedelta(days=50),
    }
    base.update(kw)
    return delivery_risk(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("closes_at", "target", "why"),
    [
        (None, 10, "no closing date — nothing to be late for"),
        (_T0 + timedelta(days=100), None, "no target — no definition of enough"),
        (_T0 + timedelta(days=100), 0, "a zero target is not a target"),
    ],
)
def test_no_signal_when_the_question_cannot_be_asked(
    closes_at: object, target: object, why: str
) -> None:
    assert _risk(closes_at=closes_at, target_hires=target) is None, why


def test_no_signal_before_a_rate_exists() -> None:
    """Three days of data projects confident nonsense; say nothing instead."""
    assert _risk(now=_T0 + timedelta(days=3)) is None


def test_meeting_the_target_is_on_track_whatever_the_dates_say() -> None:
    """Even past the closing date — the job is done."""
    assert (
        _risk(hired=10, now=_T0 + timedelta(days=200), closes_at=_T0 + timedelta(days=100))
        == "on_track"
    )


def test_closed_window_with_the_target_unmet_is_off_track() -> None:
    """An observation, not a forecast — no projection is involved."""
    assert _risk(hired=3, now=_T0 + timedelta(days=120)) == "off_track"


def test_on_track_when_the_observed_rate_reaches_the_target() -> None:
    # 5 hires in 50 days = 0.1/day; 50 days left projects 5 more -> exactly 10.
    assert _risk(hired=5) == "on_track"


def test_at_risk_when_the_rate_lands_short_but_not_hopelessly() -> None:
    # 4 in 50 days projects to 8 of 10 — short, above the 0.6 floor.
    assert _risk(hired=4) == "at_risk"


def test_off_track_when_the_rate_is_nowhere_near() -> None:
    # 1 in 50 days projects to 2 of 10.
    assert _risk(hired=1) == "off_track"


def test_zero_hires_halfway_through_is_off_track() -> None:
    """The case an HR manager most needs surfaced."""
    assert _risk(hired=0) == "off_track"


def test_risk_never_depends_on_a_candidate() -> None:
    """E3 describes an opening's throughput, never a person (D-05).

    Guarded by signature: there is no parameter through which a candidate's
    identity or score could reach this, so no future edit can make the board's
    risk badge a statement about anybody.
    """
    import inspect

    from app.requisitions import delivery_risk

    params = set(inspect.signature(delivery_risk).parameters)
    assert params == {"target_hires", "hired", "created_at", "closes_at", "now"}
