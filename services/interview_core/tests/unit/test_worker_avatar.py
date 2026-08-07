"""Unit tests for the per-avatar changes to interview_worker.

Covers:
  - _build_avatar(provider, replica_id=...) passes the per-session replica_id
    to the tavus branch; falls back to settings.tavus_replica_id when None.
  - _build_avatar simli path ignores replica_id entirely.
  - _build_avatar "none" path returns None regardless of replica_id.
  - resolve_avatar integration: voice and replica_id come from the catalog.
  - _lookup_session returns a fully-populated SessionContext
    (mocked DB path — no real connection needed).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.avatars import AVATARS_BY_ID, DEFAULT_AVATAR_ID, resolve_avatar
from app.worker.interview_worker import (
    SessionContext,
    _build_avatar,
    _lookup_session,
)

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
# _lookup_session — SessionContext population tests (no live DB connection)
#
# Strategy: patch the two symbols _lookup_session imports locally:
#   app.database.init_engine      → no-op
#   app.database.get_session_factory  → returns a factory whose __call__ is
#       an async context manager yielding a mock AsyncSession.
#
# The mock AsyncSession.execute() returns a scalar_one_or_none() on an
# awaitable Result mock.  This is the same boundary the function crosses at
# runtime; dropping a field inside the function breaks these tests immediately.
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
    company_name: str | None = None,
    competencies: object = None,
    department: str | None = None,
    interview_type: str | None = None,
) -> MagicMock:
    row = MagicMock()
    row.title = title
    row.level = level
    row.description = description
    row.company_name = company_name
    # Role-model inputs. Set explicitly (rather than left as MagicMock
    # attributes) because a MagicMock is truthy — `job.department or ""` would
    # otherwise leak a mock object into the SessionContext.
    row.competencies = competencies
    row.department = department
    row.interview_type = interview_type
    return row


@pytest.mark.asyncio
async def test_lookup_session_populates_every_context_field() -> None:
    """_lookup_session must fill the whole SessionContext from session + job.

    Was a 7-tuple arity assertion; the role-competency engine needed three more
    job fields, so the return type became a dataclass and these are now field
    assertions — a strictly better guard, since a field that silently stops
    being populated is caught, not just a change in count.
    """
    session_id = str(uuid.uuid4())
    session_row = _make_session_row(presenter_id="gloria")
    user_row = _make_user_row(resume_text="5 years building Django APIs at Acme.")
    job_row = _make_job_row(
        title="Data Analyst", level="entry", description="Analyse data",
        company_name="Google",
        competencies={"required": ["SQL", "Power BI"]},
        department="Analytics",
        interview_type="technical",
    )

    factory = _make_db_factory(
        session_row=session_row, user_row=user_row, job_row=job_row
    )

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        ctx = await _lookup_session(session_id)

    assert ctx.presenter_id == "gloria"
    assert ctx.resume_text == "5 years building Django APIs at Acme."
    assert ctx.company_name == "Google"
    assert ctx.job_title == "Data Analyst"
    assert ctx.language == "en"
    assert ctx.experience_level == "entry"
    assert ctx.jd_text == "Analyse data"
    # Role-model inputs — without these the engine falls back to classifying
    # on the job title alone.
    assert ctx.required_skills == ["SQL", "Power BI"]
    assert ctx.department == "Analytics"
    assert ctx.interview_type == "technical"


@pytest.mark.asyncio
async def test_lookup_session_normalises_nulls_to_safe_defaults() -> None:
    """Legacy rows with NULL columns must normalise, not leak None downstream."""
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
        ctx = await _lookup_session(session_id)

    assert ctx.presenter_id is None, (
        "Legacy rows with presenter_id=None must stay None — resolve_avatar(None) "
        "returns the default avatar."
    )
    assert ctx.resume_text == "", "A NULL users.resume_text must normalise to ''."
    assert ctx.company_name == "", "A NULL jobs.company_name must normalise to ''."
    assert ctx.required_skills == [], "A NULL jobs.competencies must normalise to []."
    assert ctx.department == ""
    assert ctx.interview_type == "screening"


@pytest.mark.asyncio
async def test_lookup_session_missing_session_returns_safe_defaults() -> None:
    """When the session row is absent, _lookup_session must still return a context.

    Elements 5-7 must be (None, "", "") — safe defaults so the entrypoint
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

    assert result == SessionContext(), (
        "A missing session row must yield the default SessionContext so the "
        "interview still starts generically instead of crashing."
    )


