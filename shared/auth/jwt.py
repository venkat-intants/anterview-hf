"""JWT helpers — issue and verify access tokens; manage refresh token lifecycle.

Signing is HS256 with a single key. *Verification* accepts either that one key
or a sequence of keys tried in order, which is what makes rotating a leaked
``JWT_SECRET`` possible without logging every candidate out mid-interview — the
procedure is written out above ``verify_access_token``.

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

import hashlib
import secrets
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWSError, JWTClaimsError

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
    result: str = jwt.encode(claims, secret, algorithm=algorithm)
    return result


def _is_signature_failure(exc: JWTError) -> bool:
    """True when *exc* came from the SIGNATURE layer, so another key may help.

    ``jwt.decode`` runs in two stages and reports both as a bare ``JWTError``,
    which is why the class alone cannot separate them:

    * the JWS stage (signature, header parsing) re-raises as
      ``JWTError(JWSError(...))`` — the cause is an exception INSTANCE
    * the claim stage raises ``JWTError("missing required key \\"iat\\" ...")``
      directly — the argument is a plain string

    So the wrapped cause is the discriminator, and it is a type check rather
    than a match on message text: jose is free to reword "Signature
    verification failed." in a patch release, and a verifier that silently
    changed meaning when it did would be worse than no check.

    A malformed token also lands here (``Invalid header string``) and is
    key-independent, so retrying it against the remaining keys is wasted work
    but not wrong — every candidate fails it identically and the loop ends at
    ``errors[0]``, which is the right report for a token that is not a JWT.
    """
    return bool(exc.args) and isinstance(exc.args[0], JWSError)


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
# This deliberately does NOT address SEC-2. HS256 means the verification key IS
# the signing key, so one shared secret across four services plus the worker
# gives read access in ANY of them the power to mint a token for any `sub` with
# any `roles`, `service` included. The answer to that is asymmetric signing —
# private key in data_gateway only, public key everywhere else, `kid` in the
# header to select it — which is a Tier-2 change with a key-distribution story
# of its own, not something to smuggle in behind a parameter type.
def verify_access_token(
    token: str,
    secret: str | Sequence[str],
    algorithm: str = "HS256",
    *,
    expected_issuer: str = _DEFAULT_ISSUER,
    expected_audience: str = _DEFAULT_AUDIENCE,
) -> dict[str, Any]:
    """Decode and verify a JWT access token.

    Returns the decoded payload dict.

    ``secret`` is either a single key (what every caller passes today) or a
    sequence of keys tried in order — first one whose signature matches wins.

    Raises:
        JWTError: if the token is invalid, expired, tampered, is missing
                  required claims (iss, aud, jti, iat), or verifies against
                  none of the supplied secrets.

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
    candidates: tuple[str, ...] = (secret,) if isinstance(secret, str) else tuple(secret)
    if not candidates:
        # An empty key list is a deployment mistake, never a bad token. Fail
        # closed, and do it as a JWTError so it lands on every caller's existing
        # `except JWTError -> 401` path instead of escaping as a 500 that a
        # generic handler would file under "server error".
        log.error("auth.jwt.no_verification_secret")
        raise JWTError("no verification secret configured")

    # python-jose options dict: each "require_<claim>" key forces the claim to
    # be present; combining with audience/issuer args also validates values.
    # jose supports require_exp, require_iss, require_aud, require_jti etc.
    decode_options: dict[str, bool] = {
        "require_exp": True,
        "require_iss": True,
        "require_aud": True,
        "require_jti": True,
        "require_iat": True,
    }
    errors: list[JWTError] = []
    for index, candidate in enumerate(candidates):
        try:
            payload = dict(
                jwt.decode(
                    token,
                    candidate,
                    algorithms=[algorithm],
                    audience=expected_audience,
                    issuer=expected_issuer,
                    options=decode_options,
                )
            )
        except (ExpiredSignatureError, JWTClaimsError):
            # python-jose verifies the signature BEFORE validating claims, so
            # these two are only reachable once a key has matched: the token is
            # genuinely ours and no later key can do better. Stop here, or a
            # rotation window would turn every "expired" into the next key's
            # "signature verification failed" and send an incident responder
            # hunting a key mismatch that does not exist.
            raise
        except JWTError as exc:
            if _is_signature_failure(exc):
                # The only error a LATER key can do better on. Collect and move
                # to the next candidate.
                errors.append(exc)
                continue
            # Any other JWTError means this key MATCHED and the token then failed
            # a claim rule — the missing-``iat`` case above all, which jose
            # reports as a bare JWTError ('missing required key "iat" among
            # claims') rather than as JWTClaimsError, so the two-exception clause
            # above never caught it. Collecting it meant `errors[0]` was raised
            # instead: candidates[0]'s "Signature verification failed", i.e. a
            # token rejected for a real, nameable claim defect was reported to
            # the operator as a key mismatch, during the one window (rotation)
            # when a key mismatch is what they are already looking for.
            raise

        # Explicit defence-in-depth check: jose raises JWTError when
        # require_jti=True and jti is missing, but an empty string would pass
        # the require check. Guard against that edge case explicitly. Raised
        # rather than collected, for the same reason as the claim errors above:
        # the signature already matched this key.
        if not payload.get("jti"):
            raise JWTError("jti claim is empty")

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
    # token verified against none of the deployed secrets", and candidates[0] is
    # the current signing key, which makes its message the one an operator
    # should read first. The list is non-empty: the loop is entered at least
    # once and every other exit returns or raises.
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
