"""Writes to the ``sessions`` and ``turns`` tables from the worker process.

Split out of ``interview_worker.py`` (IC-4) unchanged. Every function here obeys
the same two rules, which is why they belong together:

* **Best-effort — never raises.** These run on the interview's close path. A DB
  blip must cost telemetry, not the candidate's session; each swallows its
  exception and logs the exception TYPE only. The two that write the durable
  record (``_update_session_status``, ``_persist_turns``) RETURN whether the
  write landed, after a few backed-off attempts, because the close path deletes
  the Redis checkpoint (the only other copy of the transcript) and must only do
  so once they have succeeded.
* **Engine-defensive.** The LiveKit worker is a separate process from the
  FastAPI app, so nothing has called ``init_engine()`` for it. Each entry point
  calls it under ``contextlib.suppress``. That is cheap only because
  ``init_engine()`` is idempotent: the first call builds the engine (lazily —
  no socket opens until the first checkout) and every later one returns it.
  Before that guarantee existed, this per-operation pattern built a fresh pool
  per write and orphaned the previous one undisposed.

Logging goes to the ``"interview-worker"`` stdlib logger, the same one
``_configure_worker_logging`` fits with the PII redaction chain — a sibling
module with its own logger name would quietly sit outside that net (DPDP §8).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("interview-worker")

# Pause before each retry of the writes the checkpoint's deletion depends on.
# Short on purpose: this runs while the candidate is being disconnected, and a DB
# that is still down after ~2 s is better handled by the reaper (which retries
# from the checkpoint) than by holding the close path open.
_DB_WRITE_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.5)


async def _update_session_status(
    room_name: str,
    status: str,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_seconds: int | None = None,
) -> bool:
    """Persist session status + timing fields. Never raises; True iff it landed.

    Called with status='in_progress' when the agent starts, then with
    status='completed' or 'abandoned' when the session ends. Retried with
    backoff: it is a plain UPDATE by primary key, so a repeat is harmless.
    """
    import contextlib

    from sqlalchemy import update

    from app.database import get_session_factory, init_engine
    from app.models import Session as InterviewSession

    with contextlib.suppress(Exception):
        init_engine()
    try:
        sid = _uuid_mod.UUID(room_name)
    except ValueError:
        logger.warning("interview-worker: cannot parse room_name as UUID: %s", room_name)
        return False
    values: dict[str, Any] = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if duration_seconds is not None:
        values["duration_seconds"] = duration_seconds
    attempts = len(_DB_WRITE_BACKOFF_SECONDS) + 1
    for attempt in range(attempts):
        try:
            factory = get_session_factory()
            async with factory() as db:
                await db.execute(
                    update(InterviewSession)
                    .where(InterviewSession.id == sid)
                    .values(**values)
                )
                await db.commit()
            logger.info(
                "interview-worker.session_status room=%s status=%s", room_name, status
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker: session status update failed room=%s status=%s "
                "attempt=%d/%d err=%s",
                room_name, status, attempt + 1, attempts, type(exc).__name__,
            )
            if attempt < attempts - 1:
                await asyncio.sleep(_DB_WRITE_BACKOFF_SECONDS[attempt])
    return False


async def _read_session_status(room_name: str) -> str | None:
    """Return ``sessions.status`` for a room, or None when it cannot be read.

    Never raises. The None return deliberately conflates "no such row" with "the
    DB refused to answer", because the only caller (the crash reaper) must treat
    both the same way: do nothing. Guessing a status here would let a transient
    DB blip flip a live interview to 'abandoned' out from under the candidate.
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
        return None
    try:
        factory = get_session_factory()
        async with factory() as db:
            status = (
                await db.execute(
                    select(InterviewSession.status).where(InterviewSession.id == sid)
                )
            ).scalar_one_or_none()
        return str(status) if status is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker: session status read failed room=%s err=%s",
            room_name, type(exc).__name__,
        )
        return None


# ---------------------------------------------------------------------------
# Transcript persistence
# ---------------------------------------------------------------------------


