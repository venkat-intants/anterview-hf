"""Per-interview avatar mode — whether one candidate meets a face or a voice.

Until this existed the answer was ``AVATAR_PROVIDER``: one value for every
interview on the instance, owned by whoever holds the deploy key. That is the
wrong grain. The avatar is the only per-minute cost in a session, so "use it
for the shortlist, not the first screen" is a budget call the hiring team
makes per role — and it is an accessibility and bandwidth call too, because a
candidate on a weak rural connection has a better interview with audio only
than with a face that stalls.

So HR sets a mode per invite, it is copied onto the session at first redeem,
and the two functions here turn that stored value into the provider the worker
actually starts.

Pure, stdlib-only and in its own module for two reasons. The first is the one
``test_worker_module_surface.py`` enforces: code that is not about running a
LiveKit job does not belong in ``interview_worker.py``, which has a line
ceiling precisely so it stops reabsorbing everything. The second is testability
— inlined in ``_start_avatar`` the only way to test this logic was to re-type
it in the test, and a test that re-types the thing it checks passes happily
against an implementation that has started doing something else.
"""

from __future__ import annotations

#: The two modes, mirroring ``AVATAR_MODES`` in data_gateway's
#: ``routers/hr_interviews.py``. Two services, two copies — the DB CHECK
#: (``ck_sessions_avatar_mode``, migration f3a5c7e9b1d4) is the real arbiter,
#: and it is what makes a forgotten third value fail at the write rather than
#: reach a worker that cannot interpret it.
AVATAR_MODES: frozenset[str] = frozenset({"avatar", "audio"})


def normalise_avatar_mode(raw: object) -> str:
    """A session row's ``avatar_mode``, or ``"avatar"`` if it is not one we know.

    The DB CHECK should make an unknown value impossible, so this guards what a
    constraint cannot: a legacy row from before the column existed, a NULL, a
    hand-edited database.

    THE FALLBACK DIRECTION IS THE POINT. An unrecognised value must land on
    ``avatar`` — the behaviour every session had before this feature — and must
    never be passed through to the avatar factory, where ``"none"`` is a
    meaningful provider and would read as a deliberate voice-only session.
    Dropping the avatar for an interview nobody asked to be audio looks exactly
    like a vendor outage, and would be debugged as one.
    """
    return raw if isinstance(raw, str) and raw in AVATAR_MODES else "avatar"


def effective_avatar_provider(avatar_mode: str, configured: str) -> str:
    """Which provider ONE session runs with, given HR's mode and the instance's.

    THE OVERRIDE IS DELIBERATELY ONE-DIRECTIONAL. ``audio`` forces voice-only
    for this candidate whatever ``AVATAR_PROVIDER`` says; ``avatar`` defers to
    the instance and can never name a vendor of its own.

    HR turning the avatar off is a budget, bandwidth or accessibility call that
    belongs to the hiring team. HR turning one ON that the deployment did not
    choose would be a procurement decision — vendors carry contracts and data
    residency obligations (docs/DATA-FLOW.md) — so an instance configured
    ``none`` stays voice-only no matter what any tenant selects.
    """
    return "none" if avatar_mode == "audio" else configured
