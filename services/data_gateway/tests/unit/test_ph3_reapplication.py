"""Reapplication cooldowns — PH3-B4b.

The window runs from the REJECTION, not from the application. Measuring from
the application would give exactly the wrong answer for the only case that
matters: somebody who applied six months ago and was turned down yesterday is
one day into their cooldown, not six months past it.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0005_a7c9e1f3b5d8_ph3_b4b_reapplication.py"
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
REQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
APPLICANT = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _db(row: dict | None) -> AsyncMock:
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=row)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _prior(**kw: object) -> dict:
    base = {
        "id": uuid.uuid4(),
        "reapply_override_at": None,
        "rejected_at": NOW - timedelta(days=10),
    }
    return {**base, **kw}


# ===========================================================================
# When the window applies at all
# ===========================================================================
@pytest.mark.asyncio
async def test_no_cooldown_configured_allows_everything() -> None:
    """The default. Nothing changes for any opening until somebody sets one."""
    from app.reapplication import check

    db = _db(_prior())
    verdict = await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=None, now=NOW
    )
    assert verdict.allowed is True
    # Not even queried: there is nothing to measure.
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_a_zero_cooldown_also_allows_everything() -> None:
    """Zero is a decision — "no waiting period" — and behaves like none."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior()), requisition_id=REQ, applicant_id=APPLICANT,
        cooldown_days=0, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_somebody_who_never_applied_has_nothing_to_wait_out() -> None:
    from app.reapplication import check

    verdict = await check(
        _db(None), requisition_id=REQ, applicant_id=APPLICANT,
        cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_somebody_who_applied_but_was_never_rejected_is_not_in_a_window() -> None:
    """Their application may still be live, or they may have been hired. Either
    way there is no rejection to count from."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=None)), requisition_id=REQ, applicant_id=APPLICANT,
        cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


# ===========================================================================
# The window itself
# ===========================================================================
@pytest.mark.asyncio
async def test_inside_the_window_is_refused_with_a_date() -> None:
    """A person refused with no date has no way to act on the refusal and will
    simply retry."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=NOW - timedelta(days=10))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is False
    assert verdict.until == NOW - timedelta(days=10) + timedelta(days=90)
    assert "2026-12-05" in verdict.message()


@pytest.mark.asyncio
async def test_past_the_window_is_allowed() -> None:
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=NOW - timedelta(days=91))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_exactly_at_the_boundary_is_allowed() -> None:
    """The window is closed at its end: a 90-day cooldown means you may apply
    on day 90, not on day 91."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=NOW - timedelta(days=90))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_the_window_is_measured_from_the_ledger_not_from_updated_at() -> None:
    """updated_at moves on a rescore, which would make the window whatever the
    reconciler last touched."""
    from app.reapplication import check

    db = _db(_prior())
    await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW
    )
    sql = db.execute.await_args_list[0].args[0].text
    assert "stage_transitions" in sql
    assert "to_status = 'rejected'" in sql
    assert "updated_at" not in sql


@pytest.mark.asyncio
async def test_deleting_the_application_does_not_delete_the_cooldown() -> None:
    """Otherwise the window is trivially escapable."""
    from app.reapplication import check

    db = _db(_prior())
    await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW
    )
    sql = db.execute.await_args_list[0].args[0].text
    assert "deleted_at IS NULL" not in sql


# ===========================================================================
# Override
# ===========================================================================
@pytest.mark.asyncio
async def test_an_override_lets_somebody_back_in() -> None:
    from app.reapplication import check

    verdict = await check(
        _db(_prior(reapply_override_at=NOW - timedelta(days=1))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True
    assert verdict.reason == "override"


@pytest.mark.asyncio
async def test_the_override_is_scoped_to_the_company() -> None:
    """Another tenant's application must read as missing, not as forbidden."""
    from app.reapplication import grant_override

    db = _db(None)
    result = await grant_override(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(), reason=None,
    )
    assert result is None
    assert "company_id = :c" in db.execute.await_args_list[0].args[0].text


def test_the_override_is_recorded_against_the_rejection_it_forgives() -> None:
    """On the enrolment, not on the person: a blanket exemption is a different
    and much larger grant."""
    from app.reapplication import grant_override

    src = inspect.getsource(grant_override)
    assert "UPDATE enrolments" in src
    assert "reapply_override_at" in src


def test_granting_an_override_is_audited() -> None:
    from app.routers.hr_requisitions import override_reapply_cooldown

    assert "enrolment.reapply_override" in inspect.getsource(override_reapply_cooldown)


def test_only_hr_can_override() -> None:
    from app.routers.hr_requisitions import override_reapply_cooldown

    assert "HrCtxDep" in str(
        inspect.signature(override_reapply_cooldown).parameters["ctx"].annotation
    )


# ===========================================================================
# The apply path
# ===========================================================================
def test_a_live_application_is_never_told_to_wait() -> None:
    """"You have already applied" is a reassurance, not a refusal, and must
    keep coming first."""
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("already_applied=True") < src.index("cooldown_check")


def test_the_cooldown_is_checked_before_the_cv_is_stored() -> None:
    """A refusal should cost no stored object — the same ordering the consent
    and answer checks already use."""
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("cooldown_check") < src.index("_upload_to_s3")


def test_the_cooldown_is_checked_after_the_cv_is_read() -> None:
    """Answering it earlier would let anyone holding the link discover, by
    typing addresses, who had been turned down for this role and when."""
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("_extract_pdf_text") < src.index("cooldown_check")


def test_a_blocked_application_is_a_conflict_not_a_forbidden() -> None:
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert "HTTP_409_CONFLICT" in src


def test_the_apply_query_reads_the_cooldown_setting() -> None:
    from app.routers.public_apply import _open_posting

    assert "reapply_cooldown_days" in inspect.getsource(_open_posting)


# ===========================================================================
# Configuration
# ===========================================================================
def test_the_setting_is_per_requisition() -> None:
    from app.routers.hr_requisitions import RequisitionOut, RequisitionPatch

    assert "reapply_cooldown_days" in RequisitionPatch.model_fields
    assert "reapply_cooldown_days" in RequisitionOut.model_fields


def test_a_cooldown_cannot_be_negative_or_effectively_permanent() -> None:
    from app.routers.hr_requisitions import RequisitionPatch

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        RequisitionPatch(reapply_cooldown_days=-1)
    with pytest.raises(ValueError, match="less than or equal to 1095"):
        RequisitionPatch(reapply_cooldown_days=4000)
    assert RequisitionPatch(reapply_cooldown_days=0).reapply_cooldown_days == 0


def test_the_database_enforces_the_same_bounds() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ck_job_requisitions_reapply_cooldown" in sql
    assert "1095" in sql


def test_the_default_is_no_cooldown() -> None:
    """A platform-wide default would silently start refusing applications
    nobody had decided to refuse."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "nullable=True" in sql
    assert "server_default" not in sql.split("def upgrade")[1].split("def downgrade")[0]
