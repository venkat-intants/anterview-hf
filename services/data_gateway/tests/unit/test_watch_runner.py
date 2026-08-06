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
    _mark_notified,
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


async def test_the_suppression_check_does_not_claim_the_slot() -> None:
    """Checking must be a pure read.

    Claiming on check (SET NX) suppressed a finding for the full TTL even when
    the INSERT behind it never committed — a statutory DPDP alert that was never
    delivered and could not fire again for 8 days.
    """
    redis = MagicMock()
    redis.exists = AsyncMock(return_value=0)
    redis.set = AsyncMock()
    with patch.object(watch_runner, "get_redis", return_value=redis):
        assert await _already_notified("u-1", "k1") is False
    redis.set.assert_not_awaited()


async def test_dedupe_marker_uses_a_bounded_ttl() -> None:
    """An unbounded marker would suppress a recurring problem forever."""
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    with patch.object(watch_runner, "get_redis", return_value=redis):
        await _mark_notified("u-1", "k1")
    assert redis.set.await_args.kwargs["ex"] == DEDUPE_TTL_SECONDS


async def test_markers_are_only_set_after_the_rows_commit() -> None:
    """A failed write must be retried tomorrow, not suppressed for 8 days."""
    db = _db([])
    db.commit = AsyncMock(side_effect=RuntimeError("connection reset"))
    marked: list[tuple[str, str]] = []

    async def _mark(user_id: str, key: str) -> None:
        marked.append((user_id, key))

    with (
        patch.object(watch_runner, "_already_notified", AsyncMock(return_value=False)),
        patch.object(watch_runner, "_mark_notified", _mark),
        pytest.raises(RuntimeError),
    ):
        await deliver(db, [_finding()], ["u-1"])

    assert marked == []


async def test_markers_are_set_for_every_row_that_committed() -> None:
    db = _db([])
    marked: list[tuple[str, str]] = []

    async def _mark(user_id: str, key: str) -> None:
        marked.append((user_id, key))

    with (
        patch.object(watch_runner, "_already_notified", AsyncMock(return_value=False)),
        patch.object(watch_runner, "_mark_notified", _mark),
    ):
        await deliver(db, [_finding(key="k1")], ["u-1", "u-2"])

    assert marked == [("u-1", "k1"), ("u-2", "k1")]


async def test_two_findings_sharing_a_key_notify_once_per_recipient() -> None:
    """The old claim-on-check SET NX collapsed these as a side effect."""
    db = _db([])
    with (
        patch.object(watch_runner, "_already_notified", AsyncMock(return_value=False)),
        patch.object(watch_runner, "_mark_notified", AsyncMock()),
    ):
        written = await deliver(db, [_finding(key="k1"), _finding(key="k1")], ["u-1"])
    assert written == 1


async def test_an_empty_dedupe_key_never_collapses_distinct_findings() -> None:
    """An empty key means 'never suppress this', not 'suppress everything'."""
    db = _db([])
    with (
        patch.object(watch_runner, "_already_notified", AsyncMock(return_value=False)),
        patch.object(watch_runner, "_mark_notified", AsyncMock()),
    ):
        written = await deliver(db, [_finding(key=""), _finding(key="")], ["u-1"])
    assert written == 2


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


async def test_a_sql_failure_is_rolled_back_so_later_tenants_still_query() -> None:
    """The isolation guarantee, against a session that behaves like Postgres.

    All 500 companies share one session. A SQL-level failure leaves the
    transaction aborted, so without a rollback every remaining tenant's queries
    fail identically and the sweep silently delivers nothing platform-wide.
    """

    class AbortingSession:
        def __init__(self, rows: list[list[Any]]) -> None:
            self._rows = list(rows)
            self.aborted = False
            self.rollbacks = 0
            self.commit = AsyncMock()

        async def execute(self, *args: Any, **kwargs: Any) -> MagicMock:
            if self.aborted:
                raise RuntimeError("current transaction is aborted")
            rows = self._rows.pop(0) if self._rows else []
            result = MagicMock()
            result.all.return_value = rows
            result.first.return_value = rows[0] if rows else None
            return result

        async def rollback(self) -> None:
            self.rollbacks += 1
            self.aborted = False

    from shared.agents import StalledApplicant, WatcherInput

    db = AbortingSession(
        [
            [_row(id="co-bad"), _row(id="co-good")],  # the company list
            [_row(id="u-1")],  # recipients for co-good
        ]
    )

    async def _gather(session: Any, company_id: str) -> Any:
        if company_id == "co-bad":
            session.aborted = True
            raise RuntimeError("invalid input syntax for type integer")
        return WatcherInput(
            company_id=company_id, stalled=[StalledApplicant("a-1", "Asha", "new", 30)]
        )

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    deliver_mock = AsyncMock(return_value=1)

    with (
        patch.object(watch_runner, "get_session_factory", return_value=factory),
        patch.object(watch_runner, "gather_company_input", _gather),
        patch.object(watch_runner, "gather_erasure_deadlines", AsyncMock(return_value=[])),
        patch.object(watch_runner, "deliver", deliver_mock),
    ):
        totals = await run_watcher_sweep()

    assert db.rollbacks == 1
    # co-good's recipient lookup ran against a clean session, so it was notified.
    assert deliver_mock.await_args.args[2] == ["u-1"]
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
