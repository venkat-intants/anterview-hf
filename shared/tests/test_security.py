"""Behaviour of the config-time guards in ``shared/security.py``.

The two things pinned here are the two that were wrong (XS-04, SEC-9):

* ``validate_database_ssl`` — the one security validator that never made the
  consolidation. It lived in ``data_gateway`` alone, so three services could
  boot in production against a plaintext Postgres link carrying candidate PII
  and say nothing. These tests are written against the *shared* function, so
  they hold for whichever service adopts it.
* ``ENFORCED_ENVS`` — the single answer to "which environments are hardened".
  It is asserted to be the set every guard in the package reads, because the
  defect it closes is not "staging was open" but "two guards one import apart
  disagreed about staging".
"""

from __future__ import annotations

import pytest

from shared.security import (
    DATABASE_SSL_LOOPBACK_EXEMPT,
    ENFORCED_ENVS,
    assert_strong_secrets,
    validate_database_ssl,
)

_STRONG = "0123456789abcdef" * 4  # 64 hex chars, no placeholder marker


# ---------------------------------------------------------------------------
# ENFORCED_ENVS is the shared definition of "hardened"
# ---------------------------------------------------------------------------


def test_enforced_envs_is_production_and_staging() -> None:
    """Staging is hardened. Pinned as a literal because the *value* is the
    policy — a guard that silently drops staging is the SEC-9 defect returning."""
    assert set(ENFORCED_ENVS) == {"production", "staging"}


@pytest.mark.parametrize("env", ENFORCED_ENVS)
def test_every_enforced_env_rejects_a_weak_secret(env: str) -> None:
    """The secret guard and the metrics guard must agree on this set, so start
    by proving this one really covers all of it."""
    with pytest.raises(ValueError, match="JWT_SECRET"):
        assert_strong_secrets(env, {"JWT_SECRET": "change-me"})


# ---------------------------------------------------------------------------
# validate_database_ssl — XS-04 (CWE-319)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ENFORCED_ENVS)
@pytest.mark.parametrize("unset", [None, "", "   "])
def test_missing_ssl_fails_in_every_hardened_env(env: str, unset: str | None) -> None:
    """A database URL with no TLS requirement must not start production.

    Whitespace counts as unset: it was truthy in the ``data_gateway`` original,
    so ``DATABASE_SSL=" "`` passed the gate and was then handed to asyncpg.
    """
    with pytest.raises(ValueError, match="DATABASE_SSL"):
        validate_database_ssl(env, unset)


def test_the_error_names_the_variable_and_both_ways_out() -> None:
    """An operator hit by this at 3am needs the fix in the message, not in a doc."""
    with pytest.raises(ValueError) as exc_info:
        validate_database_ssl("production", "")
    message = str(exc_info.value)
    assert "DATABASE_SSL=require" in message
    assert DATABASE_SSL_LOOPBACK_EXEMPT in message


@pytest.mark.parametrize("env", ["Production", "PRODUCTION", "  Staging  "])
def test_capitalised_app_env_does_not_buy_a_plaintext_link(env: str) -> None:
    """``APP_ENV=Production`` must not walk past this guard — the same
    normalisation bug ``normalise_app_env`` exists to kill."""
    with pytest.raises(ValueError, match="DATABASE_SSL"):
        validate_database_ssl(env, "")


@pytest.mark.parametrize("env", ["development", "test", "local", ""])
@pytest.mark.parametrize("unset", [None, "", "   "])
def test_missing_ssl_is_allowed_outside_hardened_envs(env: str, unset: str | None) -> None:
    """Local Postgres over a loopback socket has no TLS and must keep working."""
    assert validate_database_ssl(env, unset) == ""


@pytest.mark.parametrize("mode", ["require", "verify-full", "prefer", "/etc/ssl/neon.crt"])
def test_any_non_empty_mode_satisfies_the_gate_and_survives_unchanged(mode: str) -> None:
    """Deliberately permissive: the failure being caught is 'operator set
    nothing', and enumerating asyncpg's SSL modes here would date instantly.
    The value must reach the driver untouched."""
    assert validate_database_ssl("production", mode) == mode


def test_loopback_exempt_passes_the_gate_but_never_reaches_asyncpg() -> None:
    """The sentinel is an acknowledgement token for this validator, not an SSL
    mode; asyncpg would reject it. Passing the gate and being stripped are one
    decision, which is why the function returns the value to store."""
    assert validate_database_ssl("production", DATABASE_SSL_LOOPBACK_EXEMPT) == ""


def test_loopback_exempt_is_stripped_outside_hardened_envs_too() -> None:
    """Otherwise a dev copying the prod env file hands the sentinel to asyncpg."""
    assert validate_database_ssl("development", DATABASE_SSL_LOOPBACK_EXEMPT) == ""


def test_value_is_stripped_of_surrounding_whitespace() -> None:
    """``DATABASE_SSL=require `` from a .env line with a trailing space is a
    connection failure if passed through verbatim."""
    assert validate_database_ssl("production", "  require  ") == "require"
