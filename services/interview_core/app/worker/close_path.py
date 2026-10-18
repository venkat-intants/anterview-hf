"""Helpers for the interview's close path, kept out of ``interview_worker.py``.

Split out for the reason IC-4 split the others: ``interview_worker.py`` has a
line ceiling (``test_worker_module_surface``), and code that is not about running
a LiveKit job belongs in a sibling module.

Logging goes to the ``"interview-worker"`` stdlib logger, the one fitted with the
PII redaction chain (DPDP §8) — see ``session_store``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.worker.session_store import _persist_turns, _update_session_status

logger = logging.getLogger("interview-worker")

# Upper bound on the spoken goodbye (~25 words, a few seconds of audio) before the
# close path moves on without it.
CLOSING_SAY_TIMEOUT_SECONDS: float = 45.0


async def write_final_record(
    session_id: str,
    transcript: list[dict[str, str]],
    status: str,
    *,
    completed_at: datetime,
    duration_seconds: int | None,
) -> bool:
    """Write the transcript, THEN the terminal status. True iff both landed.

    Turns first, and the status is withheld if they fail. The reaper deletes the
    checkpoint of any session that is no longer 'in_progress' ("already
    finalised"), so a terminal status written ahead of a failed transcript write
    would make the checkpoint, the only other copy of the transcript, get swept
    as litter. Leaving the row 'in_progress' keeps it recoverable.
    """
    if not await _persist_turns(session_id, transcript):
        logger.error(
            "interview-worker.final_record_turns_failed room=%s - status left "
            "in_progress and checkpoint kept for the reaper",
            session_id,
        )
        return False
    return bool(
        await _update_session_status(
            session_id,
            status,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
        )
    )


async def delete_key(
    get_client: Callable[[], Any], key: str, *, timeout: float, what: str
) -> None:
    """Delete a Redis key within ``timeout``. Never raises (logged on failure)."""
    try:
        await asyncio.wait_for(get_client().delete(key), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("interview-worker.%s_failed err=%s", what, type(exc).__name__)


async def settle_checkpoint(
    session_id: str,
    record_ok: bool,
    *,
    clear: Callable[[str], Awaitable[None]],
    release_guard: Callable[[str], Awaitable[None]],
) -> None:
    """Drop the checkpoint if the record was written; otherwise keep it.

    The checkpoint is the only durable copy of the transcript, so it goes only
    once ``write_final_record`` succeeded. When it did not, the single-fire guard
    is handed back too: the reaper reads "guard held" as "already finalised" and
    would delete the checkpoint we just kept.
    """
    if record_ok:
        await clear(session_id)
        return
    logger.error(
        "interview-worker.close_record_unwritten room=%s - checkpoint kept for the reaper",
        session_id,
    )
    await release_guard(session_id)


def log_task_failure(task: asyncio.Task[Any]) -> None:
    """Done-callback: surface an exception that would otherwise vanish silently."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "interview-worker.background_task_failed task=%s err=%s",
            task.get_name(), type(exc).__name__,
        )


async def _await_speech(handle: Any) -> None:
    await handle


async def say_closing(session: Any, make_text: Callable[[], str], session_id: str) -> None:
    """Speak the goodbye, waiting at most ``CLOSING_SAY_TIMEOUT_SECONDS``.

    ``allow_interruptions=False`` means nothing can cut the line short, so a
    stalled TTS or a dead audio track would otherwise hold the whole close path
    (status, transcript, scoring, room deletion) forever. The wait is shielded so
    the timeout abandons the WAIT without cancelling the speech handle's own
    future. A failure to speak is logged and swallowed: the close must continue.
    """
    try:
        handle = session.say(make_text(), allow_interruptions=False)
        waiter = asyncio.ensure_future(_await_speech(handle))
        waiter.add_done_callback(log_task_failure)
        await asyncio.wait_for(asyncio.shield(waiter), CLOSING_SAY_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "interview-worker: closing say() timed out after %.0fs room=%s",
            CLOSING_SAY_TIMEOUT_SECONDS, session_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker: closing say() failed room=%s err=%s",
            session_id, type(exc).__name__,
        )
