"""Every authenticated route either reaches the bootstrap-password gate or is listed.

WHY THIS FILE EXISTS
--------------------
``admin_hr`` provisions every ``hr_manager`` and ``super_admin`` with
``must_change_password = true``, and ``20260625_0002_c3e5f7a9b1d3`` provisions
the platform owner the same way — printing its password to stdout, which is
``docs/ACCEPTED-RISKS.md`` AR-11. ``dependencies.require_password_changed`` is
the server-side control over that flag, and it has exactly one consumer:
``require_role_password_ok``. A route that does not pass through that factory
has no gate, by construction.

Three rounds of review found three different routes in that state, each time by
reading code and each time missing the next one:

* round 13 and 14 — AR-11 claimed the printed password granted no API access.
  It granted ``admin`` across three services.
* round 14 — ``GET /users/{user_id}/profile`` had a role gate and no password
  gate, giving unscoped cross-tenant reads of personal data.
* round 15 — the four ``/agent/*`` routes and ``POST /jobs/{job_id}/jd-document``
  had NO role gate at all, because they decide privilege inside the handler.
  Round 14's sweep had searched for "a role gate with no password gate", a
  predicate that excludes that shape by construction. The cost was concrete: an
  unrotated ``hr_manager`` was refused ``GET /hr/applicants`` and admitted to
  ``POST /agent/panel/{applicant_id}``, which its own comment calls "the
  DENSEST candidate record in the platform".

So this stops asserting it in prose. It walks the LIVE dependency tree of every
registered route and asks the only question that matters — does
``require_password_changed`` appear in it — then requires every authenticated
route that answers no to be on the list below, with a reason. Adding a
privileged route now fails this test until somebody decides which it is.

The list is deliberately by PREFIX and deliberately short. A prefix admits a
whole family, so each one has to be a family that is self-scoped by
construction, not merely harmless today.
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

# Route families that may skip the gate, and why each is sound.
#
# "Self-scoped by construction" means the handler can only ever reach the
# caller's own rows — it resolves the subject from the token, never from a path
# or query parameter. That is what makes the bootstrap flag irrelevant: the
# holder of an unrotated password reaches nothing they do not already own.
_UNGATED_FAMILIES: dict[str, str] = {
    "/auth/": (
        "DELIBERATELY ungated, and named in AR-11. The holder must be able to "
        "reach /auth/me, /auth/change-password and /auth/logout* in order to "
        "CLEAR the flag; gating these would make the flag unclearable. It is "
        "also the reason AR-11 records that whoever reads the printed banner "
        "can take the account over."
    ),
    "/users/me/": (
        "Self-scoped by construction: the subject comes from the token, never "
        "from the path. The caller's own applications, resumes, offers, "
        "onboarding, practice plan and interview loops."
    ),
    "/consent": (
        "The caller's own DPDP consent record, resolved from the token. DPDP "
        "gives the data principal the right to act on their own data; gating "
        "that on a password-rotation chore would be the wrong trade."
    ),
    "/notifications": "The caller's own notifications, resolved from the token.",
    "/jobs": (
        "Self-serve custom jobs. No role logic anywhere in `jobs.py` — a "
        "candidate creating and reading their own practice jobs. Note "
        "`POST /jobs/{job_id}/jd-document` lives in `jd.py`, NOT here, and IS "
        "gated (round 15)."
    ),
}


def _callables(dependant: Any, out: list[Any] | None = None) -> list[Any]:
    """Every callable in a FastAPI dependant tree, including sub-dependencies."""
    if out is None:
        out = []
    if dependant.call is not None:
        out.append(dependant.call)
    for sub in dependant.dependencies:
        _callables(sub, out)
    return out


def _classify() -> tuple[list[str], list[str], list[str]]:
    """(anonymous, password-gated, authenticated-but-ungated) route labels."""
    from app.dependencies import get_current_user, require_password_changed
    from app.main import app

    anonymous: list[str] = []
    gated: list[str] = []
    ungated: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        found = _callables(route.dependant)
        methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
        label = f"{methods} {route.path}"
        if require_password_changed in found:
            gated.append(label)
        elif get_current_user in found:
            ungated.append(label)
        else:
            anonymous.append(label)
    return anonymous, gated, ungated


def test_every_authenticated_route_is_gated_or_listed() -> None:
    """The claim AR-11 used to make in prose, as a check that cannot go stale."""
    _anonymous, gated, ungated = _classify()

    assert gated, (
        "no route reaches `require_password_changed` at all — this test has "
        "stopped matching, which looks identical to a pass"
    )

    unexplained = [
        label
        for label in ungated
        if not any(prefix in label for prefix in _UNGATED_FAMILIES)
    ]
    assert not unexplained, (
        "these routes authenticate a caller but never reach "
        "`require_password_changed`, and are not in a listed family:\n    "
        + "\n    ".join(sorted(unexplained))
        + "\n\nAn account provisioned with `must_change_password = true` — every "
        "`hr_manager` and `super_admin` from `admin_hr`, and the platform owner "
        "whose password is PRINTED by its migration (AR-11) — can drive these "
        "before rotating it. Either depend on `require_role_password_ok` (or "
        "`require_password_changed` directly, if the role decision is "
        "in-handler), or add the family to `_UNGATED_FAMILIES` with the reason "
        "it is self-scoped."
    )


def test_the_listed_families_still_match_something() -> None:
    """A prefix that matches nothing is rot, and rot in an allowlist is how a
    real route ends up excused by an entry written for a different one."""
    _anonymous, _gated, ungated = _classify()
    stale = [
        prefix
        for prefix in _UNGATED_FAMILIES
        if not any(prefix in label for label in ungated)
    ]
    assert not stale, (
        f"these exempt families match no ungated route any more: {stale}. "
        "Either they were gated (good — delete the entry) or they were renamed "
        "(in which case this list is now excusing nothing and hiding that)."
    )


def test_the_agent_and_jd_routes_are_gated() -> None:
    """Round 15's finding, pinned by name as well as by the rule above.

    The general rule would catch a regression here anyway. This is here because
    these five are the ones that were actually exploitable — an unrotated
    `hr_manager` refused `GET /hr/applicants` but admitted to the denser read —
    and a named assertion says so to whoever breaks it.
    """
    _anonymous, gated, _ungated = _classify()
    for path in (
        "/agent/chat",
        "/agent/status",
        "/agent/watchers/run",
        "/agent/panel/{applicant_id}",
        "/jobs/{job_id}/jd-document",
    ):
        assert any(label.endswith(f" {path}") for label in gated), (
            f"{path} no longer reaches the bootstrap-password gate"
        )
