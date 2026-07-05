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
            headers = req.get("headers")
            if isinstance(headers, dict):
                for h in list(headers):
                    if str(h).lower() in ("authorization", "cookie", "x-csrf-token"):
                        headers[h] = "[redacted]"
        if "extra" in event:
            event["extra"] = _scrub(event["extra"])
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
        )
    except Exception as exc:  # noqa: BLE001 — observability must not break boot
        log.warning("sentry.init_failed", error=str(exc), service=service_name)
        return False
    log.info("sentry.initialized", service=service_name, environment=environment)
    return True