async def _persist_turns(
    room_name: str,
    transcript: list[dict[str, str]],
) -> bool:
    """Persist the in-memory transcript to the ``turns`` table. Never raises; True iff stored.

    An empty transcript is vacuously stored (True). A unique-constraint violation
    on ``(session_id, turn_number)`` also counts as stored: those rows can only
    be this same transcript, from an earlier attempt whose COMMIT succeeded but
    whose acknowledgement was lost, or from the other close path. Other failures
    are retried with backoff.

    Called ONCE at session close (normal or abrupt), before scoring. Writing at
    close — rather than on every committed item during the live loop — keeps the
    latency-sensitive turn loop off the cloud-DB round-trip path (NFR: p95 turn
    latency < 2 s) and makes the write atomic.

    The transcript items use the scorer role vocabulary; we map to the turns
    table's speaker vocabulary:
        "user" -> "candidate"
        "ai"   -> "interviewer"
    ``turn_number`` is a 1-based sequence in arrival order, satisfying the
    uq_turns_session_turn_number unique constraint. The close paths are mutually
    guarded by ``state.close_triggered`` so this runs at most once per session.
    """
    if not transcript:
        return True

    import contextlib

    from sqlalchemy.exc import IntegrityError

    from app.database import get_session_factory, init_engine
    from app.models import Turn

    with contextlib.suppress(Exception):
        init_engine()
    try:
        sid = _uuid_mod.UUID(room_name)
    except ValueError:
        return False

    now = datetime.now(tz=UTC)
    attempts = len(_DB_WRITE_BACKOFF_SECONDS) + 1
    for attempt in range(attempts):
        # Fresh ORM objects per attempt: a failed flush leaves the previous
        # attempt's instances unusable.
        rows = [
            Turn(
                session_id=sid,
                turn_number=i,
                speaker=("candidate" if item.get("role") == "user" else "interviewer"),
                text_content=item.get("text", ""),
                created_at=now,
            )
            for i, item in enumerate(transcript, start=1)
        ]
        try:
            factory = get_session_factory()
            async with factory() as db:
                db.add_all(rows)
                await db.commit()
            logger.info(
                "interview-worker.turns_persisted room=%s count=%d", room_name, len(rows)
            )
            return True
        except IntegrityError:
            logger.info(
                "interview-worker.turns_already_persisted room=%s attempt=%d",
                room_name, attempt + 1,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker: persist turns failed room=%s attempt=%d/%d err=%s",
                room_name, attempt + 1, attempts, type(exc).__name__,
            )
            if attempt < attempts - 1:
                await asyncio.sleep(_DB_WRITE_BACKOFF_SECONDS[attempt])
    return False


# Key under which marker names land in ``sessions.metadata``. Same name the
# scoring path already uses for the same thing
# (feedback_billing/app/resume_scorer.py), so an HR view can read one key across
# both surfaces rather than learning two vocabularies.
_INJECTION_MARKERS_METADATA_KEY: str = "injection_markers"


async def _persist_injection_markers(room_name: str, markers: list[str]) -> None:
    """Record resume injection-marker NAMES on the session row. Never raises.

    AG-07: the live path detected markers and then threw the result away after
    logging, so a candidate who planted "ignore previous instructions" in their
    CV was invisible to the HR manager reviewing them — while the same document
    on the *scoring* path surfaced its markers next to the score. Only one of
    the two surfaces was honest about it.

    WHY ``sessions.metadata`` and not a column: this service does not own
    migrations (data_gateway does), and ``metadata`` is an existing NOT NULL
    JSONB column on the row the reviewer already loads. A dedicated column
    would read better and can replace this later without changing the key.

    WHY a JSONB merge rather than read-modify-write: ``_update_session_status``
    writes other columns of the same row concurrently with this call, and a
    read-modify-write of the whole document would be the classic lost-update.
    ``metadata || :patch`` is a single statement, so the two cannot clobber
    each other.

    Marker NAMES only. They are our own literals; the matching resume text is
    the candidate's personal data (DPDP §8) and must not be copied into a
    column whose whole purpose is to be read by a human reviewer.
    """
    if not markers:
        # The overwhelmingly common case — do not spend a round trip on it.
        return

    import contextlib

    from sqlalchemy import text as _sql_text

    from app.database import get_session_factory, init_engine

    with contextlib.suppress(Exception):
        init_engine()
    try:
        sid = _uuid_mod.UUID(room_name)
    except ValueError:
        return

    patch = json.dumps({_INJECTION_MARKERS_METADATA_KEY: markers})
    try:
        factory = get_session_factory()
        async with factory() as db:
            await db.execute(
                _sql_text(
                    "UPDATE sessions "
                    "SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:patch AS jsonb) "
                    "WHERE id = :sid"
                ),
                {"patch": patch, "sid": sid},
            )
            await db.commit()
        logger.info(
            "interview-worker.injection_markers_persisted room=%s count=%d",
            room_name, len(markers),
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must never cost an interview
        logger.warning(
            "interview-worker: persist injection markers failed room=%s err=%s",
            room_name, type(exc).__name__,
        )
