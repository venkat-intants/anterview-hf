"""Tests for the nightly watcher sweep plumbing.

The detection rules are covered in shared/agents/tests. These cover the parts
that only exist here: row mapping, notification delivery, suppression, and the
isolation guarantees that keep one bad tenant from costing everyone else their
alerts.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.agents import WatcherFinding

from app.agents import watch_runner
from app.agents.watch_runner import (
    DEDUPE_TTL_SECONDS,
    _already_notified,
    deliver,
    gather_company_input,
    gather_erasure_deadlines,
    run_watcher_sweep,
)


def _row(**fields: Any) -> MagicMock:
    row = MagicMock()
    for key, value in fields.items():
        setattr(row, key, value)
    return row


def _db(sequence: list[list[Any]]) -> MagicMock:
    """A db whose successive execute() calls return successive row lists."""
    calls = list(sequence)
    db = MagicMock()
    db.commit = AsyncMock()

    async def _execute(*args: Any, **kwargs: Any) -> MagicMock:
        rows = calls.pop(0) if calls else []
        result = MagicMock()
        result.all.return_value = rows
        result.first.return_value = rows[0] if rows else None
        return result

    db.execute = _execute
    return db


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------


async def test_gather_maps_rows_onto_the_watcher_input() -> None:
    db = _db(
        [
            [_row(id="a-1", full_name="Asha", status="shortlisted", days=21)],
            [_row(title="Welder", applicants=40, interviewed=1)],
            [
                _row(
                    exam_id="e-1",
                    title="CNC Basics",
                    question_id="q-7",
                    position=7,
                    attempts=14,
                    correct=1,
                )
            ],
        ]
    )
    data = await gather_company_input(db, "co-1")

    assert data.company_id == "co-1"
    assert data.stalled[0].name == "Asha"
    assert data.stalled[0].days_in_stage == 21
    assert data.funnels[0].applicants == 40
    assert data.question_stats[0].correct == 1


async def test_null_days_since_update_does_not_crash_the_sweep() -> None:
    """A freshly-inserted row can report NULL for the interval."""
    db = _db([[_row(id="a-1", full_name="Asha", status="new", days=None)], [], []])
    data = await gather_company_input(db, "co-1")
    assert data.stalled[0].days_in_stage == 0


async def test_erasure_deadlines_are_converted_to_hours() -> None:
    db = _db([[_row(request_id="r-1", hours_left=9.4)]])
    requests = await gather_erasure_deadlines(db)
    assert requests[0].request_id == "r-1"
    assert requests[0].hours_remaining == pytest.approx(9.4)


# ---------------------------------------------------------------------------
# Delivery + suppression
# ---------------------------------------------------------------------------


def _finding(key: str = "k1", severity: str = "warning") -> WatcherFinding:
    return WatcherFinding(
        watcher="stalled_applicants",
        severity=severity,  # type: ignore[arg-type]
        title="2 applicants stalled",
        body="body",
        link="/hr/pipeline",
        dedupe_key=key,
    )


async def test_delivery_writes_one_notification_per_recipient() -> None:
    db = _db([])
    with patch.object(watch_runner, "_already_notified", AsyncMock(return_value=False)):
        written = await deliver(db, [_finding()], ["u-1", "u-2"])
    assert written == 2
    db.commit.assert_awaited()


async def test_suppressed_findings_are_not_rewritten() -> None:
    """Nightly re-notification is how a notification bell becomes wallpaper."""
    db = _db([])
    with patch.object(watch_runner, "_already_notified", AsyncMock(return_value=True)):
        written = await deliver(db, [_finding()], ["u-1", "u-2"])
    assert written == 0
    db.commit.assert_not_awaited()


async def test_critical_findings_are_marked_in_the_title() -> None:
    """notifications has no severity column, so severity rides in the title."""
    captured: list[dict[str, Any]] = []
    db = MagicMock()
    db.commit = AsyncMock()

    async def _execute(_sql: Any, params: dict[str, Any]) -> MagicMock:
        captured.append(params)
        return MagicMock()

    db.execute = _execute
    with patch.object(watch_runner, "_already_notified", AsyncMock(return_value=False)):
        await deliver(db, [_finding(severity="critical")], ["u-1"])

    assert captured[0]["title"].startswith("⚠")


async def test_dedupe_fails_open_when_redis_is_down() -> None:
    """A cache outage must cost a duplicate notification, never a missed one —
    the DPDP deadline alert especially."""
    with patch.object(watch_runner, "get_redis", side_effect=ConnectionError("down")):
        assert await _already_notified("u-1", "k1") is False


async def test_empty_dedupe_key_is_never_suppressed() -> None:
    assert await _already_notified("u-1", "") is False


async def test_dedupe_marker_uses_a_bounded_ttl() -> None:
    """An unbounded marker would suppress a recurring problem forever."""
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    with patch.object(watch_runner, "get_redis", return_value=redis):
        await _already_notified("u-1", "k1")
    assert redis.set.await_args.kwargs["ex"] == DEDUPE_TTL_SECONDS
    assert redis.set.await_args.kwargs["nx"] is True


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def test_sweep_is_a_noop_when_disabled() -> None:
    with patch.object(watch_runner.settings, "watchers_enabled", False):
        totals = await run_watcher_sweep()
    assert totals == {"companies": 0, "findings": 0, "notifications": 0}


async def test_one_broken_company_does_not_stop_the_sweep() -> None:
    """One tenant's failure must not cost every other tenant their alerts."""
    seen: list[str] = []

    async def _gather(db: Any, company_id: str) -> Any:
        seen.append(company_id)
        if company_id == "co-bad":
            raise RuntimeError("bad row")
        from shared.agents import StalledApplicant, WatcherInput

        return WatcherInput(
            company_id=company_id,
            stalled=[StalledApplicant("a-1", "Asha", "new", 30)],
        )

    factory = MagicMock()
    db = _db([[_row(id="co-bad"), _row(id="co-good")]])
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(watch_runner, "get_session_factory", return_value=factory),
        patch.object(watch_runner, "gather_company_input", _gather),
        patch.object(watch_runner, "gather_erasure_deadlines", AsyncMock(return_value=[])),
        patch.object(watch_runner, "_recipients_for_company", AsyncMock(return_value=["u-1"])),
        patch.object(watch_runner, "deliver", AsyncMock(return_value=1)),
    ):
        totals = await run_watcher_sweep()

    assert seen == ["co-bad", "co-good"]
    # Both counted; only the healthy one produced findings.
    assert totals["companies"] == 2
    assert totals["notifications"] == 1


