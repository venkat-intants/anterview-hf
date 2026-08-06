"""Optional Sentry error tracking — safe to call from every service at startup.

It is a complete NO-OP unless ``SENTRY_DSN`` is set AND the ``sentry-sdk`` package
is installed, so development and tests are never affected. When active, PII is
scrubbed from every event before it leaves the process (DPDP §8): cookies, auth
headers, request bodies, and known PII keys are stripped, and ``send_default_pii``
is disabled so Sentry never auto-attaches the client IP.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Keys whose values may carry PII / secrets and must never leave the process in an
# error payload. Mirrors the structlog PII redaction each service already applies.
_PII_KEYS = frozenset(
    {
        "email",
        "password",
        "phone",
        "full_name",
        "name",
        "ip",
        "ip_hash",
        "user_agent_hash",
        "authorization",
        "cookie",
        "token",
        "access_token",
        "refresh_token",
        "jwt",
        "transcript",
        # Names this codebase actually uses for the same data. Matching is
        # exact, so "email" alone does not cover "user_email" or
        # "candidate_email", and the resume/answer text is the largest block of
        # candidate PII the platform holds.
        "user_email",
        "candidate_email",
        "to_email",
        "candidate_name",
        "resume_text",
        "resume_excerpt",
        "jd_text",
        "api_key",
        "secret",
        "client_secret",
        "code_verifier",
        "plain",
        "password_hash",
        "csrf_token",
    }
)


def _scrub(obj: Any) -> Any:
    """Recursively redact PII-named keys in dicts/lists."""
    if isinstance(obj, Mapping):
        return {
            k: ("[redacted]" if str(k).lower() in _PII_KEYS else _scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _before_send(event: dict, _hint: dict) -> dict:
    """Strip cookies / auth headers / bodies / PII before an event is sent."""
    try:
        req = event.get("request")
        if isinstance(req, dict):
            req.pop("cookies", None)
            req.pop("data", None)
            # The query string is populated by the ASGI integration, so a 500 on
            # /auth/sso/google/callback?code=..&state=.. would ship the OAuth
            # authorization code to a third-party SaaS. The URL is kept, minus
            # its query, because the path alone is what makes an event useful.
            req.pop("query_string", None)
            url = req.get("url")
            if isinstance(url, str) and "?" in url:
                req["url"] = url.split("?", 1)[0]
            headers = req.get("headers")
            if isinstance(headers, dict):
                for h in list(headers):
                    if str(h).lower() in (
                        "authorization",
                        "cookie",
                        "x-csrf-token",
                        # Bearer-equivalent magic-link credentials — same
                        # reasoning as the Caddy access-log filter.
                        "x-exam-token",
                        "x-interview-token",
                    ):
                        headers[h] = "[redacted]"
        if "extra" in event:
            event["extra"] = _scrub(event["extra"])
        # Breadcrumbs carry their own data maps and messages, and were never
        # scrubbed — an HTTP breadcrumb or a log line recorded before the
        # exception can hold the same PII the event body is cleaned of.
        crumbs = event.get("breadcrumbs")
        if isinstance(crumbs, dict) and isinstance(crumbs.get("values"), list):
            crumbs["values"] = _scrub(crumbs["values"])
        elif isinstance(crumbs, list):
            event["breadcrumbs"] = _scrub(crumbs)
    except Exception:  # noqa: BLE001 — scrubbing must never break error reporting
        pass
    return event


def init_sentry(
    dsn: str | None,
    *,
    environment: str,
    service_name: str,
    traces_sample_rate: float = 0.0,
) -> bool:
    """Initialise Sentry iff a DSN is configured and the SDK is installed.

    Returns True when Sentry was initialised, False otherwise. Always safe to call
    (never raises) — a missing DSN or missing package is a silent no-op.
    """
    if not (dsn or "").strip():
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning(
            "sentry.sdk_missing",
            hint="pip install sentry-sdk to enable error tracking",
            service=service_name,
        )
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            traces_sample_rate=traces_sample_rate,
            send_default_pii=False,  # never auto-attach IP / cookies / headers
            before_send=_before_send,
            server_name=service_name,
            # The SDK defaults this to True, and _before_send never sees frame
            # locals — so an unhandled 500 in a PII-handling endpoint would ship
            # local variables to a third-party SaaS. Two concrete cases in this
            # codebase: agent_panel holds `bundle` (candidate name + resume
            # text) as a live local, and LocalAuthProvider._verify_password
            # holds the PLAINTEXT password in `plain`. Neither name is in the
            # SDK's built-in denylist.
            include_local_variables=False,
        )
    except Exception as exc:  # noqa: BLE001 — observability must not break boot
        log.warning("sentry.init_failed", error=str(exc), service=service_name)
        return False
    log.info("sentry.initialized", service=service_name, environment=environment)
    return True
