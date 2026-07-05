"""Unit tests for the per-avatar changes to interview_worker.

Covers:
  - _build_avatar(provider, replica_id=...) passes the per-session replica_id
    to the tavus branch; falls back to settings.tavus_replica_id when None.
  - _build_avatar simli path ignores replica_id entirely.
  - _build_avatar "none" path returns None regardless of replica_id.
  - resolve_avatar integration: voice and replica_id come from the catalog.
  - _lookup_session returns a 5-tuple with presenter_id as the 5th element
    (mocked DB path — no real connection needed).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.avatars import AVATARS_BY_ID, DEFAULT_AVATAR_ID, resolve_avatar
from app.worker.interview_worker import _build_avatar, _lookup_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simli_settings(**overrides: Any) -> Any:
    """Return a MagicMock that looks like settings for simli path."""
    s = MagicMock()
    s.simli_api_key = "test-simli-key"
    s.simli_face_id = "test-face-id"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _tavus_settings(**overrides: Any) -> Any:
    """Return a MagicMock that looks like settings for tavus path."""
    s = MagicMock()
    s.tavus_api_key = "test-tavus-key"
    s.tavus_replica_id = "settings-default-replica"
    s.tavus_persona_id = "settings-persona"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# _build_avatar — simli path
# ---------------------------------------------------------------------------


def test_build_avatar_simli_returns_session_object() -> None:
    """simli path must return a non-None object regardless of replica_id."""
    fake_simli_session = MagicMock(name="SimliAvatarSession")

    with (
        patch("app.worker.interview_worker.settings", _simli_settings()),
        patch("app.worker.interview_worker.simli") as mock_simli,
    ):
        mock_simli.AvatarSession.return_value = fake_simli_session
        mock_simli.SimliConfig.return_value = MagicMock()

        result = _build_avatar("simli", replica_id="r5f0577fc829")

    assert result is fake_simli_session


def test_build_avatar_simli_ignores_replica_id() -> None:
    """Simli does not consume replica_id; it always uses settings.simli_face_id."""
    with (
        patch("app.worker.interview_worker.settings", _simli_settings()),
        patch("app.worker.interview_worker.simli") as mock_simli,
    ):
        mock_simli.AvatarSession.return_value = MagicMock()
        mock_simli.SimliConfig.return_value = MagicMock()

        # Different replica_id values must all succeed — simli ignores them.
        for rid in (None, "", "r5f0577fc829", "rf4e9d9790f0"):
            _build_avatar("simli", replica_id=rid)

        # SimliConfig must always receive face_id from settings, never from replica_id
        for call in mock_simli.SimliConfig.call_args_list:
            assert call.kwargs.get("face_id") == "test-face-id"


# ---------------------------------------------------------------------------
# _build_avatar — none path
# ---------------------------------------------------------------------------


def test_build_avatar_none_returns_none() -> None:
    """provider='none' must return None (voice-only mode)."""
    result = _build_avatar("none", replica_id="any-replica")
    assert result is None


def test_build_avatar_none_with_no_replica_id() -> None:
    result = _build_avatar("none")
    assert result is None


# ---------------------------------------------------------------------------
# _build_avatar — tavus path
# ---------------------------------------------------------------------------


def test_build_avatar_tavus_uses_per_session_replica_id() -> None:
    """When replica_id is provided, the tavus branch must use it, not settings value."""
    per_session_replica = "rf4e9d9790f0"
    fake_tavus_session = MagicMock(name="TavusAvatarSession")
    fake_tavus_plugin = MagicMock()
    fake_tavus_plugin.AvatarSession.return_value = fake_tavus_session
    s = _tavus_settings()

    with (
        patch("app.worker.interview_worker.settings", s),
        patch("app.worker.interview_worker._TAVUS_AVAILABLE", True),
        patch("app.worker.interview_worker._tavus_plugin", fake_tavus_plugin),
    ):
        result = _build_avatar("tavus", replica_id=per_session_replica)

    assert result is fake_tavus_session
    call_kwargs = fake_tavus_plugin.AvatarSession.call_args.kwargs
    assert call_kwargs["replica_id"] == per_session_replica, (
        f"Expected per-session replica_id {per_session_replica!r}, "
        f"got {call_kwargs['replica_id']!r}"
    )
    # Persona must still come from settings (shared echo persona)
    assert call_kwargs["persona_id"] == s.tavus_persona_id


def test_build_avatar_tavus_fallback_to_settings_replica_when_none() -> None:
    """When replica_id=None, tavus branch must use settings.tavus_replica_id."""
    fake_tavus_plugin = MagicMock()
    fake_tavus_plugin.AvatarSession.return_value = MagicMock()
    s = _tavus_settings()

    with (
        patch("app.worker.interview_worker.settings", s),
        patch("app.worker.interview_worker._TAVUS_AVAILABLE", True),
        patch("app.worker.interview_worker._tavus_plugin", fake_tavus_plugin),
    ):
        _build_avatar("tavus", replica_id=None)

    call_kwargs = fake_tavus_plugin.AvatarSession.call_args.kwargs
    assert call_kwargs["replica_id"] == "settings-default-replica"


def test_build_avatar_tavus_persona_never_per_avatar() -> None:
    """persona_id must ALWAYS come from settings — never from a per-avatar value."""
    per_session_replica = "r5f0577fc829"
    fake_tavus_plugin = MagicMock()
    fake_tavus_plugin.AvatarSession.return_value = MagicMock()
    s = _tavus_settings()

    with (
        patch("app.worker.interview_worker.settings", s),
        patch("app.worker.interview_worker._TAVUS_AVAILABLE", True),
        patch("app.worker.interview_worker._tavus_plugin", fake_tavus_plugin),
    ):
        _build_avatar("tavus", replica_id=per_session_replica)

    call_kwargs = fake_tavus_plugin.AvatarSession.call_args.kwargs
    assert call_kwargs["persona_id"] == "settings-persona", (
        "Shared echo persona must always come from settings, never per-avatar"
    )


def test_build_avatar_tavus_missing_plugin_raises() -> None:
    """provider='tavus' but plugin unavailable → RuntimeError (loud failure)."""
    with (
        patch("app.worker.interview_worker.settings", _tavus_settings()),
        patch("app.worker.interview_worker._TAVUS_AVAILABLE", False),
        pytest.raises(RuntimeError, match="livekit-plugins-tavus"),
    ):
        _build_avatar("tavus", replica_id="r5f0577fc829")


def test_build_avatar_tavus_missing_persona_id_raises() -> None:
    """provider='tavus' but persona_id empty → RuntimeError."""
    s = _tavus_settings(tavus_persona_id="")

    with (
        patch("app.worker.interview_worker.settings", s),
        patch("app.worker.interview_worker._TAVUS_AVAILABLE", True),
        pytest.raises(RuntimeError, match="TAVUS_PERSONA_ID"),
    ):
        _build_avatar("tavus", replica_id="r5f0577fc829")


def test_build_avatar_tavus_missing_all_replica_ids_raises() -> None:
    """provider='tavus', no per-session replica_id and no settings fallback → RuntimeError."""
    s = _tavus_settings(tavus_replica_id="")

    with (
        patch("app.worker.interview_worker.settings", s),
        patch("app.worker.interview_worker._TAVUS_AVAILABLE", True),
        pytest.raises(RuntimeError, match="replica_id"),
    ):
        _build_avatar("tavus", replica_id=None)


# ---------------------------------------------------------------------------
# _build_avatar — unknown provider falls back to simli
# ---------------------------------------------------------------------------


def test_build_avatar_unknown_provider_falls_back_to_simli() -> None:
    """An unrecognised provider string must fall back to simli, not raise."""
    with (
        patch("app.worker.interview_worker.settings", _simli_settings()),
        patch("app.worker.interview_worker.simli") as mock_simli,
    ):
        mock_simli.AvatarSession.return_value = MagicMock()
        mock_simli.SimliConfig.return_value = MagicMock()

        result = _build_avatar("custom_three_js", replica_id=None)

    assert result is not None  # simli session object


# ---------------------------------------------------------------------------
# resolve_avatar integration — voice and replica_id thread-through verification
# ---------------------------------------------------------------------------


def test_resolve_anna_gives_kavya_voice() -> None:
    """resolve_avatar('anna') must return voice='kavya' for Sarvam TTS."""
    av = resolve_avatar("anna")
    assert av.voice == "kavya"
    assert av.replica_id == AVATARS_BY_ID["anna"].replica_id


def test_resolve_lucas_gives_rahul_voice() -> None:
    av = resolve_avatar("lucas")
    assert av.voice == "rahul"


def test_resolve_gloria_gives_priya_voice() -> None:
    av = resolve_avatar("gloria")
    assert av.voice == "priya"


def test_resolve_none_gives_default_voice() -> None:
    """resolve_avatar(None) must return the default avatar with its voice."""
    av = resolve_avatar(None)
    default = AVATARS_BY_ID[DEFAULT_AVATAR_ID]
    assert av.voice == default.voice
    assert av.replica_id == default.replica_id


# ---------------------------------------------------------------------------
# _lookup_session — 5-tuple arity regression tests (no live DB connection)
#
# Strategy: patch the two symbols _lookup_session imports locally:
#   app.database.init_engine      → no-op
#   app.database.get_session_factory  → returns a factory whose __call__ is
#       an async context manager yielding a mock AsyncSession.
#
# The mock AsyncSession.execute() returns a scalar_one_or_none() on an
# awaitable Result mock.  This is the same boundary the function crosses at
# runtime; changing the unpack to 4-tuple inside the function would break these
# tests immediately.
# ---------------------------------------------------------------------------


def _make_db_factory(
    *,
    session_row: Any,
    job_row: Any,
    user_row: Any = None,
) -> Any:
    """Return a callable that acts as an async_sessionmaker yielding a mock DB session.

    ``session_row``, ``user_row`` and ``job_row`` are what ``scalar_one_or_none()``
    returns for the Session, User, and Job queries respectively. The User query
    only fires when the session row has a non-None ``user_id``; the side_effect
    list tolerates the unused trailing entries when an early return happens.
    """

    @asynccontextmanager
    async def _fake_factory_cm() -> Any:
        mock_db = AsyncMock()

        # Each call to db.execute() returns a result object where
        # .scalar_one_or_none() is a regular (not async) method.
        session_result = MagicMock()
        session_result.scalar_one_or_none.return_value = session_row

        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user_row

        job_result = MagicMock()
        job_result.scalar_one_or_none.return_value = job_row

        # Query order in _lookup_session: session → user (if user_id) → job.
        mock_db.execute = AsyncMock(
            side_effect=[session_result, user_result, job_result]
        )
        yield mock_db

    fake_factory = MagicMock()
    fake_factory.return_value = _fake_factory_cm()
    # Make it callable multiple times (each test gets a fresh CM).
    fake_factory.side_effect = lambda: _fake_factory_cm()
    return fake_factory


def _make_session_row(
    *,
    job_id: uuid.UUID | None = None,
    language: str = "en",
    presenter_id: str | None = "anna",
    user_id: uuid.UUID | None = None,
) -> MagicMock:
    row = MagicMock()
    row.language = language
    row.presenter_id = presenter_id
    row.job_id = job_id or uuid.uuid4()
    # Default to a real UUID so the User (resume) query fires in _lookup_session.
    row.user_id = user_id or uuid.uuid4()
    return row


def _make_user_row(*, resume_text: str | None = None) -> MagicMock:
    row = MagicMock()
    row.resume_text = resume_text
    return row


def _make_job_row(
    *,
    title: str = "Backend Engineer",
    level: str = "mid",
    description: str = "Build APIs",
) -> MagicMock:
    row = MagicMock()
    row.title = title
    row.level = level
    row.description = description
    return row


@pytest.mark.asyncio
async def test_lookup_session_returns_6_tuple_with_presenter_id() -> None:
    """_lookup_session must return a 6-tuple; 5th is presenter_id, 6th is resume_text.

    This test would FAIL if someone removed presenter_id or resume_text from the
    return tuple, because the unpack in entrypoint()
        job_title, language, experience_level, jd_text, presenter_id, resume_text = result
    would raise ValueError: not enough values to unpack.
    """
    session_id = str(uuid.uuid4())
    session_row = _make_session_row(presenter_id="gloria")
    user_row = _make_user_row(resume_text="5 years building Django APIs at Acme.")
    job_row = _make_job_row(title="Data Analyst", level="entry", description="Analyse data")

    factory = _make_db_factory(
        session_row=session_row, user_row=user_row, job_row=job_row
    )

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await _lookup_session(session_id)

    # Must be a 6-tuple — this assertion catches a regression to a shorter tuple.
    assert len(result) == 6, (
        f"_lookup_session must return a 6-tuple; got {len(result)}-tuple: {result!r}"
    )
    job_title, language, experience_level, jd_text, presenter_id, resume_text = result

    assert presenter_id == "gloria", (
        f"5th element must be session.presenter_id; got {presenter_id!r}"
    )
    assert resume_text == "5 years building Django APIs at Acme.", (
        f"6th element must be the candidate's resume_text; got {resume_text!r}"
    )
    assert job_title == "Data Analyst"
    assert language == "en"
    assert experience_level == "entry"
    assert jd_text == "Analyse data"


@pytest.mark.asyncio
async def test_lookup_session_6_tuple_none_presenter_id() -> None:
    """When session.presenter_id is None (legacy row), 5th element must be None — not omitted.

    Ensures the unpack in entrypoint() ``..., presenter_id, resume_text =
    await _lookup_session(...)`` always receives exactly 6 values even for old rows.
    """
    session_id = str(uuid.uuid4())
    session_row = _make_session_row(presenter_id=None)
    user_row = _make_user_row(resume_text=None)
    job_row = _make_job_row()

    factory = _make_db_factory(
        session_row=session_row, user_row=user_row, job_row=job_row
    )

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await _lookup_session(session_id)

    assert len(result) == 6, f"Expected 6-tuple; got {len(result)}-tuple"
    *_, presenter_id, resume_text = result
    assert presenter_id is None, (
        "Legacy rows with presenter_id=None must return None as the 5th element, "
        "not be omitted — callers unpack all 6 positions."
    )
    assert resume_text == "", (
        "A NULL users.resume_text must be normalised to '' as the 6th element."
    )


@pytest.mark.asyncio
async def test_lookup_session_missing_session_returns_6_tuple_safe_defaults() -> None:
    """When the session row is absent, _lookup_session must still return a 6-tuple.

    The 5th element must be None and the 6th "" (safe defaults) so the entrypoint
    unpack never raises ValueError regardless of missing data.
    """
    session_id = str(uuid.uuid4())
    # session_row=None simulates scalar_one_or_none() returning no row.
    factory = _make_db_factory(session_row=None, user_row=None, job_row=None)

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        result = await _lookup_session(session_id)

    assert len(result) == 6, f"Even on missing session, must return 6-tuple; got {len(result)}"
    job_title, language, experience_level, jd_text, presenter_id, resume_text = result
    assert job_title == "the role"
    assert language == "en"
    assert experience_level == "entry"
    assert jd_text == ""
    assert presenter_id is None
    assert resume_text == ""


@pytest.mark.asyncio
async def test_lookup_session_invalid_uuid_returns_6_tuple_safe_defaults() -> None:
    """A non-UUID room_name must return a 6-tuple with safe defaults (no exception).

    This is the guard for malformed room names — the early ValueError branch
    must still produce exactly 6 values so the caller's unpack is always safe.
    """
    result = await _lookup_session("not-a-uuid")

    assert len(result) == 6, (
        f"Non-UUID room_name must still yield 6-tuple; got {len(result)}-tuple"
    )
    job_title, language, experience_level, jd_text, presenter_id, resume_text = result
    assert job_title == "the role"
    assert language == "en"
    assert experience_level == "entry"
    assert jd_text == ""
    assert presenter_id is None
    assert resume_text == ""
