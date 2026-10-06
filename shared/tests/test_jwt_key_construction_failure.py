"""A key that fails CONSTRUCTION must still be a 401, not a 500.

``jose.exceptions.JWKError`` and ``jose.JWTError`` are SIBLINGS — both derive from
``JOSEError``, neither from the other — and ``jose.jwt.decode`` converts only
``JWSError`` into ``JWTError``. So the one jose exception that means "this key
cannot be used for this algorithm" escaped ``verify_access_token``'s loop entirely,
past every caller's ``except JWTError -> 401``, and surfaced as a 500 on EVERY
authenticated request in all four services (review 2026-10-06).

THE OPERATOR MISTAKE THAT GETS YOU THERE is the mirror of the one
``forbid_private_signing_key`` exists to catch: PEM key material pasted into
``JWT_SECRET`` while the algorithm is still HS256. ``assert_strong_secrets`` is
happy — it is long and has no placeholder marker — the service boots clean, and
then every request 500s with nothing in the auth log to point at the cause. The
symptom (total authenticated outage) is as bad as it gets and the diagnosis is as
slow as it gets, which is the combination worth a test.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog
from jose import JWTError
from jose.exceptions import JOSEError, JWKError

from shared.auth.jwt import VerificationKey, issue_access_token, verify_access_token

_GOOD_SECRET = "test-only-secret-0123456789abcdefghij"

#: Not a real key — only the PEM *envelope* matters, because jose refuses on the
#: armour before it parses the body. Deliberately not a parseable key, so nothing
#: here can be mistaken for committed key material.
_PEM_SHAPED_SECRET = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEnotarealkeyjustthepemenvelope00\n"
    "-----END PUBLIC KEY-----\n"
)


def test_the_two_exception_types_are_still_siblings() -> None:
    """The whole defect is this hierarchy, so pin it. If a future jose makes
    JWKError a subclass of JWTError, the handler becomes redundant rather than
    wrong — but a reader should be told by a failing test, not by guessing."""
    assert issubclass(JWKError, JOSEError)
    assert issubclass(JWTError, JOSEError)
    assert not issubclass(JWKError, JWTError), "the re-raise exists because of this"


def test_pem_material_in_jwt_secret_raises_jwterror_not_jwkerror() -> None:
    """The fix, stated as the caller sees it: anything that reaches a caller from
    this function is catchable as JWTError, so it lands on the 401 path."""
    token = issue_access_token(str(uuid.uuid4()), ["candidate"], _GOOD_SECRET)

    with pytest.raises(JWTError) as exc:
        verify_access_token(token, _PEM_SHAPED_SECRET)

    assert "key could not be used for verification" in str(exc.value)


def test_the_original_jwkerror_is_kept_as_the_cause() -> None:
    """``raise ... from exc``. jose's own sentence ("should not be used as an HMAC
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
