"""Openings that publish themselves — PH3-B4a.

The two properties that matter: the gate is re-checked at fire time (a
requisition approved on Monday and rejected on Tuesday must not publish on
Wednesday), and the promise the API makes about *when* is the one the
architecture can actually keep.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0004_f6b8d0e2a4c7_ph3_b4a_scheduled_publishing.py"
)

#: Used to split a module's docstring from its code when asserting on source.
TRIPLE_QUOTE = chr(34) * 3

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
REQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY = uuid.UUID("22222222-2222-2222-2222-222222222222")
SETTER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _due(**kw: object) -> dict:
    base = {
        "id": REQ,
        "company_id": COMPANY,
        "title": "Platform Engineer",
        "approval_status": "approved",
        "status": "open",
        "publish_at": NOW - timedelta(minutes=1),
        "publish_at_set_by_user_id": SETTER,
        "public_apply_enabled": False,
    }
    return {**base, **kw}


def _factory(rows: list[dict]) -> tuple[object, AsyncMock]:
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.all = MagicMock(return_value=rows)
        mapped.first = MagicMock(return_value=rows[0] if rows else None)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.commit = AsyncMock()

    @asynccontextmanager
    async def factory():  # noqa: ANN202
        yield db

    return factory, db


# ===========================================================================
# The gate is re-checked when it fires
# ===========================================================================
@pytest.mark.asyncio
async def test_an_approved_opening_publishes() -> None:
    from app.scheduled_publishing import publish_due

    factory, db = _factory([_due()])
    result = await publish_due(factory)
    assert result == {"published": 1, "skipped": 0}
    assert any(
        "public_apply_enabled = true" in c.args[0].text
        for c in db.execute.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"approval_status": "pending_approval"},
        {"approval_status": "rejected"},
        {"approval_status": "draft"},
        {"status": "paused"},
        {"status": "closed"},
    ],
)
async def test_a_no_longer_eligible_opening_does_not_publish(override: dict) -> None:
    """Approved on Monday, rejected on Tuesday, scheduled for Wednesday."""
    from app.scheduled_publishing import publish_due

    factory, db = _factory([_due(**override)])
    result = await publish_due(factory)
    assert result == {"published": 0, "skipped": 1}
    assert not any(
        "public_apply_enabled = true" in c.args[0].text
        for c in db.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_a_blocked_opening_keeps_its_schedule() -> None:
    """Somebody asked for this to go live. Silently unscheduling would lose the
    request; silently publishing would defeat the gate. It waits."""
    from app.scheduled_publishing import publish_due

    factory, db = _factory([_due(approval_status="pending_approval")])
    await publish_due(factory)
    assert not any(
        "publish_at = NULL" in c.args[0].text for c in db.execute.await_args_list
    )


def test_the_block_reason_names_what_is_wrong() -> None:
    from app.scheduled_publishing import _blocked_reason

    assert _blocked_reason(_due()) is None
    assert "approval_status" in str(_blocked_reason(_due(approval_status="draft")))
    assert "status" in str(_blocked_reason(_due(status="paused")))


# ===========================================================================
# Idempotency and concurrency
# ===========================================================================
@pytest.mark.asyncio
async def test_publishing_clears_the_request_so_a_second_pass_does_nothing() -> None:
    from app.scheduled_publishing import publish_due

    factory, db = _factory([_due()])
    await publish_due(factory)
    # Matched on the statement's opening word: the SELECT above it ends in
    # FOR UPDATE SKIP LOCKED, so a bare "UPDATE" substring finds the wrong one.
    update = next(
        c.args[0].text for c in db.execute.await_args_list
        if c.args[0].text.lstrip().startswith("UPDATE")
    )
    assert "publish_at = NULL" in update


def test_two_instances_never_publish_the_same_opening() -> None:
    """The same claim-don't-coordinate approach the erasure executor takes."""
    from app.scheduled_publishing import due_requisitions

    assert "FOR UPDATE SKIP LOCKED" in inspect.getsource(due_requisitions)


def test_a_pass_is_bounded() -> None:
    """So a bad data migration that schedules ten thousand openings cannot turn
    one pass into a ten-thousand-row transaction."""
    from app.scheduled_publishing import _BATCH, due_requisitions

    assert _BATCH <= 1000
    assert "LIMIT :lim" in inspect.getsource(due_requisitions)


def test_the_query_can_use_the_partial_index() -> None:
    from app.scheduled_publishing import due_requisitions

    sql = inspect.getsource(due_requisitions)
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "publish_at IS NOT NULL" in sql
    assert "publish_at IS NOT NULL AND deleted_at IS NULL" in migration
    assert "ix_job_requisitions_publish_due" in migration


# ===========================================================================
# The promise about time
# ===========================================================================
def test_the_publisher_is_an_interval_loop_not_a_cron_job() -> None:
    """A clock trigger cannot fire while the container is suspended, and the
    demo Space suspends after ~48h — the window is skipped, not delayed."""
    from app import scheduled_publishing

    src = inspect.getsource(scheduled_publishing)
    # The module docstring NAMES CronTrigger in order to explain why it is not
    # used, so the assertion is against the code below the docstring.
    body = src.split(TRIPLE_QUOTE, 2)[2]
    assert "CronTrigger" not in body
    assert "asyncio.sleep(interval)" in body


