"""Behaviour of the config-time guards in ``shared/security.py``.

The things pinned here are the ones that were wrong (XS-04, SEC-9, and the two
bypasses found reviewing PR #9):

* ``validate_database_ssl`` — the one security validator that never made the
  consolidation. It lived in ``data_gateway`` alone, so three services could
  boot in production against a plaintext Postgres link carrying candidate PII
  and say nothing. These tests are written against the *shared* function, so
  they hold for whichever service adopts it.
* ``ENFORCED_ENVS`` — the single answer to "which environments are hardened".
  It is asserted to be the set every guard in the package reads, because the
  defect it closes is not "staging was open" but "two guards one import apart
  disagreed about staging".
* ``normalise_app_env`` — casing was fixed, abbreviations were not, so
  ``APP_ENV=PROD`` lowercased to ``"prod"``, missed ``ENFORCED_ENVS`` entirely
  and opened every gate at once.
* ``loopback-exempt`` — an acknowledgement that TLS terminates upstream *on this
  machine*, which was being accepted for any endpoint, including a remote Neon
  instance in production.
"""

from __future__ import annotations

import pytest

from shared.security import (
    DATABASE_SSL_LOOPBACK_EXEMPT,
    ENFORCED_ENVS,
    assert_strong_secrets,
    normalise_app_env,
    validate_database_ssl,
)

_STRONG = "0123456789abcdef" * 4  # 64 hex chars, no placeholder marker

# A URL the app can only reach over the local machine, and one it cannot.
_LOOPBACK_URL = "postgresql+asyncpg://u:p@localhost:5432/db"
_REMOTE_URL = "postgresql+asyncpg://u:p@ep-cool-lab.ap-south-1.aws.neon.tech/db"


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
# normalise_app_env — abbreviations, not just casing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["PROD", "prod", "  Prod  ", "prd", "LIVE", "production", "Production", " PRODUCTION "],
)
def test_values_meaning_production_reach_the_enforced_set(raw: str) -> None:
    """``"PROD".lower()`` is ``"prod"``, which is not in ``ENFORCED_ENVS``.

    Lowercasing alone therefore left ``APP_ENV=PROD`` — an entirely ordinary
    thing to type into a deploy config — bypassing every gate in the package
    while looking correct.
    """
    assert normalise_app_env(raw) == "production"
    assert normalise_app_env(raw) in ENFORCED_ENVS


@pytest.mark.parametrize("raw", ["STAGE", "stage", " Stage ", "stg", "Staging", "STAGING"])
def test_values_meaning_staging_reach_the_enforced_set(raw: str) -> None:
    """Staging holds real candidate data here, so it is hardened (SEC-9) and the
    same shorthand problem applies to it."""
    assert normalise_app_env(raw) == "staging"
    assert normalise_app_env(raw) in ENFORCED_ENVS


@pytest.mark.parametrize(
    "raw", ["sandbox", "pre-prod", "prod-mirror", "staging-2", "qa", "demo", "dev", "test"]
)
def test_an_unrecognised_value_never_becomes_production(raw: str) -> None:
    """The alias table is exact-match and hardening-only on purpose.

    A prefix rule would silently claim ``pre-prod`` and ``prod-mirror``, which
    are precisely the environments that are *not* production; and an unknown
    value quietly turning into production would break every local run.
    """
    assert normalise_app_env(raw) not in ENFORCED_ENVS
    assert normalise_app_env(raw) == raw.lower()


