"""Optional Sentry error tracking — safe to call from every service at startup.

It is a complete NO-OP unless ``SENTRY_DSN`` is set AND the ``sentry-sdk`` package
is installed, so development and tests are never affected. When active, PII is
scrubbed from every event before it leaves the process (DPDP §8): cookies, auth
headers, request bodies, and known PII keys are stripped, and ``send_default_pii``
is disabled so Sentry never auto-attaches the client IP.

"Every event" means both classes of event. Errors and transactions leave through
separate hooks (``before_send`` / ``before_send_transaction``) and the SDK never
applies one to the other, so a single-hook install scrubs half the traffic. Both
are wired to ``_before_send`` below.

The PII key set is derived from ``observability/pii.py`` — see ``_PII_KEYS``
below for why it is derived rather than declared here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

from shared.observability.pii import PII_FIELDS

if TYPE_CHECKING:
    # Type-only. sentry-sdk is an OPTIONAL dependency — `init_sentry` imports it
    # lazily inside a try/ImportError so a service without it still starts, and
    # shared/ must stay importable without it. A TYPE_CHECKING import keeps the
    # `before_send` callback correctly typed against the SDK's own signature
    # (Callable[[Event, dict], Event | None]) without adding a runtime import.
    # Where the SDK is absent, --ignore-missing-imports resolves Event to Any
    # and the annotation stays valid.
    from sentry_sdk.types import Event, Hint

log = structlog.get_logger(__name__)

# Wire-only names. Sentry sees these because the ASGI integration attaches the
# request headers and because HTTP breadcrumbs carry their own header maps; a
# structlog event dict never holds one, which is the whole reason they live here
# and not in the canonical set. Used for both the header sweep in
# ``_before_send`` and the recursive ``_scrub``, so the two cannot disagree.
_TRANSPORT_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-csrf-token",
        # Bearer-equivalent magic-link credentials — same reasoning as the
        # Caddy access-log filter.
        "x-exam-token",
        "x-interview-token",
    }
)

# Keys whose values may carry PII / secrets and must never leave the process in
# an error payload. DERIVED from the canonical structlog deny-list, not restated
# beside it: this file used to keep its own copy under a comment claiming it
# "mirrors" the structlog redaction, and the claim was false in both directions
# — it missed answer / question / text_content / turn_text / target_jd_text /
# address / raw_token, while the canonical set missed the credential and contact
# names only this list had (those have since moved into pii.py). Two lists plus
# a comment asserting parity is precisely the defect that hid the original
# per-service drift; deriving makes the parity true by construction instead.
#
# "name" is the one data key that stays local. In a Sentry ``extra`` or
# breadcrumb it is nearly always a serialised user object's name, but as a
# structlog kwarg this codebase uses it for non-PII identifiers (see
# ``circuit_breaker.closed name=...``), so promoting it would blank an ops
# signal in four services without protecting a candidate. Log ``full_name``,
# never ``name``, when the value really is a person.
_PII_KEYS: frozenset[str] = PII_FIELDS | _TRANSPORT_HEADERS | frozenset({"name"})


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


def _before_send(event: Event, _hint: Hint) -> Event | None:
    """Strip cookies / auth headers / bodies / PII before an event is sent.

    Wired to ``before_send`` AND ``before_send_transaction`` — the SDK routes
    error events and transaction (performance) events through *different* hooks
    and applies neither to the other, so an install that sets only ``before_send``
    ships every traced request unscrubbed the moment ``traces_sample_rate`` goes
    above 0 (SEC-8). A transaction carries the same ``request`` the error path
    does, query string included.

    Signature matches both ``sentry_sdk.init`` hooks exactly. It was previously
    ``(dict, dict) -> dict``, which only type-checked because
    ``shared/observability`` was not in the mypy step's path list until
    2026-08-07 — returning ``None`` to DROP an event was not expressible.
    """
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
                    if str(h).lower() in _TRANSPORT_HEADERS:
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
        # A transaction event keeps its payload in span ``data``/``tags`` rather
        # than in ``extra``, so the surfaces above miss it entirely. Only those
        # two maps are scrubbed, never the span dict itself: a span's own keys
        # are structural (``op``, ``description``, ``trace_id``) and a
        # transaction's include ``name`` — the route — which ``_PII_KEYS``
        # contains and would blank, turning every performance event into
        # "[redacted]" and destroying the signal we enabled tracing for.
        spans = event.get("spans")
        if isinstance(spans, list):
            for span in spans:
                if not isinstance(span, dict):
                    continue
                for field in ("data", "tags"):
                    if isinstance(span.get(field), Mapping):
                        span[field] = _scrub(span[field])
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
            # Both hooks, same scrubber. before_send is NOT applied to
            # transaction events — the SDK dispatches them separately
            # (client.py: `before_send_transaction = self.options[...]`), so
            # scrubbing only the error path left every sampled request
            # unscrubbed. Silently: traces_sample_rate defaults to 0.0 here, so
            # the gap is invisible until someone turns tracing on.
            before_send_transaction=_before_send,
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
