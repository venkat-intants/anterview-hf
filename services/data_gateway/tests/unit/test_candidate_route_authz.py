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


def test_the_candidate_router_rejects_guest_candidate() -> None:
    """The pin. Gated on the router, so a route added later inherits it."""
    from app.routers.candidate_applications import router

    denied: set[str] = set()
    for dependant in router.dependencies:
        denied |= _denied_roles(dependant)

    assert "guest_candidate" in denied, (
        "the candidate router must reject guest_candidate: an interview magic "
        "link mints a token whose sub is the candidate's own account id"
    )


def test_every_candidate_route_is_covered_by_the_router_gate() -> None:
    """Router-level, not route-level, so nothing can be added outside it."""
    from app.routers.candidate_applications import router

    assert router.routes, "no routes found — this test would pass vacuously"
    assert router.dependencies, (
        "the gate is declared on the router so every route inherits it; a "
        "per-route gate is one someone forgets on the next route"
    )
