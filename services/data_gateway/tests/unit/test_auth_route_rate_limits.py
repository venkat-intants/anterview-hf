"""Every auth route that accepts a GUESSABLE credential must be throttled.

``/change-password`` took the account's current password in the body and had no
limiter, while ``/login``, ``/forgot-password`` and ``/reset-password`` all had
one (review 2026-10-06). An attacker holding a stolen access token — XSS, a
borrowed laptop, a shared machine — could brute-force the current password from
inside the session at request speed, and getting it right is a full takeover: the
response rotates the session and the real owner is logged out.

This is written as a coverage guard rather than a test of the one route, because
the defect was a route being FORGOTTEN. An exemption has to be named here with a
reason, so adding an unthrottled credential route becomes a deliberate act.
"""

from __future__ import annotations

import pytest

from app.routers.auth import router

#: Routes that take no guessable secret, keyed by the FULL path — ``router.prefix``
#: is ``/auth`` and ``route.path`` carries it.
#:
#: The two that do take a credential accept an opaque random token:
#: ``app.auth_tokens`` mints 32 bytes (256 bits) and
#: ``shared.auth.jwt.issue_refresh_token`` 48 (384), so there is nothing to
#: brute-force at any request rate. They are listed rather than inferred, because
#: "this token is long enough" is exactly the judgement that should be visible.
_EXEMPT: dict[str, str] = {
    "/auth/refresh": "opaque 384-bit refresh token; rotation is already single-use",
    "/auth/verify-email": "opaque 256-bit single-use token from the signup email",
    "/auth/logout": "destroys only the caller's own session",
    "/auth/logout-all": "destroys only the caller's own sessions",
    "/auth/me": "read-only; authorisation is the control",
    "/auth/me/profile": "no credential in the body",
}


def _limited_paths() -> set[str]:
    return {
        route.path  # type: ignore[attr-defined]
        for route in router.routes
        if any(
            "rate_limit" in getattr(d.dependency, "__qualname__", "")
            for d in getattr(route, "dependencies", [])
        )
    }


def _all_paths() -> set[str]:
    return {route.path for route in router.routes}  # type: ignore[attr-defined]


def test_change_password_is_throttled() -> None:
    """The route this change fixed, named explicitly so the reason survives even
    if the sweep below is ever relaxed."""
    assert "/auth/change-password" in _limited_paths()


@pytest.mark.parametrize(
    "path",
    [
        "/auth/login",
        "/auth/register",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/resend-verification",
    ],
)
def test_the_routes_that_were_already_throttled_still_are(path: str) -> None:
    """A regression guard on the neighbours: the fix added a limiter, and the
    cheapest way to break this file later is to move one of these."""
    assert path in _limited_paths()


def test_every_auth_route_is_throttled_or_has_a_written_reason_not_to_be() -> None:
    unexplained = _all_paths() - _limited_paths() - set(_EXEMPT)

    assert not unexplained, (
        "these auth routes are neither rate-limited nor exempted with a reason: "
        f"{sorted(unexplained)}. Add the limiter, or add the route to _EXEMPT "
        "with the reason it needs none."
    )


def test_the_exemption_list_cannot_rot() -> None:
    """A reason for a route that no longer exists hides the next one. Same rule
    the camera-proctoring contract test applies to its citations."""
    stale = set(_EXEMPT) - _all_paths()

    assert not stale, f"_EXEMPT names routes that are gone: {sorted(stale)}"


def test_an_exempt_route_that_gains_a_limiter_is_not_a_failure() -> None:
    """Deliberately NOT asserted the other way round. Tightening is always
    allowed; this test exists to say so, so nobody reads the sweep as a ceiling."""
    assert _EXEMPT.keys() >= (set(_EXEMPT) - _limited_paths())
