"""Production secret-strength guard (fail-fast).

A misconfigured production deploy that boots with an empty, placeholder, or
too-short secret is a critical footgun: a known ``JWT_SECRET`` lets anyone forge
tokens, and a known ``CONSENT_IP_SALT`` defeats the DPDP IP-hashing. This helper
lets each service refuse to start in production/staging when any of its critical
secrets is weak — surfacing the mistake at boot instead of silently running
insecure. It is a deliberate NO-OP in development/test so local runs and the test
suite are unaffected.
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
