"""Avatar transport layer — the PLUGGABLE avatar slot.

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
