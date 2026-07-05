"""Unit tests for the guest-token guard (HR workflow Phase 3, B7).

A magic-link interview guest (role 'guest_candidate' ONLY) must be rejected from
the session create/list endpoints so a leaked guest token can't self-mint or
enumerate sessions.
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