@pytest.mark.asyncio
async def test_lookup_session_invalid_uuid_returns_safe_defaults() -> None:
    """A non-UUID room_name must return safe defaults, never raise.

    Guard for malformed room names — the early ValueError branch must still
    produce a usable context.
    """
    assert await _lookup_session("not-a-uuid") == SessionContext()


# ---------------------------------------------------------------------------
# _start_avatar_or_fallback — avatar failure must degrade to voice-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_avatar_or_fallback_none_avatar_returns_none() -> None:
    """avatar=None (provider "none" or _build_avatar failure) → voice-only."""
    from app.worker.interview_worker import _start_avatar_or_fallback

    result = await _start_avatar_or_fallback(
        None, MagicMock(), MagicMock(), provider="none", session_id="s-1"
    )

    assert result is None


@pytest.mark.asyncio
async def test_start_avatar_or_fallback_success_returns_avatar() -> None:
    """Happy path: start() succeeds → the avatar object is returned unchanged."""
    from app.worker.interview_worker import _start_avatar_or_fallback

    avatar = MagicMock()
    avatar.start = AsyncMock()
    session = MagicMock()
    room = MagicMock()

    result = await _start_avatar_or_fallback(
        avatar, session, room, provider="tavus", session_id="s-1"
    )

    assert result is avatar
    avatar.start.assert_awaited_once_with(session, room=room)
    avatar.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_start_avatar_or_fallback_start_failure_returns_none_and_acloses() -> None:
    """Provider API failure (e.g. Tavus HTTP 402 credits exhausted) must NOT
    propagate — the half-started avatar is aclosed and None (voice-only) is
    returned. This locks in the dead-room fix."""
    from app.worker.interview_worker import _start_avatar_or_fallback

    avatar = MagicMock()
    avatar.start = AsyncMock(side_effect=RuntimeError("Tavus HTTP 402: payment required"))
    avatar.aclose = AsyncMock()

    result = await _start_avatar_or_fallback(
        avatar, MagicMock(), MagicMock(), provider="tavus", session_id="s-1"
    )

    assert result is None
    avatar.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_avatar_or_fallback_aclose_failure_still_returns_none() -> None:
    """Even if aclose() itself blows up, the fallback must stay silent (voice-only)."""
    from app.worker.interview_worker import _start_avatar_or_fallback

    avatar = MagicMock()
    avatar.start = AsyncMock(side_effect=ConnectionError("provider unreachable"))
    avatar.aclose = AsyncMock(side_effect=RuntimeError("cleanup also failed"))

    result = await _start_avatar_or_fallback(
        avatar, MagicMock(), MagicMock(), provider="simli", session_id="s-1"
    )

    assert result is None


# ---------------------------------------------------------------------------
# Mid-session avatar death → voice-only fallback
#
# The provider can kill the avatar conversation MID-interview (e.g. the Tavus
# free-plan per-conversation duration cap fires at ~3 minutes). The avatar
# participant leaves the room and, without the watch, the interviewer's audio
# streams into the dead datastream forever — a silent-but-"Connected" room.
# ---------------------------------------------------------------------------

from app.worker.interview_worker import (  # noqa: E402 — grouped with its test section
    InterviewState,
    _degrade_to_voice_only_midsession,
    _install_avatar_death_watch,
)

_AVATAR_ID = "tavus-avatar-agent"


def _make_watch_fixtures() -> tuple[Any, Any, Any, InterviewState]:
    """Return (avatar, session, room, state) mocks for the death-watch tests."""
    avatar = MagicMock()
    avatar.avatar_identity = _AVATAR_ID
    session = MagicMock()
    session.generate_reply = AsyncMock()
    room = MagicMock()
    state = InterviewState()
    return avatar, session, room, state


def _installed_handler(room: Any) -> Any:
    """Extract the participant_disconnected handler registered on the room."""
    assert room.on.call_count == 1
    event_name, handler = room.on.call_args[0]
    assert event_name == "participant_disconnected"
    return handler


def _participant(identity: str) -> MagicMock:
    p = MagicMock()
    p.identity = identity
    return p


def test_death_watch_none_avatar_registers_nothing() -> None:
    """avatar=None (voice-only already) must not register a room handler."""
    _, session, room, state = _make_watch_fixtures()

    _install_avatar_death_watch(
        avatar=None, session=session, room=room, state=state, session_id="s-1"
    )

    room.on.assert_not_called()


def test_death_watch_missing_identity_registers_nothing() -> None:
    """An avatar object without avatar_identity must not register a handler."""
    _, session, room, state = _make_watch_fixtures()
    avatar = MagicMock(spec=[])  # no avatar_identity attribute at all

    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )

    room.on.assert_not_called()


