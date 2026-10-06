"""A key that fails CONSTRUCTION must still be a 401, not a 500.

THE SAME TRAP IN TWO LIBRARIES, which is why this file outlived the migration that
prompted it.

Under python-jose: ``JWKError`` and ``JWTError`` were SIBLINGS — both derived from
``JOSEError``, neither from the other — and ``jose.jwt.decode`` converted only
``JWSError`` into ``JWTError``. So the one exception meaning "this key cannot be used
for this algorithm" escaped ``verify_access_token``'s loop entirely, past every
caller's ``except JWTError -> 401``, and surfaced as a 500 on EVERY authenticated
request in all four services (review 2026-10-06).

Under PyJWT, measured against the pinned 2.15.1: ``InvalidKeyError`` is a
``PyJWTError`` but NOT an ``InvalidTokenError``. Identical shape. Which is why
``shared.auth.jwt`` now re-exports ``TokenError`` and the five services catch that
rather than a name from the library — one place to get wrong instead of five.

THE OPERATOR MISTAKE THAT GETS YOU THERE is the mirror of the one
``forbid_private_signing_key`` exists to catch: PEM key material pasted into
``JWT_SECRET``. ``assert_strong_secrets`` is happy — it is long and has no placeholder
marker — the service boots clean, and then every authenticated request fails with
nothing in the auth log to point at the cause. PyJWT 2.15.1 says the same for BARE DER
material, which jose ACCEPTED as an HMAC secret: that is CVE-2026-85394's root cause,
and taking this library is what closes it.

The symptom (total authenticated outage) is as bad as it gets and the diagnosis is as
slow as it gets, which is the combination worth a test.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog
from jwt.exceptions import InvalidKeyError as JWKError

from shared.auth.jwt import TokenError as JOSEError
from shared.auth.jwt import TokenError as JWTError
from shared.auth.jwt import VerificationKey, issue_access_token, verify_access_token

_GOOD_SECRET = "test-only-secret-0123456789abcdefghij"

#: Not a real key — only the PEM *envelope* matters, because the library refuses on the
#: armour before it parses the body. Deliberately not a parseable key, so nothing
#: here can be mistaken for committed key material.
_PEM_SHAPED_SECRET = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEnotarealkeyjustthepemenvelope00\n"
    "-----END PUBLIC KEY-----\n"
)


def test_the_two_exception_types_are_still_siblings() -> None:
    """The whole defect is this hierarchy, so pin it.

    IT SURVIVED THE LIBRARY CHANGE UNCHANGED, which is the thing worth knowing.
    Under jose: `JWKError` and `JWTError` both derive from `JOSEError`, neither from
    the other. Under PyJWT, measured against the pinned 2.15.1:

        issubclass(InvalidKeyError, PyJWTError)        -> True
        issubclass(InvalidKeyError, InvalidTokenError) -> False

    Two libraries in a row have put the one exception meaning "this key cannot be
    used" outside the family a caller would naturally catch. If a future release makes
    it a subclass, the re-raise becomes redundant rather than wrong — but a reader
    should be told that by a failing test and not have to guess.
    """
    from jwt.exceptions import InvalidKeyError, InvalidTokenError, PyJWTError

    assert issubclass(InvalidKeyError, PyJWTError)
    assert issubclass(InvalidTokenError, PyJWTError)
    assert not issubclass(InvalidKeyError, InvalidTokenError), (
        "the re-raise exists because of this"
    )


def test_the_module_re_exports_what_a_caller_should_catch() -> None:
    """The defence against this trap, asserted where it lives.

    Because `InvalidKeyError` sits outside `InvalidTokenError`, a caller who
    reasonably wrote `except InvalidTokenError -> 401` would let a key-construction
    failure escape as a 500 — the exact 2026-10-06 defect, in a new library. So
    `shared.auth.jwt` re-exports `TokenError` and the five services catch THAT: one
    place to get wrong instead of five, and the next library swap cannot recreate it
    at the call sites.
    """
    from jwt.exceptions import InvalidKeyError, InvalidTokenError

    from shared.auth.jwt import TokenError

    assert issubclass(InvalidKeyError, TokenError), (
        "TokenError must be wide enough to catch a key-construction failure — that "
        "is the only reason it exists"
    )
    assert issubclass(InvalidTokenError, TokenError)


def test_pem_material_in_jwt_secret_raises_jwterror_not_jwkerror() -> None:
    """The fix, stated as the caller sees it: anything that reaches a caller from
    this function is catchable as JWTError, so it lands on the 401 path."""
    token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)

    with pytest.raises(JWTError) as exc:
        verify_access_token(token, _PEM_SHAPED_SECRET)

    assert "key could not be used for verification" in str(exc.value)


def test_the_original_jwkerror_is_kept_as_the_cause() -> None:
    """``raise ... from exc``. The library's own sentence ("should not be used as an HMAC
    secret") is the one that tells an operator what they did, so losing it would
    trade a 500 for a 401 and leave the diagnosis just as slow."""
    token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)

    with pytest.raises(JWTError) as exc:
        verify_access_token(token, _PEM_SHAPED_SECRET)

    assert isinstance(exc.value.__cause__, JWKError)
    assert "HMAC" in str(exc.value.__cause__)


def test_a_bad_key_in_a_rotation_window_does_not_mask_a_good_one() -> None:
    """A construction failure is NOT collected and retried like a signature
    failure, because no later key can fix a key THIS one cannot build — but it
    must not take out the keys around it either. ``[good, broken]`` verifies on
    the first candidate and never reaches the second."""
    secret_token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)

    payload = verify_access_token(secret_token, [_GOOD_SECRET, _PEM_SHAPED_SECRET])

    assert payload["roles"] == ["candidate"]


def test_a_broken_key_listed_first_still_fails_closed() -> None:
    """The order that is actually dangerous: the broken key is tried first. It must
    raise rather than silently fall through to a key that happens to work, because
    "my pasted PEM is being ignored" is a misconfiguration an operator needs told
    about — the deployment is not signing with what they think it is."""
    token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)

    with pytest.raises(JWTError):
        verify_access_token(token, [_PEM_SHAPED_SECRET, _GOOD_SECRET])


def test_a_kid_selected_asymmetric_candidate_with_unusable_material_is_a_401_too() -> None:
    """The same gap through the asymmetric path: a ``VerificationKey`` whose PEM
    cannot be built for RS256. The ``kid`` filter selects it, so there is no other
    candidate to fall back to and the raise is the only exit."""
    token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)

    with pytest.raises(JOSEError):
        verify_access_token(
            token,
            [VerificationKey(key="-----BEGIN PUBLIC KEY-----\nnope\n-----END PUBLIC KEY-----\n",
                             algorithm="RS256", kid="k1")],
        )


def test_the_failure_is_logged_so_an_operator_has_something_to_grep() -> None:
    """A 401 with no log line moves the outage from "500s everywhere" to "nobody
    can log in and nothing says why", which is not an improvement. The empty-
    candidate-list branch logs for the same reason."""
    events: list[dict[str, Any]] = []

    def capture(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        events.append(dict(event_dict))
        return event_dict

    original = structlog.get_config()["processors"]
    structlog.configure(processors=[capture, *original])
    try:
        token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)
        with pytest.raises(JWTError):
            verify_access_token(token, _PEM_SHAPED_SECRET)
    finally:
        structlog.configure(processors=original)

    assert any("key" in str(e.get("event", "")).lower() for e in events), (
        f"no log line names the key problem; saw {[e.get('event') for e in events]}"
    )


def test_a_good_secret_is_unaffected() -> None:
    """The control: the new except clause must not change the ordinary path."""
    user_id = str(uuid.uuid4())
    token = issue_access_token(user_id, ["hr_manager"], _GOOD_SECRET)

    payload = verify_access_token(token, _GOOD_SECRET)

    assert payload["sub"] == user_id
    assert payload["roles"] == ["hr_manager"]
