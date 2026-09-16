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
    # It sleeps and re-polls; it does not arm a clock. The exact sleep is now
    # computed (see _sleep_seconds), so asserting the literal `sleep(interval)`
    # would be asserting an implementation detail that has already changed once.
    assert "asyncio.sleep(" in body
    assert "while True:" in body


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


# ===========================================================================
# PH3-B4 criterion 12 — publish AT the scheduled time
#
# A fixed sleep made the interval the tolerance every time: an opening set for
# 09:00:00 published at up to 09:00:59, because the loop simply was not looking.
# The loop now sleeps until the next schedule actually falls due, capped at the
# interval so the heartbeat cadence and the worst case are both unchanged.
# ===========================================================================
@pytest.mark.asyncio
async def test_it_sleeps_until_the_next_schedule_is_due() -> None:
    from app.scheduled_publishing import _sleep_seconds

    soon = datetime.now(tz=UTC) + timedelta(seconds=5)
    factory = _factory_returning(soon)
    delay = await _sleep_seconds(factory, 60)
    assert 1.0 <= delay <= 6.0, f"slept {delay}s for a schedule 5s away"


@pytest.mark.asyncio
async def test_it_never_sleeps_longer_than_the_interval() -> None:
    """The cap is what preserves the old guarantees: the loop-pass heartbeat
    keeps its cadence, and a schedule created DURING a sleep is still picked up
    no later than it would have been before."""
    from app.scheduled_publishing import _sleep_seconds

    far = datetime.now(tz=UTC) + timedelta(days=30)
    assert await _sleep_seconds(_factory_returning(far), 60) == 60.0


@pytest.mark.asyncio
async def test_nothing_scheduled_falls_back_to_the_interval() -> None:
    from app.scheduled_publishing import _sleep_seconds

    assert await _sleep_seconds(_factory_returning(None), 60) == 60.0


@pytest.mark.asyncio
async def test_it_never_becomes_a_hot_loop() -> None:
    """A row that moves under us, or a clock that steps backwards, must not
    turn this into a spin against the database."""
    from app.scheduled_publishing import _sleep_seconds

    past = datetime.now(tz=UTC) - timedelta(hours=3)
    assert await _sleep_seconds(_factory_returning(past), 60) == 1.0


@pytest.mark.asyncio
async def test_a_failed_probe_falls_back_rather_than_stopping_the_publisher() -> None:
    """The publisher surviving is worth more than the extra precision."""
    from app.scheduled_publishing import _sleep_seconds

    factory = MagicMock(side_effect=RuntimeError("database gone"))
    assert await _sleep_seconds(factory, 60) == 60.0


@pytest.mark.asyncio
async def test_a_naive_timestamp_is_read_as_utc_not_local() -> None:
    """asyncpg can hand back a naive datetime. Treating it as local time would
    shift every wake-up by the host's offset — hours, on an IST box."""
    from app.scheduled_publishing import _sleep_seconds

    naive = (datetime.now(tz=UTC) + timedelta(seconds=5)).replace(tzinfo=None)
    delay = await _sleep_seconds(_factory_returning(naive), 60)
    assert 1.0 <= delay <= 6.0, f"naive value mis-read: slept {delay}s"


def _factory_returning(value: object) -> MagicMock:
    """A session factory whose scalar() answers the next-due lookup."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=value)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)
