"""Config-time security guards, shared by all four services (fail-fast).

Three guards live here rather than in each service's ``config.py``:

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

All three are deliberate NO-OPs (or permissive) in development/test so local
runs and the suite are unaffected.

Why shared and not copied into each config: they WERE copied, and they drifted.
``normalise_app_env`` existed only in ``interview_core`` and ``validate_cors_origins``
only in ``interview_core`` and ``data_gateway``, so the two services that
enforce neither were the ones a capitalised ``APP_ENV`` would have walked
straight past. A guard that exists in some services is a guard you cannot reason
about. ``shared/security.py`` is stdlib-only and already imported by all four
configs, so there is no dependency cost to putting them here.
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

_ENFORCED_ENVS = ("production", "staging")


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
    if (app_env or "").strip().lower() not in _ENFORCED_ENVS:
        return
    weak = sorted(name for name, value in secrets.items() if is_weak_secret(value))
    if weak:
        raise ValueError(
            f"Refusing to start in {app_env!r}: weak or placeholder secret(s): "
            f"{', '.join(weak)}. Set strong random values — generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
