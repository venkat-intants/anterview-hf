"""JWT helpers — issue and verify access tokens; manage refresh token lifecycle.

AR-2 (2026-09-28): signing is no longer HS256-only. ``issue_access_token``
still signs with exactly ONE key — a second signing key would be a second thing
to leak — but that key can now be an RSA private key (``algorithm="RS256"``),
with a ``kid`` stamped into the header so a verifier can pick the right public
key without trial-and-error. *Verification* generalises the same way:
``verify_access_token`` takes an ordered sequence of candidates, each either a
bare secret (legacy HS256, tried when the token carries no ``kid``) or a
:class:`VerificationKey` (self-describing algorithm + key + ``kid``, tried only
when its ``kid`` matches the token's header). ``build_verification_keys`` builds
that sequence from a service's ``Settings`` object, and ``resolve_signing_key``
picks the active signing key for an issuer — see both for the exact shape.

Why RS256 and not EdDSA. The original reason was that ``python-jose==3.5.0``'s
``jose.constants.ALGORITHMS`` had no ``EdDSA`` member — true, verified against the
installed package at the time, and NO LONGER THE CONSTRAINT: jose was removed on
2026-10-06 and PyJWT's default algorithms do include ``EdDSA``.

The choice stands, on reasons that do not depend on the library:

* RS256 is deployed nowhere yet — every default is still HS256-only — so changing
  the signing algorithm in the same change that swapped the JWT library would put
  two untested variables into one rollout.
* ``_KNOWN_VERIFY_ALGORITHMS`` below is an ALLOWLIST of ``{"HS256", "RS256"}``, and
  that is what keeps ``none`` and every ``ES*`` unreachable whatever the library
  happens to support. Widening it is the deliberate edit EdDSA would need, and it
  should be made on its own, with its own key-generation and rollout steps.

Only ``data_gateway`` is ever configured with an RSA *private* key
(``jwt_private_key`` — a field that does not even exist on the other three
services' ``Settings`` classes) and is therefore the only process that can
issue an RS256 token. The other three services and the LiveKit worker hold only
``jwt_public_keys`` and can verify, never sign. See
``shared.security.forbid_private_signing_key`` for the loud-failure guard, and
``docs/ACCEPTED-RISKS.md`` AR-2 for the risk this reduces once an operator runs
the rollout below, and the one path it deliberately does not reach
(interview_core's internal service-token mint — see the comment on
``interview_worker.py::_mint_service_jwt`` for why that one path is unchanged).

Rollout sequence for an operator moving a running deployment from HS256 to
RS256 (see also the comment above ``verify_access_token``):

  1. Generate a keypair (``scripts/generate_jwt_rsa_keypair.py``). Set
     ``JWT_PUBLIC_KEYS`` (the new kid -> public key) on ALL FIVE processes
     (four services + the worker), alongside the existing ``JWT_SECRET``.
     Add ``"RS256"`` to ``JWT_VERIFY_ALGORITHMS`` everywhere, keeping
     ``"HS256"`` — e.g. ``JWT_VERIFY_ALGORITHMS=HS256,RS256``. Deploy. Nothing
     issues RS256 yet, so this step is a no-op for live traffic; it only makes
     every verifier capable of accepting the new algorithm once it appears.
  2. On ``data_gateway`` ONLY: set ``JWT_PRIVATE_KEY`` and ``JWT_ACTIVE_KID``
     (the private half of the same keypair) and ``JWT_SIGNING_ALGORITHM=RS256``.
     Redeploy data_gateway. New tokens are now RS256; verifiers everywhere
     already accept them from step 1.
  3. Wait out ``ACCESS_TOKEN_TTL_SECONDS`` (15 minutes) and confirm no verifier
     is still seeing HS256 traffic (there is no drain-signal log event for this
     transition the way key rotation has one — the token TTL is the bound).
  4. Remove ``"HS256"`` from ``JWT_VERIFY_ALGORITHMS`` on all five processes
     (``JWT_VERIFY_ALGORITHMS=RS256``) and redeploy. ``JWT_SECRET`` itself can
     stay set — ``app/auth_tokens.py`` and friends still derive unrelated HMAC
     secrets from it — but it no longer signs or verifies a JWT anywhere.

Getting step 2 and step 4 backwards logs everyone out: signing RS256 before
every verifier can accept it (skipping step 1) means every token minted after
the cutover 401s everywhere except data_gateway itself; dropping HS256 before
waiting out the TTL in step 3 does the same to whatever HS256 traffic is still
in flight.

Claims: ``iss`` and ``aud`` are validated on every decode; ``iat`` is required
because the revocation epoch is compared against it; ``jti`` is required and
must be non-empty. ``iss``/``aud`` have safe defaults so callers that pass
neither keep working.

What ``jti`` does NOT do: it is not checked against a denylist. Nothing in this
repo reads a ``jti`` after the token is minted — it exists so that one token is
distinguishable from another in logs and in an incident timeline. Replay of a
captured access token is contained by two other mechanisms instead: the
15-minute TTL (``ACCESS_TOKEN_TTL_SECONDS``) and the per-user revocation epoch
(``is_token_revoked``), which invalidates every outstanding token for a user the
moment they log out everywhere, reset a password, or are erased. That design is
sound; what was not sound is the docstring this replaces, which asserted
"replay prevention via a Redis blocklist in interview_core" (2026-08 review,
SEC-5). No such blocklist was ever built, and a claim like that is the kind that
makes a reviewer conclude replay is handled and stop looking for the real
control.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import jwt
import structlog
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAlgorithmError,
    InvalidKeyError,
    InvalidTokenError,
    PyJWTError,
)

#: What a CALLER of this module catches. Re-exported deliberately, and the five
#: services import this rather than a name from the JWT library.
#:
#: WHY. ``InvalidKeyError`` is a ``PyJWTError`` but NOT an ``InvalidTokenError`` —
#: measured, not assumed. A caller that reasonably wrote
#: ``except InvalidTokenError -> 401`` would therefore let a key-construction failure
#: escape as a 500 on every authenticated request, which is exactly the defect fixed
#: on 2026-10-06 when ``JWKError`` escaped jose's ``JWTError`` the same way. Catching
#: a name this module controls means the next library swap cannot recreate that bug at
#: five call sites, because there is only one place left to get it wrong.
#:
#: ``verify_access_token`` normalises everything it raises to an ``InvalidTokenError``
#: subclass, so this alias is wider than what actually comes out — on purpose. A
#: caller is better off catching too much than discovering a sibling class in
#: production.
TokenError = PyJWTError

#: Kept so existing ``except JWTError`` call sites keep compiling during a staged
#: rollout. Not for new code: it says "jose" and there is no jose here any more.
JWTError = PyJWTError

#: Re-exported, not caught by name any more — ``InvalidTokenError`` covers it, since
#: an expired token is a claim defect and terminal for the same reason the others are.
#: It stays exported because telling "expired" from "invalid" is a real distinction for
#: a caller and for three test modules, and they should not have to import it from the
#: library directly: that is how a call site ends up depending on which library this
#: module happens to use.
__all__ = [  # noqa: RUF022 — grouped by what it is, not alphabetically
    # What a caller catches
    "TokenError",
    "JWTError",
    "ExpiredSignatureError",
    "InvalidTokenError",
    # The API
    "VerificationKey",
    "build_verification_keys",
    "decode_private_key",
    "encode_key_material",
    "issue_access_token",
    "generate_refresh_token",
    "hash_refresh_token",
    "parse_public_keys",
    "parse_verify_algorithms",
    "resolve_signing_key",
    "verify_access_token",
]

log = structlog.get_logger(__name__)

# Access token TTL is 15 minutes regardless of JWT_EXPIRY_HOURS env setting.
# (The env setting is intentionally kept for legacy compat; Sprint 1 spec
# mandates short-lived access tokens.)
ACCESS_TOKEN_TTL_SECONDS: int = 900  # 15 minutes

# Default iss/aud values — must match JWT_ISSUER / JWT_AUDIENCE in both
# services' settings.  Callers that do not pass explicit values get these
# defaults so the function signature stays backward-compatible.
_DEFAULT_ISSUER: str = "intants-data-gateway"
_DEFAULT_AUDIENCE: str = "intants-services"

# TTL for internal service-to-service tokens. Far shorter than a user session
# because the threat model is different: a service token's `sub` is a service
# name, so it is NOT covered by the auth_epoch kill switch (logout_all only ever
# writes auth_epoch:<user-uuid>). Its lifetime IS its containment — a captured
# one is usable until it expires and there is no way to revoke it early.
SERVICE_TOKEN_TTL_SECONDS: int = 60


def issue_access_token(
    user_id: str,
    roles: list[str],
    secret: str,
    algorithm: str = "HS256",
    *,
    issuer: str = _DEFAULT_ISSUER,
    audience: str = _DEFAULT_AUDIENCE,
    extra_claims: dict[str, Any] | None = None,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
    kid: str | None = None,
) -> str:
    """Sign and return a JWT access token.

    Includes the required claims iss, aud, jti (plus sub/roles/iat/exp).

    Signing takes exactly ONE secret even though verification accepts several:
    a second signing key would be a second thing to leak while buying nothing —
    a rotation window only needs the *verifiers* to straddle two keys.

    The jti (JWT ID) is a fresh uuid4.hex per call. It is for traceability, not
    replay prevention — nothing consults it. See the module docstring for what
    actually contains a replayed token.

    extra_claims: optional additional claims (e.g. a ``session_id`` binding for a
        guest interview token). They are added via setdefault so they can NEVER
        override a standard claim (sub/roles/iss/aud/exp/jti) — defence against a
        caller accidentally forging identity through extra_claims.

    ttl_seconds: defaults to the 15-minute user-session TTL. Service-to-service
        callers should pass ``SERVICE_TOKEN_TTL_SECONDS``. This parameter exists
        because without it the only way to mint a short-lived token was to
        hand-roll the claims dict — which interview_core did, and which is how a
        second implementation of token minting came to exist.

    kid: stamped into the JWT header (not a claim) when given. Meaningless for
        HS256 — every verifier there is handed the one secret out of band and
        there is nothing to select between — but required in practice for
        RS256, where ``verify_access_token`` uses it to pick the right public
        key out of ``JWT_PUBLIC_KEYS`` instead of trying every one in turn.
        ``resolve_signing_key`` supplies it for RS256 callers; leave it ``None``
        for HS256.
    """
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iss": issuer,
        "aud": audience,
        "jti": uuid.uuid4().hex,
    }
    for key, value in (extra_claims or {}).items():
        claims.setdefault(key, value)
    headers = {"kid": kid} if kid else None

    # AR-2's structural guarantee, enforced here rather than left to the library.
    #
    # jose refused to sign with a public key and raised a JOSEError;
    # ``test_jwt_asymmetric.py::test_public_key_cannot_be_used_to_sign_at_all`` pins
    # that as "a service holding only public keys CANNOT mint a token". PyJWT does
    # not: ``RSAAlgorithm.prepare_key`` happily returns an ``RSAPublicKey`` and
    # ``sign`` then raises ``AttributeError: 'RSAPublicKey' object has no attribute
    # 'sign'`` — measured, and NOT a ``PyJWTError``, so it would surface as a 500
    # instead of a refusal.
    #
    # Checked by marker rather than by catching the AttributeError, because the
    # guarantee should not depend on which attribute a future cryptography release
    # happens to expose. ``_validate_rsa_pem`` already rejects the inverse mistake at
    # boot; this is the runtime half, and it is cheap.
    if algorithm.startswith("RS") and _PRIVATE_KEY_MARKER not in secret:
        raise ValueError(
            f"cannot sign with {algorithm}: the key material has no "
            f"{_PRIVATE_KEY_MARKER!r} marker, so it is a PUBLIC key. Only "
            "data_gateway holds a private key — see docs/ACCEPTED-RISKS.md AR-2."
        )

    result: str = jwt.encode(claims, secret, algorithm=algorithm, headers=headers)
    return result


# ---------------------------------------------------------------------------
# Asymmetric key material (AR-2)
#
# A dataclass, some parsing/validation, and the two functions that decide
# "what do I sign with" (resolve_signing_key, data_gateway only) and "what do
# I verify against" (build_verification_keys, every service) from a Settings
# object — see the module docstring for the full rollout sequence.
# ---------------------------------------------------------------------------

_PRIVATE_KEY_MARKER = "PRIVATE KEY"


@dataclass(frozen=True, slots=True)
class VerificationKey:
    """One asymmetric verification candidate: its PEM key, algorithm and ``kid``.

    Passed inside the SAME sequence ``verify_access_token`` already accepted
    for HS256 rotation (``secret: str | Sequence[str | VerificationKey]``) —
    per the brief for this change, building on that rather than inventing a
    parallel parameter. A bare ``str`` in that sequence is still a legacy
    HS256 secret with no ``kid``; a ``VerificationKey`` is everything a bare
    string cannot express: its own algorithm, and a ``kid`` a token's header
    can name to select it instead of being tried in order.
    """

    key: str
    algorithm: str
    kid: str


def encode_key_material(pem: str) -> str:
    """Base64-encode PEM *pem* for a single-line env var.

    The inverse of the decoding in :func:`decode_private_key` /
    :func:`parse_public_keys`. Base64, not raw PEM-with-embedded-newlines,
    because a ``.env`` file, a shell export and most cloud providers' env-var
    UIs each do their own quoting/escaping of multi-line values — the wrong
    place to discover that when the value is a private key. Used by
    ``scripts/generate_jwt_rsa_keypair.py`` so the encoding is defined once and
    the operator-facing script and the config loader can never disagree about
    the format.
    """
    return base64.b64encode(pem.encode("utf-8")).decode("ascii")


def _b64decode_utf8(value: str, *, label: str) -> str:
    """Decode base64 *value* to UTF-8 text, or raise ``ValueError`` naming *label*."""
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is not valid base64-encoded text") from exc


def _validate_rsa_pem(pem: str, *, label: str, want_private: bool) -> None:
    """Raise ``ValueError`` unless *pem* is a well-formed RSA key of the right half.

    Two checks, deliberately both present:

    * ``RSAAlgorithm.prepare_key`` proves the bytes actually parse as an RSA key
      — catches truncated base64, a JSON blob, a double-encoded value, anything
      that plainly is not a PEM key. (Was ``jose.jwk.construct``; same
      accept/reject partition, verified on good public PEM, good private PEM,
      garbage PEM and a plain secret.)
    * The ``PRIVATE KEY`` marker check catches the operator mistake this
      design exists to prevent: pasting the WRONG half of the keypair into the
      WRONG setting. ``jose`` alone would not catch this — an RSA private key
      parses equally well when handed to code that only wants to verify with
      it (the public half is embedded inside a private key), so a private key
      sitting in ``JWT_PUBLIC_KEYS`` would work by accident and quietly defeat
      the entire point of AR-2 without ever raising.
    """
    try:
        RSAAlgorithm(RSAAlgorithm.SHA256).prepare_key(pem)
    except (InvalidKeyError, ValueError, TypeError) as exc:
        # InvalidKeyError is PyJWT's own; ValueError/TypeError come straight out of
        # `cryptography` for malformed DER inside a well-formed PEM envelope, which
        # jose used to wrap. Caught together so a bad key is always a ValueError
        # with our sentence on it, never a library type a caller has to know.
        raise ValueError(f"{label} is not a valid RS256 PEM key: {exc}") from exc
    is_private = _PRIVATE_KEY_MARKER in pem
    if want_private and not is_private:
        raise ValueError(
            f"{label} looks like a PUBLIC key (no {_PRIVATE_KEY_MARKER!r} marker) "
            "but a PRIVATE key is required here."
        )
    if not want_private and is_private:
        raise ValueError(
            f"{label} looks like a PRIVATE key (contains a {_PRIVATE_KEY_MARKER!r} "
            "marker) but only a PUBLIC key belongs here. Only data_gateway may "
            "ever hold a private key — see docs/ACCEPTED-RISKS.md AR-2."
        )


@lru_cache(maxsize=4)
def decode_private_key(raw_b64: str) -> str:
    """Decode+validate ``JWT_PRIVATE_KEY`` (base64 PKCS8 RSA private key PEM).

    Cached: *raw_b64* is a Settings value, fixed for the life of the process,
    and re-validating a PEM key (a real RSA parse) on every token issuance
    would be pure waste. Called both by a config validator — so a malformed
    key fails at boot, not on the first login — and by
    :func:`resolve_signing_key` at issuance time, which then hits the cache.
    """
    pem = _b64decode_utf8(raw_b64, label="JWT_PRIVATE_KEY")
    _validate_rsa_pem(pem, label="JWT_PRIVATE_KEY", want_private=True)
    return pem


@lru_cache(maxsize=16)
def parse_public_keys(raw_json: str) -> dict[str, str]:
    """Parse+validate ``JWT_PUBLIC_KEYS`` -> ``{kid: PEM}``.

    Format: a JSON object mapping a ``kid`` to a base64-encoded RSA public key
    (SubjectPublicKeyInfo PEM) — e.g. ``{"2026-09-28": "<base64>"}``. Multiple
    entries express a rotation: a verifier can accept several kids at once; an
    issuer (:func:`resolve_signing_key`) signs with exactly one.

    Cached for the same reason as :func:`decode_private_key`: *raw_json* is a
    fixed Settings value for the process lifetime, and every entry is
    otherwise re-validated (a real RSA parse) on every authenticated request.
    """
    try:
        raw: Any = json.loads(raw_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"JWT_PUBLIC_KEYS is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("JWT_PUBLIC_KEYS must be a JSON object of {kid: base64-pem}")
    parsed: dict[str, str] = {}
    for kid, encoded in raw.items():
        if not isinstance(kid, str) or not kid:
            raise ValueError("JWT_PUBLIC_KEYS keys (kids) must be non-empty strings")
        if not isinstance(encoded, str):
            raise ValueError(f"JWT_PUBLIC_KEYS[{kid!r}] must be a base64 string")
        pem = _b64decode_utf8(encoded, label=f"JWT_PUBLIC_KEYS[{kid!r}]")
        _validate_rsa_pem(pem, label=f"JWT_PUBLIC_KEYS[{kid!r}]", want_private=False)
        parsed[kid] = pem
    return parsed


_KNOWN_VERIFY_ALGORITHMS = frozenset({"HS256", "RS256"})


@lru_cache(maxsize=8)
def parse_verify_algorithms(raw: str) -> frozenset[str]:
    """Parse+validate ``JWT_VERIFY_ALGORITHMS`` -> the set of accepted families.

    ``"HS256,RS256"`` during the AR-2 transition; ``"RS256"`` once HS256 is
    retired. Never empty — an operator who blanks this out has locked every
    caller out of the platform, and that is a config error to catch at boot,
    not a firewall rule to honour silently.
    """
    algorithms = frozenset(a.strip().upper() for a in raw.split(",") if a.strip())
    if not algorithms:
        raise ValueError("JWT_VERIFY_ALGORITHMS must name at least one algorithm")
    unknown = algorithms - _KNOWN_VERIFY_ALGORITHMS
    if unknown:
        raise ValueError(
            f"JWT_VERIFY_ALGORITHMS names unsupported algorithm(s): {sorted(unknown)}. "
            f"Supported: {sorted(_KNOWN_VERIFY_ALGORITHMS)}"
        )
    return algorithms


def build_verification_keys(settings: Any) -> list[str | VerificationKey]:
    """Build the candidate list ``verify_access_token`` expects, from a
    service's ``Settings`` object.

    One function so every verifier (data_gateway, interview_core,
    feedback_billing's two dependencies, admin_ops) moves together when an
    operator edits ``JWT_VERIFY_ALGORITHMS`` — five call sites that used to
    each read ``settings.jwt_secret``/``settings.jwt_algorithm`` directly is
    exactly the copy-and-drift shape ``shared/security.py``'s own docstring
    describes for the guards that live there.

    Reads, from *settings*:

    * ``jwt_verify_algorithms`` — comma-separated family list (see
      :func:`parse_verify_algorithms`); defaults to ``"HS256"`` if the
      attribute is absent, so a Settings object that predates this change
      keeps its exact current behaviour.
    * ``jwt_secret`` — included, with no ``kid``, when ``"HS256"`` is in the
      family list.
    * ``jwt_public_keys`` — parsed (see :func:`parse_public_keys`) and every
      entry included, each tagged with its own ``kid``, when ``"RS256"`` is in
      the family list. Defaults to ``"{}"`` if the attribute is absent.
    """
    algorithms = parse_verify_algorithms(
        getattr(settings, "jwt_verify_algorithms", "HS256")
    )
    keys: list[str | VerificationKey] = []
    if "HS256" in algorithms:
        secret = getattr(settings, "jwt_secret", "") or ""
        if secret:
            keys.append(secret)
    if "RS256" in algorithms:
        public_keys = parse_public_keys(getattr(settings, "jwt_public_keys", "{}") or "{}")
        keys.extend(
            VerificationKey(key=pem, algorithm="RS256", kid=kid)
            for kid, pem in public_keys.items()
        )
    return keys


def resolve_signing_key(settings: Any) -> tuple[str, str, str | None]:
    """Return ``(algorithm, key, kid)`` to sign a NEW token with, from an
    ISSUER's ``Settings`` object.

    The one place that decides HS256 vs RS256 for signing, so every issuance
    call site in ``data_gateway`` — the only issuer, see the module docstring —
    moves together the moment ``jwt_signing_algorithm`` flips from HS256 to
    RS256, rather than some call sites migrating and others being missed.

    Reads ``jwt_signing_algorithm`` (default ``"HS256"``, so a Settings object
    that predates this change signs exactly as it always did); when it is
    ``"RS256"`` also reads ``jwt_private_key`` (base64 PKCS8 PEM, decoded and
    validated by :func:`decode_private_key`) and ``jwt_active_kid``.

    Only ``data_gateway``'s ``Settings`` class defines ``jwt_private_key`` /
    ``jwt_active_kid`` / ``jwt_signing_algorithm`` at all — the other three
    services' Settings classes have no such fields, so calling this on one of
    theirs hits the ``getattr`` defaults and returns the HS256 tuple; there is
    no private key for them to read even by mistake.

    Raises ``RuntimeError`` (not ``ValueError``) when RS256 signing is
    selected but incompletely configured: this is an ISSUANCE-time check
    reached from a request handler, not a Settings validator, so the failure
    mode a caller should expect is "this request cannot be serviced" rather
    than a Pydantic validation error. The corresponding config validator
    raises ``ValueError`` at boot for the same condition so it is normally
    caught long before any request reaches this function.
    """
    algorithm = str(getattr(settings, "jwt_signing_algorithm", "HS256")).strip().upper()
    if algorithm == "RS256":
        private_key_b64 = getattr(settings, "jwt_private_key", "") or ""
        active_kid = getattr(settings, "jwt_active_kid", "") or ""
        if not private_key_b64 or not active_kid:
            raise RuntimeError(
                "JWT_SIGNING_ALGORITHM=RS256 requires both JWT_PRIVATE_KEY and "
                "JWT_ACTIVE_KID to be set."
            )
        return "RS256", decode_private_key(private_key_b64), active_kid
    return "HS256", str(getattr(settings, "jwt_secret", "")), None


# `_is_signature_failure` lived here and is DELETED, not ported.
#
# It existed because jose reported both the signature stage and the claim stage as a
# bare `JWTError`, so the class could not separate them — it discriminated on whether
# the wrapped cause was a `JWSError` INSTANCE or a plain string. PyJWT gives every
# case its own class, so the question the function answered is answered by the
# `except` clauses in `verify_access_token` instead. Measured mapping:
#
#   bad signature              -> InvalidSignatureError (a DecodeError)
#   malformed header/segments  -> DecodeError
#   alg not in `algorithms`    -> InvalidAlgorithmError (NOT a DecodeError)
#   missing required claim     -> MissingRequiredClaimError
#   wrong iss / aud            -> InvalidIssuerError / InvalidAudienceError
#   expired                    -> ExpiredSignatureError
#   key unusable for the alg   -> InvalidKeyError (NOT an InvalidTokenError)
#
# Keeping a predicate over those would be re-deriving a type hierarchy the library
# already states.


# ---------------------------------------------------------------------------
# Why verification takes a SEQUENCE of secrets (2026-08 review, SEC-1)
# ---------------------------------------------------------------------------
#
# With a single key there is no way to change JWT_SECRET without invalidating
# every live token at the same instant. On a platform whose unit of work is a
# 10-minute voice interview that means dropping in-flight sessions and the
# scorecards they were about to produce — so the rotation gets postponed, and a
# key that can only be rotated by causing an outage is a key that never gets
# rotated. The leak stays live indefinitely. Making the verifier accept several
# keys is the cheap half of the fix: it makes a rotation *window* expressible.
#
#   1. deploy every verifier with secrets = [new, old]; keep signing with old
#   2. flip the signing key to new (redeploy whatever calls issue_access_token)
#   3. wait out ACCESS_TOKEN_TTL_SECONDS and watch
#      `auth.jwt.verified_with_rotated_key` fall to zero — that event is the
#      only evidence that old-key traffic has actually drained
#   4. redeploy with secrets = [new] alone
#
# New key FIRST, always: the drain signal is "verified with something other
# than candidates[0]", so an ordering where the current key is not at index 0
# inverts its meaning and the operator can never tell when step 4 is safe.
#
# Nobody is logged out at any step, and the window is minutes, not days: every
# JWT this platform signs is ≤ ACCESS_TOKEN_TTL_SECONDS (service tokens are
# 60s), and refresh tokens are opaque random strings stored hashed — they are
# not signed with this secret at all, so a refresh arriving mid-window simply
# mints a new-key access token.
#
# AR-2 UPDATE (2026-09-28): implemented. HS256 meant the verification key WAS
# the signing key, so one shared secret across four services plus the worker
# gave read access in ANY of them the power to mint a token for any `sub` with
# any `roles`, `service` included. The answer is asymmetric signing — private
# key in data_gateway only, public key everywhere else, `kid` in the header to
# select it — and that is now what this function does when a caller supplies
# :class:`VerificationKey` candidates rather than bare secrets. Plain ``str``
# candidates (and the ``algorithm`` parameter) are untouched and remain the
# HS256 path described above; the two compose in one sequence rather than
# through a second parameter, because a rollout needs BOTH accepted at once
# (see the module docstring's rollout sequence).
#
# `kid` is the selector: a candidate list can hold both a legacy HS256 secret
# (no `kid`) and one or more RS256 `VerificationKey`s (each with a `kid`). A
# token with no `kid` header is tried only against the kid-less candidate(s);
# a token WITH a `kid` header is tried only against the candidate(s) that
# declare that exact `kid`. An unknown `kid` therefore matches nothing and is
# rejected immediately — it can never fall through and be tried against a
# secret it was never signed with, which is what makes "reject an unrecognised
# kid" a real guarantee rather than an accident of every key failing to match.
def verify_access_token(
    token: str,
    secret: str | Sequence[str | VerificationKey],
    algorithm: str = "HS256",
    *,
    expected_issuer: str = _DEFAULT_ISSUER,
    expected_audience: str = _DEFAULT_AUDIENCE,
) -> dict[str, Any]:
    """Decode and verify a JWT access token.

    Returns the decoded payload dict.

    ``secret`` is a single key, or a sequence of keys tried in order — first
    one whose signature matches wins. Every element is either a bare ``str``
    (a legacy HS256 secret, verified with *algorithm* and never selected by
    ``kid``) or a :class:`VerificationKey` (its own algorithm and ``kid``,
    selected by a matching ``kid`` header rather than tried in sequence). Most
    callers do not build this by hand — see :func:`build_verification_keys`,
    which assembles it from a service's ``Settings`` object.

    Raises:
        JWTError: if the token is invalid, expired, tampered, is missing
                  required claims (iss, aud, jti, iat), names a ``kid`` no
                  supplied candidate declares, or verifies against none of the
                  supplied secrets.

    iss and aud are validated against expected_issuer / expected_audience.
    jti presence is required — absence raises JWTError.

    Security-audit follow-up (2026-08): require_iat is now enforced. The
    "log out all devices" kill switch (app/dependencies.py in every service)
    compares a token's ``iat`` against the user's revocation epoch in Redis —
    a token minted without ``iat`` skipped that comparison entirely (`iat is
    None` was treated as "can't compare, let it through" by some callers) and
    was therefore silently unrevocable. Rejecting it here, at decode time, is
    defence in depth on top of every verifier now also treating a missing
    ``iat`` as revoked.
    """
    # `str` is itself a Sequence[str], so this test has to come before any
    # iteration — otherwise a plain secret "abc" would be tried as the three
    # one-character keys "a", "b", "c" and nothing would ever verify.
    raw_candidates: tuple[str | VerificationKey, ...] = (
        (secret,) if isinstance(secret, str) else tuple(secret)
    )
    # Normalise to (key, algorithm, kid) triples up front so the loop below
    # never has to branch on the candidate's type.
    all_candidates: list[tuple[str, str, str | None]] = [
        (c.key, c.algorithm, c.kid) if isinstance(c, VerificationKey) else (c, algorithm, None)
        for c in raw_candidates
    ]
    if not all_candidates:
        # An empty key list is a deployment mistake, never a bad token. Fail
        # closed, and do it as a JWTError so it lands on every caller's existing
        # `except JWTError -> 401` path instead of escaping as a 500 that a
        # generic handler would file under "server error".
        log.error("auth.jwt.no_verification_secret")
        raise InvalidTokenError("no verification secret configured")

    # `kid`-based selection (see the comment block above this function). A
    # token whose header cannot even be parsed is left UNFILTERED — every
    # candidate is tried, so a malformed token still fails identically against
    # each one, exactly as it did before `kid` existed (see
    # `_is_signature_failure`'s docstring for why "everyone fails the same
    # way" matters during a rotation window).
    try:
        token_kid: str | None = jwt.get_unverified_header(token).get("kid") or None
    except PyJWTError:
        # A token whose header cannot be parsed: PyJWT raises DecodeError here.
        # Caught at the base class because the point is "we could not read a kid",
        # not which way it failed.
        candidates = all_candidates
    else:
        if token_kid is None:
            candidates = [c for c in all_candidates if c[2] is None]
        else:
            candidates = [c for c in all_candidates if c[2] == token_kid]
        if not candidates:
            log.warning("auth.jwt.unknown_kid", kid=token_kid)
            detail = (
                f"no verification key for kid={token_kid!r}"
                if token_kid
                else "no legacy (kid-less) verification key configured"
            )
            raise InvalidTokenError(detail)

    # PyJWT's options dict. THE SPELLING IS LOAD-BEARING AND WAS A TRAP.
    #
    # This was jose's `{"require_exp": True, "require_iss": True, ...}`. PyJWT takes a
    # LIST under one `require` key — and it SILENTLY IGNORES option keys it does not
    # recognise. Measured against the pinned 2.15.1: a token carrying only `sub` and
    # `exp` decoded clean under the jose-style dict, with no error and no warning. A
    # copy-paste port therefore switches all five required-claim checks off while
    # every test that mints a COMPLETE token stays green.
    # `shared/tests/test_jwt_required_claims.py` was written before this change to
    # make that impossible; if you edit this dict, read that file first.
    #
    # `verify_iat: False` is the second deliberate choice. PyJWT rejects a FUTURE
    # `iat` with ImmatureSignatureError at +1 SECOND (measured); jose did not reject
    # it at all. Every verifier is a different process from the issuer, usually a
    # different host, so a verifier whose clock trails by one second would start
    # 401-ing freshly minted tokens — a platform-wide outage this repo has never had.
    # Turning the check off does NOT weaken the `iat` requirement: `require` enforces
    # PRESENCE independently of `verify_iat` (also measured), and presence is all the
    # revocation epoch needs. The alternative, a `leeway`, would widen `exp` by the
    # same amount, which is the wrong thing to trade.
    decode_options: dict[str, Any] = {
        "require": ["exp", "iss", "aud", "jti", "iat"],
        "verify_iat": False,
    }
    errors: list[PyJWTError] = []
    for index, (candidate_key, candidate_algorithm, _candidate_kid) in enumerate(candidates):
        try:
            payload = dict(
                jwt.decode(
                    token,
                    candidate_key,
                    algorithms=[candidate_algorithm],
                    audience=expected_audience,
                    issuer=expected_issuer,
                    options=decode_options,
                )
            )
        except DecodeError as exc:
            # The ONLY family a later key can do better on: a bad signature
            # (InvalidSignatureError, which is a DecodeError) and a malformed token.
            # Collect and try the next candidate.
            #
            # A malformed token is key-independent, so retrying it is wasted work but
            # not wrong — every candidate fails it identically and the loop ends at
            # `errors[0]`, which is the right report for something that is not a JWT.
            errors.append(exc)
            continue
        except InvalidAlgorithmError as exc:
            # The token's `alg` is not the one this candidate is configured for.
            # COLLECTED rather than raised, to preserve jose's behaviour exactly:
            # jose wrapped this as JWTError(JWSError(...)), which `_is_signature_failure`
            # classed as retryable. During an HS256->RS256 rotation the candidate list
            # holds both, and an HS256 token must be allowed to reach the HS256
            # candidate rather than being refused by the RS256 one it was offered to
            # first. Raising here would be defensible on its own terms and is a
            # BEHAVIOUR CHANGE, so it is not made silently.
            errors.append(exc)
            continue
        except InvalidKeyError as exc:
            # THE SAME TRAP, IN A SECOND LIBRARY. Under jose this was `JWKError`,
            # which is not a subclass of `JWTError` — both derive from `JOSEError` —
            # so a key that failed CONSTRUCTION for its algorithm escaped this loop
            # entirely, past every caller's `except JWTError -> 401`, and became a
            # 500 on every authenticated request across all four services
            # (review 2026-10-06). PyJWT has the identical shape, measured:
            #
            #     issubclass(InvalidKeyError, PyJWTError)        -> True
            #     issubclass(InvalidKeyError, InvalidTokenError) -> False
            #
            # Which is why this module re-exports `TokenError = PyJWTError` and the
            # five services catch THAT rather than a name from the library. Two
            # libraries in a row have put the one exception meaning "this key cannot
            # be used" outside the family a caller would naturally catch, so the
            # defence belongs here, once, and not at five call sites.
            #
            # The operator mistake that gets you here is the mirror of the one
            # `forbid_private_signing_key` exists to catch: PEM key material pasted
            # into JWT_SECRET. `assert_strong_secrets` accepts it (long enough, no
            # placeholder marker), the service boots, and then the library says
            # "asymmetric key ... should not be used as an HMAC secret" on every
            # request with no auth log line to point at it. PyJWT 2.15.1 says the same
            # for BARE DER material, which jose accepted — that is CVE-2026-85394's
            # root cause, and taking this library is what closes it.
            #
            # Re-raised as `InvalidTokenError` so it lands on the 401 path, and logged
            # first, exactly as the empty-candidate-list branch above does
            # deliberately. A 401 with nothing in the log would move the outage from
            # "500s everywhere" to "nobody can log in and nothing says why", which is
            # not an improvement — and the library's own sentence is the only thing
            # that names what the operator actually did. It describes the key's SHAPE,
            # never its bytes, which is why it is safe to log here.
            log.error(
                "auth.jwt.key_unusable",
                error_type=type(exc).__name__,
                error=str(exc),
                key_index=index,
            )
            raise InvalidTokenError(
                f"key could not be used for verification: {exc}"
            ) from exc
        except InvalidTokenError:
            # Everything else: the signature MATCHED this key and the token then
            # failed a claim rule — expired, wrong issuer, wrong audience, a missing
            # required claim. PyJWT verifies the signature before validating claims
            # (measured: an expired token under the wrong key yields
            # InvalidSignatureError, not ExpiredSignatureError), so reaching here
            # means this key is genuinely the token's and no later key can do better.
            #
            # Raised, not collected, and that is the fix a previous review paid for:
            # collecting it meant `errors[0]` was raised instead — candidates[0]'s
            # "Signature verification failed" — so a token rejected for a real,
            # nameable claim defect was reported as a key mismatch during the one
            # window (a rotation) when a key mismatch is what the operator is already
            # hunting.
            raise

        # Explicit defence-in-depth check: `require` only tests PRESENCE — both jose
        # and PyJWT compare against None — so `jti: ""` satisfies it. Guard against
        # that edge case explicitly. Raised rather than collected, for the same reason
        # as the claim errors above: the signature already matched this key.
        if not payload.get("jti"):
            raise InvalidTokenError("jti claim is empty")

        if index > 0:
            # The signal that lets a rotation actually be finished. While this
            # fires, tokens signed with a superseded key are still in
            # circulation and the trailing secret must stay deployed; when it
            # stops, dropping that secret is safe. Without it, "has everyone
            # rotated yet?" is unanswerable and the old key stays valid forever.
            log.info("auth.jwt.verified_with_rotated_key", key_index=index)
        return payload

    # Every collected error is now a signature-layer failure — a claim failure
    # against a key that DID match is raised at the point it happens, because
    # only that key's opinion is worth reporting. So what is left here is "this
    # token verified against none of the eligible candidates" (the `kid` filter
    # above already narrowed that set), and candidates[0] is either the current
    # HS256 signing key (no `kid` in play) or the one key this token's `kid`
    # named, which makes its message the one an operator should read first.
    # The list is non-empty: the loop is entered at least once and every other
    # exit returns or raises.
    raise errors[0]


# ---------------------------------------------------------------------------
# Revocation epoch — the "log out all devices" kill switch
# ---------------------------------------------------------------------------
#
# Redis key prefix for the per-user revocation epoch. Any access token whose
# ``iat`` predates this value is revoked, so logout_all / password reset / admin
# delete / DPDP erasure take effect immediately rather than waiting out the
# 15-minute access-token TTL.
#
# It lives HERE, not in shared/auth/local.py, for one reason: local.py imports
# bcrypt at module scope, and three of the four services do not ship bcrypt. So
# every verifier except data_gateway's re-declared the literal with a comment
# saying "kept in sync — do not change". Six copies held together by a comment
# is not synchronisation. jwt.py has no bcrypt in its import graph, so every
# service can import the real constant.
#
# local.py re-exports this name, so its existing importers are unaffected.
USER_TOKEN_EPOCH_PREFIX: str = "auth_epoch:"


class _RedisGet(Protocol):
    """The one Redis method the revocation check needs.

    A Protocol rather than ``redis.asyncio.Redis`` so this module imports no
    Redis client at all: each service passes its own, and a test passes a dict
    wrapper. Typing it structurally is what keeps this helper importable from any
    of the four service images regardless of which client they ship.
    """

    async def get(self, key: str) -> Any: ...


async def is_token_revoked(
    redis_factory: Callable[[], _RedisGet], user_id: str, iat: Any
) -> bool:
    """True if *iat* predates the user's revocation epoch.

    Takes a FACTORY, not a client. Each service's ``get_redis()`` raises
    ``RuntimeError("Redis not initialised")`` when the app has no lifespan — and
    every local copy of this check called ``get_redis()`` INSIDE its try block,
    so that error was part of what fail-open absorbed. Accepting an already-
    resolved client would move the call outside the protected region and turn a
    fail-open path into a 500. Pass the function itself: ``is_token_revoked(
    get_redis, ...)``, not ``is_token_revoked(get_redis(), ...)``.

    The single implementation of a check that existed in five places, one of
    which had drifted: ``feedback_billing``'s scorecard-list copy shipped without
    it, so "log out all devices", password reset, HR account deletion and DPDP
    erasure all failed to revoke access to scorecard history until 40df357.

    FAILS OPEN — any Redis error, or a missing/unparseable epoch, returns False.
    This is deliberate and unchanged from every copy it replaces. The real auth
    control is the signature and ``exp``, both verified locally with no Redis
    involved; the epoch only *accelerates* revocation. Failing closed would make
    cache availability equal platform availability, turning a bounded 15-minute
    revocation delay into a total outage. The trade-off worth knowing during an
    incident: this and the per-IP rate limiter share a Redis and therefore fail
    open together.

    Consolidating also makes that fail-open state ALERTABLE, which is the point:
    five copies logged five different event names, so no single alert could say
    "revocation is not currently being enforced". There is now one:
    ``auth.token_epoch.check_skipped``.

    A missing or non-integer ``iat`` counts as REVOKED. verify_access_token sets
    require_iat, so a token reaching here without one is already anomalous, and
    treating it as unrevocable was the exact hole the 2026-08 audit closed.
    """
    try:
        raw = await redis_factory().get(USER_TOKEN_EPOCH_PREFIX + user_id)
    except Exception as exc:  # noqa: BLE001 — fail open on any Redis/client error
        log.warning("auth.token_epoch.check_skipped", error_type=type(exc).__name__)
        return False

    if raw is None:
        return False
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        # A corrupt epoch value must not lock the user out; treat as unset.
        log.warning("auth.token_epoch.unparseable")
        return False

    if iat is None:
        return True
    try:
        # `<=`, not `<`. Both are whole-second Unix timestamps and logout_all
        # sets epoch = now(), so a token minted in the SAME second as the
        # revocation has iat == epoch. Under a strict `<` it survived its full
        # 15-minute TTL immediately after the user asked to be logged out
        # everywhere.
        return int(iat) <= epoch
    except (TypeError, ValueError):
        return True


def generate_refresh_token() -> str:
    """Return a cryptographically random opaque refresh token (URL-safe, 48 bytes)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Return SHA-256 hex digest of the raw refresh token."""
    return hashlib.sha256(token.encode()).hexdigest()