@pytest.mark.asyncio
async def test_death_watch_ignores_other_participants() -> None:
    """A candidate (non-avatar) disconnect must NOT trigger the fallback."""
    avatar, session, room, state = _make_watch_fixtures()
    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )
    handler = _installed_handler(room)

    with patch(
        "app.worker.interview_worker._degrade_to_voice_only_midsession",
        new_callable=AsyncMock,
    ) as degrade:
        handler(_participant("candidate-123"))
        await asyncio.sleep(0)

    degrade.assert_not_awaited()


@pytest.mark.asyncio
async def test_death_watch_ignores_disconnect_after_close() -> None:
    """Normal teardown also removes the avatar — no fallback once close fired."""
    avatar, session, room, state = _make_watch_fixtures()
    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )
    handler = _installed_handler(room)
    state.mark_close_triggered()

    with patch(
        "app.worker.interview_worker._degrade_to_voice_only_midsession",
        new_callable=AsyncMock,
    ) as degrade:
        handler(_participant(_AVATAR_ID))
        await asyncio.sleep(0)

    degrade.assert_not_awaited()


@pytest.mark.asyncio
async def test_death_watch_avatar_disconnect_degrades_and_repeats_question() -> None:
    """Avatar identity disconnect mid-session → audio swap + spoken recovery line."""
    avatar, session, room, state = _make_watch_fixtures()
    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )
    handler = _installed_handler(room)

    with patch(
        "app.worker.interview_worker._degrade_to_voice_only_midsession",
        new_callable=AsyncMock,
        return_value=True,
    ) as degrade:
        handler(_participant(_AVATAR_ID))
        # Let the scheduled recovery task run to completion.
        for _ in range(5):
            await asyncio.sleep(0)

    degrade.assert_awaited_once()
    session.generate_reply.assert_awaited_once()
    instructions = session.generate_reply.await_args.kwargs["instructions"]
    assert "repeat" in instructions.lower(), (
        "Recovery reply must repeat the current question, not advance"
    )


@pytest.mark.asyncio
async def test_death_watch_fires_at_most_once() -> None:
    """A second disconnect event for the avatar identity must be a no-op."""
    avatar, session, room, state = _make_watch_fixtures()
    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )
    handler = _installed_handler(room)

    with patch(
        "app.worker.interview_worker._degrade_to_voice_only_midsession",
        new_callable=AsyncMock,
        return_value=True,
    ) as degrade:
        handler(_participant(_AVATAR_ID))
        handler(_participant(_AVATAR_ID))
        for _ in range(5):
            await asyncio.sleep(0)

    assert degrade.await_count == 1


@pytest.mark.asyncio
async def test_death_watch_failed_degrade_skips_reply() -> None:
    """If the audio swap failed there is no working output — do not try to speak."""
    avatar, session, room, state = _make_watch_fixtures()
    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )
    handler = _installed_handler(room)

    with patch(
        "app.worker.interview_worker._degrade_to_voice_only_midsession",
        new_callable=AsyncMock,
        return_value=False,
    ):
        handler(_participant(_AVATAR_ID))
        for _ in range(5):
            await asyncio.sleep(0)

    session.generate_reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# _degrade_to_voice_only_midsession — the audio-output swap itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degrade_swaps_session_audio_output() -> None:
    """Success path: interrupt in-flight speech, publish track, swap output.audio."""
    session = MagicMock()
    room = MagicMock()
    fake_output = MagicMock()
    fake_output.start = AsyncMock()

    with patch(
        "app.worker.interview_worker._ParticipantAudioOutput",
        return_value=fake_output,
    ) as output_cls:
        ok = await _degrade_to_voice_only_midsession(session, room, session_id="s-1")

    assert ok is True
    session.interrupt.assert_called_once_with(force=True)
    fake_output.start.assert_awaited_once()
    assert session.output.audio is fake_output, (
        "session.output.audio must point at the directly-published room output"
    )
    # The replacement must publish at RoomIO's voice-only defaults (24 kHz mono).
    assert output_cls.call_args.kwargs["sample_rate"] == 24000
    assert output_cls.call_args.kwargs["num_channels"] == 1


@pytest.mark.asyncio
async def test_degrade_start_failure_returns_false_without_swap() -> None:
    """If the fallback track can't publish, return False and leave output alone."""
    session = MagicMock()
    original_output = session.output.audio
    room = MagicMock()
    fake_output = MagicMock()
    fake_output.start = AsyncMock(side_effect=RuntimeError("publish failed"))

    with patch(
        "app.worker.interview_worker._ParticipantAudioOutput",
        return_value=fake_output,
    ):
        ok = await _degrade_to_voice_only_midsession(session, room, session_id="s-1")

    assert ok is False
    assert session.output.audio is original_output, (
        "A failed swap must not replace the session's audio output"
    )


