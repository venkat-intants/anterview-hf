"""Verify the PRODUCTION dispatch path: launcher.dispatch_interview_agent ->
worker picks it up -> Simli publishes video. Writes verdict to _vd.txt.

Worker must be running:  poetry run python -m app.worker.interview_worker start
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livekit import api, rtc

from app.agent.launcher import dispatch_interview_agent
from app.config import settings

OUT = Path(__file__).resolve().parent / "_vd.txt"


def w(m: str) -> None:
    with OUT.open("a", encoding="utf-8") as f:
        f.write(m + "\n")
        f.flush()


async def main() -> None:
    OUT.write_text("", encoding="utf-8")
    room = f"vd-{uuid.uuid4().hex[:8]}"

    # Candidate joins + publishes mic (so VAD has input).
    tok = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity("candidate-vd").with_ttl(timedelta(minutes=10))
        .with_grants(api.VideoGrants(room_join=True, room=room,
                                     can_publish=True, can_subscribe=True))
        .to_jwt()
    )
    r = rtc.Room()
    await r.connect(settings.livekit_url, tok)
    src = rtc.AudioSource(16000, 1)
    mic = rtc.LocalAudioTrack.create_audio_track("mic", src)
    await r.local_participant.publish_track(
        mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    w(f"candidate in {room}, mic published")

    # PRODUCTION dispatch — the exact call the token endpoint makes.
    ok = await dispatch_interview_agent(
        room_name=room, session_id=str(uuid.uuid4()), job_id=str(uuid.uuid4()),
        job_title="Senior Python Developer", language="en",
    )
    w(f"dispatch_interview_agent returned {ok}")

    lkapi = api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
    try:
        for i in range(12):
            await asyncio.sleep(5)
            res = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room))
            parts = {p.identity: [t.type for t in p.tracks] for p in res.participants}
            w(f"[t+{(i+1)*5}s] {parts}")
            av = next((p for p in res.participants if p.identity == "simli-avatar-agent"), None)
            if av and 1 in [t.type for t in av.tracks]:
                w("PASS avatar_publishes_VIDEO via production dispatch")
                break
        else:
            w("FAIL avatar_no_video")
    finally:
        await lkapi.aclose()
        await r.disconnect()
    w("DONE")


if __name__ == "__main__":
    asyncio.run(main())
