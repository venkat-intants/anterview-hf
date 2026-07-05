"""Integrity (proctoring) scoring — Phase B.

Pure functions that turn a list of flagged integrity events into a single
integrity score (0-100, higher = cleaner) plus a human-readable summary. Kept
free of DB / FastAPI imports so it is trivially unit-testable.

DESIGN NOTE — this is an *advisory* score for HUMAN REVIEW, never an automated
pass/fail. Webcam gaze/face detection is noisy (lighting, glasses, disability,
skin tone), so the weights below are deliberately gentle and the score should be
read alongside the event timeline, not in isolation. Tune the penalty tables as
real data arrives; do not wire this to auto-reject candidates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

# Ranged events carry an ended_at and are penalised per second of duration.
_PER_SECOND_PENALTY: dict[str, float] = {
    "gaze_away": 0.5,       # looking off-screen
    "face_absent": 1.5,     # candidate left the frame
    "multiple_faces": 2.0,  # more than one person visible
    "second_voice": 2.0,    # another voice detected
}

# Instantaneous events are penalised per occurrence.
_PER_EVENT_PENALTY: dict[str, float] = {
    "tab_blur": 3.0,         # switched away from the interview tab/window
    "fullscreen_exit": 2.0,
    "copy": 2.0,
    "paste": 4.0,
    "devtools_open": 5.0,
}

# All event types this pipeline understands (used for validation upstream).
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    set(_PER_SECOND_PENALTY) | set(_PER_EVENT_PENALTY)
)

_MAX_SCORE = 100

# Defense-in-depth: a single ranged event cannot plausibly exceed the interview
# length (wall-clock cap is 720s). Clamp anything longer so a buggy/forged
# client timestamp can never blow up the score (a unit bug once produced
# "169-year" gaze events). 900s leaves headroom above the 12-min cap.
_MAX_EVENT_SECONDS = 900.0


def _duration_seconds(started_at: Any, ended_at: Any) -> float:
    """Seconds between started_at and ended_at; 0 if missing/invalid, clamped to a sane max."""
    if not isinstance(started_at, datetime) or not isinstance(ended_at, datetime):
        return 0.0
    delta = (ended_at - started_at).total_seconds()
    if delta <= 0:
        return 0.0
    return min(delta, _MAX_EVENT_SECONDS)


def compute_integrity(
    events: Iterable[Mapping[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Return (integrity_score, summary) for a session's integrity events.

    Each event is a mapping with at least ``event_type`` and ``started_at``;
    ranged events also have ``ended_at``. Unknown event types are counted in the
    summary but contribute no penalty (forward-compatible with new client
    signals).

    score   : 0-100, higher = cleaner. 100 = no flags.
    summary : {
        by_type: {type: count},
        flagged_seconds: {type: seconds},   # ranged types only
        total_events: int,
        total_flagged_seconds: float,
    }
    """
    by_type: dict[str, int] = {}
    flagged_seconds: dict[str, float] = {}
    penalty = 0.0
    total_events = 0

    for ev in events:
        etype = str(ev.get("event_type", ""))
        if not etype:
            continue
        total_events += 1
        by_type[etype] = by_type.get(etype, 0) + 1

        if etype in _PER_SECOND_PENALTY:
            secs = _duration_seconds(ev.get("started_at"), ev.get("ended_at"))
            flagged_seconds[etype] = round(flagged_seconds.get(etype, 0.0) + secs, 1)
            penalty += secs * _PER_SECOND_PENALTY[etype]
        elif etype in _PER_EVENT_PENALTY:
            penalty += _PER_EVENT_PENALTY[etype]
        # unknown types: counted, no penalty

    score = max(0, min(_MAX_SCORE, round(_MAX_SCORE - penalty)))
    summary: dict[str, Any] = {
        "by_type": by_type,
        "flagged_seconds": flagged_seconds,
        "total_events": total_events,
        "total_flagged_seconds": round(sum(flagged_seconds.values()), 1),
    }
    return score, summary
