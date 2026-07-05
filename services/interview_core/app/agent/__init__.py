"""Real-time interview agent (the thin LiveKit 'conductor').

Two layers (docs/ARCH-realtime-interview.md §5):

  • ``orchestrator.py`` — TRANSPORT-AGNOSTIC turn loop. Ties together STT ->
    InterviewBrain -> TTS -> AvatarTransport. Knows nothing about LiveKit; it
    talks to small hook protocols. Fully unit-testable with fakes.

  • ``livekit_agent.py`` — the thin LiveKit shell (added next): joins the room,
    pumps candidate mic frames into the orchestrator, publishes interviewer
    audio back. Owns ONLY the LiveKit-specific plumbing.

This split keeps the conductor logic correct and testable independent of the
LiveKit SDK, and makes the eventual self-hosted/bid transport a shell swap.
"""

from __future__ import annotations

from app.agent.orchestrator import (
    AudioSink,
    InterviewOrchestrator,
    OrchestratorHooks,
)

__all__ = [
    "AudioSink",
    "InterviewOrchestrator",
    "OrchestratorHooks",
]
