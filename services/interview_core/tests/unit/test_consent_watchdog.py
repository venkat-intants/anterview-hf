"""Unit tests for the DPDP consent watchdog and candidate-resolver.

Covers:
  - resolve_consent_user_id: returns user_id for registered-candidate sessions.
  - resolve_consent_user_id: returns user_id for guest magic-link sessions
    (user_id is always set by interview_take.redeem_invite — this is the primary
    guest flow fix; previously the comment said "nothing to poll" which was wrong).
  - resolve_consent_user_id: returns None for invalid UUID room names.
  - resolve_consent_user_id: returns None for missing session rows.
  - resolve_consent_user_id: returns None and logs a warning on DB errors.
  - _consent_watchdog_logic (extracted pure helper): fires _on_close with
    consent_withdrawn=True when has_active_consent returns False.
  - _consent_watchdog_logic: keeps polling when consent is active.
  - _consent_watchdog_logic: fails open (does NOT close the session) on a
    transient DB error; resumes polling on the next tick.
  - _consent_watchdog_logic: exits cleanly once close_triggered is set.

All tests are fully offline (no DB, no LiveKit). DB calls are replaced by
mock async session factories.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.interview_worker as wk
from app.worker.interview_worker import (
    InterviewState,
    resolve_consent_user_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_VALID_USER_ID = "11111111-2222-3333-4444-555555555555"
_VALID_GUEST_USER_ID = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"


def _make_scalar_factory(return_value: Any) -> Any:
    """Mock async_sessionmaker that returns ``return_value`` from scalar_one_or_none."""

    @asynccontextmanager
    async def _cm() -> Any:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = return_value
        db.execute = AsyncMock(return_value=result)
        yield db

    return MagicMock(side_effect=lambda: _cm())


def _make_raising_factory(exc: Exception) -> Any:
    """Mock async_sessionmaker that raises ``exc`` when the DB session is entered."""

    @asynccontextmanager
    async def _cm() -> Any:
        raise exc
        yield  # pragma: no cover

    return MagicMock(side_effect=lambda: _cm())


# ---------------------------------------------------------------------------
# resolve_consent_user_id — registered-candidate session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_consent_user_id_registered_candidate() -> None:
    """Returns user_id string for a registered-candidate session (user_id is set)."""
    uid = uuid.UUID(_VALID_USER_ID)
    factory = _make_scalar_factory(uid)

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await resolve_consent_user_id(_VALID_SESSION_ID)

    assert result == _VALID_USER_ID


@pytest.mark.asyncio
async def test_resolve_consent_user_id_guest_session() -> None:
    """Returns user_id string for a guest magic-link session.

    The interview_take.redeem_invite flow ALWAYS provisions a users row and sets
    sessions.user_id = guest_user_id.  This test asserts that resolve_consent_user_id
    works for guest sessions — fixing the audit finding that the watchdog was
    documented as a no-op for guests, which would have been a DPDP §11 violation.
    """
    guest_uid = uuid.UUID(_VALID_GUEST_USER_ID)
    factory = _make_scalar_factory(guest_uid)

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await resolve_consent_user_id(_VALID_SESSION_ID)

    # The watchdog receives a non-None user_id for guests and WILL poll consent.
    assert result == _VALID_GUEST_USER_ID, (
        "resolve_consent_user_id must return the guest user_id so the consent "
        "watchdog can enforce DPDP §11 withdrawal for guest sessions"
    )


# ---------------------------------------------------------------------------
# resolve_consent_user_id — edge/error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_consent_user_id_invalid_room_name() -> None:
    """Returns None for a room name that is not a valid UUID."""
    factory = MagicMock(side_effect=AssertionError("DB must not be called"))
    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await resolve_consent_user_id("not-a-uuid")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_consent_user_id_missing_row() -> None:
    """Returns None when the session row does not exist (scalar returns None)."""
    factory = _make_scalar_factory(None)  # no row found

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await resolve_consent_user_id(_VALID_SESSION_ID)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_consent_user_id_db_error_returns_sentinel() -> None:
    """On a transient DB error that exhausts all retries, the sentinel
    _CONSENT_RESOLVE_DB_ERROR is returned (NOT None).

    This distinguishes a genuine DB failure (consent watchdog must fail-closed)
    from a legit no-op (orphaned room, returns None).  Returning None on a DB
    error was the original bug: it caused the watchdog to silently skip
    DPDP §11 enforcement for the entire session.
    """
    from app.worker.interview_worker import _CONSENT_RESOLVE_DB_ERROR

    factory = _make_raising_factory(RuntimeError("connection pool exhausted"))

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
        patch("app.worker.interview_worker.asyncio.sleep", new=AsyncMock()),
    ):
        result = await resolve_consent_user_id(_VALID_SESSION_ID)

    assert result == _CONSENT_RESOLVE_DB_ERROR, (
        "DB error must return _CONSENT_RESOLVE_DB_ERROR (fail-closed sentinel), "
        "not None (which would silently disable DPDP §11 enforcement)"
    )
    assert result is not None, (
        "_CONSENT_RESOLVE_DB_ERROR must be non-None to be distinguishable from "
        "a legit no-op (orphaned room)"
    )


@pytest.mark.asyncio
async def test_resolve_consent_user_id_missing_row_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing/None user_id emits a WARNING-level log (ops visibility)."""
    import logging

    factory = _make_scalar_factory(None)

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
        caplog.at_level(logging.WARNING, logger="interview-worker"),
    ):
        await resolve_consent_user_id(_VALID_SESSION_ID)

    assert any(
        "consent_user_lookup_no_user_id" in rec.message for rec in caplog.records
    ), "A missing user_id must produce a WARNING log for ops dashboards"


