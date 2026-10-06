"""Config-time security guards, shared by all four services (fail-fast).

Four guards live here rather than in each service's ``config.py``:

``normalise_app_env``
    Strips, lowercases and expands abbreviations of ``APP_ENV``. Every production
    gate in this codebase tests membership of ``ENFORCED_ENVS``, so
    ``APP_ENV=Production`` silently bypassed all of them — including
    ``assert_strong_secrets`` below, which means a one-character typo was enough
    to boot production with a placeholder JWT secret and no complaint. Casing was
    fixed first and the abbreviations were missed: ``"PROD".lower()`` is
    ``"prod"``, which is not ``"production"``, so the value an operator is most
    likely to type by hand was still a silent full bypass.

``assert_strong_secrets``
    A known ``JWT_SECRET`` lets anyone forge tokens and a known
    ``CONSENT_IP_SALT`` defeats DPDP IP-hashing. Refuse to start rather than run
    insecure.

``validate_cors_origins``
    Wildcard origins are incompatible with ``allow_credentials=True``.

``validate_database_ssl``
    An unencrypted database link carries candidate PII in cleartext (DPDP §8).
    It also owns the ``loopback-exempt`` acknowledgement token, and therefore
    has to be handed ``DATABASE_URL``: the exemption claims TLS is terminated
    upstream *on this machine*, which is only true of a loopback address or a
    local unix socket. Without the URL the validator accepted the token for any
    endpoint, so one env var disabled database TLS to a remote Neon instance in
    production.

``forbid_private_signing_key``
    AR-2 (asymmetric JWT signing): ``data_gateway`` is the only issuer and the
    only service whose ``Settings`` class even declares a ``jwt_private_key``
    field. The other three services' configs have no such field, so
    ``pydantic-settings``' ``extra="ignore"`` would otherwise make a
    ``JWT_PRIVATE_KEY`` set on one of them by mistake (a copy-pasted .env is
    exactly how this repo's own history says that happens) silently do
    nothing — which is safe, but invisible. This makes the same mistake loud:
    call it from every Settings class EXCEPT data_gateway's.

All five are deliberate NO-OPs (or permissive) in development/test so local
runs and the suite are unaffected — except ``forbid_private_signing_key``,
which has no environment exemption: a non-issuer holding a private key is
wrong in every environment, not just the hardened ones.

Why shared and not copied into each config: they WERE copied, and they drifted.
``normalise_app_env`` existed only in ``interview_core`` and ``validate_cors_origins``
only in ``interview_core`` and ``data_gateway``, so the two services that
enforce neither were the ones a capitalised ``APP_ENV`` would have walked
straight past. A guard that exists in some services is a guard you cannot reason
about. ``shared/security.py`` is stdlib-only and already imported by all four
configs, so there is no dependency cost to putting them here.

``validate_database_ssl`` is the same story caught a second time: it lived in
``data_gateway`` alone, so three services could boot in production against a
plaintext Postgres link and say nothing (XS-04, CWE-319).
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from urllib.parse import parse_qs, unquote, urlsplit

# Substrings that mark a value as an unrotated placeholder. Real secrets are
# random hex (token_hex) which can never contain these, so false positives on a
# genuine secret are effectively impossible.
_PLACEHOLDER_MARKERS = (
    "change-me",
    "placeholder",
    "your-",
    "replace-me",
    "example",
    "todo",
)

# Minimum length for a 256-bit-class secret expressed as hex/base64 text.
_MIN_SECRET_LEN = 32

# PUBLIC on purpose. This is the package's single answer to "which environments
# are hardened", and it is exported so other guards can *reuse* it instead of
# restating the literal. ``shared/metrics_auth.py`` did restate it — as
# ``== "production"`` only — so two guards one file apart disagreed about
# whether staging was hardened (SEC-9). Import this; never re-type the tuple.
ENFORCED_ENVS = ("production", "staging")

# The value an operator sets to acknowledge, in writing, that TLS is terminated
# upstream of the app. Named rather than inlined because ``validate_database_ssl``
# has to both accept it and strip it, and the two must be the same string.
DATABASE_SSL_LOOPBACK_EXEMPT = "loopback-exempt"

# libpq/asyncpg SSL modes that do NOT guarantee an encrypted link. These are the
# one exception to this module's "permissive about the exact value" rule, and the
# reason is that they are the only non-empty values that defeat the guard while
# satisfying it: the production check is "did the operator set something", and
# every one of these IS something.
#
# ``prefer`` is the dangerous one and the reason this list exists. It is libpq's
# own default, it reads as security-conscious, and it silently DOWNGRADES to
# plaintext whenever the server does not offer TLS — so a misconfigured or
# impersonated endpoint gets candidate PII in clear with no error anywhere.
# ``allow`` is the same downgrade with the preference inverted; ``disable``
# refuses TLS outright.
#
# ``require`` and everything above it (``verify-ca``, ``verify-full``) are absent
# because they all encrypt; they differ only in how much of the certificate they
# check, which is a separate hardening decision this guard does not make. Values
# outside this set stay permissive on purpose — enumerating every valid mode here
# would mean re-releasing ``shared`` each time the driver grows one, and an
# unrecognised value fails at connect time rather than silently going plaintext.
_INSECURE_DATABASE_SSL_MODES = frozenset({"disable", "allow", "prefer"})

# Shorthands an operator plausibly types for a hardened environment, mapped to
# the canonical spelling ``ENFORCED_ENVS`` holds. Two deliberate limits:
#
# * Only the HARDENING direction is mapped. ``dev``/``tst``/``testing`` are
#   absent on purpose. An unrecognised value already behaves as "not hardened",
#   so aliasing them towards development buys no safety — while a wrong guess in
#   that direction would switch ON a permissive branch (open ``/metrics``,
#   dev-only auth shortcuts) for a value the operator never meant. Guessing
#   towards production merely over-hardens, and over-hardening fails loudly at
#   boot with a message naming the variable; guessing away from it fails
#   silently, which is the whole defect.
# * Exact match, never a prefix. ``pre-prod``, ``prod-mirror`` and ``staging-2``
#   are not the thing they resemble, and a ``startswith("prod")`` rule would
#   quietly claim all of them.
_APP_ENV_ALIASES = {
    "prod": "production",
    "prd": "production",
    # "live" is not an abbreviation but it is unambiguous English for the
    # environment real users hit, and the cost of being wrong is a boot failure
    # that says exactly what to set.
    "live": "production",
    "stage": "staging",
    "stg": "staging",
}


def strip_pasted_settings(values: object) -> object:
    """Strip whitespace a paste added, across EVERY setting at once.

    Use as ``@model_validator(mode="before")`` on each service's Settings. This
    replaced a hand-written list of variable names, which was the wrong shape:
    the list missed ``GROQ_MODEL``, and the failure came back a third time. Any
    setting can be pasted, so every setting is covered — an allowlist of the
    ones someone remembered is a guarantee of another outage, just later.

    Two rules, because the safe treatment differs:

    * Every string value has its ENDS stripped. Leading/trailing whitespace is
      never meaningful in anything we accept, and ends-only is safe even for a
      multi-line PEM (``JWT_PRIVATE_KEY``), whose internal newlines are load
      bearing and must survive.
    * A value whose field name ends in ``_api_key`` additionally has ALL inner
      whitespace removed. These go straight into an ``Authorization`` header,
      HTTP forbids newlines there, and no provider key contains whitespace
      anywhere — so a key split across two lines by a wrapped paste is repaired
      rather than sent as an illegal header. This is deliberately NOT applied to
      passwords or DSNs, where an inner space can be genuine.

    The incidents behind this, all one root cause and all invisible in the
    settings UI: ``DATABASE_URL`` with a trailing newline (``database "neondb\\n"
    does not exist`` — every route 503); ``GROQ_API_KEY`` split mid-value by a
    wrapped paste (``Illegal header value``, which also printed the key into a
    user-facing error); ``GROQ_MODEL`` with a trailing newline (``The model
    `openai/gpt-oss-120b\\n` does not exist``).
    """
    if not isinstance(values, dict):
        return values
    cleaned: dict[object, object] = {}
    for name, value in values.items():
        if isinstance(value, str):
            value = value.strip()
            if isinstance(name, str) and name.lower().endswith("_api_key"):
                value = "".join(value.split())
        cleaned[name] = value
    return cleaned


def normalise_app_env(value: object) -> str:
    """Canonicalise ``APP_ENV``: strip, lowercase, then expand known shorthands.

    Use as a ``@field_validator("app_env", mode="before")`` in every service's
    Settings. Security gates test membership of :data:`ENFORCED_ENVS`, i.e. the
    literal strings ``"production"`` and ``"staging"``, so any value that *means*
    production without being spelled that way opens all of them at once:
    :func:`assert_strong_secrets`, :func:`validate_database_ssl` and the
    ``/metrics`` gate in ``shared.metrics_auth``.

    Casing alone was handled before; the shorthands were not, and ``PROD`` is the
    ordinary thing to type. ``_APP_ENV_ALIASES`` (above) says which shorthands
    are recognised and why the table only ever points *towards* hardening.

    Anything outside that table is returned stripped and lowercased but otherwise
    untouched — ``sandbox`` stays ``sandbox`` — so an unknown value can never
    become production and break a local run.
    """
    if not isinstance(value, str):
        value = str(value)
    normalised = value.strip().lower()
    return _APP_ENV_ALIASES.get(normalised, normalised)


def validate_cors_origins(value: str) -> str:
    """Reject wildcard and non-http(s) CORS origins.

    RFC 6454 and the CORS spec forbid combining credentials with ``*``, and every
    service in this platform sets ``allow_credentials=True``. Browsers enforce
    this too — the practical effect of a wildcard here is that auth breaks in a
    confusing way, so failing at boot with a clear message is strictly better.

    Returns *value* unchanged when valid; raises ValueError otherwise.
    """
    origins = [o.strip() for o in value.split(",") if o.strip()]
    for origin in origins:
        if origin in ("*", "null"):
            raise ValueError(
                "CORS allow_credentials=True is incompatible with wildcard '*' origin"
            )
        if not origin.startswith(("http://", "https://")):
            raise ValueError(
                f"CORS origin {origin!r} must start with http:// or https://"
            )
    return value


def is_weak_secret(value: str | None) -> bool:
    """True if *value* is empty, too short, or contains a placeholder marker."""
    v = (value or "").strip()
    if len(v) < _MIN_SECRET_LEN:
        return True
    low = v.lower()
    return any(marker in low for marker in _PLACEHOLDER_MARKERS)


def assert_strong_secrets(app_env: str | None, secrets: dict[str, str | None]) -> None:
    """Raise ValueError if any named secret is weak — production/staging only.

    ``secrets`` maps a human-facing name (e.g. ``"JWT_SECRET"``) to its value.
    No-op when ``app_env`` is not production/staging (dev/test pass untouched).
    """
    if normalise_app_env(app_env or "") not in ENFORCED_ENVS:
        return
    weak = sorted(name for name, value in secrets.items() if is_weak_secret(value))
    if weak:
        raise ValueError(
            f"Refusing to start in {app_env!r}: weak or placeholder secret(s): "
            f"{', '.join(weak)}. Set strong random values — generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )


# The only env-var spelling this guard recognises. Pydantic-settings' own
# ``case_sensitive=False`` matching is irrelevant here — this function reads
# the process environment directly, on purpose, because the field it is
# guarding against does not exist for the services that call it (see below),
# so there is no Settings attribute to inspect.
_PRIVATE_KEY_ENV_VAR = "JWT_PRIVATE_KEY"


def forbid_private_signing_key(service_name: str, env: Mapping[str, str] | None = None) -> None:
    """Refuse to boot if THIS process has been handed a JWT private key (AR-2).

    Asymmetric signing draws a hard line: ``data_gateway`` is the only issuer
    and the only service whose ``Settings`` class declares a ``jwt_private_key``
    field at all. Every other service's ``Settings`` simply has no attribute to
    populate, so ``pydantic-settings``' own ``extra="ignore"`` would otherwise
    make a stray ``JWT_PRIVATE_KEY`` in this process's environment silently do
    nothing — safe, but invisible, and the exact way a production .env gets
    copy-pasted between services in this codebase's own incident history. This
    is the loud version of that safety: if a private key reaches an
    environment it was never meant to, refuse to start rather than quietly
    ignoring it, so the mistake is caught at the next deploy instead of never.

    Call from every Settings class EXCEPT data_gateway's, in a
    ``@model_validator(mode="after")``, passing ``self.service_name``. No
    environment exemption (unlike ``assert_strong_secrets``): a non-issuer
    holding a private key is wrong in development too, and dev is exactly
    where an operator wants to find out, not in production.

    :param env: defaults to ``os.environ``; overridable so a test can assert
        the guard fires without mutating real process environment.
    """
    source = env if env is not None else os.environ
    # Matched case-insensitively and whitespace-tolerantly on the NAME, not by
    # an exact `source.get(...)`. Security review, 2026-09-28: an exact match
    # meant `jwt_private_key` or `"JWT_PRIVATE_KEY "` (trailing space in the
    # name) sailed past the guard on Linux, where env names are case-sensitive
    # — landing in precisely the silent-ignore failure mode this function
    # exists to make loud. It was not exploitable, because a non-issuer's
    # Settings has no `jwt_private_key` field to populate whatever the name's
    # casing; but a boot alarm that only fires for one spelling is not one to
    # rely on during the rollout it exists to protect.
    for name, value in source.items():
        if name.strip().upper() != _PRIVATE_KEY_ENV_VAR:
            continue
        if (value or "").strip():
            raise ValueError(
                f"{service_name!r} must never hold {_PRIVATE_KEY_ENV_VAR} "
                f"(found as {name!r}) — only data_gateway signs tokens (see "
                "docs/ACCEPTED-RISKS.md AR-2). Remove it from this service's "
                "environment; verification only ever needs JWT_PUBLIC_KEYS."
            )


# Names for the local machine that ``ipaddress`` cannot classify because they
# are not IP literals. Kept short and exact rather than "anything resolving to
# 127.0.0.1": a config-time guard must not do DNS, and a name whose answer can
# change between boot and connect is not evidence of anything.
_LOOPBACK_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


def _database_host(database_url: str | None) -> str | None:
    """Host that *database_url* connects to, or ``None`` when that is unknowable.

    ``None`` means "cannot tell" and every caller must read it as *remote*. An
    unset or unparseable URL is not evidence of a loopback socket, and this
    function's answer is the only thing standing between an operator's
    ``loopback-exempt`` and a plaintext link across the internet.
    """
    raw = (database_url or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        host = parts.hostname
    except ValueError:
        # Malformed authority — a non-numeric port, a broken IPv6 literal.
        return None
    if not host:
        # libpq/asyncpg spell a unix socket either with an empty authority and
        # ``?host=/var/run/postgresql``, or with the directory percent-encoded
        # into the authority itself. Both must be recognised, or the one shape
        # the exemption exists to serve gets rejected.
        host = parse_qs(parts.query).get("host", [""])[0]
    host = unquote(host or "").strip()
    return host or None


def _is_loopback_database(database_url: str | None) -> bool:
    """True only when the database link provably cannot leave the machine."""
    host = _database_host(database_url)
    if host is None:
        return False
    if host.startswith("/"):
        # A unix-domain socket path. No network involved, so nothing to encrypt.
        return True
    if host.lower() in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an IP literal and not a known local name: treat as remote.
        return False


def validate_redis_tls(app_env: str | None, redis_url: str | None) -> str:
    """Require TLS on the Redis link in production/staging (DPDP §8, CWE-319).

    The third copy of a gap this module exists to close. ``validate_database_ssl``
    above was itself the second — its docstring records the same drift — and
    Redis never got one at all: ``redis_url`` is declared as a bare ``str`` in all
    four services with no validator, and ``shared/redis_factory`` takes the scheme
    from the URL without looking at it. A ``redis://`` link in a hardened
    environment is accepted in silence and sends the AUTH token in cleartext on
    the first command.

    WHY THIS ONE IS WORTH A BOOT FAILURE. Write access to this Redis is account
    takeover for any user whose id is known, because the refresh store lives
    here: write ``refresh:<sha256(R)>`` = ``"<victim_uuid>:<now>"`` for a chosen
    ``R``, then POST it to ``/auth/refresh`` in the BODY (the body path skips the
    CSRF check by design) and the provider mints an access token for that user.
    Deleting the ``auth_epoch:<uuid>`` keys additionally defeats "log out all
    devices", password change and the DPDP erasure session purge. So the
    credential travelling in plaintext is not one secret among many.

    Returns *redis_url* unchanged when valid; raises ValueError otherwise.

    Loopback is exempt for the same reason it is on the database link — a socket
    that cannot leave the machine has nothing to encrypt — and that exemption is
    derived from the host, never from an operator's assertion about it.
    """
    env = normalise_app_env(app_env)
    if env not in ENFORCED_ENVS:
        return redis_url or ""
    url = (redis_url or "").strip()
    if not url:
        # Absence is another validator's problem; this one is about the scheme.
        return url
    if url.startswith("rediss://"):
        return url
    if _is_loopback_redis(url):
        return url
    raise ValueError(
        f"APP_ENV={app_env!r} requires a TLS Redis link: REDIS_URL must use "
        "rediss:// (or point at loopback). A redis:// link to a managed "
        "instance sends the AUTH token and every session key in cleartext, and "
        "write access to this store is account takeover (DPDP §8, CWE-319)."
    )


def _is_loopback_redis(redis_url: str) -> bool:
    """True only when the Redis link provably cannot leave the machine."""
    try:
        host = urlsplit(redis_url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_database_ssl(
    app_env: str | None,
    database_ssl: str | None,
    database_url: str | None = None,
) -> str:
    """Require TLS on the database link in production/staging (DPDP §8, CWE-319).

    Returns the value to STORE — not a bool — because the check and the
    normalisation are one decision: ``loopback-exempt`` is an acknowledgement
    token for the validator, never a value asyncpg can understand, so whoever
    enforces the rule must also be the one to strip it. Splitting them invites a
    service that validates and then hands the sentinel to the driver.

    The check is intentionally permissive about the exact value (``require``,
    ``verify-full``, a CA path …): the failure this catches is an operator who
    set *nothing*, and enumerating valid asyncpg SSL modes here would mean
    re-releasing ``shared`` every time the driver grows one.

    With one exception, added because "permissive about the value" and "the test
    is emptiness" together left a hole the size of the guard:
    ``_INSECURE_DATABASE_SSL_MODES`` (``disable``/``allow``/``prefer``) is
    refused in a hardened env. Those are the values that ARE set and still do
    not encrypt — ``prefer`` in particular is libpq's own default and downgrades
    to plaintext in silence — so accepting them let one env var buy exactly the
    cleartext link this function exists to forbid. Denylist rather than
    allowlist for the reason above: an unrecognised mode still passes and fails
    loudly at connect time, which is the safe direction.

    The ``loopback-exempt`` sentinel is the one thing checked strictly, and it is
    why ``database_url`` is a parameter. The sentinel asserts "TLS terminates
    upstream of the app and the DB socket never leaves this machine" — a claim
    about the *endpoint*, not about the SSL setting. The validator used to accept
    it without ever seeing the endpoint, so a production deploy pointed at a
    remote Neon instance could turn off database TLS with one env var and pass
    every guard. It is now honoured in a hardened env only when the parsed host is
    genuinely loopback (127.0.0.0/8, ``::1``, ``localhost``) or a local unix
    socket.

    :param app_env: raw ``APP_ENV``; normalised here so ``"Production"`` cannot
        buy a plaintext link.
    :param database_ssl: raw ``DATABASE_SSL`` (``None``/``""`` = unset).
    :param database_url: raw ``DATABASE_URL``. Defaults to ``None`` purely so the
        two-argument callers that predate the sentinel check keep compiling —
        ``None`` is the *safe* default, not a lenient one: an unknown endpoint
        cannot be proven loopback, so the sentinel is refused in production and
        staging. Always pass it.
    :returns: ``""`` for unset or for an accepted loopback-exempt sentinel,
        otherwise the value unchanged.
    :raises ValueError: production/staging with no ``DATABASE_SSL`` set, with a
        non-encrypting mode (``disable``/``allow``/``prefer``), or with
        ``loopback-exempt`` against an endpoint that is not loopback.

    Call it from a ``@model_validator(mode="after")``, assigning the result::

        @model_validator(mode="after")
        def _validate_database_ssl(self) -> "Settings":
            object.__setattr__(
                self,
                "database_ssl",
                validate_database_ssl(
                    self.app_env, self.database_ssl, self.database_url
                ),
            )
            return self

    ``object.__setattr__`` rather than plain assignment: with
    ``validate_assignment`` enabled a normal write re-enters model validation
    from inside a model validator.

    One deliberate difference from the ``data_gateway`` original this replaces:
    the value is stripped before the emptiness test. ``DATABASE_SSL="   "`` was
    truthy there, so it passed the production gate and was then handed to
    asyncpg as an SSL mode — a config typo that bought a plaintext link *and* a
    connection error. Nothing legitimate is whitespace.
    """
    value = (database_ssl or "").strip()
    hardened = normalise_app_env(app_env or "") in ENFORCED_ENVS

    if not value and hardened:
        raise ValueError(
            f"APP_ENV={app_env!r} requires DATABASE_SSL to be set "
            "(e.g. DATABASE_SSL=require).  Without SSL, PII travels in "
            "cleartext to Neon/Postgres.  Set DATABASE_SSL=require in your "
            "environment, or DATABASE_SSL=loopback-exempt if TLS is "
            "terminated upstream and the DB socket is loopback-only."
        )

    if hardened and value.lower() in _INSECURE_DATABASE_SSL_MODES:
        raise ValueError(
            f"APP_ENV={app_env!r} with DATABASE_SSL={value!r} is not an encrypted "
            "link. 'disable' refuses TLS; 'allow' and 'prefer' fall back to "
            "plaintext without error whenever the server does not offer TLS, so "
            "the setting looks deliberate while carrying candidate PII in clear "
            "(DPDP §8, CWE-319). Set DATABASE_SSL=require (or verify-full), or "
            f"DATABASE_SSL={DATABASE_SSL_LOOPBACK_EXEMPT} if TLS is terminated "
            "upstream and the DB socket is loopback-only."
        )

    if value == DATABASE_SSL_LOOPBACK_EXEMPT:
        # The exemption is a claim about the endpoint, so verify the endpoint.
        # Outside a hardened env there is no gate to exempt from, and a developer
        # who copied the production .env still has to boot — so only
        # production/staging pay for the proof.
        if hardened and not _is_loopback_database(database_url):
            host = _database_host(database_url)
            detail = (
                f"but DATABASE_URL points at host {host!r}"
                if host
                else "but DATABASE_URL is unset or could not be parsed"
            )
            raise ValueError(
                f"APP_ENV={app_env!r} with DATABASE_SSL={DATABASE_SSL_LOOPBACK_EXEMPT}"
                " requires DATABASE_URL to point at a loopback address "
                f"(127.0.0.0/8, ::1, localhost) or a local unix socket, {detail}. "
                "The exemption means TLS terminates upstream on this machine; a "
                "remote endpoint has no such upstream, so the link would carry "
                "PII in cleartext across the network (DPDP §8, CWE-319). "
                "Set DATABASE_SSL=require."
            )
        # Strip the sentinel before it reaches asyncpg, which would reject it.
        return ""
    return value
