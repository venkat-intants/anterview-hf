"""Config-time security guards, shared by all four services (fail-fast).

Four guards live here rather than in each service's ``config.py``:

``normalise_app_env``
    Lowercases and strips ``APP_ENV``. Every production gate in this codebase
    tests ``== "production"``, so ``APP_ENV=Production`` silently bypassed all of
    them — including ``assert_strong_secrets`` below, which means a one-character
    typo was enough to boot production with a placeholder JWT secret and no
    complaint.

``assert_strong_secrets``
    A known ``JWT_SECRET`` lets anyone forge tokens and a known
    ``CONSENT_IP_SALT`` defeats DPDP IP-hashing. Refuse to start rather than run
    insecure.

``validate_cors_origins``
    Wildcard origins are incompatible with ``allow_credentials=True``.

``validate_database_ssl``
    An unencrypted database link carries candidate PII in cleartext (DPDP §8).

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


def normalise_app_env(value: object) -> str:
    """Lowercase + strip an ``APP_ENV`` value.

    Use as a ``@field_validator("app_env", mode="before")`` in every service's
    Settings. Security gates compare ``app_env == "production"``, so without this
    ``APP_ENV=Production`` or ``APP_ENV=PROD`` bypasses every one of them while
    looking correct in the deploy config.
    """
    if not isinstance(value, str):
        return str(value)
    return value.strip().lower()


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


def validate_database_ssl(app_env: str | None, database_ssl: str | None) -> str:
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

    :param app_env: raw ``APP_ENV``; normalised here so ``"Production"`` cannot
        buy a plaintext link.
    :param database_ssl: raw ``DATABASE_SSL`` (``None``/``""`` = unset).
    :returns: ``""`` for unset or for the loopback-exempt sentinel, otherwise the
        value unchanged.
    :raises ValueError: production/staging with no ``DATABASE_SSL`` set.

    Call it from a ``@model_validator(mode="after")``, assigning the result::

        @model_validator(mode="after")
        def _validate_database_ssl(self) -> "Settings":
            object.__setattr__(
                self, "database_ssl", validate_database_ssl(self.app_env, self.database_ssl)
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
    if not value and normalise_app_env(app_env or "") in ENFORCED_ENVS:
        raise ValueError(
            f"APP_ENV={app_env!r} requires DATABASE_SSL to be set "
            "(e.g. DATABASE_SSL=require).  Without SSL, PII travels in "
            "cleartext to Neon/Postgres.  Set DATABASE_SSL=require in your "
            "environment, or DATABASE_SSL=loopback-exempt if TLS is "
            "terminated upstream and the DB socket is loopback-only."
        )
    # Strip the sentinel before it reaches asyncpg, which would reject it.
    if value == DATABASE_SSL_LOOPBACK_EXEMPT:
        return ""
    return value