@pytest.mark.parametrize("env", ["PROD", "prod", "  Prod  ", "STAGE", "stage"])
def test_abbreviated_env_arms_the_real_gates(env: str) -> None:
    """Pins the consequence, not the string. Asserting only the return value
    would still pass if the guards read the raw ``APP_ENV`` instead."""
    with pytest.raises(ValueError, match="JWT_SECRET"):
        assert_strong_secrets(env, {"JWT_SECRET": "change-me"})
    with pytest.raises(ValueError, match="DATABASE_SSL"):
        validate_database_ssl(env, "", _LOOPBACK_URL)


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


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@localhost:5432/db",
        "postgresql://u:p@127.0.0.1/db",
        "postgresql://u:p@127.1.2.3/db",  # all of 127.0.0.0/8 is loopback
        "postgresql://u:p@[::1]:5432/db",
        "postgresql://u:p@LOCALHOST/db",
        # libpq unix sockets, in both spellings asyncpg accepts.
        "postgresql:///db?host=/var/run/postgresql",
        "postgresql://%2Fvar%2Frun%2Fpostgresql/db",
    ],
)
def test_loopback_exempt_passes_the_gate_but_never_reaches_asyncpg(url: str) -> None:
    """The sentinel is an acknowledgement token for this validator, not an SSL
    mode; asyncpg would reject it. Passing the gate and being stripped are one
    decision, which is why the function returns the value to store.

    Every URL here is genuinely local, which is the only case the exemption was
    ever meant to cover."""
    assert validate_database_ssl("production", DATABASE_SSL_LOOPBACK_EXEMPT, url) == ""


@pytest.mark.parametrize("env", ENFORCED_ENVS)
@pytest.mark.parametrize(
    "url",
    [
        _REMOTE_URL,
        "postgresql://u:p@db.internal:5432/db",
        "postgresql://u:p@10.0.0.5/db",  # private, but still across a network
        "postgresql://u:p@[2001:db8::1]/db",
    ],
)
def test_loopback_exempt_is_refused_for_a_remote_endpoint(env: str, url: str) -> None:
    """The whole bypass: the sentinel claims TLS terminates upstream *on this
    machine*, and nothing checked the endpoint. One env var therefore disabled
    database TLS to a remote Neon instance in production while every guard
    reported success (DPDP §8, CWE-319)."""
    with pytest.raises(ValueError, match="loopback"):
        validate_database_ssl(env, DATABASE_SSL_LOOPBACK_EXEMPT, url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "not a url",
        "postgresql://u:p@[::1/db",  # unterminated IPv6 literal: urlsplit raises
    ],
)
def test_loopback_exempt_is_refused_when_the_endpoint_is_unknowable(url: str | None) -> None:
    """``None`` is the parameter's default, so this is the case that decides
    whether the default is safe. An unset or unparseable URL is not evidence of
    a loopback socket — "cannot tell" has to mean "refuse", or the default
    quietly restores the bypass for any caller that forgets the argument."""
    with pytest.raises(ValueError, match="loopback"):
        validate_database_ssl("production", DATABASE_SSL_LOOPBACK_EXEMPT, url)


def test_the_remote_exemption_error_names_the_host_and_the_fix() -> None:
    """An operator hit by this at 3am needs to see which endpoint disqualified
    the exemption, not just that something was rejected."""
    with pytest.raises(ValueError) as exc_info:
        validate_database_ssl("production", DATABASE_SSL_LOOPBACK_EXEMPT, _REMOTE_URL)
    message = str(exc_info.value)
    assert "ep-cool-lab.ap-south-1.aws.neon.tech" in message
    assert "DATABASE_SSL=require" in message


@pytest.mark.parametrize("url", [None, _REMOTE_URL])
def test_loopback_exempt_is_stripped_outside_hardened_envs_too(url: str | None) -> None:
    """Otherwise a dev copying the prod env file hands the sentinel to asyncpg.

    Dev/test have no TLS gate to be exempted from, so there is nothing to prove
    and no reason to make a local run pass a residency check."""
    assert validate_database_ssl("development", DATABASE_SSL_LOOPBACK_EXEMPT, url) == ""


def test_value_is_stripped_of_surrounding_whitespace() -> None:
    """``DATABASE_SSL=require `` from a .env line with a trailing space is a
    connection failure if passed through verbatim."""
    assert validate_database_ssl("production", "  require  ") == "require"
