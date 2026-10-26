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
  before the candidate is actually there.
* ``ensure_interviewer`` - the idempotent "no agent joined" path behind
  ``POST /api/rooms/{id}/redispatch``: if an agent is already in the room (or a
  job for it is running) do nothing; otherwise clear any dead dispatch and
  create a fresh one.

``INTERVIEW_AGENT_NAME=""`` is the kill switch back to automatic dispatch: this
module then returns ``None`` / ``AUTOMATIC`` and the worker registers unnamed.
The API and worker share one image, so the two sides cannot disagree.

PII: dispatch metadata is empty on purpose. The worker resolves everything from
the DB by room name (== session_id); nothing about the candidate goes to LiveKit.
"""

from __future__ import annotations

from enum import Enum

import structlog
from livekit import api

from app.config import settings

log = structlog.get_logger(__name__)

# Dispatch jobs in these states mean "an interviewer process is on its way or
# there"; creating another dispatch would run TWO interviews in one room.
_LIVE_JOB_STATUSES = (api.JobStatus.JS_RUNNING,)


class DispatchOutcome(str, Enum):
    """What ``ensure_interviewer`` did."""

    ALREADY_CONNECTED = "already_connected"  # an agent participant is in the room
    STARTING = "starting"  # a dispatch has a running job; agent not joined yet
    DISPATCHED = "dispatched"  # a fresh dispatch was created
    AUTOMATIC = "automatic"  # explicit dispatch is switched off


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


def _is_agent(participant: api.ParticipantInfo) -> bool:
    return participant.kind == api.ParticipantInfo.Kind.AGENT


async def ensure_interviewer(room_name: str) -> DispatchOutcome:
    """Make sure an interviewer is, or is about to be, in *room_name*. Idempotent.

    Safe to call repeatedly: it never creates a second dispatch while an agent is
    present or a job is running. Raises ``livekit.api.TwirpError`` /
    ``aiohttp.ClientError`` on a LiveKit outage; the caller maps that to a 503.
    """
    if not explicit_dispatch_enabled():
        return DispatchOutcome.AUTOMATIC

    async with api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    ) as lkapi:
        # Does the room exist at all? Asked explicitly because LiveKit does NOT
        # answer uniformly for a missing room (verified against a local
        # livekit-server, 2026-10): ListParticipants returns an empty list, but
        # ListDispatch fails with 503 "no response from servers". A room that
        # does not exist has no participants and no dispatches, so skip both.
        rooms = await lkapi.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
        participants: list[api.ParticipantInfo] = []
        dispatches: list[api.AgentDispatch] = []
        if rooms.rooms:
            try:
                listing = await lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                participants = list(listing.participants)
            except api.TwirpError as exc:
                # The room can vanish between the two calls; that is "no agent".
                if exc.code != api.TwirpErrorCode.NOT_FOUND:
                    raise
            if any(_is_agent(p) for p in participants):
                return DispatchOutcome.ALREADY_CONNECTED
            dispatches = list(await lkapi.agent_dispatch.list_dispatch(room_name))

        stale: list[str] = []
        for dispatch in dispatches:
            if dispatch.agent_name != settings.interview_agent_name:
                continue
            if dispatch.state.deleted_at:
                continue
            if any(job.state.status in _LIVE_JOB_STATUSES for job in dispatch.state.jobs):
                log.info("agent.dispatch.job_running", room=room_name, dispatch_id=dispatch.id)
                return DispatchOutcome.STARTING
            stale.append(dispatch.id)

        # A dispatch with no running job is dead weight: it was never offered,
        # was refused, or its job failed. Clear it so there is one live dispatch
        # per room, then ask again.
        for dispatch_id in stale:
            try:
                await lkapi.agent_dispatch.delete_dispatch(dispatch_id, room_name)
            except api.TwirpError as exc:
                # Already gone is fine; anything else is surfaced by create below
                # if it matters.
                log.warning(
                    "agent.dispatch.delete_failed",
                    room=room_name,
                    dispatch_id=dispatch_id,
                    code=exc.code,
                )

        created = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=settings.interview_agent_name, room=room_name
            )
        )
        log.info(
            "agent.dispatch.created",
            room=room_name,
            dispatch_id=created.id,
            replaced=len(stale),
        )
        return DispatchOutcome.DISPATCHED
