"""Every magic-link credential header is protected in every place it can leak.

A candidate's token rides in a custom header precisely so it stays out of
URLs, and therefore out of logs. That design only holds if each such header
is ALSO removed from the reverse proxy's access log, scrubbed from Sentry
events, and allowed through CORS -- four places, in three different files
across two services, none of which a router author is naturally looking at.

PH4-D4 added ``X-Task-Token`` and none of the four was updated, so the raw
token would have been written to the access log on every request to the
candidate's task page. Nothing failed. This test makes that a failure: it
finds every credential header the routers actually read, and checks each one
is registered everywhere.
"""

from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
REPO = pathlib.Path(__file__).resolve().parents[4]

# `X-CSRF-Token` is a double-submit token paired with a cookie, not a bearer
# credential on its own, so it is exempt from the proxy-log rule. It is the
# only exemption; add another only with a reason as specific as this one.
_NOT_A_BEARER_CREDENTIAL = {"X-CSRF-Token"}


def _credential_headers() -> set[str]:
    """Every ``Header(alias="X-...-Token")`` or ``-Session`` a route reads."""
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        for name in re.findall(r'Header\(alias="(X-[A-Za-z-]+)"\)', path.read_text(encoding="utf-8")):
            if name.endswith(("-Token", "-Session")) and name not in _NOT_A_BEARER_CREDENTIAL:
                found.add(name)
    return found


def test_the_scan_finds_the_headers_we_know_about() -> None:
    """Guards the guard: if the pattern stops matching, every check below
    would pass vacuously."""
    assert {"X-Exam-Token", "X-Offer-Token", "X-Task-Token"} <= _credential_headers()


def test_every_credential_header_is_kept_out_of_both_proxy_access_logs() -> None:
    for caddyfile in (REPO / "Caddyfile", REPO / "space" / "Caddyfile"):
        text = caddyfile.read_text(encoding="utf-8")
        missing = sorted(
            h for h in _credential_headers() if f"request>headers>{h} delete" not in text
        )
        assert not missing, f"{caddyfile.relative_to(REPO)} would log these tokens verbatim: {missing}"


def test_every_credential_header_is_scrubbed_from_sentry() -> None:
    text = (REPO / "shared" / "observability" / "sentry.py").read_text(encoding="utf-8")
    missing = sorted(h for h in _credential_headers() if f'"{h.lower()}"' not in text)
    assert not missing, f"Sentry would receive these tokens: {missing}"


def test_every_credential_header_is_allowed_through_cors() -> None:
    """Without this the browser's preflight refuses the header, and the
    candidate's page fails in any cross-origin setup -- which is local dev."""
    text = (APP / "main.py").read_text(encoding="utf-8")
    missing = sorted(h for h in _credential_headers() if f'"{h}"' not in text)
    assert not missing, f"CORS would refuse these headers: {missing}"
