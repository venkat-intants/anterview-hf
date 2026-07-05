"""Agent dispatch — NO-OP under automatic dispatch.

PIVOT 2026-05-31: the interview WORKER (app/worker/interview_worker.py) runs in
AUTOMATIC dispatch mode (no agent_name), so it joins every LiveKit room as soon
as it's created — no explicit dispatch needed. The worker resolves the
job/language from the DB by room name (== session_id).

This module is kept so the token endpoint's call site is unchanged; it simply
records intent and returns. The worker process must be running:
    poetry run python -m app.worker.interview_worker dev   (or 'start' in prod)
"""

from __future__ import annotations

import structlog

from app.graph.state import Language

log = structlog.get_logger(__name__)


async def dispatch_interview_agent(
    *,
    room_name: str,
    session_id: str,
    job_id: str,
    job_title: str,
    language: Language,
    voice: str = "kavya",
) -> bool:
    """No-op under automatic dispatch — the worker auto-joins the room.

    Always returns True. Kept async + same signature so the token endpoint's
    call site is stable if we later switch back to explicit dispatch.
    """
    log.info(
        "agent.dispatch.auto",
        room=room_name,
        session_id=session_id,
        note="worker auto-joins (automatic dispatch)",
    )
    return True