async def test_a_company_with_no_recipients_is_skipped_quietly() -> None:
    """A tenant can exist before its HR managers are created."""
    from shared.agents import StalledApplicant, WatcherInput

    factory = MagicMock()
    db = _db([[_row(id="co-1")]])
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    deliver_mock = AsyncMock(return_value=0)
    with (
        patch.object(watch_runner, "get_session_factory", return_value=factory),
        patch.object(
            watch_runner,
            "gather_company_input",
            AsyncMock(
                return_value=WatcherInput(
                    company_id="co-1",
                    stalled=[StalledApplicant("a-1", "Asha", "new", 30)],
                )
            ),
        ),
        patch.object(watch_runner, "gather_erasure_deadlines", AsyncMock(return_value=[])),
        patch.object(watch_runner, "_recipients_for_company", AsyncMock(return_value=[])),
        patch.object(watch_runner, "deliver", deliver_mock),
    ):
        totals = await run_watcher_sweep()

    deliver_mock.assert_not_awaited()
    assert totals["notifications"] == 0


async def test_dpdp_findings_go_to_platform_owners_not_company_hr() -> None:
    """Erasure deadlines are a platform obligation, not a per-tenant one."""
    from shared.agents import ErasureRequest

    factory = MagicMock()
    db = _db([[]])  # no companies
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    deliver_mock = AsyncMock(return_value=1)
    with (
        patch.object(watch_runner, "get_session_factory", return_value=factory),
        patch.object(
            watch_runner,
            "gather_erasure_deadlines",
            AsyncMock(return_value=[ErasureRequest("r-1", 6.0)]),
        ),
        patch.object(watch_runner, "_platform_owners", AsyncMock(return_value=["owner-1"])),
        patch.object(watch_runner, "deliver", deliver_mock),
    ):
        totals = await run_watcher_sweep()

    assert deliver_mock.await_args.args[2] == ["owner-1"]
    assert totals["findings"] == 1
