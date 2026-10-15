"""A guest (interview magic-link) token must not reach /auth account routes or /jobs.

Same hole as ``test_candidate_route_authz`` closed for ``/users/me`` and
``/notifications``, one router along: ``get_current_user`` ignores roles and the
guest token's ``sub`` is the applicant's REAL account id, so ``/auth/me``,
``PATCH /auth/me/profile``, ``/auth/logout-all``, ``/auth/change-password`` (whose
bootstrap path needs no current password) and ``/auth/resend-verification`` all
worked for whoever held a forwarded interview link, and so did every ``/jobs``
route.

``/auth/logout`` stays open on purpose, and ``/auth/refresh`` / ``login`` /
``register`` are unauthenticated by design.
"""

from __future__ import annotations

from typing import Any

# Reuse the structural walker the /users/me pin uses, so "is the gate wired" is
# answered from the mounted app's resolved dependency tree.
from tests.unit.test_candidate_route_authz import _gate_covers

_GUARDED_AUTH = {
    ("GET", "/auth/me"),
    ("PATCH", "/auth/me/profile"),
    ("POST", "/auth/logout-all"),
    ("POST", "/auth/change-password"),
    ("POST", "/auth/resend-verification"),
}


def _routes() -> list[Any]:
    from app.main import app

    return list(app.routes)


def test_auth_account_routes_refuse_guest_token() -> None:
    seen: set[tuple[str, str]] = set()
    for r in _routes():
        path = getattr(r, "path", "")
        for method in getattr(r, "methods", None) or ():
            if (method, path) in _GUARDED_AUTH:
                seen.add((method, path))
                assert "guest_candidate" in _gate_covers(r), f"{method} {path}"
                assert "service" in _gate_covers(r), f"{method} {path}"
    assert seen == _GUARDED_AUTH, f"routes not found (vacuous pass?): {_GUARDED_AUTH - seen}"


def test_logout_stays_reachable_by_a_guest() -> None:
    """A guest ending their own session must keep working."""
    found = [
        r
        for r in _routes()
        if getattr(r, "path", "") == "/auth/logout" and "POST" in (r.methods or ())
    ]
    assert found
    for r in found:
        assert "guest_candidate" not in _gate_covers(r)


def test_every_jobs_route_refuses_guest_token() -> None:
    routes = [r for r in _routes() if getattr(r, "path", "").startswith("/jobs")]
    assert routes, "no /jobs routes found - this test would pass vacuously"
    for r in routes:
        assert "guest_candidate" in _gate_covers(r), f"{r.path}"
