"""Verifier for proof_worker: join a room as candidate, dispatch the worker's
agent into it, wait, then query the LiveKit SERVER API for the avatar's tracks.

PASS = simli-avatar-agent publishes a VIDEO track. Writes verdict to _proof.txt.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livekit import api, rtc

from app.config import settings

OUT = Path(__file__).resolve().parent / "_proof.txt"


def w(m: str) -> None:
    with OUT.open("a", encoding="utf-8") as f:
        f.write(m + "\n")
        f.flush()


async def main() -> None:
    OUT.write_text("", encoding="utf-8")
    room = sys.argv[1] if len(sys.argv) > 1 else "proof-room"

    # Candidate joins + publishes a silent mic (so VAD has an input track).
    tok = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity("candidate-proof").with_ttl(timedelta(minutes=10))
        .with_grants(api.VideoGrants(room_join=True, room=room,
                                     can_publish=True, can_subscribe=True))
        .to_jwt()
    )
    r = rtc.Room()
    tracks_seen: list[str] = []

    @r.on("track_subscribed")
    def _ts(track, pub, p):  # type: ignore[no-untyped-def]
        tracks_seen.append(f"{p.identity}:{track.kind}")
        w(f"candidate subscribed: {p.identity}:{track.kind}")

    await r.connect(settings.livekit_url, tok)
    w(f"candidate connected to {room}")
    src = rtc.AudioSource(16000, 1)
    mic = rtc.LocalAudioTrack.create_audio_track("mic", src)
    await r.local_participant.publish_track(
        mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    w("candidate published mic")

    # Dispatch the worker's agent into THIS room (the worker must be running in
    # dev mode with an agent_name, or auto-dispatch on room create).
    lkapi = api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
    try:
        # Give the worker time to pick up the room + Simli to join + publish.
        for i in range(12):
            await asyncio.sleep(5)
            res = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room))
            parts = {p.identity: [(t.type, t.name) for t in p.tracks] for p in res.participants}
            w(f"[t+{(i+1)*5}s] participants: {parts}")
            avatar = next((p for p in res.participants if p.identity == "simli-avatar-agent"), None)
            if avatar:
                kinds = [t.type for t in avatar.tracks]
                # TrackType: 0=AUDIO 1=VIDEO
                if 1 in kinds:
                    w("PASS avatar_publishes_VIDEO")
                    break
        else:
            w("FAIL avatar_never_published_video")
    finally:
        await lkapi.aclose()
        await r.disconnect()
    w("DONE")


if __name__ == "__main__":
    asyncio.run(main())
