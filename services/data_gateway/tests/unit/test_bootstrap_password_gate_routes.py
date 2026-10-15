"""The copilot and the cross-user profile read honour the bootstrap-password gate (L3).

``require_password_changed`` exists because the forced first-password reset lived
only in the React router: an account still on the password an admin set could
drive the API directly. It was wired into the HR/admin routers but not into
``/agent/*`` (the copilot reads company records) or ``GET /users/{id}/profile``
(another person's email, phone and bio). The routes are walked on the mounted
app, so a refactor that swaps the dependency back fails here.
"""

from __future__ import annotations

from typing import Any

_GATED = {
    ("POST", "/agent/chat"),
    ("POST", "/agent/panel/{applicant_id}"),
    ("POST", "/agent/watchers/run"),
    ("GET", "/users/{user_id}/profile"),
}


def _tree_calls(route: Any) -> set[Any]:
    out: set[Any] = set()
    stack = [getattr(route, "dependant", None)]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        out.add(getattr(node, "call", None))
        stack.extend(getattr(node, "dependencies", []))
    return out


def test_agent_and_profile_routes_require_a_changed_password() -> None:
    from app.dependencies import require_password_changed
    from app.main import app

    seen: set[tuple[str, str]] = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        for method in getattr(r, "methods", None) or ():
            if (method, path) in _GATED:
                seen.add((method, path))
                assert require_password_changed in _tree_calls(r), f"{method} {path}"
    assert seen == _GATED, f"routes not found (vacuous pass?): {_GATED - seen}"


def test_agent_status_stays_open_to_any_signed_in_user() -> None:
    """Candidates poll it to learn the assistant is not for them; gating it would 403 the poll."""
    from app.dependencies import require_password_changed
    from app.main import app

    routes = [
        r
        for r in app.routes
        if getattr(r, "path", "") == "/agent/status" and "GET" in (r.methods or ())
    ]
    assert routes
    for r in routes:
        assert require_password_changed not in _tree_calls(r)
