"""Security-audit finding M-6 (CWE-497): ``/metrics`` must not be world readable.

These tests pin the four branches of :func:`shared.metrics_auth.check_metrics_auth`
as *behaviour* — "is this scrape allowed, and if not, what does the prober learn"
— because the whole point of the helper is that the answer is identical in all
four services. In particular:

* the production-with-no-token case must be a refusal, not a boot failure;
* it must be a 404, so an unauthenticated prober cannot even confirm the route
  exists;
* ``APP_ENV=Production`` must be treated as production (the capitalisation bug
  ``shared/security.normalise_app_env`` was written to kill).
"""

from __future__ import annotations

import pytest

from shared.metrics_auth import MetricsAuthError, check_metrics_auth

_TOKEN = "metrics-token-not-real-0123456789abcdef"


# --------------------------------------------------------------------------
# Token configured
# --------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["production", "development", "test"])
def test_correct_bearer_token_is_allowed(app_env: str) -> None:
    """The configured token grants access in every environment."""
    check_metrics_auth(
        authorization=f"Bearer {_TOKEN}",
        metrics_token=_TOKEN,
        app_env=app_env,
    )


def test_wrong_token_is_rejected_with_401() -> None:
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(
            authorization="Bearer wrong-token-0123456789abcdefghij",
            metrics_token=_TOKEN,
            app_env="production",
        )
    assert exc_info.value.status_code == 401


def test_missing_header_is_rejected_with_401() -> None:
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(
            authorization=None,
            metrics_token=_TOKEN,
            app_env="production",
        )
    assert exc_info.value.status_code == 401


def test_401_advertises_the_bearer_scheme() -> None:
    """RFC 7235: a 401 has to say how to authenticate, or the scraper cannot."""
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(authorization=None, metrics_token=_TOKEN, app_env="production")
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize(
    "header",
    [
        _TOKEN,  # raw token, no scheme
        f"bearer {_TOKEN}",  # lowercased scheme
        f"Bearer  {_TOKEN}",  # doubled separator
        f"Bearer {_TOKEN} ",  # trailing whitespace
        f"Basic {_TOKEN}",  # wrong scheme
        f"Bearer {_TOKEN}extra",  # correct token as a prefix
        f"Bearer {_TOKEN[:-1]}",  # one character short
        "Bearer ",
        "",
    ],
)
def test_only_the_exact_bearer_header_is_accepted(header: str) -> None:
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(authorization=header, metrics_token=_TOKEN, app_env="production")
    assert exc_info.value.status_code == 401


def test_non_ascii_header_is_rejected_not_crashed() -> None:
    """A non-ASCII header must 401, never blow up.

    Starlette decodes raw header bytes as latin-1, so ``Bearer \\xff`` reaches us
    as a str with a non-ASCII codepoint. ``hmac.compare_digest`` raises TypeError
    on such a str, which would have turned an unauthorised scrape into a 500 —
    reachable by anyone who can open a socket.
    """
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(
            authorization="Bearer \xff€\udcff",
            metrics_token=_TOKEN,
            app_env="production",
        )
    assert exc_info.value.status_code == 401


# --------------------------------------------------------------------------
# Token NOT configured
# --------------------------------------------------------------------------


def test_no_token_in_production_is_refused_as_404() -> None:
    """Fail closed — and give a prober no confirmation the route exists."""
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(authorization=None, metrics_token=None, app_env="production")
    assert exc_info.value.status_code == 404


def test_no_token_in_production_refuses_a_bearer_header_too() -> None:
    """With nothing configured there is no credential that can open the door."""
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(
            authorization=f"Bearer {_TOKEN}",
            metrics_token=None,
            app_env="production",
        )
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("app_env", ["Production", "PRODUCTION", "  production  "])
def test_production_gate_is_case_and_whitespace_insensitive(app_env: str) -> None:
    """``APP_ENV=Production`` must not buy an open metrics endpoint."""
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(authorization=None, metrics_token=None, app_env=app_env)
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_token_counts_as_unset_in_production(blank: str | None) -> None:
    """``METRICS_TOKEN=`` in .env.example arrives as ``""``.

    Treating that as "configured" would require the literal header ``"Bearer "``
    and lock production out of its own scrape endpoint instead of refusing
    cleanly.
    """
    with pytest.raises(MetricsAuthError) as exc_info:
        check_metrics_auth(authorization=None, metrics_token=blank, app_env="production")
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("app_env", ["development", "test", "staging", "local"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_no_token_outside_production_is_allowed(app_env: str, blank: str | None) -> None:
    """Local dev, docker-compose and CI keep scraping with no config change."""
    check_metrics_auth(authorization=None, metrics_token=blank, app_env=app_env)


def test_no_token_outside_production_ignores_any_header() -> None:
    check_metrics_auth(authorization="Bearer whatever", metrics_token=None, app_env="development")
