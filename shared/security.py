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

All four are deliberate NO-OPs (or permissive) in development/test so local
runs and the suite are unaffected.

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
    :raises ValueError: production/staging with no ``DATABASE_SSL`` set, or with
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