def test_the_api_states_the_tolerance_rather_than_implying_a_guarantee() -> None:
    from app.routers.hr_requisitions import PublishScheduleOut
    from app.scheduled_publishing import tolerance_note

    assert "tolerance" in PublishScheduleOut.model_fields
    note = tolerance_note()
    assert "minute" in note
    # It says what happens when the service is asleep, which is the case a
    # console would otherwise quietly misrepresent.
    assert "wake" in note.lower()


def test_the_interval_is_floored() -> None:
    """A one-second interval would be a busy loop against the database."""
    from app.scheduled_publishing import _MIN_INTERVAL_SECONDS, interval_seconds

    assert interval_seconds() >= _MIN_INTERVAL_SECONDS


def test_a_naive_datetime_is_refused() -> None:
    """"Publish at 09:00" landing five and a half hours out is exactly the bug
    a scheduling feature must not have."""
    from app.routers.hr_requisitions import PublishScheduleIn

    with pytest.raises(ValueError, match="timezone"):
        PublishScheduleIn(publish_at=datetime(2099, 1, 1, 9, 0))


def test_a_past_time_is_refused() -> None:
    from app.routers.hr_requisitions import PublishScheduleIn

    with pytest.raises(ValueError, match="future"):
        PublishScheduleIn(publish_at=datetime(2020, 1, 1, 9, 0, tzinfo=UTC))


# ===========================================================================
# Authorisation and audit
# ===========================================================================
def test_scheduling_an_unapproved_opening_is_refused() -> None:
    from app.routers.hr_requisitions import set_publish_schedule

    src = inspect.getsource(set_publish_schedule)
    assert "APPROVED" in src
    assert "HTTP_409_CONFLICT" in src


def test_the_gate_is_enforced_twice_and_the_code_says_why() -> None:
    from app.routers.hr_requisitions import set_publish_schedule
    from app.scheduled_publishing import publish_due

    assert "APPROVED" in inspect.getsource(set_publish_schedule)
    assert "_blocked_reason" in inspect.getsource(publish_due)


def test_every_scheduling_action_is_audited() -> None:
    from app.routers import hr_requisitions

    assert "requisition.publish_schedule.set" in inspect.getsource(
        hr_requisitions.set_publish_schedule
    )
    assert "requisition.publish_schedule.updated" in inspect.getsource(
        hr_requisitions.set_publish_schedule
    )
    assert "requisition.publish_schedule.cancelled" in inspect.getsource(
        hr_requisitions.cancel_publish_schedule
    )


@pytest.mark.asyncio
async def test_the_execution_is_audited_to_whoever_scheduled_it() -> None:
    """An audit trail that attributes every scheduled publication to nobody
    cannot answer who decided it."""
    from app.scheduled_publishing import publish_due

    factory, db = _factory([_due()])
    await publish_due(factory)
    entry = db.add.call_args.args[0]
    assert entry.action == "requisition.published.scheduled"
    assert entry.actor_id == SETTER
    assert entry.actor_type == "system"
    # The gap between asked-for and actually-done, made visible.
    assert "delay_seconds" in entry.details


def test_cancelling_does_not_take_down_a_live_opening() -> None:
    from app.routers.hr_requisitions import cancel_publish_schedule

    src = inspect.getsource(cancel_publish_schedule)
    assert "public_apply_enabled" not in src.split('"""')[2]


def test_cancelling_twice_is_not_an_error() -> None:
    from app.routers.hr_requisitions import cancel_publish_schedule

    assert 'row.get("publish_at") is None' in inspect.getsource(cancel_publish_schedule)


def test_only_hr_can_schedule() -> None:
    from app.routers.hr_requisitions import (
        cancel_publish_schedule,
        get_publish_schedule,
        set_publish_schedule,
    )

    for fn in (get_publish_schedule, set_publish_schedule, cancel_publish_schedule):
        assert "HrCtxDep" in str(inspect.signature(fn).parameters["ctx"].annotation)


# ===========================================================================
# Observability
# ===========================================================================
def test_every_pass_leaves_a_record() -> None:
    """So a publisher that has silently stopped is visible in the same place a
    missed retention purge is (A6)."""
    from app import scheduled_publishing

    src = inspect.getsource(scheduled_publishing)
    assert "record_loop_pass" in src
    assert "LOOP_JOB_ID" in src


def test_the_loop_outlives_a_failing_pass() -> None:
    from app.scheduled_publishing import _loop

    src = inspect.getsource(_loop)
    assert "except asyncio.CancelledError" in src
    assert "raise" in src


def test_the_publisher_is_started_and_stopped_with_the_app() -> None:
    main = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "scheduled_publishing.start(_factory)" in main
    assert "await scheduled_publishing.stop()" in main
