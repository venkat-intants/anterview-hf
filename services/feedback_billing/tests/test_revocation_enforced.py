"""Every auth dependency in this service must enforce the revocation epoch.

This service is where the drift happened: ``scorecard_list.py`` was a fourth
copy of the auth dependency and shipped WITHOUT the epoch check, so "log out all
devices", password reset, HR account deletion and DPDP erasure all failed to
revoke access to scorecard history until 40df357.

Nothing tested that at the time. Consolidating onto a shared helper reduces the
odds of a repeat but does not remove them — someone can still add a sixth
dependency that skips the call. This test is what actually closes it: it walks
each verifier with a revoked token and demands a 401.

Add a new auth dependency to this service → add it to the list below.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from shared.auth.jwt import issue_access_token

_SECRET = "test-secret-at-least-32-bytes-long-xxxx"
_ISSUER = "intants-data-gateway"
_AUDIENCE = "intants-services"


class _FakeRedis:
    """Returns a revocation epoch in the future for every user."""

    def __init__(self, epoch: int | None) -> None:
        self._epoch = epoch

    async def get(self, key: str) -> Any:
        return None if self._epoch is None else str(self._epoch)


def _token(sub: str, roles: list[str]) -> str:
    return issue_access_token(
        user_id=sub,
        roles=roles,
        secret=_SECRET,
        issuer=_ISSUER,
        audience=_AUDIENCE,
    )


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture()
def patched_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both modules import the same singleton as `_app_settings`, so patching it
    # once covers both verifiers.
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "jwt_secret", _SECRET, raising=False)
    monkeypatch.setattr(app_settings, "jwt_algorithm", "HS256", raising=False)
    monkeypatch.setattr(app_settings, "jwt_issuer", _ISSUER, raising=False)
    monkeypatch.setattr(app_settings, "jwt_audience", _AUDIENCE, raising=False)


# Every dependency in this service that authenticates a bearer token.
# (label, module path, callable name, subject, roles)
_VERIFIERS = [
    ("require_jwt", "app.auth", "require_jwt", str(uuid.uuid4()), ["candidate"]),
    (
        "_require_service_jwt",
        "app.routers.score",
        "_require_service_jwt",
        "interview_core",
        ["service"],
    ),
]


@pytest.mark.parametrize(("label", "module", "func", "sub", "roles"), _VERIFIERS)
@pytest.mark.asyncio
async def test_verifier_rejects_a_revoked_token(
    label: str,
    module: str,
    func: str,
    sub: str,
    roles: list[str],
    monkeypatch: pytest.MonkeyPatch,
    patched_settings: None,
) -> None:
    """A token whose iat predates the user's epoch must be refused with 401."""
    import importlib

    mod = importlib.import_module(module)
    # Epoch is in the future relative to the token's iat, so the token is stale.
    monkeypatch.setattr(mod, "get_redis", lambda: _FakeRedis(int(time.time()) + 60))

    with pytest.raises(HTTPException) as exc:
        await getattr(mod, func)(credentials=_creds(_token(sub, roles)))

    assert exc.value.status_code == 401, (
        f"{label} accepted a REVOKED token. 'Log out all devices', password "
        f"reset, HR account deletion and DPDP erasure all silently fail to "
        f"revoke access through this dependency."
    )


@pytest.mark.parametrize(("label", "module", "func", "sub", "roles"), _VERIFIERS)
@pytest.mark.asyncio
async def test_verifier_accepts_a_live_token(
    label: str,
    module: str,
    func: str,
    sub: str,
    roles: list[str],
    monkeypatch: pytest.MonkeyPatch,
    patched_settings: None,
) -> None:
    """The control half of the test above.

    Without it, a dependency that rejects EVERYTHING would pass the revocation
    test — the same shape of false confidence as a test that passes with the
    control deleted.
    """
    import importlib

    mod = importlib.import_module(module)
    monkeypatch.setattr(mod, "get_redis", lambda: _FakeRedis(None))

    result = await getattr(mod, func)(credentials=_creds(_token(sub, roles)))
    assert result is not None


@pytest.mark.asyncio
async def test_verifiers_fail_open_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None
) -> None:
    """Deliberate availability trade-off — pinned so a change is a decision.

    A cache outage must not lock every user out. If this test starts failing
    because someone made the epoch check fail closed, that is a posture change
    that needs to be argued, not a cleanup.
    """
    import importlib

    class _BrokenRedis:
        async def get(self, key: str) -> Any:
            raise ConnectionError("upstash unreachable")

    for _label, module, func, sub, roles in _VERIFIERS:
        mod = importlib.import_module(module)
        monkeypatch.setattr(mod, "get_redis", lambda: _BrokenRedis())
        result = await getattr(mod, func)(credentials=_creds(_token(sub, roles)))
        assert result is not None
