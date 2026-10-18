"""Per-interview avatar mode: the session's choice beats the environment.

THE ONE THING WORTH TESTING HERE is the override, and specifically its
DIRECTION. Until this feature the avatar was ``AVATAR_PROVIDER``, one value for
the whole instance; now a session row can say "audio" and that must win. The
reverse must NOT be true — a session saying "avatar" on an instance configured
for voice-only must not conjure a vendor the deployment did not choose, because
which vendor to pay carries contracts and data residency (docs/DATA-FLOW.md).
A test that only checked "audio means no avatar" would pass against an
implementation that let HR pick a vendor, which is the failure that costs money
rather than a pixel.

The second thing worth testing is the fallback. ``SessionContext`` defaults to
"avatar" and ``_lookup_session`` refuses unknown values, so a pre-migration
session row, a missing row, or a DB that is simply down all land on today's
behaviour rather than silently dropping the avatar for everyone — a failure
that would look like a vendor outage and be a one-line bug.
"""

from __future__ import annotations

import pytest

from app.worker.avatar_mode import (
    AVATAR_MODES as _AVATAR_MODES,
)
from app.worker.avatar_mode import (
    effective_avatar_provider as _effective,
)
from app.worker.avatar_mode import (
    normalise_avatar_mode,
)
from app.worker.interview_worker import SessionContext, _build_avatar


class TestTheOverrideAndItsDirection:
    @pytest.mark.parametrize("configured", ["tavus", "simli", "none"])
    def test_audio_forces_voice_only_whatever_the_instance_runs(self, configured: str) -> None:
        assert _effective("audio", configured) == "none"

    @pytest.mark.parametrize("configured", ["tavus", "simli"])
    def test_avatar_defers_to_the_instance_and_never_picks_a_vendor(
        self, configured: str
    ) -> None:
        assert _effective("avatar", configured) == configured

    def test_avatar_cannot_create_an_avatar_the_deployment_turned_off(self) -> None:
        """The direction that must NOT work. An instance running voice-only has
        no avatar contract; a tenant toggle must not start billing one."""
        assert _effective("avatar", "none") == "none"


class TestFallbacksLandOnTodaysBehaviour:
    def test_default_is_avatar(self) -> None:
        """A SessionContext built from nothing — DB down, row missing, lookup
        failed — keeps the avatar. Defaulting to audio would read as a vendor
        outage across every interview at once."""
        assert SessionContext().avatar_mode == "avatar"

    def test_a_pre_migration_row_reads_as_avatar(self) -> None:
        assert _effective(SessionContext().avatar_mode, "tavus") == "tavus"

    @pytest.mark.parametrize("junk", ["", "AUDIO", "video", "tavus", "none", "null"])
    def test_an_unrecognised_mode_is_not_trusted_through(self, junk: str) -> None:
        """What ``_lookup_session`` does with a value the CHECK should have made
        impossible. Anything but a known mode falls back rather than being
        passed to the avatar factory, where "none" in particular would be read
        as a deliberate voice-only session."""
        assert normalise_avatar_mode(junk) == "avatar"

    @pytest.mark.parametrize("junk", [None, 0, True, [], {"mode": "audio"}])
    def test_a_non_string_is_not_trusted_either(self, junk: object) -> None:
        """A NULL column or a driver returning something unexpected. `in` on a
        frozenset is happy to take any hashable, and an unhashable one raises —
        neither is an answer the avatar factory can use."""
        assert normalise_avatar_mode(junk) == "avatar"

    @pytest.mark.parametrize("good", ["avatar", "audio"])
    def test_a_known_mode_passes_through_unchanged(self, good: str) -> None:
        """Guards the guard: a normaliser that returned "avatar" for everything
        would pass every test above while silently disabling the feature."""
        assert normalise_avatar_mode(good) == good

    def test_the_two_modes_are_exactly_these(self) -> None:
        """Guards the guard above: with _AVATAR_MODES widened by accident, the
        fallback test would pass while admitting the new value."""
        assert frozenset({"avatar", "audio"}) == _AVATAR_MODES


class TestTheFactoryHonoursNone:
    def test_none_builds_no_avatar_session(self) -> None:
        """The end of the chain. 'audio' is only meaningful because the factory
        returns None for "none" — and None is what makes the worker run
        voice-only."""
        assert _build_avatar("none") is None

    def test_an_unknown_provider_still_falls_back_to_simli_not_to_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Pins the PRE-EXISTING behaviour this change must not alter: an
        unrecognised AVATAR_PROVIDER is a misconfiguration that degrades to
        simli with a warning (docs/ACCEPTED-RISKS.md AR-4). If this ever
        returned None, a typo in the env would silently become "audio-only for
        everyone" and look exactly like the feature working.

        Asserted on the BRANCH TAKEN, not the return value: building a real
        ``simli.AvatarSession`` needs a LiveKit job context, so offline it
        raises from inside the constructor. That raise is itself the proof —
        the "none" branch returns before any construction and cannot raise.
        """
        with caplog.at_level("WARNING"), pytest.raises(RuntimeError) as exc:
            _build_avatar("custom")

        assert "http session" in str(exc.value), (
            "expected the Simli constructor's own out-of-job-context error; a "
            "different RuntimeError means the fallback branch changed"
        )
        assert any(
            "unknown avatar_provider" in r.getMessage() for r in caplog.records
        ), "the fallback must stay LOUD - a silent degrade to simli is AR-4's whole point"


def test_the_worker_actually_calls_it() -> None:
    """The behavioural tests above now exercise the real function, so the only
    gap left is whether ``_start_avatar`` still routes through it. A worker that
    quietly went back to reading ``settings.avatar_provider`` directly would
    pass every other test in this file while ignoring HR's choice entirely.
    """
    import inspect

    from app.worker.interview_worker import InterviewJob

    src = inspect.getsource(InterviewJob._start_avatar)
    assert "effective_avatar_provider(" in src, (
        "_start_avatar no longer routes the provider choice through "
        "effective_avatar_provider - the per-interview mode is being ignored."
    )
    # Comments stripped first: the body explains WHY it does not log
    # settings.avatar_provider, and that prose is not a use.
    code = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines() if line.strip()
    )
    uses = code.count("settings.avatar_provider")
    assert uses == 1, (
        f"_start_avatar names settings.avatar_provider {uses} times; exactly one "
        "is right - the argument handed to effective_avatar_provider. Any other "
        "use means a line below is describing or starting a provider this "
        "session is not running, which is how a voice-only interview ends up "
        "logged as 'tavus'."
    )


def test_the_session_lookup_normalises_what_it_reads() -> None:
    """``_lookup_session`` needs a database, so its one line of interest is
    pinned by source rather than exercised. Without this, a lookup that
    assigned ``sess.avatar_mode`` straight through would pass every test in
    this file — the normaliser would still be correct, and simply never called.
    (Verified: that exact mutation survived the suite before this test.)
    """
    import inspect

    from app.worker import interview_worker

    src = inspect.getsource(interview_worker._lookup_session)
    assert "normalise_avatar_mode(sess.avatar_mode)" in src, (
        "_lookup_session no longer normalises the session's avatar_mode; a "
        "legacy NULL or a hand-edited row would reach the avatar factory, "
        "where 'none' means a deliberate voice-only interview."
    )
