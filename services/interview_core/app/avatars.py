"""Avatar catalog for the Intants interview platform.

Static, in-memory catalog — no DB table, no migration required.
The ``presenter_id`` DB column (nullable Text) stores the chosen catalog
``id`` string (e.g. "anna") per session. Defaults to ``DEFAULT_AVATAR_ID``
("anna") when the client omits avatar_id or sends an unknown id.

Key design constraint:
    Tavus ECHO mode is face-agnostic: ALL avatars share ONE echo persona
    (``settings.tavus_persona_id``). Only the ``replica_id`` (face) and the
    Sarvam TTS ``voice`` vary per avatar. The persona is NEVER stored per
    avatar — it always comes from settings.

Voice note:
    Voices listed here MUST be valid Sarvam bulbul:v3 speakers:
      - "rahul"  — male speaker (confirmed valid in livekit-plugins-sarvam)
      - "amit"   — male speaker (confirmed valid in livekit-plugins-sarvam)
      - "rohan"  — male speaker (confirmed valid in livekit-plugins-sarvam)
      - "kavya"  — female speaker (confirmed valid in livekit-plugins-sarvam)
      - "priya"  — female speaker (confirmed valid in livekit-plugins-sarvam)
      - "shreya" — female speaker (confirmed valid in livekit-plugins-sarvam)
    Do NOT add a new avatar voice without verifying it is accepted by the
    installed Sarvam plugin.
"""

from __future__ import annotations

from typing import Literal


class Avatar:
    """Immutable avatar descriptor.

    Attributes:
        id:            Short slug used as the client-facing identifier and
                       stored in ``sessions.presenter_id``.
        name:          Human-readable display name.
        gender:        "male" or "female" — used for frontend filtering.
        replica_id:    Tavus replica ID (face). Server-side only — never
                       exposed in the GET /api/avatars response.
        voice:         Sarvam bulbul:v3 speaker name. Server-side only.
        thumbnail_url: CDN URL of a preview clip/image for the picker UI.
    """

    __slots__ = ("id", "name", "gender", "replica_id", "voice", "thumbnail_url")

    def __init__(
        self,
        *,
        id: str,  # noqa: A002 — intentional; matches field name in API docs
        name: str,
        gender: Literal["male", "female"],
        replica_id: str,
        voice: str,
        thumbnail_url: str,
    ) -> None:
        self.id = id
        self.name = name
        self.gender: Literal["male", "female"] = gender
        self.replica_id = replica_id
        self.voice = voice
        self.thumbnail_url = thumbnail_url

    def __repr__(self) -> str:  # pragma: no cover — debugging only
        return f"Avatar(id={self.id!r}, name={self.name!r}, gender={self.gender!r})"


# ---------------------------------------------------------------------------
# The catalog — add new avatars here only after verifying replica_id + voice.
# ---------------------------------------------------------------------------

AVATARS: list[Avatar] = [
    Avatar(
        id="lucas",
        name="Lucas",
        gender="male",
        replica_id="r5f0577fc829",
        voice="rahul",
        thumbnail_url="https://cdn.replica.tavus.io/40779/d5481d67_normalized.mp4",
    ),
    Avatar(
        id="raj",
        name="Raj",
        gender="male",
        replica_id="ra066ab28864",
        voice="amit",
        thumbnail_url="https://cdn.replica.tavus.io/20280/9da9e4f2.mp4",
    ),
    Avatar(
        id="benjamin",
        name="Benjamin",
        gender="male",
        replica_id="r1a4e22fa0d9",
        voice="rohan",
        thumbnail_url="https://cdn.replica.tavus.io/20269/3448746b_normalized.mp4",
    ),
    Avatar(
        id="anna",
        name="Anna",
        gender="female",
        replica_id="rf4e9d9790f0",
        voice="kavya",
        thumbnail_url="https://cdn.replica.tavus.io/39895/8c44fce6.mp4",
    ),
    Avatar(
        id="gloria",
        name="Gloria (Greenscreen)",
        gender="female",
        replica_id="rb67667672ad",
        voice="priya",
        thumbnail_url="https://cdn.replica.tavus.io/21831/4c38aca4.mp4",
    ),
    Avatar(
        id="gloria_warm",
        name="Gloria (Warm)",
        gender="female",
        replica_id="r3f427f43c9d",
        voice="shreya",
        thumbnail_url="https://cdn.replica.tavus.io/40031/b4192a5a_normalized.mp4",
    ),
]

# Default avatar when client omits avatar_id (or sends an unknown id).
DEFAULT_AVATAR_ID: str = "anna"

# Fast O(1) lookup by id.
AVATARS_BY_ID: dict[str, Avatar] = {av.id: av for av in AVATARS}


# Module-level constant: avoids allocating a new set on every validation call.
_VALID_AVATAR_IDS: frozenset[str] = frozenset(AVATARS_BY_ID)


def valid_avatar_ids() -> frozenset[str]:
    """Return the frozenset of recognised avatar id strings."""
    return _VALID_AVATAR_IDS


def resolve_avatar(avatar_id: str | None) -> Avatar:
    """Return the Avatar for ``avatar_id``, or the default avatar.

    Never raises — None, empty string, and any unknown id all silently fall
    back to the default (``DEFAULT_AVATAR_ID``).
    This is the single resolution point used by both the session endpoint
    (at create-time) and the worker (at session-start time).
    """
    if avatar_id is None:
        return AVATARS_BY_ID[DEFAULT_AVATAR_ID]
    return AVATARS_BY_ID.get(avatar_id, AVATARS_BY_ID[DEFAULT_AVATAR_ID])
