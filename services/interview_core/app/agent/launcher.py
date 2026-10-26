"""Agent dispatch - EXPLICIT dispatch of the interview worker into a room.

History. The worker used to run in AUTOMATIC dispatch (no ``agent_name``): it
was offered every room LiveKit created, ONCE. If the worker was FULL at that
instant, or the assignment timed out, LiveKit never offered the room again and
the candidate sat alone until the room timed out (12.7 minutes observed in the
2026-10 load test). Nothing on our side could ask for a second attempt.

Now the worker registers under ``settings.interview_agent_name`` and a dispatch
is something we create, so it is something we can create again:

* ``room_configuration`` - the dispatch RIDES IN THE CANDIDATE'S JOIN TOKEN.
  LiveKit applies it when the candidate's join creates the room, i.e. at the
  same moment automatic dispatch used to fire, so no interviewer slot is held
  before the candidate is actually there. A rejoin to a room that still exists
  gets no new dispatch from the token; recovery then depends on the frontend
  watchdog and ``ensure_interviewer``.
* ``ensure_interviewer`` - the idempotent "no agent joined" path behind
  ``POST /api/rooms/{id}/redispatch``.

``ensure_interviewer`` is a RECOVERY path, never an entry path. LiveKit creates a
room as a side effect of creating a dispatch for it, so an unguarded call would
start a paid interviewer in a room nobody joined (security review H1). It
therefore refuses unless the room exists AND contains the candidate's own
participant (``candidate-<user_id>``, the identity the token endpoint mints).

Duplicate interviewers (one room, two agents talking over each other, double
billing) are prevented in layers: a per-room Redis lock around
list -> delete -> create; a young dispatch whose job is still pending counts as
live; and the worker refuses a second job for a room it already runs
(``app/worker/admission.py``).

``INTERVIEW_AGENT_NAME=""`` is the kill switch back to automatic dispatch: this
module then returns ``None`` / ``AUTOMATIC`` and the worker registers unnamed.
The API and worker share one image, so the two sides cannot disagree.

PII: dispatch metadata is empty on purpose. The worker resolves everything from
the DB by room name (== session_id); nothing about the candidate goes to LiveKit.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import aiohttp
import structlog
from livekit import api

from app.config import settings
from app.worker_capacity import acquire_dispatch_lock, release_dispatch_lock

log = structlog.get_logger(__name__)

# A dispatch whose job is RUNNING is clearly an interviewer on its way. Anything
# else is ambiguous: the worker may have accepted the job and be starting its
# process (a second dispatch would run two interviews), or nobody ever took it
# (the stranded case we exist to fix). Age separates them. Verified against a
# local livekit-server (2026-10): a dispatch whose job IS running can still list
# ``jobs: []`` a few seconds in, so a young dispatch is live whatever its job list
# says. 15 s is deliberately below the frontend watchdog's 20 s, so the first
# watchdog re-dispatch can still replace a dispatch that really was never taken;
# the worker refuses a duplicate job for a room it already runs, which covers the
# residual race under heavy CPU contention.
_LIVE_GRACE_SECONDS = 15.0


class DispatchOutcome(str, Enum):
    """What ``ensure_interviewer`` did."""

    ALREADY_CONNECTED = "already_connected"  # an agent participant is in the room
    STARTING = "starting"  # a live dispatch/job exists; agent not joined yet
    DISPATCHED = "dispatched"  # a fresh dispatch was created
    AUTOMATIC = "automatic"  # explicit dispatch is switched off
    NOT_JOINED = "not_joined"  # the candidate is not in the room: nothing to recover


def explicit_dispatch_enabled() -> bool:
    """True when the worker is registered under a name we dispatch to."""
    return bool(settings.interview_agent_name)


def room_configuration() -> api.RoomConfiguration | None:
    """Room configuration that dispatches the interviewer on room creation.

    Embedded in the candidate's join token. ``None`` under automatic dispatch.
    """
    if not explicit_dispatch_enabled():
        return None
    return api.RoomConfiguration(
        agents=[api.RoomAgentDispatch(agent_name=settings.interview_agent_name)]
    )


def candidate_identity(user_id: str) -> str:
    """The participant identity the token endpoint mints for this user."""
    return f"candidate-{user_id}"


def _is_agent(participant: api.ParticipantInfo) -> bool:
    return participant.kind == api.ParticipantInfo.Kind.AGENT


def _age_seconds(created_at: int) -> float:
    """Age of a dispatch from LiveKit's ``created_at`` (s, ms or ns; unknown = old)."""
    if created_at <= 0:
        return float("inf")
    scale = 1.0
    if created_at > 10**17:  # nanoseconds
        scale = 1e9
    elif created_at > 10**14:  # microseconds
        scale = 1e6
    elif created_at > 10**11:  # milliseconds
        scale = 1e3
    return max(0.0, time.time() - created_at / scale)


