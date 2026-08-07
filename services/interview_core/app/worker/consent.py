"""DPDP §11 — resolving the candidate to poll, and the mid-session watchdog.

Split out of ``interview_worker.py`` (IC-4) unchanged. Both functions are about
one obligation: a candidate who withdraws consent mid-interview must be cut off
within one poll interval, and a worker that cannot CONFIRM consent must stop
rather than keep recording.

The two halves are here together because they share the sentinel contract —
``resolve_consent_user_id`` returns a value that means "the DB would not answer",
and ``_run_consent_watchdog`` is the only code allowed to interpret it. Splitting
them would put the producer and the sole consumer of a fail-closed signal in
different files.

Logging goes to the ``"interview-worker"`` stdlib logger, the same one
``_configure_worker_logging`` fits with the PII redaction chain — a sibling
module with its own logger name would quietly sit outside that net (DPDP §8).
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid_mod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.worker.constants import CONSENT_RECHECK_INTERVAL_SECONDS

if TYPE_CHECKING:  # pragma: no cover — import exists for the annotation only
    from app.worker.interview_worker import InterviewState

logger = logging.getLogger("interview-worker")


_RESOLVE_CONSENT_MAX_ATTEMPTS: int = 3
_RESOLVE_CONSENT_BACKOFF_SECONDS: float = 1.0

# Sentinel returned by resolve_consent_user_id to distinguish a transient DB
# error (consent watchdog must fail-closed) from a genuine no-op such as an
# unrecognised room name (consent watchdog may legitimately skip polling).
_CONSENT_RESOLVE_DB_ERROR: str = "__DB_ERROR__"


async def resolve_consent_user_id(room_name: str) -> str | None:
    """Return the ``user_id`` to poll for consent for a session.

    Covers BOTH the registered-candidate flow and the primary guest magic-link
    flow:

    Registered-candidate flow
        ``POST /api/sessions`` creates a session with ``user_id`` set to the
        authenticated candidate's id.  The consent ledger entry was recorded
        when the candidate accepted the DPDP modal (``POST /consent``).

    Guest magic-link flow (primary invite path — ``interview_take.py``)
        ``POST /interview-invite/redeem`` always lazy-provisions a real ``users``
        row for the applicant (``role='guest_candidate'``) and writes
        ``sessions.user_id = guest_user_id`` in the same transaction.  It also
        records a ``dpdp_consent_ledger`` entry for that ``guest_user_id``
        (the applicant's landing-page checkbox tick).  Therefore the ``user_id``
        column is NEVER NULL for live guest sessions and the watchdog CAN and
        SHOULD poll it — returning ``None`` here and silently skipping consent
        re-checking would mean a guest who withdraws consent mid-session is
        never cut off (DPDP §11 violation).

    Returns:
        ``str``  — the ``user_id`` UUID string for a known, live session.
        ``None`` — only for *genuine* no-ops where consent polling is
                   impossible: ``room_name`` is not a UUID, or the session row
                   does not exist (orphaned/CI dispatch).  The watchdog may
                   safely skip polling in these cases.
        ``_CONSENT_RESOLVE_DB_ERROR`` — the DB was reachable on a previous call
                   but a *transient* error occurred on every retry attempt.
                   The watchdog treats this as a fail-closed signal and ends
                   the session rather than recording without withdrawal
                   protection (DPDP §11 fail-safe).

    Retry policy:
        Up to _RESOLVE_CONSENT_MAX_ATTEMPTS attempts with linear backoff of
        _RESOLVE_CONSENT_BACKOFF_SECONDS between retries. This distinguishes a
        genuine transient error (exhausts retries → fail-closed) from a
        permanent "room not found" (returns None immediately, no retries).

    Isolated from ``_lookup_session`` so the consent watchdog can resolve the
    candidate without disturbing that function's stable return tuple.
    """
    import contextlib

    from sqlalchemy import select

    from app.database import get_session_factory, init_engine
    from app.models import Session as InterviewSession

    with contextlib.suppress(Exception):
        init_engine()

    try:
        sid = _uuid_mod.UUID(room_name)
    except ValueError:
        # Not a UUID — bare/CI dispatch; no DB row possible. Legit no-op.
        return None

    last_exc: Exception | None = None
    for attempt in range(1, _RESOLVE_CONSENT_MAX_ATTEMPTS + 1):
        try:
            factory = get_session_factory()
            async with factory() as db:
                uid = (
                    await db.execute(
                        select(InterviewSession.user_id).where(InterviewSession.id == sid)
                    )
                ).scalar_one_or_none()
            # scalar_one_or_none() returns None for two sub-cases:
            #   (a) No session row — orphaned room. Legit no-op.
            #   (b) session row exists but user_id IS NULL — data integrity
            #       problem; log a WARNING and treat as no-op.
            if uid is None:
                logger.warning(
                    "interview-worker.consent_user_lookup_no_user_id room=%s "
                    "— session row missing or user_id NULL; consent watchdog "
                    "will be a no-op for this session",
                    room_name,
                )
                return None
            return str(uid)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "interview-worker.consent_user_lookup_failed room=%s attempt=%d/%d err=%s",
                room_name, attempt, _RESOLVE_CONSENT_MAX_ATTEMPTS, type(exc).__name__,
            )
            if attempt < _RESOLVE_CONSENT_MAX_ATTEMPTS:
                await asyncio.sleep(_RESOLVE_CONSENT_BACKOFF_SECONDS * attempt)

    # All attempts failed — transient DB error. Caller (watchdog) must treat
    # this as fail-closed to preserve DPDP §11 right-to-withdraw protection.
    logger.error(
        "interview-worker.consent_user_lookup_exhausted room=%s — "
        "all %d attempts failed (last: %s); watchdog will fail-closed",
        room_name, _RESOLVE_CONSENT_MAX_ATTEMPTS,
        type(last_exc).__name__ if last_exc else "unknown",
    )
    return _CONSENT_RESOLVE_DB_ERROR


# Keep the old name as an alias so any external callers (e.g. tests pinned to
# the old name) continue to work during the transition period.
_lookup_candidate_user_id = resolve_consent_user_id


# ---------------------------------------------------------------------------
# Consent watchdog — extracted module-level helper for testability
# ---------------------------------------------------------------------------
#
# The core watchdog logic is split out of the job object so tests can drive it
# directly without spinning up a full LiveKit session.
# ``InterviewJob._consent_watchdog`` delegates to this function.  Any change to
# sentinel-branch behaviour here will be caught by the unit tests.
#
# ``on_close`` has the same signature as ``InterviewJob._on_close``:
#     async def on_close(*, timed_out: bool, consent_withdrawn: bool = False) -> None

_OnCloseFn = Callable[..., Awaitable[None]]


async def _run_consent_watchdog(
    *,
    user_id: str | None,
    on_close: _OnCloseFn,
    state: InterviewState,
    session_id: str,
) -> None:
    """Module-level consent watchdog body — delegates from InterviewJob._consent_watchdog.

    Sentinel values for ``user_id`` (see ``resolve_consent_user_id`` docstring):
      - valid UUID string → poll the consent ledger for this user every
        CONSENT_RECHECK_INTERVAL_SECONDS.
      - None              → legit no-op: unrecognised room / CI dispatch.
      - _CONSENT_RESOLVE_DB_ERROR → transient DB error exhausted all retries
                            at session start; FAIL-CLOSED: end the session now
                            rather than continue without withdrawal protection
                            (DPDP §11 fail-safe).

    Mid-session consent checks FAIL OPEN: a transient DB blip keeps the
    interview running and retries on the next tick.  Only a definitive
    'consent is no longer active' response ends the session.
    """
    if user_id == _CONSENT_RESOLVE_DB_ERROR:
        # Resolver exhausted retries at session start — we cannot confirm
        # active consent.  Fail-closed: end the session immediately.
        logger.error(
            "interview-worker.consent_watchdog_fail_closed room=%s — "
            "resolver DB error exhausted; ending session to protect DPDP §11",
            session_id,
        )
        await on_close(timed_out=False, consent_withdrawn=True)
        return

    if not user_id:
        # Legit no-op: unrecognised room (e.g. bare CI dispatch), orphaned
        # row, or non-UUID room name.
        return

    import contextlib as _contextlib

    from app.consent_guard import has_active_consent
    from app.database import get_session_factory, init_engine

    with _contextlib.suppress(Exception):
        init_engine()

    while not state.close_triggered:
        await asyncio.sleep(CONSENT_RECHECK_INTERVAL_SECONDS)
        if state.close_triggered:
            return
        try:
            factory = get_session_factory()
            async with factory() as db:
                active = await has_active_consent(db, user_id)
        except Exception as exc:  # noqa: BLE001 — fail open, retry next tick
            logger.warning(
                "interview-worker.consent_recheck_failed room=%s err=%s",
                session_id, type(exc).__name__,
            )
            continue
        if not active:
            logger.warning(
                "interview-worker.consent_withdrawn room=%s — ending session",
                session_id,
            )
            await on_close(timed_out=False, consent_withdrawn=True)
            return