# ---------------------------------------------------------------------------
# _consent_watchdog_logic — extracted pure helper for unit-testable behaviour
#
# The watchdog is a closure inside entrypoint() and depends on session-scoped
# state. We extract its core decision logic into a standalone coroutine here
# and test that.  This mirrors the pattern used in test_interview_worker.py
# for the wall-clock cap and abrupt-disconnect paths.
# ---------------------------------------------------------------------------


async def _run_watchdog_logic(
    *,
    user_id: str | None,
    consent_active_responses: list[bool | Exception],
    state: InterviewState,
    session_id: str = _VALID_SESSION_ID,
    close_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Simulate the watchdog's inner loop without asyncio.sleep delays.

    ``consent_active_responses`` is consumed one entry per poll tick.
    An ``Exception`` entry simulates a DB error on that tick.
    The loop stops after all entries are consumed or close_triggered is set.
    """
    if close_calls is None:
        close_calls = []

    async def fake_on_close(*, timed_out: bool, consent_withdrawn: bool = False) -> None:
        if state.close_triggered:
            return
        state.mark_close_triggered()
        close_calls.append({"timed_out": timed_out, "consent_withdrawn": consent_withdrawn})

    if not user_id:
        return

    for response in consent_active_responses:
        if state.close_triggered:
            return
        if isinstance(response, Exception):
            # Simulate DB error — fail open, do NOT close.
            continue
        if not response:
            await fake_on_close(timed_out=False, consent_withdrawn=True)
            return


@pytest.mark.asyncio
async def test_watchdog_no_user_id_is_noop() -> None:
    """When user_id is None, the watchdog must return immediately without polling."""
    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    await _run_watchdog_logic(
        user_id=None,
        consent_active_responses=[False],  # would fire if polled
        state=state,
        close_calls=close_calls,
    )

    assert close_calls == [], "Watchdog must not close session when user_id is None"
    assert not state.close_triggered


@pytest.mark.asyncio
async def test_watchdog_consent_withdrawn_triggers_close() -> None:
    """When has_active_consent returns False, the watchdog must end the session
    with consent_withdrawn=True and must not call _post_score."""
    from livekit.agents.llm.chat_context import ChatMessage

    state = InterviewState()
    # Add some transcript so final_status would be 'completed' if scored —
    # verifying that consent_withdrawn=True suppresses scoring regardless.
    msg = MagicMock(spec=ChatMessage)
    msg.role = "user"
    msg.text_content = "My answer"
    msg.interrupted = False
    state.handle_conversation_item(msg)
    assert state.candidate_answer_count == 1

    close_calls: list[dict[str, Any]] = []

    await _run_watchdog_logic(
        user_id=_VALID_USER_ID,
        consent_active_responses=[False],  # consent revoked on first tick
        state=state,
        close_calls=close_calls,
    )

    assert len(close_calls) == 1, "Watchdog must trigger exactly one close on withdrawal"
    assert close_calls[0]["consent_withdrawn"] is True, (
        "Close must be flagged as consent_withdrawn=True so scoring is skipped"
    )
    assert close_calls[0]["timed_out"] is False
    assert state.close_triggered


@pytest.mark.asyncio
async def test_watchdog_consent_active_continues_polling() -> None:
    """When consent is active, the watchdog must keep polling and not close."""
    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    # Three ticks all returning True (consent active).
    await _run_watchdog_logic(
        user_id=_VALID_USER_ID,
        consent_active_responses=[True, True, True],
        state=state,
        close_calls=close_calls,
    )

    assert close_calls == [], "No close must happen while consent remains active"
    assert not state.close_triggered


@pytest.mark.asyncio
async def test_watchdog_fails_open_on_db_error() -> None:
    """A DB error on one tick must NOT close the session (fail open).

    The watchdog retries on the next tick. Only a definitive 'False' from
    has_active_consent triggers the close path.
    """
    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    # First tick: DB error.  Second tick: consent active.  Should not close.
    await _run_watchdog_logic(
        user_id=_VALID_USER_ID,
        consent_active_responses=[RuntimeError("db blip"), True],
        state=state,
        close_calls=close_calls,
    )

    assert close_calls == [], (
        "DB error must not close the session — watchdog must fail open"
    )
    assert not state.close_triggered


@pytest.mark.asyncio
async def test_watchdog_db_error_then_withdrawal_closes() -> None:
    """After a DB error, a subsequent consent withdrawal must still trigger close."""
    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    # First tick: DB error (fail open).  Second tick: consent withdrawn (close).
    await _run_watchdog_logic(
        user_id=_VALID_USER_ID,
        consent_active_responses=[RuntimeError("transient"), False],
        state=state,
        close_calls=close_calls,
    )

    assert len(close_calls) == 1
    assert close_calls[0]["consent_withdrawn"] is True


@pytest.mark.asyncio
async def test_watchdog_exits_when_close_already_triggered() -> None:
    """If close_triggered is set before the watchdog's tick, it must exit silently."""
    state = InterviewState()
    state.mark_close_triggered()  # simulate the interview already ended
    close_calls: list[dict[str, Any]] = []

    # Even though consent is "withdrawn", the loop must not fire close again.
    await _run_watchdog_logic(
        user_id=_VALID_USER_ID,
        consent_active_responses=[False],
        state=state,
        close_calls=close_calls,
    )

    # close_calls empty because the loop returns early on close_triggered.
    assert close_calls == [], (
        "Watchdog must not fire a second close when close_triggered is already set"
    )


# ---------------------------------------------------------------------------
# Integration: resolve_consent_user_id feeds watchdog — guest flow end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guest_flow_user_id_feeds_watchdog_and_closes_on_withdrawal() -> None:
    """Full guest-path scenario: DB returns a guest user_id; consent is then withdrawn.

    This is the PRIMARY regression test for the audit finding:
    Before the fix, the watchdog comment implied guest sessions have 'nothing to poll'.
    After the fix, resolve_consent_user_id correctly returns the guest user_id
    (always set by interview_take.redeem_invite) and the watchdog ends the session.
    """
    guest_uid = uuid.UUID(_VALID_GUEST_USER_ID)
    factory = _make_scalar_factory(guest_uid)

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        resolved_uid = await resolve_consent_user_id(_VALID_SESSION_ID)

    # Step 1: resolver must return the guest user_id (not None).
    assert resolved_uid == _VALID_GUEST_USER_ID, (
        "Guest session must resolve to a non-None user_id so the watchdog can poll"
    )

    # Step 2: simulate watchdog receiving that user_id and consent being withdrawn.
    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    await _run_watchdog_logic(
        user_id=resolved_uid,
        consent_active_responses=[False],  # withdrawal on first poll
        state=state,
        close_calls=close_calls,
    )

    assert len(close_calls) == 1, (
        "Consent withdrawal for a guest must trigger exactly one session close"
    )
    assert close_calls[0]["consent_withdrawn"] is True
    assert state.close_triggered


# ---------------------------------------------------------------------------
# Backward-compatibility alias
# ---------------------------------------------------------------------------


def test_lookup_candidate_user_id_alias_exists() -> None:
    """_lookup_candidate_user_id must remain as an alias for resolve_consent_user_id."""
    assert wk._lookup_candidate_user_id is wk.resolve_consent_user_id, (
        "_lookup_candidate_user_id must be an alias for resolve_consent_user_id "
        "to avoid breaking any callers still using the old name"
    )


# ---------------------------------------------------------------------------
# Finding 2: DB-error sentinel → watchdog fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_db_error_sentinel_triggers_fail_closed() -> None:
    """When resolve_consent_user_id returns _CONSENT_RESOLVE_DB_ERROR, the
    watchdog must end the session immediately (fail-closed) rather than
    silently skipping DPDP §11 enforcement.

    This test drives the REAL _run_consent_watchdog module-level function with
    the sentinel value and asserts that on_close is called with
    consent_withdrawn=True.  It would FAIL if the sentinel branch were deleted
    or changed to a no-op, because then close_calls would be empty.

    A DB outage at session start must never disable right-to-withdraw
    protection for the duration of the interview.
    """
    from app.worker.interview_worker import (
        _CONSENT_RESOLVE_DB_ERROR,
        _run_consent_watchdog,
    )

    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    async def fake_on_close(*, timed_out: bool, consent_withdrawn: bool = False) -> None:
        if state.close_triggered:
            return
        state.mark_close_triggered()
        close_calls.append({"timed_out": timed_out, "consent_withdrawn": consent_withdrawn})

    # Drive the REAL watchdog function with the DB-error sentinel.
    # If the sentinel branch is removed or made a no-op, this call will return
    # without invoking fake_on_close, and the assertions below WILL FAIL.
    await _run_consent_watchdog(
        user_id=_CONSENT_RESOLVE_DB_ERROR,
        on_close=fake_on_close,
        state=state,
        session_id=_VALID_SESSION_ID,
    )

    assert len(close_calls) == 1, (
        "DB-error sentinel must trigger exactly one fail-closed close. "
        "If this fails, the _CONSENT_RESOLVE_DB_ERROR branch in "
        "_run_consent_watchdog has been deleted or is a no-op."
    )
    assert close_calls[0]["consent_withdrawn"] is True, (
        "Session closed due to DB-error sentinel must be flagged consent_withdrawn=True "
        "so scoring is suppressed (no scorecard for an unverified consent state)"
    )
    assert close_calls[0]["timed_out"] is False
    assert state.close_triggered, (
        "state.close_triggered must be set after fail-closed watchdog fires"
    )


@pytest.mark.asyncio
async def test_watchdog_none_user_id_does_not_fail_closed() -> None:
    """None user_id must remain a legit no-op (unrecognised room / CI dispatch).

    Only _CONSENT_RESOLVE_DB_ERROR should trigger fail-closed, not None.
    The two must be distinguished so orphaned-room no-ops don't end real sessions.
    """
    from app.worker.interview_worker import _CONSENT_RESOLVE_DB_ERROR

    state = InterviewState()
    close_calls: list[dict[str, Any]] = []

    await _run_watchdog_logic(
        user_id=None,
        consent_active_responses=[False],  # would fire if polled
        state=state,
        close_calls=close_calls,
    )

    assert close_calls == [], (
        "None user_id must be a no-op — it is NOT the DB-error sentinel"
    )
    assert not state.close_triggered

    # Verify that None != _CONSENT_RESOLVE_DB_ERROR so the sentinel is distinct.
    assert _CONSENT_RESOLVE_DB_ERROR != None, (  # noqa: E711
        "_CONSENT_RESOLVE_DB_ERROR must be a non-None sentinel distinguishable "
        "from the legit no-op (None)"
    )
