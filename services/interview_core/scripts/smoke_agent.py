"""Live smoke test for the LiveKit interview agent (backend half).

Proves the riskiest live integration points WITHOUT a browser:
  - LiveKit token mint format is accepted
  - room.connect() to LiveKit Cloud succeeds
  - publish_track() + AudioSource work
  - the brain produces a greeting + first question
  - Sarvam TTS synthesizes and the audio is captured into the room

It joins a throwaway room, speaks the greeting + first question, waits a short
window for a candidate to (optionally) join and talk, then exits cleanly.

Run from the service root:
    poetry run python scripts/smoke_agent.py [seconds]   (default 30)

Then (optional) open scripts/smoke_test.html in a browser, paste the printed
candidate token + url, and actually talk to it.

This is a MANUAL smoke tool, not a pytest. It makes real API calls (LiveKit,
Sarvam, the LLM) and therefore costs a few paise — run intentionally.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from datetime import timedelta
from pathlib import Path

# Allow `python scripts/smoke_agent.py` (sys.path[0] = scripts/) to import `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog  # noqa: E402
from livekit import api  # noqa: E402

from app.agent.livekit_agent import InterviewAgent  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import build_default_adapter  # noqa: E402

# Human-readable console logging so the agent's structlog checkpoints show up.
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)


def _mint(identity: str, name: str, room: str, ttl_min: int = 30) -> str:
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_ttl(timedelta(minutes=ttl_min))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


async def main() -> int:
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        print("ERROR: LiveKit not configured in .env", file=sys.stderr)
        return 1

    window = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    room_name = f"smoke-{uuid.uuid4().hex[:8]}"
    agent_token = _mint(f"agent-{room_name}", "Interviewer", room_name)
    candidate_token = _mint(f"cand-{room_name}", "Candidate", room_name)

    print("=" * 70)
    print(f"LiveKit URL : {settings.livekit_url}")
    print(f"Room        : {room_name}")
    print("-" * 70)
    print("To TALK to the agent, open scripts/smoke_test.html and paste:")
    print(f"  URL   : {settings.livekit_url}")
    print(f"  TOKEN : {candidate_token}")
    print("=" * 70)

    agent = InterviewAgent(
        session_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        job_title="Junior Python Developer",
        llm_adapter=build_default_adapter(),
        language="en",
        voice="kavya",
        max_turns=3,
    )

    try:
        await asyncio.wait_for(agent.run(agent_token), timeout=window)
    except TimeoutError:
        print(f"\n[smoke] {window}s window elapsed — exiting (expected if no candidate joined).")
    print("[smoke] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
