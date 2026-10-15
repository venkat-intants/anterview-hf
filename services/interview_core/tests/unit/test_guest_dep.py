"""Unit tests for the guest-token guard (HR workflow Phase 3, B7).

A magic-link interview guest (ANY token carrying 'guest_candidate') must be rejected from
the session create endpoint so a leaked guest token can't self-mint sessions.

Listing is handled separately: a guest is narrowed to the one session its token
names rather than refused (that is how a guest finds their own scorecard), so
the second half of this file pins ``_guest_bound_session_id``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_guest_only_token_rejected() -> None:
    from app.dependencies import get_non_guest_user

    with pytest.raises(HTTPException) as exc:
        await get_non_guest_user({"sub": "u1", "roles": ["guest_candidate"]})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_guest_alongside_another_role_is_rejected_too() -> None:
    # Changed 2026-10-12: previously "a real role alongside guest still passes".
    # Any token carrying guest_candidate is a guest -- never an unrestricted account.
    from app.dependencies import get_non_guest_user

    for roles in (["guest_candidate", "candidate"], ["hr_manager", "guest_candidate"]):
        with pytest.raises(HTTPException) as exc:
            await get_non_guest_user({"sub": "u1", "roles": roles})
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_candidate_token_allowed() -> None:
    from app.dependencies import get_non_guest_user

    out = await get_non_guest_user({"sub": "u1", "roles": ["candidate"]})
    assert out["sub"] == "u1"


@pytest.mark.asyncio
async def test_no_roles_allowed() -> None:
    # A normal token with no/other roles is not a guest — must pass through.
    from app.dependencies import get_non_guest_user

    out = await get_non_guest_user({"sub": "u1", "roles": []})
    assert out["sub"] == "u1"


# --- GET /api/sessions: a guest sees its one bound session, nothing else ------


def test_normal_account_is_not_narrowed() -> None:
    from app.routers.sessions import _guest_bound_session_id

    assert _guest_bound_session_id({"sub": "u", "roles": ["candidate"]}) is None
    assert _guest_bound_session_id({"sub": "u", "roles": []}) is None
    assert _guest_bound_session_id({"sub": "u"}) is None


def test_role_alongside_guest_is_still_narrowed() -> None:
    # CHANGED DELIBERATELY (2026-10-12, low-severity review item): this used to
    # assert the opposite -- that a mixed-role token was a normal account. Every
    # other guest predicate (get_guest_bound_user, feedback_billing
    # is_guest_token, data_gateway reject_role) treats ANY guest_candidate as a
    # guest, so the mixed token was the one path that listed every session.
    import uuid

    from app.routers.sessions import _guest_bound_session_id

    sid = uuid.uuid4()
    claims = {"sub": "u", "roles": ["guest_candidate", "candidate"], "session_id": str(sid)}
    assert _guest_bound_session_id(claims) == sid


def test_mixed_role_guest_without_session_claim_is_refused() -> None:
    from app.routers.sessions import _guest_bound_session_id

    with pytest.raises(HTTPException) as exc:
        _guest_bound_session_id({"sub": "u", "roles": ["hr_manager", "guest_candidate"]})
    assert exc.value.status_code == 403


def test_guest_is_narrowed_to_its_session_claim() -> None:
    import uuid

    from app.routers.sessions import _guest_bound_session_id

    sid = uuid.uuid4()
    claims = {"sub": "u", "roles": ["guest_candidate"], "session_id": str(sid)}
    assert _guest_bound_session_id(claims) == sid


@pytest.mark.parametrize("claim", [None, "", "not-a-uuid", 12345])
def test_guest_without_usable_session_claim_is_refused(claim: object) -> None:
    # Refused, never widened to "every session this guest identity owns".
    from app.routers.sessions import _guest_bound_session_id

    claims: dict[str, object] = {"sub": "u", "roles": ["guest_candidate"]}
    if claim is not None:
        claims["session_id"] = claim
    with pytest.raises(HTTPException) as exc:
        _guest_bound_session_id(claims)
    assert exc.value.status_code == 403
