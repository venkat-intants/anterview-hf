"""ISOLATION PROOF — official LiveKit worker pattern with Simli + Sarvam.

Goal: prove the Simli avatar PUBLISHES VIDEO when driven the official way
(AgentSession inside a real worker JobContext), with our Sarvam voice + Groq LLM
+ our SIMLI_API_KEY/SIMLI_FACE_ID. If this publishes video, the approach is
sound and we integrate it. If not, we caught it cheaply.

This is the EXACT official example pattern (examples/avatar_agents/simli), only
swapping OpenAI-realtime -> Groq LLM + Sarvam STT/TTS (our approved stack).

Run as a worker in dev mode against a FIXED room so the verifier can join it:
    poetry run python scripts/proof_worker.py dev --room proof-room
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai, sarvam, silero, simli

from app.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("proof")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    log.info("worker connected to room %s", ctx.room.name)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=sarvam.STT(
            language="en-IN",
            model=settings.sarvam_stt_model,
            api_key=settings.sarvam_api_key,
        ),
        llm=openai.LLM(
            model="llama-3.3-70b-versatile",
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        ),
        tts=sarvam.TTS(
            model="bulbul:v3",
            target_language_code="en-IN",
            speaker="kavya",
            api_key=settings.sarvam_api_key,
        ),
    )

    # CRITICAL ORDER (from official example): avatar.start BEFORE session.start.
    simli_avatar = simli.AvatarSession(
        simli_config=simli.SimliConfig(
            api_key=settings.simli_api_key,
            face_id=settings.simli_face_id,
        ),
    )
    log.info("starting simli avatar...")
    await simli_avatar.start(session, room=ctx.room)
    log.info("simli avatar started")

    await session.start(
        agent=Agent(
            instructions=(
                "You are a friendly interviewer. Greet the candidate warmly "
                "and ask them to introduce themselves. Keep it short."
            )
        ),
        room=ctx.room,
    )
    log.info("agent session started — should be speaking now")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
    )
