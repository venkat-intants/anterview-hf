"""DPDP-5 — the consent gate must ask about the purpose it is gating.

``has_active_consent`` hardcoded ``interview_voice_recording`` and took no
consent-type argument, while ``data_gateway`` recorded a second type
(``video_capture``, the webcam/proctoring opt-in the React intro screen
collects separately). Nothing on the server ever read the second one, so a
candidate who declined the camera still had gaze/face events persisted — one
purpose's consent silently authorising another, which DPDP §6(1)/§7(a) purpose
limitation exists to prevent.

Two things are pinned here:

1. The gate passes through whatever type it is asked for, and defaults to the
   voice type so the session-create and WS-connect call sites are unchanged.
2. The two literals still match ``data_gateway``'s. Both files carry a "keep in
   sync" comment; a comment is not a mechanism, and a silent divergence here
   fails OPEN in the worst way — the gate would query a consent_type no row
   ever has, which looks like "consent absent" and blocks every candidate, or
   (if the drift is on data_gateway's write side) records under a type nobody
   checks and blocks nobody.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.consent_guard import _CONSENT_TYPE, VIDEO_CONSENT_TYPE, has_active_consent

# services/interview_core/tests/unit/<this file> → services/
_SERVICES_ROOT = Path(__file__).resolve().parents[3]
_DG_CONSENT_ROUTER = _SERVICES_ROOT / "data_gateway" / "app" / "routers" / "consent.py"


class _RecordingSession:
    """Minimal AsyncSession stand-in that captures the bound parameters.

    A real DB is not needed to assert which consent_type the gate asked for,
    and the assertion is about the query we send, not about what Postgres
    would answer.
    """

    def __init__(self, row: object | None) -> None:
        self.params: list[dict[str, Any]] = []
        self._row = row

    async def execute(self, statement: Any, parameters: Any = None) -> Any:
        self.params.append(parameters)
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._row
        return result


# ---------------------------------------------------------------------------
# The parameter itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_consent_type_is_the_voice_grant() -> None:
    """Existing call sites (session create, WS connect) must be byte-identical.

    The whole reason the parameter has a default is that adding it must not
    change what the voice path asks for.
    """
    db = _RecordingSession(row=1)

    assert await has_active_consent(db, str(uuid.uuid4())) is True  # type: ignore[arg-type]

    assert db.params[0]["consent_type"] == _CONSENT_TYPE == "interview_voice_recording"


@pytest.mark.asyncio
async def test_video_capture_type_reaches_the_query() -> None:
    """The proctoring caller's type must actually be what is queried."""
    db = _RecordingSession(row=1)

    await has_active_consent(  # type: ignore[arg-type]
        db, str(uuid.uuid4()), consent_type=VIDEO_CONSENT_TYPE
    )

    assert db.params[0]["consent_type"] == "video_capture"
    assert db.params[0]["purpose"] == "interview"


@pytest.mark.asyncio
async def test_absent_row_is_false_for_the_video_type() -> None:
    """No ledger row for video_capture → not consented, whatever else exists."""
    db = _RecordingSession(row=None)

    assert (
        await has_active_consent(  # type: ignore[arg-type]
            db, str(uuid.uuid4()), consent_type=VIDEO_CONSENT_TYPE
        )
        is False
    )


@pytest.mark.asyncio
async def test_malformed_user_id_fails_closed_without_querying() -> None:
    """A non-UUID ``sub`` must not reach the DB and must not be treated as consent."""
    db = _RecordingSession(row=1)

    assert await has_active_consent(db, "not-a-uuid", consent_type=VIDEO_CONSENT_TYPE) is False  # type: ignore[arg-type]
    assert db.params == []


# ---------------------------------------------------------------------------
# Cross-service literal pin
# ---------------------------------------------------------------------------


def _module_level_str_constants(path: Path) -> dict[str, str]:
    """Return every module-level ``NAME = "literal"`` assignment in a file.

    Parsed rather than imported: ``data_gateway`` and ``interview_core`` both
    ship a top-level ``app`` package, so importing the other service's module
    from this test process would either shadow ours or fail on its own
    ``Settings``. The AST needs neither.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.value.value
    return found


def test_consent_type_literals_match_data_gateway() -> None:
    """The gate reads a ledger data_gateway writes; the two vocabularies are one.

    ``consent_type`` is a free-text column, so a rename on either side produces
    no error anywhere — just a gate that never matches or a grant nobody reads.
    """
    assert _DG_CONSENT_ROUTER.is_file(), (
        f"{_DG_CONSENT_ROUTER} not found. This test pins interview_core's "
        "consent vocabulary against data_gateway's; if the router moved, "
        "re-point the path — do not delete the pin."
    )

    dg = _module_level_str_constants(_DG_CONSENT_ROUTER)

    assert dg.get("_CONSENT_TYPE") == _CONSENT_TYPE, (
        "data_gateway writes consent rows with consent_type="
        f"{dg.get('_CONSENT_TYPE')!r} but interview_core's gate queries "
        f"{_CONSENT_TYPE!r}. Every candidate would be refused a session."
    )
    assert dg.get("_VIDEO_CONSENT_TYPE") == VIDEO_CONSENT_TYPE, (
        "data_gateway writes webcam consent as "
        f"{dg.get('_VIDEO_CONSENT_TYPE')!r} but interview_core's proctoring "
        f"gate queries {VIDEO_CONSENT_TYPE!r} (DPDP-5). Proctoring events would "
        "be rejected for candidates who did consent, or — if the drift is the "
        "other way — accepted for candidates who did not."
    )
