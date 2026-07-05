"""Unit tests for the interview-invite redeem guard + HR validation (HR Phase 3).

The full provision-and-grade flow is covered by a live end-to-end smoke test; here
we lock the cheap, security-relevant guards: uniform 404 on bad/absent tokens and
input validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_preview_missing_token_404() -> None:
    from app.routers.interview_take import preview_invite

    with pytest.raises(HTTPException) as exc:
        await preview_invite(AsyncMock(), x_interview_token=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_redeem_missing_token_404() -> None:
    from app.routers.interview_take import RedeemIn, redeem_invite

    with pytest.raises(HTTPException) as exc:
        await redeem_invite(
            RedeemIn(consent_granted=True), AsyncMock(), MagicMock(), x_interview_token=None
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_redeem_unknown_token_404() -> None:
    from app.routers.interview_take import RedeemIn, redeem_invite

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # no invite resolves
    with pytest.raises(HTTPException) as exc:
        await redeem_invite(
            RedeemIn(consent_granted=True), db, MagicMock(), x_interview_token="bogus-token"
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# resume_invite — guest reload recovery (httpOnly cookie, no login)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_missing_cookie_404() -> None:
    from app.routers.interview_take import resume_invite

    with pytest.raises(HTTPException) as exc:
        await resume_invite(AsyncMock(), MagicMock(), iv_cookie=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_resume_unknown_cookie_404() -> None:
    from app.routers.interview_take import resume_invite

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # cookie resolves to no in-progress invite
    with pytest.raises(HTTPException) as exc:
        await resume_invite(db, MagicMock(), iv_cookie="bogus")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_resume_happy_path_reissues_token_and_sets_cookie() -> None:
    from app.routers.interview_take import RedeemOut, resume_invite

    inv = SimpleNamespace(
        session_id=uuid.uuid4(),
        guest_user_id=uuid.uuid4(),
        applicant_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        updated_at=None,
    )
    applicant = SimpleNamespace(full_name="Jane Doe")
    db = AsyncMock()
    # scalars in order: invite resolve, session status (not completed),
    # scorecard exists (none), applicant resolve.
    db.scalar = AsyncMock(side_effect=[inv, "in_progress", None, applicant])
    response = MagicMock()
    sentinel = RedeemOut(
        session_id=str(inv.session_id), access_token="fresh", language="en",
        user_id=str(inv.guest_user_id), full_name="Jane Doe", email=None,
        roles=["guest_candidate"],
    )
    with patch("app.routers.interview_take._issue_guest_token", return_value=sentinel):
        out = await resume_invite(db, response, iv_cookie="raw-token")

    assert out is sentinel  # fresh guest token re-issued for the same session
    db.commit.assert_awaited_once()
    response.set_cookie.assert_called_once()  # resume cookie TTL slid forward


def test_invite_create_rejects_bad_language() -> None:
    from pydantic import ValidationError

    from app.routers.hr_interviews import InviteCreateIn

    with pytest.raises(ValidationError):
        InviteCreateIn(applicant_id=uuid.uuid4(), language="fr")


def test_invite_create_accepts_day1_languages() -> None:
    from app.routers.hr_interviews import InviteCreateIn

    for lang in ("en", "hi", "te"):
        m = InviteCreateIn(applicant_id=uuid.uuid4(), language=lang)
        assert m.language == lang
