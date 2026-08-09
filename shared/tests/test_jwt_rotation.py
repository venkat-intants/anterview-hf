"""JWT secret rotation (2026-08 review, SEC-1).

``verify_access_token`` accepts either one secret or a sequence of them. That is
what makes a rotation *window* expressible: deploy the verifiers with
``[new, old]``, flip signing to ``new``, wait for old-key traffic to drain, then
redeploy with ``[new]``. Nobody is logged out, so the rotation can actually be
performed instead of being postponed forever.

These tests pin the three properties that make the window work:
  - a token signed with a superseded key still verifies while that key is listed
  - the same token stops verifying the moment the key is dropped
  - every existing caller, all of which pass a single ``str``, is unaffected

Plus the two diagnostics that make a rotation finishable rather than merely
possible: the ``auth.jwt.verified_with_rotated_key`` drain signal, and reporting
the *current* key's failure reason rather than a trailing key's inevitable
"signature verification failed".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from jose import JWTError
from jose import jwt as jose_jwt
from jose.exceptions import ExpiredSignatureError

from shared.auth.jwt import issue_access_token, verify_access_token

_OLD_SECRET = "test-only-old-secret-0123456789abcdef"
_NEW_SECRET = "test-only-new-secret-fedcba9876543210"
_ISSUER = "intants-data-gateway"
_AUDIENCE = "intants-services"


def _issue(secret: str) -> str:
    return issue_access_token(str(uuid.uuid4()), ["candidate"], secret)


def _handcraft(secret: str, **overrides: Any) -> str:
    """Mint a token bypassing issue_access_token, to omit/expire claims at will."""
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "roles": ["candidate"],
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    for key, value in list(claims.items()):
        if value is None:
            del claims[key]
    return str(jose_jwt.encode(claims, secret, algorithm="HS256"))


# ---------------------------------------------------------------------------
# The rotation window itself
# ---------------------------------------------------------------------------


def test_old_key_token_verifies_during_the_rotation_window() -> None:
    """Step 1 of the procedure: [new, old] keeps already-issued tokens alive."""
    token = _issue(_OLD_SECRET)

    payload = verify_access_token(token, [_NEW_SECRET, _OLD_SECRET])

    assert payload["roles"] == ["candidate"]
    assert payload["jti"]


def test_new_key_token_verifies_during_the_rotation_window() -> None:
    """Step 2: once signing flips, the new key is the one that matches first."""
    token = _issue(_NEW_SECRET)

    payload = verify_access_token(token, [_NEW_SECRET, _OLD_SECRET])

    assert payload["roles"] == ["candidate"]


def test_old_key_token_rejected_once_the_old_key_is_dropped() -> None:
    """Step 4: dropping the trailing secret is what actually ends the leak."""
    token = _issue(_OLD_SECRET)

    with pytest.raises(JWTError):
        verify_access_token(token, [_NEW_SECRET])


def test_key_order_does_not_matter_for_acceptance() -> None:
    """Acceptance is order-independent — only the drain signal reads the order.

    The documented procedure puts the current key first for the sake of
    ``verified_with_rotated_key``; getting that backwards must degrade the
    diagnostic, never lock anybody out.
    """
    old_token = _issue(_OLD_SECRET)
    new_token = _issue(_NEW_SECRET)

    assert verify_access_token(old_token, [_OLD_SECRET, _NEW_SECRET])["sub"]
    assert verify_access_token(new_token, [_OLD_SECRET, _NEW_SECRET])["sub"]


def test_any_sequence_type_is_accepted() -> None:
    """A tuple works too — the parameter is Sequence[str], not list[str]."""
    token = _issue(_OLD_SECRET)

    assert verify_access_token(token, (_NEW_SECRET, _OLD_SECRET))["sub"]


def test_a_key_matching_none_of_the_secrets_is_rejected() -> None:
    token = _issue("some-third-secret-that-was-never-deployed")

    with pytest.raises(JWTError):
        verify_access_token(token, [_NEW_SECRET, _OLD_SECRET])


def test_empty_secret_sequence_fails_closed() -> None:
    """A misconfigured deploy must reject tokens, not accept them.

    Raised as JWTError specifically so it exits through each caller's existing
    ``except JWTError -> 401`` rather than as an uncaught 500.
    """
    token = _issue(_NEW_SECRET)

    with pytest.raises(JWTError):
        verify_access_token(token, [])


# ---------------------------------------------------------------------------
# Backward compatibility — every caller in the repo passes a single str
# ---------------------------------------------------------------------------


def test_single_str_secret_positional_still_works() -> None:
    """admin_ops/feedback_billing pass the secret positionally."""
    token = _issue(_NEW_SECRET)

    payload = verify_access_token(token, _NEW_SECRET)

    assert payload["iss"] == _ISSUER
    assert payload["aud"] == _AUDIENCE


def test_single_str_secret_by_keyword_still_works() -> None:
    """data_gateway/interview_core pass secret=..., algorithm=..., iss/aud."""
    token = _issue(_NEW_SECRET)

    payload = verify_access_token(
        token,
        secret=_NEW_SECRET,
        algorithm="HS256",
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
    )

    assert payload["roles"] == ["candidate"]


def test_single_str_secret_is_not_iterated_character_by_character() -> None:
    """Regression guard for the str-is-a-Sequence[str] trap.

    ``str`` satisfies ``Sequence[str]``, so without the isinstance check first,
    "abc" would be tried as the keys "a", "b", "c" — the wrong-key path — and
    every request on the platform would 401.
    """
    token = _issue(_NEW_SECRET)

    assert verify_access_token(token, _NEW_SECRET)["sub"]
    # ...and one character of it must not be enough.
    with pytest.raises(JWTError):
        verify_access_token(token, _NEW_SECRET[0])


def test_single_str_secret_rejects_a_token_signed_with_another_key() -> None:
    token = _issue(_OLD_SECRET)

    with pytest.raises(JWTError):
        verify_access_token(token, _NEW_SECRET)


# ---------------------------------------------------------------------------
# Diagnostics — what makes the window finishable
# ---------------------------------------------------------------------------


def test_drain_signal_emitted_only_for_a_superseded_key() -> None:
    """``auth.jwt.verified_with_rotated_key`` is the "old traffic remains" flag.

    It is the only evidence that the trailing secret is still needed, so it must
    fire for a superseded key and stay silent for the current one — a signal
    that fired on every request would be useless for deciding when to drop a key.
    """
    old_token = _issue(_OLD_SECRET)
    new_token = _issue(_NEW_SECRET)

    with structlog.testing.capture_logs() as captured:
        verify_access_token(old_token, [_NEW_SECRET, _OLD_SECRET])
    events = [e for e in captured if e["event"] == "auth.jwt.verified_with_rotated_key"]
    assert len(events) == 1
    assert events[0]["key_index"] == 1

    with structlog.testing.capture_logs() as captured:
        verify_access_token(new_token, [_NEW_SECRET, _OLD_SECRET])
    assert not [
        e for e in captured if e["event"] == "auth.jwt.verified_with_rotated_key"
    ]


def test_expiry_is_reported_as_expiry_not_as_a_signature_failure() -> None:
    """An expired old-key token must not read as a key mismatch.

    jose checks the signature before the claims, so the old key matching proves
    the token is ours. Letting the loop continue would surface the *new* key's
    "signature verification failed" and point an incident responder at a
    rotation bug that does not exist.
    """
    expired = _handcraft(
        _OLD_SECRET, exp=datetime.now(tz=UTC) - timedelta(minutes=1)
    )

    with pytest.raises(ExpiredSignatureError):
        verify_access_token(expired, [_NEW_SECRET, _OLD_SECRET])


def test_failure_reason_comes_from_the_current_signing_key() -> None:
    """The reported error is candidates[0]'s, not the last key's.

    A token signed with the CURRENT key but missing ``iat`` has a real defect
    worth naming; the trailing key can only ever add "signature verification
    failed", which would bury it.
    """
    no_iat = _handcraft(_NEW_SECRET, iat=None)

    with pytest.raises(JWTError) as excinfo:
        verify_access_token(no_iat, [_NEW_SECRET, _OLD_SECRET])
    assert "iat" in str(excinfo.value)


def test_empty_jti_still_rejected_with_multiple_secrets() -> None:
    """The empty-jti guard must not be skipped just because more keys remain."""
    empty_jti = _handcraft(_OLD_SECRET, jti="")

    with pytest.raises(JWTError, match="jti"):
        verify_access_token(empty_jti, [_NEW_SECRET, _OLD_SECRET])
