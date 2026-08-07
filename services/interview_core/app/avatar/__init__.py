"""Avatar transport layer — the PLUGGABLE avatar slot.

NOT ``app.avatars`` (with a trailing "s"). The two names differ by one character
and both import cleanly, so the wrong one typechecks silently under this repo's
non-strict mypy. THIS package — ``app.avatar`` — is the DORMANT Tier-2 transport
interface: nothing in the live demo path constructs an ``AvatarTransport`` yet
(the shipped avatar is Tavus/Simli over LiveKit). The LIVE module is
``app/avatars.py``, the per-session catalog of faces and Sarvam voices. If you
are picking or looking up an avatar for a session, you want ``app.avatars``.

The old D-ID-specific avatar package (did.py + adapters) was deleted 2026-05-31.
This package now holds only the provider-neutral ``AvatarTransport`` interface
(``base.py``). Concrete implementations (demo vendors / bid self-hosted) are
chosen by the avatar bake-off and wired behind this interface — exactly like
``speech/base.py`` abstracts STT/TTS and ``llm/base.py`` abstracts the LLM.

See docs/ARCH-realtime-interview.md §6.
"""

from __future__ import annotations

from app.avatar.base import (
    AvatarError,
    AvatarMode,
    AvatarSpeechResult,
    AvatarTransport,
    VisemeFrame,
)

__all__ = [
    "AvatarError",
    "AvatarMode",
    "AvatarSpeechResult",
    "AvatarTransport",
    "VisemeFrame",
]
