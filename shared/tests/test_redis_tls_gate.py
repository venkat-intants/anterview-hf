"""Redis must not carry the session store over a plaintext link in production.

The third copy of a gap shared/security.py exists to close. `validate_database_ssl`
was the second — its own docstring records that drift — and Redis had no gate at
all: `redis_url` is a bare `str` in all four services' Settings with no validator,
and `shared/redis_factory` takes the scheme from the URL without looking at it.

WHY THIS ONE IS WORTH A BOOT FAILURE rather than a warning. The refresh-token
store lives in this Redis. Write access is account takeover for any user whose id
is known: write `refresh:<sha256(R)>` = `"<victim_uuid>:<now>"` for a chosen R,
POST it to /auth/refresh in the BODY (that path skips CSRF by design) and the
provider mints an access token for that user. Deleting the `auth_epoch:<uuid>`
keys additionally defeats "log out all devices", password change, and the DPDP
erasure session purge. So the AUTH token travelling in cleartext on the first
command is not one secret among many.
"""

from __future__ import annotations

import pytest

from shared.security import ENFORCED_ENVS, validate_redis_tls


@pytest.mark.parametrize("env", sorted(ENFORCED_ENVS))
def test_a_plaintext_link_to_a_managed_instance_is_refused(env: str) -> None:
    """The live Space uses rediss:// Upstash today, so this is a missing-gate
    test rather than a live misconfiguration — but one env var was all that stood
    between the two."""
    with pytest.raises(ValueError) as exc:
        validate_redis_tls(env, "redis://my-db.upstash.io:6379")
    assert "rediss://" in str(exc.value)


@pytest.mark.parametrize("env", sorted(ENFORCED_ENVS))
def test_a_tls_link_is_accepted_unchanged(env: str) -> None:
    url = "rediss://my-db.upstash.io:6379"
    assert validate_redis_tls(env, url) == url


@pytest.mark.parametrize(
    "url",
    [
        "redis://127.0.0.1:6379",
        "redis://localhost:6379/0",
        "redis://[::1]:6379",
    ],
)
def test_loopback_is_exempt_because_it_cannot_leave_the_machine(url: str) -> None:
    """Same exemption as the database gate, and derived from the HOST — never
    from an operator asserting that the link is local."""
    assert validate_redis_tls("production", url) == url


def test_a_hostname_that_merely_looks_local_is_not_exempt() -> None:
    """`localhost.evil.example` resolves wherever its owner wants."""
    with pytest.raises(ValueError):
        validate_redis_tls("production", "redis://localhost.evil.example:6379")


@pytest.mark.parametrize("env", ["development", "test", "local", None, ""])
def test_an_unhardened_environment_is_left_alone(env: str | None) -> None:
    """Local dev runs a plaintext container; failing there would be noise."""
    assert validate_redis_tls(env, "redis://localhost:6379") is not None


def test_a_capitalised_env_cannot_buy_a_plaintext_link() -> None:
    """normalise_app_env is the reason: "Production" and "PROD" are production."""
    for spelling in ("Production", "PRODUCTION", "prod", "live"):
        with pytest.raises(ValueError):
            validate_redis_tls(spelling, "redis://remote.example:6379")


def test_an_unset_url_is_another_validators_problem() -> None:
    """This function is about the SCHEME. A missing REDIS_URL fails elsewhere, and
    raising here would report it as a TLS fault."""
    assert validate_redis_tls("production", "") == ""
    assert validate_redis_tls("production", None) == ""


def test_a_malformed_url_is_refused_rather_than_assumed_local() -> None:
    """Fail closed: anything the parser cannot place is treated as remote."""
    with pytest.raises(ValueError):
        validate_redis_tls("production", "not a url at all")


def test_every_service_wires_the_gate() -> None:
    """A guard that exists in some services is a guard you cannot reason about —
    shared/security.py's own words, and the reason validate_database_ssl is
    shared. This test is what stops Redis becoming the fourth copy of that gap.

    Checked by AST rather than by substring, for two reasons a text match got
    wrong in a row: the import line alone satisfies ``"validate_redis_tls" in
    source`` even with the call deleted, and the four services do not spell the
    call the same way (two alias it to ``_validate_redis_tls``, two do not). What
    is actually being asserted is stronger than either: the Redis gate is called
    from the SAME function that calls the database gate, so it runs in a
    ``@model_validator`` that is already known to fire at construction rather
    than from somewhere that may never execute.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    missing: list[str] = []
    for svc in ("data_gateway", "interview_core", "feedback_billing", "admin_ops"):
        tree = ast.parse(
            (root / "services" / svc / "app" / "config.py").read_text(encoding="utf-8")
        )
        wired = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            called = {
                c.func.id
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
            names = {n.removeprefix("_") for n in called}
            if "validate_database_ssl" in names and "validate_redis_tls" in names:
                wired = True
                break
        if not wired:
            missing.append(svc)

    assert not missing, (
        "these services would boot with a plaintext Redis — no validate_redis_tls "
        f"call in the validator that enforces the database gate: {missing}"
    )