def _is_live(dispatch: api.AgentDispatch) -> bool:
    """Is an interviewer on its way for this dispatch?"""
    if any(job.state.status == api.JobStatus.JS_RUNNING for job in dispatch.state.jobs):
        return True
    return _age_seconds(dispatch.state.created_at) < _LIVE_GRACE_SECONDS


async def ensure_interviewer(
    room_name: str,
    user_id: str,
    *,
    redis_getter: Callable[[], Any],
    before_create: Callable[[], Awaitable[None]] | None = None,
) -> DispatchOutcome:
    """Make sure an interviewer is, or is about to be, in *room_name*. Idempotent.

    Refuses (``NOT_JOINED``) unless the room exists and holds this user's own
    candidate participant. ``before_create`` runs after every cheap check and
    right before the dispatch is created - the caller reserves capacity there, so
    a call that was going to be refused never takes a slot.

    Raises ``livekit.api.TwirpError`` / ``aiohttp.ClientError`` / ``TimeoutError``
    on a LiveKit outage, and whatever the Redis lock raises: the lock FAILS
    CLOSED, because without it two callers can create two interviewers.
    """
    if not explicit_dispatch_enabled():
        return DispatchOutcome.AUTOMATIC

    timeout = aiohttp.ClientTimeout(total=settings.livekit_api_timeout_seconds)
    async with api.LiveKitAPI(
        settings.livekit_url,
        settings.livekit_api_key,
        settings.livekit_api_secret,
        timeout=timeout,
    ) as lkapi:
        # Does the room exist at all? Asked explicitly because LiveKit does NOT
        # answer uniformly for a missing room (verified against a local
        # livekit-server, 2026-10): ListParticipants returns an empty list, but
        # ListDispatch fails with 503 "no response from servers".
        rooms = await lkapi.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
        if not rooms.rooms:
            return DispatchOutcome.NOT_JOINED
        try:
            listing = await lkapi.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
        except api.TwirpError as exc:
            # The room can vanish between the two calls.
            if exc.code != api.TwirpErrorCode.NOT_FOUND:
                raise
            return DispatchOutcome.NOT_JOINED
        participants = list(listing.participants)

        wanted = candidate_identity(user_id)
        if not any(p.identity == wanted and not _is_agent(p) for p in participants):
            return DispatchOutcome.NOT_JOINED
        if any(_is_agent(p) for p in participants):
            return DispatchOutcome.ALREADY_CONNECTED

        redis = redis_getter()
        if not await acquire_dispatch_lock(redis, room_name, settings.dispatch_lock_seconds):
            return DispatchOutcome.STARTING
        created = False
        try:
            stale: list[str] = []
            for dispatch in await lkapi.agent_dispatch.list_dispatch(room_name):
                if dispatch.agent_name != settings.interview_agent_name:
                    continue
                if dispatch.state.deleted_at:
                    continue
                if _is_live(dispatch):
                    log.info("agent.dispatch.live", room=room_name, dispatch_id=dispatch.id)
                    return DispatchOutcome.STARTING
                stale.append(dispatch.id)

            # A dispatch with no live job is dead weight: it was never taken, was
            # refused, or its job failed. Clear it so there is one live dispatch
            # per room, then ask again.
            for dispatch_id in stale:
                try:
                    await lkapi.agent_dispatch.delete_dispatch(dispatch_id, room_name)
                except api.TwirpError as exc:
                    log.warning(
                        "agent.dispatch.delete_failed",
                        room=room_name,
                        dispatch_id=dispatch_id,
                        code=exc.code,
                    )

            if before_create is not None:
                await before_create()
            new = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=settings.interview_agent_name, room=room_name
                )
            )
            created = True
            log.info(
                "agent.dispatch.created",
                room=room_name,
                dispatch_id=new.id,
                replaced=len(stale),
            )
            return DispatchOutcome.DISPATCHED
        finally:
            # Keep the lock after a successful create (it debounces the next
            # call); drop it on any other exit so a retry is not blocked.
            if not created:
                await release_dispatch_lock(redis, room_name)