@pytest.mark.asyncio
async def test_degrade_interrupt_failure_does_not_abort_swap() -> None:
    """session.interrupt() raising must not prevent the audio swap."""
    session = MagicMock()
    session.interrupt.side_effect = RuntimeError("nothing to interrupt")
    room = MagicMock()
    fake_output = MagicMock()
    fake_output.start = AsyncMock()

    with patch(
        "app.worker.interview_worker._ParticipantAudioOutput",
        return_value=fake_output,
    ):
        ok = await _degrade_to_voice_only_midsession(session, room, session_id="s-1")

    assert ok is True
    assert session.output.audio is fake_output


# ---------------------------------------------------------------------------
# IC-5 — the private livekit symbol the fallback depends on
# ---------------------------------------------------------------------------
# _ParticipantAudioOutput lives in livekit.agents.voice.room_io._output — a
# PRIVATE module of a pinned dependency. It used to be imported unguarded at
# module scope, so a livekit-agents upgrade that moved or renamed it would stop
# the worker STARTING: every interview fails, including the ones that would
# never need the mid-session fallback. It is now imported behind the same
# try/except the tavus plugin uses, with a capability flag.
#
# The guard creates a new risk of its own — a silently-absent symbol degrading
# an install nobody notices — so both halves are pinned: the symbol IS present
# on the pinned version, AND its absence degrades cleanly rather than raising a
# bare TypeError from calling None.
# ---------------------------------------------------------------------------

from app.worker.interview_worker import (  # noqa: E402 — grouped with its test section
    _PARTICIPANT_AUDIO_OUTPUT_AVAILABLE,
)


def test_participant_audio_output_is_present_on_the_pinned_version() -> None:
    """The guard must not be quietly hiding a broken install.

    livekit-agents is pinned in requirements.txt, so on the supported version
    this flag is True. When it flips, the mid-session voice-only fallback is
    GONE — this test failing is the notice that an upgrade needs a new home for
    the audio-output class, and is what the unguarded import used to provide by
    crashing the worker.
    """
    assert _PARTICIPANT_AUDIO_OUTPUT_AVAILABLE is True, (
        "livekit.agents.voice.room_io._output._ParticipantAudioOutput is no "
        "longer importable. The worker still starts (that is the point of the "
        "guard), but the mid-session avatar-death fallback is disabled: find "
        "the class's new home before shipping this upgrade."
    )


@pytest.mark.asyncio
async def test_degrade_returns_false_when_the_private_symbol_is_absent() -> None:
    """With the symbol gone the fallback must degrade, not raise or half-swap."""
    session = MagicMock()
    original_output = session.output.audio
    room = MagicMock()

    with (
        patch("app.worker.interview_worker._PARTICIPANT_AUDIO_OUTPUT_AVAILABLE", False),
        patch("app.worker.interview_worker._ParticipantAudioOutput", None),
    ):
        ok = await _degrade_to_voice_only_midsession(session, room, session_id="s-1")

    assert ok is False
    assert session.output.audio is original_output, (
        "an unavailable output class must leave the session's audio path alone"
    )
    # Nothing should be interrupted when the swap cannot happen: cutting the
    # interviewer off mid-sentence buys nothing if no new audio path follows.
    session.interrupt.assert_not_called()


@pytest.mark.asyncio
async def test_death_watch_recovery_survives_missing_symbol() -> None:
    """The avatar-death handler must not blow up when the fallback is unavailable.

    This is the path that actually runs in production on an upgrade: the avatar
    dies, _recover() is scheduled, and the degrade fails. It must stay quiet
    (the interview is already lost) rather than raising out of a task nobody
    awaits.
    """
    avatar, session, room, state = _make_watch_fixtures()
    _install_avatar_death_watch(
        avatar=avatar, session=session, room=room, state=state, session_id="s-1"
    )
    handler = _installed_handler(room)

    participant = MagicMock()
    participant.identity = _AVATAR_ID

    with (
        patch("app.worker.interview_worker._PARTICIPANT_AUDIO_OUTPUT_AVAILABLE", False),
        patch("app.worker.interview_worker._ParticipantAudioOutput", None),
    ):
        handler(participant)
        # Drain the recovery task the handler just scheduled. gather() rather
        # than a sleep so the test fails on a raised exception instead of
        # racing it.
        current = asyncio.current_task()
        await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not current))

    # No reassurance should be spoken when the audio path was never restored —
    # the candidate would hear nothing anyway.
    session.generate_reply.assert_not_awaited()
