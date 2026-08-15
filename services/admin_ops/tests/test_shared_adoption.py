"""admin_ops must actually USE the shared modules, not merely import them.

Three findings from the 2026-08-07 review were one missing library each
(XS-01/XS-05 HTTP observability, XS-08 the four copies of ``init_engine``,
DPDP-required TLS on the DB link). The shared modules carry their own unit
tests; what these pin is the adoption — the failure mode of a "shared module"
is a service that keeps its private copy and looks compliant from the outside.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from shared.db import engine as shared_engine
from shared.security import validate_database_ssl

from app import database as database_mod
from app.config import Settings, settings
from app.main import app

# ---------------------------------------------------------------------------
# shared/db/engine.py  (XS-08)
# ---------------------------------------------------------------------------


def test_init_engine_delegates_to_the_shared_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the shared module is that this service has no second copy.

    Asserting on the resulting pool class would pass just as well against a
    re-pasted local branch — which is the exact state XS-08 describes — so this
    asserts the call instead. tests/test_database_pool.py still covers the
    behaviour the three branches must produce.
    """
    calls: list[dict[str, object]] = []

    def _spy(**kwargs: object) -> object:
        calls.append(kwargs)
        return shared_engine.build_engine(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(database_mod, "build_engine", _spy)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setattr(settings, "database_ssl", "")
    monkeypatch.setattr(settings, "database_pool_size", 5)

    database_mod.init_engine()

    assert calls == [
        {
            "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
            "database_ssl": "",
            "pool_size": 5,
        }
    ]


# ---------------------------------------------------------------------------
# shared/security.py :: validate_database_ssl  (DPDP §8, CWE-319)
# ---------------------------------------------------------------------------


def _settings_kwargs(**overrides: str) -> dict[str, str]:
    """Every required field, so a Settings() built here never reads a real .env.

    Passing them explicitly matters: a stray .env beside the tests is how this
    repo previously got config tests that only passed locally.
    """
    base = {
        "database_url": "postgresql+asyncpg://u:p@db.example.com:5432/db",
        "redis_url": "redis://localhost:6379/0",
        # Long enough to clear the production strength check, but deliberately
        # LOW-ENTROPY and self-describing. The first version of this line used a
        # 64-char hex string, which read as a real key to the secret scanner and
        # turned the `security` job red (generic-api-key, entropy 3.98). The
        # repo's CI env block already uses this shape for exactly that reason —
        # matching it keeps the scanner strict instead of adding a .gitleaks.toml
        # allowlist entry, which would blunt the rule for every future file.
        "jwt_secret": "ci-only-not-a-real-secret-0123456789abcdef",
        "app_env": "test",
        "database_ssl": "",
    }
    base.update(overrides)
    return base


def test_production_without_database_ssl_refuses_to_start() -> None:
    """Unset TLS means candidate names and transcripts crossing the WAN in clear."""
    with pytest.raises(ValueError, match="DATABASE_SSL"):
        Settings(**_settings_kwargs(app_env="production"))  # type: ignore[arg-type]


def test_capitalised_production_cannot_buy_a_plaintext_link() -> None:
    """APP_ENV normalisation and the SSL gate have to compose.

    Either one alone is bypassed by ``APP_ENV=Production``; the gate is only
    real if the value it reads has already been lowercased.
    """
    with pytest.raises(ValueError, match="DATABASE_SSL"):
        Settings(**_settings_kwargs(app_env="Production"))  # type: ignore[arg-type]


def test_loopback_exempt_sentinel_is_stripped_before_the_driver_sees_it() -> None:
    """asyncpg cannot understand 'loopback-exempt' — storing it would break the
    connection the acknowledgement was meant to permit.

    The URL is overridden to loopback because that is now a PRECONDITION of the
    sentinel being honoured at all: the exemption claims TLS terminates upstream
    on this machine, so the validator checks the endpoint before accepting it.
    The default fixture URL is ``db.example.com`` — see the test below.
    """
    cfg = Settings(  # type: ignore[arg-type]
        **_settings_kwargs(
            app_env="production",
            database_ssl="loopback-exempt",
            database_url="postgresql+asyncpg://u:p@127.0.0.1:5432/db",
        )
    )
    assert cfg.database_ssl == ""


def test_loopback_exempt_is_refused_for_a_remote_database() -> None:
    """The whole point of passing DATABASE_URL to the validator.

    Before the URL was a parameter, this one env var turned off TLS to a remote
    Neon instance and passed every startup guard — candidate PII in cleartext
    across the internet with a written acknowledgement that it was fine
    (DPDP §8, CWE-319). The sentinel is an assertion about the ENDPOINT, so an
    endpoint that is not loopback must refuse to boot.
    """
    with pytest.raises(ValueError, match="loopback"):
        Settings(  # type: ignore[arg-type]
            **_settings_kwargs(app_env="production", database_ssl="loopback-exempt")
        )


def test_development_without_ssl_still_starts() -> None:
    """Local Postgres over loopback has no TLS and must stay runnable."""
    cfg = Settings(**_settings_kwargs(app_env="development"))  # type: ignore[arg-type]
    assert cfg.database_ssl == ""


def test_settings_agrees_with_the_shared_helper() -> None:
    """Guards against a local re-implementation drifting from shared/security.py."""
    cfg = Settings(  # type: ignore[arg-type]
        **_settings_kwargs(app_env="production", database_ssl="require")
    )
    assert cfg.database_ssl == validate_database_ssl("production", "require")


# ---------------------------------------------------------------------------
# shared/http_observability.py  (XS-01 / XS-05, CWE-209)
# ---------------------------------------------------------------------------


def test_http_metrics_land_on_the_registry_this_service_exposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed with the private registry, not the global default.

    The default would collect the series into a registry admin_ops never
    serves — instrumented in code, invisible to Prometheus.
    """
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "test")
    client = TestClient(app, raise_server_exceptions=False)

    client.get("/")
    body = client.get("/metrics").text

    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    # Business metrics must still be there — the shared install shares a registry
    # with them rather than replacing it.
    assert "admin_ops_erasure_requests_completed_total" in body


def test_request_counter_labels_the_route_template_not_the_raw_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XS-06 (CWE-770): an unmatched path must not mint a time series per URL."""
    monkeypatch.setattr(settings, "metrics_token", "")
    monkeypatch.setattr(settings, "app_env", "test")
    client = TestClient(app, raise_server_exceptions=False)

    client.get("/no-such-endpoint-aaa")
    client.get("/no-such-endpoint-bbb")
    body = client.get("/metrics").text

    assert "no-such-endpoint-aaa" not in body
    assert "no-such-endpoint-bbb" not in body
    assert "__unmatched__" in body


def test_unhandled_exception_returns_a_generic_500() -> None:
    """CWE-209: admin_ops had no exception handler at all.

    The leak this prevents is specific — asyncpg puts the failing SQL and its
    bound parameter VALUES into str(exc), and in this service those parameters
    are erasure user_ids and analytics filters over candidate data — so the
    assertion is that the exception text does not reach the client, not merely
    that the status is 500.

    The route is added to the real app and removed again in ``finally``: the
    handler under test is installed on THIS app object, and a throwaway app
    would only prove that the shared module works (which shared/tests already
    proves) rather than that admin_ops installed it.
    """
    secret = "candidate-email-in-the-driver-message"

    async def _boom() -> dict[str, str]:
        raise RuntimeError(secret)

    app.add_api_route("/_test_boom", _boom, methods=["GET"])
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/_test_boom")
        assert resp.status_code == 500
        assert secret not in resp.text
        assert resp.json() == {"detail": "Internal server error."}
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) != "/_test_boom"
        ]
