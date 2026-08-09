"""Why this service keeps TWO bearer-token dependencies (SEC-4, SEC-6).

``app.auth.require_jwt`` and ``app.routers.score._require_service_jwt`` look like
duplication, and every few months someone proposes unifying them. The reason not
to is that they enforce **opposite** requirements on ``sub``:

* ``require_jwt`` demands a UUID, because that value is compared against a
  ``uuid`` column and a non-UUID reaching the driver is a 500 where a 401
  belongs;
* ``_require_service_jwt`` demands one of the pinned service names
  (``interview_core`` / ``data_gateway``), which are deliberately not UUIDs.

An earlier review recorded a different rationale — guest-session binding — which
is not what the code does. A prose correction on the function is only worth as
much as the next person's willingness to believe it, so these tests assert the
disjointness directly: if someone unifies the two, one of them starts accepting
what it used to reject and this file goes red.

``test_epoch_prefix_is_the_shared_constant`` covers SEC-6 from the same angle:
the docstring in app/auth.py claimed the prefix was a hand-synced copy, and this
pins that it is the imported original so the claim cannot silently become true
again.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from shared.auth.jwt import USER_TOKEN_EPOCH_PREFIX, issue_access_token

_SECRET = "test-secret-at-least-32-bytes-long-xxxx"
_ISSUER = "intants-data-gateway"
_AUDIENCE = "intants-services"


class _NoEpochRedis:
    """No revocation epoch set for anyone — isolates the sub/role policy."""

    async def get(self, key: str) -> Any:
        return None


def _creds(sub: str, roles: list[str]) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=issue_access_token(
            user_id=sub,
            roles=roles,
            secret=_SECRET,
            issuer=_ISSUER,
            audience=_AUDIENCE,
        ),
    )


@pytest.fixture()
def signing_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both verifiers at the test signing key.

    Both modules bind the same Settings singleton as ``_app_settings``, so one
    patch covers both — which is itself part of the point: the *signature*
    policy is shared, only the authorization policy differs.
    """
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "jwt_secret", _SECRET, raising=False)
    monkeypatch.setattr(app_settings, "jwt_algorithm", "HS256", raising=False)
    monkeypatch.setattr(app_settings, "jwt_issuer", _ISSUER, raising=False)
    monkeypatch.setattr(app_settings, "jwt_audience", _AUDIENCE, raising=False)


@pytest.fixture()
def no_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.auth as auth_mod
    import app.routers.score as score_mod

    monkeypatch.setattr(auth_mod, "get_redis", lambda: _NoEpochRedis())
    monkeypatch.setattr(score_mod, "get_redis", lambda: _NoEpochRedis())


@pytest.mark.parametrize("service_sub", ["interview_core", "data_gateway"])
@pytest.mark.asyncio
async def test_user_verifier_rejects_the_service_subs(
    service_sub: str, signing_settings: None, no_epoch: None
) -> None:
    """The user verifier cannot stand in for the service one.

    A valid, unrevoked, correctly-signed service token is refused by
    ``require_jwt`` at the UUID check. This is the half of the disjointness that
    is easy to get wrong when unifying: a shared dependency that kept the UUID
    rule would take down /internal/score.
    """
    from app.auth import require_jwt

    with pytest.raises(HTTPException) as exc:
        await require_jwt(credentials=_creds(service_sub, ["service"]))

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_service_verifier_rejects_a_uuid_user_sub(
    signing_settings: None, no_epoch: None
) -> None:
    """And the service verifier cannot stand in for the user one.

    403 rather than 401 on purpose: the token is authentic, the caller is simply
    not allowed here. The candidate is the principal that must never reach
    /internal/* — that route spends Gemini quota on our key.
    """
    from app.routers.score import _require_service_jwt

    with pytest.raises(HTTPException) as exc:
        await _require_service_jwt(credentials=_creds(str(uuid.uuid4()), ["candidate"]))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_service_verifier_rejects_a_uuid_sub_even_with_the_service_role(
    signing_settings: None, no_epoch: None
) -> None:
    """The allowlist, not the role, is what makes the two policies disjoint.

    Without this the previous test would also pass if the only rule were
    ``"service" in roles`` — and then a leaked JWT_SECRET used to mint
    ``roles=["service"]`` for an arbitrary UUID would walk straight in.
    """
    from app.routers.score import _require_service_jwt

    with pytest.raises(HTTPException) as exc:
        await _require_service_jwt(credentials=_creds(str(uuid.uuid4()), ["service"]))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_each_verifier_accepts_its_own_principal(
    signing_settings: None, no_epoch: None
) -> None:
    """Control for the three tests above.

    A pair of verifiers that rejected everything would satisfy all of them.
    """
    from app.auth import require_jwt
    from app.routers.score import _require_service_jwt

    user_sub = str(uuid.uuid4())
    user_payload = await require_jwt(credentials=_creds(user_sub, ["candidate"]))
    service_payload = await _require_service_jwt(
        credentials=_creds("interview_core", ["service"])
    )

    assert user_payload["sub"] == user_sub
    assert service_payload["sub"] == "interview_core"


def test_epoch_prefix_is_the_shared_constant() -> None:
    """SEC-6: the prefix is imported, not hand-copied.

    Identity rather than equality — two independently-typed strings that happen
    to match today would pass an ``==`` assertion and is exactly the state the
    old docstring described. The prefix names the Redis key data_gateway's
    ``logout_all`` writes and this service reads; a private copy here means
    revocation stops working on one side of a rename with nothing failing.
    """
    from app.auth import TOKEN_EPOCH_PREFIX

    assert TOKEN_EPOCH_PREFIX is USER_TOKEN_EPOCH_PREFIX
