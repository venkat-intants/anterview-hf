"""All FIVE claims ``verify_access_token`` requires, each tested on its own.

``decode_options`` in ``shared/auth/jwt.py`` sets ``require_exp``, ``require_iss``,
``require_aud``, ``require_jti`` and ``require_iat``. Only ONE of the five had a test
— ``shared/auth/tests/test_jwt_require_iat.py``, written for a 2026-08 audit finding.
The other four were enforced by a dict literal nothing read back, so deleting any of
them, or misspelling one, broke nothing visible.

WHY THIS FILE EXISTS NOW, AND WHY IT IS WORTH MORE THAN IT LOOKS. The
python-jose → PyJWT migration is scoped (two advisories, CVE-2026-85394 and the
`ecdsa` one, have no released fix and are reachable only through jose). PyJWT does
not use jose's ``require_<claim>`` keys — it takes ``options={"require": [...]}`` —
and **it silently ignores option keys it does not recognise**. Measured against the
pinned PyJWT 2.15.1:

    jwt.decode(token_with_only_sub_and_exp, secret, algorithms=["HS256"],
               options={"require_exp": True, "require_iss": True, ...})
    -> decoded clean, claims ['exp', 'sub']

So a copy-paste port of that dict disables all five checks with **no error, no
warning, and every existing test still green**, because every other test in the repo
mints a COMPLETE token. These five tests are the instrument that would catch it.
They pass against the current jose code, which is the point — they are written before
the migration, not after it.

Each test hand-crafts its token with jose directly, bypassing
``issue_access_token``, which always sets every claim.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
import pytest

from shared.auth.jwt import TokenError as JWTError
from shared.auth.jwt import verify_access_token

_SECRET = "test-only-secret-not-real-0123456789"
_ISSUER = "intants-data-gateway"
_AUDIENCE = "intants-services"

#: Every claim the verifier requires, and what each one is FOR. A test that only
#: said "five claims are required" would not tell the next person which to restore.
_REQUIRED: dict[str, str] = {
    "exp": "without it a leaked token never stops working",
    "iss": "without it any of our services' tokens are interchangeable with a forgery "
    "from a system we do not control",
    "aud": "without it a token minted for one audience is accepted by another",
    "jti": "the per-token identifier the refresh store and the replay reasoning need",
    "iat": "the revocation kill switch compares it against the user's epoch in Redis, "
    "so a token without one was silently unrevocable",
}


def _full_claims(**overrides: Any) -> dict[str, Any]:
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
    return claims


def _token_without(claim: str) -> str:
    claims = _full_claims()
    del claims[claim]
    assert claim not in claims, "the fixture still carries the claim it should omit"
    return pyjwt.encode(claims, _SECRET, algorithm="HS256")


def _verify(token: str) -> dict[str, Any]:
    return verify_access_token(
        token,
        secret=_SECRET,
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
    )


# ---------------------------------------------------------------------------
# The five, one at a time
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("claim", "why"), sorted(_REQUIRED.items()))
def test_a_token_missing_one_required_claim_is_rejected(claim: str, why: str) -> None:
    """Each claim on its own, so a failure names which one stopped being enforced.

    Parametrised rather than written five times because the five are the same
    assertion; the ``why`` string travels into the failure message so whoever reads
    it does not have to go and find out what the claim was for.
    """
    token = _token_without(claim)

    with pytest.raises(JWTError) as exc:
        _verify(token)

    assert claim in str(exc.value) or "required" in str(exc.value).lower(), (
        f"a token with no {claim!r} was rejected, but not for a reason that names it "
        f"— {why}. Got: {exc.value}"
    )


def test_the_control_verifies() -> None:
    """Guards the guard. If the full fixture did not verify, all five tests above
    would pass for the wrong reason — every token would be rejected regardless."""
    payload = _verify(pyjwt.encode(_full_claims(), _SECRET, algorithm="HS256"))

    assert payload["roles"] == ["candidate"]
    for claim in _REQUIRED:
        assert claim in payload, f"the control token is missing {claim!r}"


def test_every_claim_the_options_require_has_a_test_here() -> None:
    """The anti-rot half. ``decode_options`` is a dict literal in
    ``shared/auth/jwt.py``; this reads it back so a sixth required claim added there
    cannot go untested, and a fifth removed cannot go unnoticed.

    Source-read rather than imported because ``decode_options`` is a local inside
    ``verify_access_token``.
    """
    import inspect
    import re

    from shared.auth import jwt as jwt_module

    source = inspect.getsource(jwt_module.verify_access_token)

    # PyJWT's spelling: options={"require": ["exp", "iss", ...]}. Updated from jose's
    # per-claim `"require_exp": True` keys when the migration landed — which is the
    # case this test's own failure message told the next reader to handle, and the
    # reason it reads the source rather than trusting a dict literal nobody checks.
    block = re.search(r'"require":\s*\[([^\]]*)\]', source)
    assert block is not None, (
        "no `require` list found in verify_access_token. If the spelling changed "
        "again, update this regex — and check all five claims are still in it, "
        "because a library that ignores an unrecognised options key turns a typo "
        "here into five silently disabled checks"
    )
    declared = set(re.findall(r'"(\w+)"', block.group(1)))
    assert declared == set(_REQUIRED), (
        f"the verifier requires {sorted(declared)} but this file tests "
        f"{sorted(_REQUIRED)} — reconcile the two"
    )


# ---------------------------------------------------------------------------
# The edges that `require` alone does not cover
# ---------------------------------------------------------------------------
def test_an_empty_jti_is_rejected_although_it_is_present() -> None:
    """``require_jti`` only checks PRESENCE: jose and PyJWT both accept ``jti: ""``
    as satisfying it (both test ``is None``). ``verify_access_token`` carries an
    explicit guard for that, and this is the test that keeps it when the options dict
    changes shape under it.
    """
    token = pyjwt.encode(_full_claims(jti=""), _SECRET, algorithm="HS256")

    with pytest.raises(JWTError) as exc:
        _verify(token)

    assert "jti" in str(exc.value)


def test_an_expired_token_is_rejected_as_expired() -> None:
    """Not as a signature failure. The distinction matters during a key rotation:
    reporting "expired" as the next key's "signature verification failed" sends an
    incident responder hunting a key mismatch that does not exist."""
    past = datetime.now(tz=UTC) - timedelta(minutes=30)
    token = pyjwt.encode(
        _full_claims(iat=past, exp=past + timedelta(minutes=1)), _SECRET, algorithm="HS256"
    )

    with pytest.raises(JWTError) as exc:
        _verify(token)

    assert "expire" in str(exc.value).lower()


def test_a_wrong_issuer_is_rejected() -> None:
    """``require_iss`` checks presence; the ``issuer=`` argument checks the VALUE.
    Both matter, and only one of them is in the options dict."""
    token = pyjwt.encode(
        _full_claims(iss="https://not-us.example"), _SECRET, algorithm="HS256"
    )

    with pytest.raises(JWTError):
        _verify(token)


def test_a_wrong_audience_is_rejected() -> None:
    token = pyjwt.encode(_full_claims(aud="some-other-service"), _SECRET, algorithm="HS256")

    with pytest.raises(JWTError):
        _verify(token)


def test_a_future_iat_is_accepted_today_and_this_will_change() -> None:
    """RECORDED, NOT ENDORSED — and this test is a tripwire for the migration.

    jose accepts an ``iat`` in the future. PyJWT 2.15.1 does NOT: measured, it raises
    ``ImmatureSignatureError`` at ``iat`` + 1 second. Every verifier is a different
    process from the issuer, and often a different host, so a verifier whose clock
    trails by one second would start 401-ing freshly minted tokens — an outage this
    repo has no test for, because the behaviour does not exist yet.

    So this test asserts today's behaviour deliberately. When it fails, the migration
    has reached this decision and it has to be made explicitly: either
    ``verify_iat: False`` (presence still enforced by ``require``, which is
    independent of it — measured) or a ``leeway``, which also widens ``exp``.
    """
    ahead = datetime.now(tz=UTC) + timedelta(seconds=30)
    token = pyjwt.encode(
        _full_claims(iat=ahead, exp=ahead + timedelta(minutes=15)), _SECRET, algorithm="HS256"
    )

    payload = _verify(token)

    assert payload["iat"] is not None
