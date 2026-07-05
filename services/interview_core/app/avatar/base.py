"""AvatarTransport protocol — provider-neutral avatar surface.

The interview agent never imports a concrete avatar vendor. It depends only on
``AvatarTransport`` so that swapping demo-vendor -> another-vendor -> bid
self-hosted is a constructor change — no agent code edits. Same pattern as
``speech/base.py`` (STT/TTS) and ``llm/base.py`` (LLM).

CRITICAL DESIGN CONSTRAINT (cto-architect review, docs/ARCH §6 + §9):
    This interface MUST serve BOTH avatar tiers without leaking the
    vendor-video assumption:

      • DEMO tier — vendor video (D-ID / HeyGen / Simli / Bey): the vendor joins
        the LiveKit room as its own participant and publishes a lip-synced VIDEO
        track directly. The agent just hands the vendor the spoken audio; there
        is no per-frame data to forward. ``render()`` returns an
        ``AvatarSpeechResult`` with ``visemes=None`` (the video is already in
        the room).

      • BID tier — client-side Ready Player Me: there is NO server video hop.
        The agent emits VISEME + timing frames; the browser renders the RPM
        avatar locally from the same Sarvam audio. ``render()`` returns an
        ``AvatarSpeechResult`` with ``visemes=[...]`` that the agent forwards to
        the browser over a LiveKit data channel. Server avatar first-frame ≈ 0ms
        — this is why the bid latency path holds p95<2s (docs/ARCH §7a).

    A "none" mode (voice-only, no avatar) is also first-class so the very first
    real-time milestone can be a working VOICE interview before any avatar
    vendor is wired or paid for.

VOICE BINDING (memory project_voice_per_avatar): each avatar binds ONE fixed
Sarvam voice for the whole session. That binding lives at the AGENT level (it
picks the TTS speaker); the avatar transport only consumes the resulting audio.
Emotion varies via Sarvam pace/temperature, never by swapping the avatar's voice.

COMPLIANCE: no real-time avatar SaaS is bid-compliant (₹12/session cap + India
residency). Vendor impls are DEMO-ONLY; the bid uses the client-viseme path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------
# "vendor_video"   — vendor publishes a video track to the LiveKit room itself.
# "client_visemes" — agent emits visemes; browser renders RPM client-side (bid).
# "none"           — voice-only, no avatar (first real-time milestone).
AvatarMode = Literal["vendor_video", "client_visemes", "none"]


# ---------------------------------------------------------------------------
# Viseme frame (bid / client-side path)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VisemeFrame:
    """One mouth-shape keyframe for client-side avatar rendering.

    Frozen so it can be shared across coroutines without defensive copies.

    ``viseme`` is a provider-neutral mouth-shape id. We use the Oculus/RPM
    viseme set names (``sil``, ``PP``, ``FF``, ``TH``, ``DD``, ``kk``, ``CH``,
    ``SS``, ``nn``, ``RR``, ``aa``, ``E``, ``I``, ``O``, ``U``) so the browser
    RPM driver maps them directly to morph targets. ``offset_ms`` is the start
    time of this shape relative to the START of the audio clip it belongs to.
    """

    viseme: str
    offset_ms: int
    # Optional blend weight 0.0–1.0 (defaults to full). Some drivers crossfade.
    weight: float = 1.0


# ---------------------------------------------------------------------------
# Result of rendering one unit of speech
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AvatarSpeechResult:
    """What the transport produced for one spoken audio unit (one sentence).

    ``visemes`` is:
      • ``None``  for ``vendor_video`` mode — the vendor already published video
        to the room; the agent forwards nothing.
      • a list   for ``client_visemes`` mode — the agent forwards these to the
        browser data channel for client-side RPM rendering.
      • ``None``  for ``none`` mode — voice-only, nothing to render.

    ``duration_ms`` is the audio clip length, so the agent can schedule the next
    sentence / detect end-of-turn for barge-in windows.
    """

    visemes: list[VisemeFrame] | None = None
    duration_ms: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


class AvatarError(RuntimeError):
    """Single exception type for ANY avatar transport failure.

    Carries an optional vendor status code and a truncated body for debugging.
    Callers catch ``AvatarError`` and degrade gracefully — an avatar failure
    must NEVER kill the interview; fall back to voice-only (``none`` mode).
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body[:500] if body else None


@runtime_checkable
class AvatarTransport(Protocol):
    """Provider-neutral async avatar surface used by the LiveKit interview agent.

    Lifecycle: ``start_session`` once -> ``render`` per spoken sentence ->
    ``interrupt`` on barge-in -> ``close`` at session end.
    """

    @property
    def mode(self) -> AvatarMode:
        """Which rendering model this transport uses. Drives how the agent
        treats ``render`` results (forward visemes vs. nothing)."""
        ...

    async def start_session(
        self,
        *,
        session_id: str,
        room_name: str,
        avatar_id: str,
        language: str,
    ) -> None:
        """Prepare the avatar for a session.

        For ``vendor_video``: dispatch the vendor worker to join the LiveKit
        room ``room_name`` as a participant, bound to ``avatar_id`` (which face).
        For ``client_visemes``: usually a no-op (the browser already loaded the
        RPM model for ``avatar_id``). For ``none``: no-op.

        Raises ``AvatarError`` on setup failure — the agent should fall back to
        voice-only rather than abort the interview.
        """
        ...

    async def render(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        language: str,
        is_first: bool = False,
    ) -> AvatarSpeechResult:
        """Render one unit of interviewer speech (typically one sentence).

        ``audio_bytes`` is the Sarvam TTS WAV for this sentence (the avatar's
        bound voice). ``is_first`` marks the first sentence of a turn so impls
        can measure/optimise first-frame latency (the bake-off metric).

        Returns an ``AvatarSpeechResult`` whose ``visemes`` field is populated
        only in ``client_visemes`` mode (see the dataclass docstring).

        Raises ``AvatarError`` on a render failure — the agent degrades to
        voice-only for the rest of the turn.
        """
        ...

    async def interrupt(self) -> None:
        """Stop any in-flight avatar output immediately (barge-in).

        Called when the candidate starts speaking over the interviewer. Must be
        idempotent and fast — the audible/visible stop is the UX that matters.
        Pairs with the agent cancelling TTS + the LLM stream.
        """
        ...

    async def close(self) -> None:
        """Tear down the avatar session (vendor worker leaves the room, etc.).

        Must be safe to call multiple times and must not raise on an
        already-closed transport.
        """
        ...
