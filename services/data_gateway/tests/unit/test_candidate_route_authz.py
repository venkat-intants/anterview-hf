"""A guest token must not be able to act as the candidate's account.

Redeeming an interview invitation mints a real access token whose ``sub`` is
the applicant's ``user_id`` — which, once they have activated, is their actual
account id — carrying the role ``guest_candidate``. ``get_current_user``
verifies the signature, issuer, audience and revocation epoch and returns the
roles without looking at them. So a forwarded interview link was a working
credential for every candidate route: their applications at every company they
applied to, and the ability to rotate their own links out from under them.

Two comments in the tree asserted the opposite, which is why it survived. These
tests are the thing that makes the assertion true, and the router-level pin
below is the one that matters: the hole was never a missing check on a route,
it was a router nobody had gated.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from shared.auth.base import User


def _user(*roles: str) -> User:
    return User(user_id=str(uuid.uuid4()), full_name="", email="", roles=list(roles))


def _denied_roles(dependant: object) -> set[str]:
    """The role names a ``reject_role`` dependency was built with.

    Read out of the closure rather than called, so this stays a structural
    assertion about the router: it answers "is the gate wired on?", which is
    the question, without standing up an app or a database.
    """
    fn = getattr(dependant, "dependency", None)
    names: set[str] = set()
    for cell in getattr(fn, "__closure__", None) or ():
        value = cell.cell_contents
        if isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
            names |= set(value)
    return names


@pytest.mark.asyncio
async def test_reject_role_refuses_a_caller_holding_the_denied_role() -> None:
    from app.dependencies import reject_role

    dep = reject_role("guest_candidate")

    with pytest.raises(HTTPException) as exc:
        await dep(_user("guest_candidate"))  # type: ignore[call-arg]
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reject_role_refuses_it_even_alongside_a_legitimate_role() -> None:
    """A token carrying both is still a guest token.

    Activation grants ``candidate`` and drops ``guest_candidate``, in that
    order, so the pair should never coexist — but an ordering bug or a token
    minted mid-activation must not become a way through.
    """
    from app.dependencies import reject_role

    dep = reject_role("guest_candidate")

    with pytest.raises(HTTPException) as exc:
        await dep(_user("candidate", "guest_candidate"))  # type: ignore[call-arg]
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reject_role_admits_an_account_that_holds_no_role_at_all() -> None:
    """Why this refuses a role instead of requiring one.

    ``candidate`` is granted by activation and by Google SSO. An account that
    reached its applications another way — an HR user who also applied, an
    account provisioned before the role existed — may hold no candidate role,
    and ``require_role("candidate")`` would lock them out of their own page.
    What must never reach the router is narrow and nameable; the legitimate
    callers are not.
    """
    from app.dependencies import reject_role

    dep = reject_role("guest_candidate")

    assert await dep(_user()) is not None  # type: ignore[call-arg]
    assert await dep(_user("candidate")) is not None  # type: ignore[call-arg]
    assert await dep(_user("hr_manager")) is not None  # type: ignore[call-arg]


def _gate_covers(route: object) -> set[str]:
    """The roles refused on this route, read out of its resolved dependency tree.

    FastAPI flattens a router's ``dependencies`` into each route's dependant at
    include time, so this sees the gate wherever it was declared — on the
    router, on the route, or on a parent — rather than trusting where we think
    we put it.
    """
    denied: set[str] = set()
    stack = [getattr(route, "dependant", None)]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        fn = getattr(node, "call", None)
        for cell in getattr(fn, "__closure__", None) or ():
            value = cell.cell_contents
            if isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
                denied |= set(value)
        stack.extend(getattr(node, "dependencies", []))
    return denied


def test_every_route_under_users_me_refuses_a_guest_token() -> None:
    """The pin that the first version of this fix did not have.

    A router's dependencies bind to ITS routes, not to a URL prefix, and six
    routers are mounted at ``/users/me``: tasks, offers, interview scheduling,
    onboarding, resumes and applications. Gating one of them left a forwarded
    interview link able to rotate the candidate's take-home credential, read
    their CV and read their offer — the same hole, one door along.

    So this walks the mounted application rather than any single router. A
    seventh ``/users/me`` router added without the gate fails here.
    """
    from app.main import app

    ungated = [
        f"{getattr(r, 'name', '?')} {r.path}"
        for r in app.routes
        if getattr(r, "path", "").startswith("/users/me")
        and "guest_candidate" not in _gate_covers(r)
    ]
    assert not ungated, (
        "these /users/me routes accept a guest token, which carries the "
        f"candidate's own account id as its sub: {ungated}"
    )


def test_notifications_refuses_a_guest_token_too() -> None:
    """Same credential, different prefix — it reads the account's notifications."""
    from app.main import app

    routes = [r for r in app.routes if getattr(r, "path", "").startswith("/notifications")]
    assert routes, "no notification routes found — this test would pass vacuously"
    for r in routes:
        assert "guest_candidate" in _gate_covers(r), r.path


def test_consent_withdrawal_stays_reachable_by_a_guest() -> None:
    """Deliberately NOT gated, and this says so out loud.

    A guest token is an unactivated applicant's only credential — they have no
    password — so gating ``/consent`` would leave the people whose data was
    collected with no way to withdraw it. Refusing a role is a security
    control; applying it here would remove a DPDP right.
    """
    from app.main import app

    consent = [r for r in app.routes if getattr(r, "path", "") == "/consent"]
    assert consent, "no /consent route found — this test would pass vacuously"
    for r in consent:
        assert "guest_candidate" not in _gate_covers(r), (
            "withdrawal must stay reachable without an account; if this is "
            "deliberately changing, give guests another route first"
        )
