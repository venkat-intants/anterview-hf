"""One HTTP metrics + unhandled-exception layer, shared by all four services.

Closes three findings that are really one missing module (code-review
2026-08-07, XS-01 / XS-05 / XS-06):

* **XS-01 (CWE-209).** Only ``data_gateway`` registered a global exception
  handler. On the other three an unhandled exception reached the ASGI server,
  whose default body is server-dependent and can echo driver text — asyncpg puts
  the offending SQL *and its parameter values* into ``str(exc)``, and those
  parameters are candidate PII. Their request counters also structurally could
  not record a ``500``, so ``sum(rate(http_requests_total{status_code=~"5.."}))``
  was incapable of firing for three quarters of the platform.
* **XS-05.** Four divergent Prometheus implementations, and ``interview_core`` —
  the service the p95 < 2s NFR is written about — exposed only default process
  metrics and no HTTP metrics at all.
* **XS-06 (CWE-770).** The original middleware normalised UUID segments to
  ``{id}`` for *matched* routes but labelled an unmatched request with its raw
  path, so an unauthenticated caller looping ``/aaa``, ``/aab``, … minted a new
  time series per request.

Install it once, at import time, before the app starts serving::

    from shared.http_observability import install_http_observability

    app = FastAPI(...)
    install_http_observability(app, service_name=settings.service_name)

``admin_ops`` keeps its business metrics by passing its own registry::

    install_http_observability(app, service_name=..., registry=_registry)

Each service also installs the readable 422 (audit X1, see the section at the
end of this module)::

    install_validation_error_handler(app)

What the label means
--------------------
The ``path`` label is the **matched route template** (``/hr/jobs/{job_id}``),
read from ``scope["route"].path`` — not a regex over the raw URL. That is the
part that makes XS-06 fixable rather than merely narrowed: a regex can only
collapse the ID shapes someone remembered to write down (the previous version
collapsed UUIDs but not integer IDs, slugs or e-mail addresses in a path), while
the router already knows the exact template it matched. Anything the router did
*not* match is labelled with the constant :data:`UNMATCHED_PATH`, which bounds
cardinality by construction instead of by enumeration.

Only ``fastapi.routing.APIRoute`` publishes ``scope["route"]``; plain Starlette
routes (FastAPI's auto-generated ``/openapi.json``, ``/docs``, ``/redoc``) and
``Mount``\\ s therefore land in :data:`UNMATCHED_PATH` too. That is the safe
direction to fail — those paths are fixed in number and carry no SLO signal.

What reaches the log on a 500
-----------------------------
``exc_type`` (a class name, never data) and ``exc_msg`` — the exception text
after :func:`_safe_exc_message` has masked value-shaped substrings and capped
the length. It is **not** ``str(exc)``. The structlog PII processor cannot help
here: ``shared/observability/pii.py`` matches on KEY NAME, popping fields called
``email`` / ``candidate_email`` / ``transcript``, and it never inspects a value —
so an ``exc_msg`` holding asyncpg's statement text and its bound parameters
passes straight through it. Value scrubbing has to happen at the call site, and
this is the call site.

Dependencies
------------
prometheus_client, structlog and **starlette** — deliberately not fastapi.
``shared/`` is imported by all four service images (and by ``shared/agents`` and
``shared/intelligence``), so it may only use what every image already installs;
all four ``requirements.txt`` pin ``starlette==0.46.2``, ``structlog==24.4.0``
and a ``prometheus-client``. Starlette rather than FastAPI because ``FastAPI``
subclasses ``Starlette`` and nothing here needs the FastAPI layer, so the
narrower import keeps this module usable from a plain Starlette app.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import structlog
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "UNKNOWN_METHOD",
    "UNMATCHED_PATH",
    "HTTPObservabilityMiddleware",
    "humanise_field",
    "install_http_observability",
    "install_validation_error_handler",
    "sanitise_validation_errors",
    "validation_error_message",
]

#: Label used for any request the router did not match to a route template.
UNMATCHED_PATH = "__unmatched__"

#: Label used for a request method outside the registered HTTP verb set.
UNKNOWN_METHOD = "__unknown__"

# The method is as attacker-controlled as the path: h11 accepts any RFC 9110
# token, so a caller looping "X-1", "X-2", … is the same CWE-770 vector XS-06
# describes for paths. Membership test rather than .upper() on purpose —
# methods are case-sensitive, so a lowercase "get" is genuinely not GET and
# collapsing it would hide a misbehaving client.
_KNOWN_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)

# /metrics excludes itself from its own counters: at a 15s scrape interval it is
# otherwise the single busiest "endpoint" in every service and drowns the data
# being scraped.
_DEFAULT_EXCLUDED_PATHS = frozenset({"/metrics"})

# 2.0 is present because the platform NFR is "p95 turn latency < 2 seconds".
# histogram_quantile interpolates *within* a bucket, so an SLO threshold with no
# bucket boundary on it can only ever be answered by interpolation; with the
# boundary present, `http_request_duration_seconds_bucket{le="2.0"}` answers
# "what fraction was under the NFR" exactly. The rest is the previous
# data_gateway ladder, kept so its existing series stay comparable.
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0)

_REDACTED = "[redacted]"

_TRUNCATION_MARKER = "...[truncated]"

# Hard ceiling on the exception text that reaches the log stream. str(exc) is
# unbounded — asyncpg renders the whole failing statement plus every bound
# parameter — so an uncapped field turns one 500 in a retry loop into megabytes
# of candidate data in a log store that has no consent-ledger entry behind it
# (DPDP §8). 300 characters is past the end of every driver message worth
# reading: the diagnostic sentence comes first, the payload comes after.
_MAX_EXC_MSG_CHARS = 300

# How much of str(exc) is examined at all. Masking has to run BEFORE the cap (an
# address straddling the boundary must be removed whole rather than halved into
# a fragment that is still personal data), so without a window every regex below
# would scan an unbounded string on the error path — and one of the bound
# parameters asyncpg renders back is a resume.
#
# Four times the cap rather than the cap itself because masking SHORTENS the
# text: a 1KB quoted literal early in the message collapses to nine characters
# and pulls later text into the visible 300. Whatever falls outside the window is
# DROPPED, never merely left unscanned, so the emitted string is always a prefix
# of a string that was fully masked.
_SCAN_WINDOW_CHARS = _MAX_EXC_MSG_CHARS * 4

# A word-internal apostrophe is a contraction or a possessive ("doesn't",
# "candidate's"), not a literal delimiter — but the literal rule below cannot
# tell one from the other, so it pairs that apostrophe with the next real quote
# and every pairing after it is off by one. That is not theoretical: before this
# was parked, `candidate's stored name 'Ravi Kumar' is invalid` masked
# `'s stored name '` and emitted the name.
#
# Postgres escapes an embedded quote by DOUBLING it, and `''` has no word
# character between the two quotes, so real SQL escaping never matches this and
# keeps being over-redacted by the pairwise rule, which is the safe direction.
_APOSTROPHE_SENTINEL = "\x00"
_WORD_APOSTROPHE = re.compile(r"(?<=\w)'(?=\w)")

# Value-shaped substrings, masked before the message is logged. This is a
# denylist and denylists are never complete, which is why the length cap above
# stands behind it rather than beside it — the cap bounds the blast radius of
# anything these miss. What they miss by construction is PII with no lexical
# shape sitting in no delimiter at all (`no candidate named Ravi Kumar`); a
# driver does not produce that, application code could, and the rule for
# application code is still "never put PII in a log message".
#
# The split that makes this work is Postgres' own: DOUBLE quotes delimit
# identifiers (table, column, constraint, type names) and are the entire
# diagnostic value of the message, so they are kept; SINGLE quotes delimit
# literals — the values — so they go.
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # An address anywhere in the text, quoted or bare. First because a match
    # inside a literal or a DETAIL group is the case we most expect. The
    # sentinel is part of the local-part class so that a parked apostrophe
    # (o'brien@example.com) does not split the address and leave half of it.
    re.compile(rf"[\w.+\-{_APOSTROPHE_SENTINEL}]+@[\w-]+\.[\w.-]+"),
    # SQL string literals and the bound parameters asyncpg appends. The closing
    # quote is OPTIONAL because a literal can reach the end of the text with its
    # quote unseen — the scan window above may have cut it there, and it would be
    # perverse for our own truncation to be what exposes the value.
    re.compile(r"'[^']*'?"),
    # A unique-violation DETAIL reads `Key (email)=(x@example.com) already
    # exists.` — the offending value sits in the second group and is NOT quoted,
    # so the rule above misses it. Only the group after the `=` is masked; the
    # first group is the column name, which is what makes the error actionable.
    re.compile(r"(?<==)\([^)]*\)"),
    # Runs of six or more digits: phone numbers, Aadhaar, roll numbers, the
    # candidate IDs a driver quotes back. Shorter runs stay, because SQLSTATE
    # codes, line numbers and column widths are diagnostic.
    re.compile(r"\d{6,}"),
)


def _safe_exc_message(exc: BaseException) -> str:
    """Return exception text with value-shaped substrings masked and capped.

    Over-redaction is the deliberate direction, same trade ``PII_FIELDS`` makes:
    a masked literal costs one round-trip of debugging, an unmasked one writes a
    candidate's e-mail address into the log stream permanently.

    What an operator does now to diagnose a 500: the log line still carries
    ``exc_type``, the route template, and the driver's own prose with identifiers
    intact — ``relation "sessions" does not exist``, ``duplicate key value
    violates unique constraint "users_email_key"`` — which is what names the bug.
    A line ending in ``...[truncated]`` means the rest was longer than the cap or
    the scan window, not that anything was hidden selectively. If the *value* is
    genuinely needed, reproduce against a non-production database; do not reach
    for the raw text here. (Sentry holds the full traceback when ``SENTRY_DSN``
    is set, but note its scrubber is key-based over
    ``request``/``extra``/``breadcrumbs`` and does not touch the exception value
    either, so it is not a PII-free copy — it is an access-controlled one.)
    """
    try:
        raw = str(exc)
    except Exception:  # noqa: BLE001 — see below
        # A __str__ that raises would otherwise throw from inside the handler
        # whose whole job is to be the last thing that can fail, and Starlette
        # would fall back to the bare ASGI 500 this module exists to replace.
        return f"<unprintable {type(exc).__name__}>"

    text = raw[:_SCAN_WINDOW_CHARS]
    dropped = len(raw) > len(text)
    # An incoming NUL would come back out as an apostrophe at the restore below,
    # and it carries no diagnostic value, so it goes first and the sentinel is
    # then unambiguously ours.
    text = text.replace(_APOSTROPHE_SENTINEL, "")
    text = _WORD_APOSTROPHE.sub(_APOSTROPHE_SENTINEL, text)
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    # Only apostrophes in the surviving prose are restored; any that sat inside a
    # masked span went with it.
    text = text.replace(_APOSTROPHE_SENTINEL, "'")
    if len(text) > _MAX_EXC_MSG_CHARS:
        text = text[:_MAX_EXC_MSG_CHARS]
        dropped = True
    return f"{text}{_TRUNCATION_MARKER}" if dropped else text


# Namespaced scope keys. Starlette itself adds top-level scope keys ("route",
# "endpoint", "path_params", "state"); dotted names keep ours from ever colliding
# with those or with another middleware's.
_SCOPE_START = "shared.http_observability.start"
_SCOPE_RECORDED = "shared.http_observability.recorded"

# ``duration is None`` means "this request was never timed" — see _make_recorder.
_Recorder = Callable[[Scope, int, "float | None"], None]


class _HTTPMetrics(NamedTuple):
    """The two collectors every service exposes, bound to one registry."""

    requests_total: Counter
    duration_seconds: Histogram


# One pair of collectors per registry. prometheus_client refuses to register the
# same metric name twice, so without this cache a second install() on the same
# registry — two apps in one test process, or a service that mounts a sub-app —
# would raise at import time.  Keyed by the registry object itself, which is
# identity-hashed (CollectorRegistry defines no __eq__).
_METRICS_BY_REGISTRY: dict[CollectorRegistry, _HTTPMetrics] = {}


def _metrics_for(registry: CollectorRegistry) -> _HTTPMetrics:
    """Return (creating once) the request counter and latency histogram."""
    cached = _METRICS_BY_REGISTRY.get(registry)
    if cached is not None:
        return cached

    try:
        metrics = _HTTPMetrics(
            requests_total=Counter(
                "http_requests_total",
                "Total HTTP requests received",
                ["service", "method", "path", "status_code"],
                registry=registry,
            ),
            duration_seconds=Histogram(
                "http_request_duration_seconds",
                "HTTP request latency in seconds",
                ["service", "method", "path"],
                buckets=_LATENCY_BUCKETS,
                registry=registry,
            ),
        )
    except ValueError as exc:
        # prometheus_client's own message ("Duplicated timeseries in
        # CollectorRegistry") does not say what to do about it, and the only way
        # to reach this branch is a service that still defines these metrics in
        # its own main.py — the exact state adoption of this module removes.
        raise RuntimeError(
            "http_requests_total / http_request_duration_seconds are already "
            "registered on this Prometheus registry by something other than "
            "shared.http_observability. Delete the service-local Counter/Histogram "
            "definitions when adopting install_http_observability()."
        ) from exc

    _METRICS_BY_REGISTRY[registry] = metrics
    return metrics


def _route_label(scope: Scope) -> str:
    """Return the matched route template, or :data:`UNMATCHED_PATH`.

    ``scope["route"]`` is set by ``fastapi.routing.APIRoute.matches`` for every
    match — including a ``Match.PARTIAL`` (a 405: right path, wrong method), so a
    405 is still attributed to the endpoint it was aimed at rather than being
    lost in the unmatched bucket.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else UNMATCHED_PATH


def _method_label(method: object) -> str:
    return method if isinstance(method, str) and method in _KNOWN_METHODS else UNKNOWN_METHOD


def _make_recorder(
    *,
    metrics: _HTTPMetrics,
    service_name: str,
    excluded_paths: frozenset[str],
) -> _Recorder:
    """Build the single function that writes a request into both collectors.

    Recording is idempotent per request. The middleware records first because it
    holds the timer; the exception handler then calls the same function as a
    fallback for exceptions raised *outside* the middleware (by a middleware
    added after it, therefore wrapping it). Without the idempotency flag those
    two paths would double-count every 500 in the normal case.
    """

    def record(scope: Scope, status_code: int, duration: float | None) -> None:
        if scope.get(_SCOPE_RECORDED):
            return
        # Set before the exclusion check: an excluded path is "already handled",
        # not "still to be recorded by the next caller".
        scope[_SCOPE_RECORDED] = True

        path = _route_label(scope)
        if path in excluded_paths:
            return

        method = _method_label(scope.get("method"))
        metrics.requests_total.labels(
            service=service_name,
            method=method,
            path=path,
            status_code=str(status_code),
        ).inc()
        # An untimed request contributes to the count but NOT to the histogram:
        # a fabricated 0.0 observation would drag down the very p95 the NFR is
        # measured against, and a missing sample is honest where a wrong one is
        # not.
        if duration is not None:
            metrics.duration_seconds.labels(
                service=service_name, method=method, path=path
            ).observe(duration)

    return record


class HTTPObservabilityMiddleware:
    """Pure-ASGI request timer. Installed by :func:`install_http_observability`.

    Not ``BaseHTTPMiddleware``, which is what the original data_gateway version
    used: BaseHTTPMiddleware wraps every request in an anyio task group and
    proxies the response through memory object streams, which costs latency on
    the hot path and interposes on streaming responses. ``interview_core`` is
    both the service this module exists to instrument and the one holding the
    p95 < 2s NFR, so the measuring instrument must not be part of what is
    measured.

    A pure-ASGI wrapper also handles the case BaseHTTPMiddleware cannot: when the
    application raises *after* the response has started, the real status code has
    already gone out on the wire and is recorded, instead of being reported as a
    500 that the client never saw.
    """

    def __init__(self, app: ASGIApp, *, recorder: _Recorder) -> None:
        self.app = app
        self._record = recorder

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan and websocket scopes pass through untouched: neither has a
        # status code, and a websocket's "duration" is its session length, which
        # would be indistinguishable from a slow request in the same histogram.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        scope[_SCOPE_START] = start
        # Only survives as the recorded value if the app raises before sending
        # anything, which is precisely the case that becomes a 500.
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self._record(scope, status_code, time.perf_counter() - start)
            # Re-raised so Starlette's ServerErrorMiddleware still produces the
            # response (below), Sentry still sees the exception, and a test
            # client still fails loudly rather than silently getting a 500.
            raise
        self._record(scope, status_code, time.perf_counter() - start)


def install_http_observability(
    app: Starlette,
    *,
    service_name: str,
    registry: CollectorRegistry | None = None,
    excluded_paths: Iterable[str] | None = None,
) -> None:
    """Install HTTP request metrics and a generic unhandled-exception handler.

    Must be called at import time, before the application starts: Starlette
    refuses ``add_middleware`` once the middleware stack has been built.

    Call it **after** the service's other ``add_middleware`` calls. Starlette
    inserts each new middleware at the outside of the stack, so installing last
    makes this the outermost user middleware and the recorded latency is what
    the client actually experienced rather than what was left after CORS. The
    cost is that a CORS preflight, which never reaches the router, is attributed
    to :data:`UNMATCHED_PATH` — one extra series, not a per-path one.

    :param app: the ``FastAPI``/``Starlette`` application.
    :param service_name: value of the ``service`` metric label and of the
        ``service`` log field. The four services export identically *named*
        series, and not every scrape topology distinguishes them by target
        labels (Railway's built-in plugin, or one host serving several services
        on different paths), so the series carries its own origin.
    :param registry: Prometheus registry to register on. Defaults to the global
        one; ``admin_ops`` passes its private ``CollectorRegistry`` so the shared
        HTTP metrics land alongside its business metrics rather than in a
        registry it never exposes.
    :param excluded_paths: route templates to leave out of the metrics; defaults
        to ``{"/metrics"}``.
    """
    metrics = _metrics_for(registry if registry is not None else REGISTRY)
    excluded = (
        frozenset(excluded_paths) if excluded_paths is not None else _DEFAULT_EXCLUDED_PATHS
    )
    record = _make_recorder(
        metrics=metrics, service_name=service_name, excluded_paths=excluded
    )
    # Deliberately the lazy proxy, never a bound logger: the proxy re-resolves
    # structlog's configuration on every call, so a service that configures
    # structlog *after* installing this layer (and structlog.testing.capture_logs
    # in the tests) still sees these events.
    log = structlog.get_logger("shared.http_observability")

    # Starlette's ServerErrorMiddleware checks ``self.debug`` BEFORE it checks
    # for a registered 500 handler, so a debug-mode app answers with the full
    # traceback page and this handler is never called. That is the one way to
    # install the CWE-209 fix and have it silently do nothing, so say so out
    # loud at startup rather than leaving it to be discovered in an incident.
    if getattr(app, "debug", False):
        log.warning("http_observability.debug_bypasses_exception_handler", service=service_name)

    app.add_middleware(HTTPObservabilityMiddleware, recorder=record)

    async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
        """Turn any unhandled exception into a generic 500 (XS-01, CWE-209).

        The default for an unhandled exception is to let it reach the ASGI
        server, whose response body depends on the server and can carry a driver
        message; asyncpg puts the offending SQL and its parameter values into
        ``str(exc)``, and those parameters are candidate PII. So the client gets
        a fixed body with no exception text, and the detail goes to the log
        stream **through :func:`_safe_exc_message`** — masked and length-capped
        at this call site.

        That last part used to read "the log stream, which every service has
        already fitted with the PII-redacting processor from
        ``shared/observability/pii.py``". It was a false assurance: that
        processor pops known PII *key names* off the event dict and never looks
        at a value, and ``exc_msg`` is not one of those keys. Moving the SQL out
        of the response body and into an unscrubbed log field would have changed
        where the PII lands, not whether it lands (DPDP §8 — log storage has no
        consent-ledger entry behind it).

        This runs inside Starlette's ``ServerErrorMiddleware``, i.e. *outside*
        the metrics middleware, which has therefore already recorded the 500 and
        marked the scope; the ``record`` call below is a no-op in that ordinary
        case and only fires for an exception raised outside the middleware.
        Starlette re-raises after this returns, so nothing downstream loses the
        exception.
        """
        start = request.scope.get(_SCOPE_START)
        duration = time.perf_counter() - start if isinstance(start, float) else None
        record(request.scope, 500, duration)

        log.error(
            "http.unhandled_exception",
            service=service_name,
            method=request.scope.get("method"),
            path=_route_label(request.scope),
            exc_type=type(exc).__name__,
            exc_msg=_safe_exc_message(exc),
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    app.add_exception_handler(Exception, unhandled_exception_handler)


# ---------------------------------------------------------------------------
# Request validation errors (audit X1)
# ---------------------------------------------------------------------------
#
# FastAPI answers a request that fails its Pydantic model with
# ``{"detail": [{"type", "loc", "msg", "input", "ctx"}, ...]}``. Two things were
# wrong with that default, and both are fixed here once instead of per service:
#
# * **Nobody could read it.** The web client only understood a string or an
#   object ``detail``, so every one of these surfaced as the toast "HTTP 422" on
#   every form in the product. The handler below keeps ``detail`` as the same
#   array (tests and callers index ``detail[0]["loc"]``) and ADDS a top-level
#   ``message``: one plain sentence about the first problem, with the field name
#   written the way a person would say it.
# * **It echoed what was submitted.** ``input`` is the raw value the caller sent,
#   so a register form that failed on a weak password sent the password back in
#   the response body, where proxies, browser extensions and error trackers can
#   keep it. ``input`` is dropped, and the submitted value is masked wherever an
#   application validator quoted it back inside ``msg`` or ``ctx``.

#: Where a value came from. The first ``loc`` element names it, and it is never
#: the field a person typed into.
_LOC_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})

#: Tags Pydantic inserts into ``loc`` for union members and wrapped validators
#: (``("body", "email", "function-after[...]")``). They name a type, not a field.
_LOC_TYPE_TAGS = frozenset(
    {"str", "int", "float", "bool", "list", "dict", "set", "tuple", "bytes", "none", "literal"}
)

#: Field-name words that read wrong in sentence case.
_FIELD_ACRONYMS = {
    "id": "ID",
    "ids": "IDs",
    "url": "URL",
    "urls": "URLs",
    "jd": "JD",
    "ats": "ATS",
    "ctc": "CTC",
    "mcq": "MCQ",
    "pdf": "PDF",
    "otp": "OTP",
    "sla": "SLA",
    "utc": "UTC",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_EMAIL_IN_TEXT = _VALUE_PATTERNS[0]

#: Shortest free-text value that is masked where a message quotes it back. One-
#: to three-character words ("a", "no", "and") are not secrets, and masking them
#: would shred ordinary words in the sentence ("a" inside "a valid integer").
#: Two kinds of value are masked when shorter, because short is exactly what
#: they are: everything submitted under a secret-looking field name is
#: masked at any length, and an all-digit value (a PIN or an OTP under a field
#: name we did not foresee) from three digits. One- and two-digit numbers stay:
#: they are the counts and limits a message legitimately states ("at least 1").
_MIN_ECHO_MASK_CHARS = 4
_MIN_ECHO_MASK_DIGITS = 3

#: A field whose value is a secret whatever its length. Matched against the
#: snake-cased field name one word at a time, so "pin" catches ``pin`` and
#: ``login_pin`` but not ``shipping``.
_SECRET_FIELD_WORDS = frozenset(
    {
        "password",
        "passwd",
        "passcode",
        "passphrase",
        "pwd",
        "pin",
        "otp",
        "token",
        "secret",
        "apikey",
        "credential",
        "credentials",
    }
)

#: A submitted value is a structure, not a scalar, when a model-level validator
#: fails (``loc == ("body",)``, ``input`` is the whole body). Walking it is
#: bounded so a hostile, deeply nested body cannot make the 422 path expensive.
_ECHO_WALK_MAX_DEPTH = 8
_ECHO_WALK_MAX_LEAVES = 500

#: What replaces a submitted value that a message quoted back.
_ECHO_PLACEHOLDER = "this value"

_REQUEST_UNREADABLE = "The request could not be read. Refresh the page and try again."

#: Pydantic error types that mean "the body is not the right shape at all".
_SHAPE_ERRORS = frozenset({"json_invalid", "model_attributes_type", "dict_type", "model_type"})

#: Bound errors: the ``ctx`` key holding the limit, and how to say it.
_BOUND_ERRORS = {
    "greater_than_equal": ("ge", "{} or more"),
    "greater_than": ("gt", "more than {}"),
    "less_than_equal": ("le", "{} or less"),
    "less_than": ("lt", "less than {}"),
}


def humanise_field(loc: Sequence[object]) -> str | None:
    """The field a validation error is about, as a person would name it.

    ``("body", "max_candidates")`` gives ``"Max candidates"``,
    ``("body", "items", 2, "unitPrice")`` gives ``"Unit price"`` (the innermost
    named field; list indexes are skipped), and ``("body",)`` gives ``None``
    because the error is about the request as a whole.
    """
    names = [
        part
        for i, part in enumerate(loc)
        if isinstance(part, str)
        and _IDENTIFIER.match(part)
        and not (i == 0 and part in _LOC_SOURCES)
    ]
    # Drop trailing union/validator tags, but only while a real field name is
    # still in front of them: a field genuinely called "list" stays.
    while len(names) > 1 and names[-1].lower() in _LOC_TYPE_TAGS:
        names.pop()
    if not names:
        return None
    words = [w for w in _CAMEL_BOUNDARY.sub("_", names[-1]).lower().split("_") if w]
    if not words:
        return None
    words = [_FIELD_ACRONYMS.get(w, w) for w in words]
    words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def _is_secret_field(name: object) -> bool:
    """Whether a field name says its value is a secret (password, PIN, token)."""
    if not isinstance(name, str):
        return False
    words = _CAMEL_BOUNDARY.sub("_", name).lower().replace("-", "_").split("_")
    return any(w in _SECRET_FIELD_WORDS for w in words) or "api_key" in "_".join(words)


def _loc_field_name(loc: object) -> str | None:
    """The innermost named field in ``loc``, raw (not humanised)."""
    if not isinstance(loc, list | tuple):
        return None
    names = [
        part
        for i, part in enumerate(loc)
        if isinstance(part, str) and not (i == 0 and part in _LOC_SOURCES)
    ]
    while len(names) > 1 and names[-1].lower() in _LOC_TYPE_TAGS:
        names.pop()
    return names[-1] if names else None


def _echo_values(submitted: object, field: str | None) -> list[tuple[str, bool]]:
    """Every scalar inside *submitted*, as ``(text, is_secret)``.

    A scalar is one value. A mapping or a list (a model-level validator's
    ``input`` is the whole body) is walked, and each leaf is judged by the key it
    sits under, so ``{"password": "1234"}`` marks ``"1234"`` as a secret.
    """
    found: list[tuple[str, bool]] = []

    def walk(value: object, key: str | None, depth: int) -> None:
        if len(found) >= _ECHO_WALK_MAX_LEAVES or depth > _ECHO_WALK_MAX_DEPTH:
            return
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, str | int | float):
            found.append((str(value), _is_secret_field(key)))
        elif isinstance(value, Mapping):
            for k, v in value.items():
                walk(v, k if isinstance(k, str) else key, depth + 1)
        elif isinstance(value, list | tuple | set | frozenset):
            for v in value:
                walk(v, key, depth + 1)

    walk(submitted, field, 0)
    return found


def _mask_echo(text: str, submitted: object, field: str | None = None) -> str:
    """Remove the submitted value(s) (and any e-mail address) from *text*.

    *field* is the raw field name the error is about; a secret-looking one masks
    its value at any length. Longer values are replaced first so a short value
    that is part of a longer one cannot leave half of the longer one behind.
    """
    values = sorted(set(_echo_values(submitted, field)), key=lambda v: -len(v[0]))
    for value, secret in values:
        if not value or value not in text:
            continue
        short_digits = value.isdigit() and len(value) >= _MIN_ECHO_MASK_DIGITS
        if secret or short_digits:
            # Short secrets are masked only where they stand alone, so PIN "12"
            # does not eat the "12" inside "2012"; a long one is masked anywhere.
            if len(value) >= _MIN_ECHO_MASK_CHARS:
                text = text.replace(value, _ECHO_PLACEHOLDER)
            else:
                text = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])",
                    _ECHO_PLACEHOLDER,
                    text,
                )
        elif len(value) >= _MIN_ECHO_MASK_CHARS:
            text = text.replace(value, _ECHO_PLACEHOLDER)
    return _EMAIL_IN_TEXT.sub(_REDACTED, text)


def _sentence(text: str) -> str:
    text = text.strip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _describe(error: Mapping[str, Any]) -> str:
    """One plain sentence for one Pydantic error (already sanitised)."""
    kind = str(error.get("type", ""))
    raw_ctx = error.get("ctx")
    ctx: Mapping[str, Any] = raw_ctx if isinstance(raw_ctx, Mapping) else {}
    raw_loc = error.get("loc")
    field = humanise_field(raw_loc if isinstance(raw_loc, list | tuple) else ())
    subject = field or "This value"
    msg = str(error.get("msg", "")).strip()
    for prefix in ("Value error, ", "Assertion failed, "):
        if msg.startswith(prefix):
            msg = msg[len(prefix) :]

    if kind in _SHAPE_ERRORS and not field:
        return _REQUEST_UNREADABLE
    if kind == "missing":
        return f"{field} is required." if field else "Some required information is missing."
    if kind == "string_too_short":
        minimum = ctx.get("min_length")
        if minimum in (None, 1):
            return f"{subject} cannot be empty."
        return f"{subject} must be at least {minimum} characters."
    if kind == "string_too_long" and "max_length" in ctx:
        return f"{subject} must be at most {ctx['max_length']} characters."
    if kind == "too_short" and "min_length" in ctx:
        return f"{subject} needs at least {ctx['min_length']} items."
    if kind == "too_long" and "max_length" in ctx:
        return f"{subject} can have at most {ctx['max_length']} items."
    if kind in _BOUND_ERRORS and _BOUND_ERRORS[kind][0] in ctx:
        key, template = _BOUND_ERRORS[kind]
        return f"{subject} must be {template.format(ctx[key])}."
    if kind in {"int_parsing", "int_type", "int_from_float"}:
        return f"{subject} must be a whole number."
    if kind in {"float_parsing", "float_type", "decimal_parsing", "decimal_type"}:
        return f"{subject} must be a number."
    if kind in {"bool_parsing", "bool_type"}:
        return f"{subject} must be yes or no."
    if kind in {"literal_error", "enum"} and "expected" in ctx:
        return f"{subject} must be one of {ctx['expected']}."
    if kind.startswith("uuid"):
        return f"{subject} is not a valid ID."
    if kind.startswith(("datetime", "date", "time_")):
        return f"{subject} must be a valid date and time."
    if kind in {"timezone_aware", "timezone_naive"}:
        return f"{subject} needs a time zone."
    if kind.startswith("url"):
        return f"{subject} must be a valid web address."
    if msg.startswith("value is not a valid email address"):
        return f"{subject} must be a valid email address."
    if not msg:
        return f"{subject} is not valid."
    return _sentence(f"{field}: {msg}" if field else msg)


def sanitise_validation_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The ``detail`` array with every echo of the submitted values removed.

    Keeps ``type``, ``loc``, ``msg`` and ``ctx`` (callers index ``loc`` and read
    ``ctx["max_length"]``); drops ``input`` and ``url``; masks the submitted
    value wherever ``msg`` or a string in ``ctx`` quotes it back, plus any e-mail
    address in either. A ``ctx`` value that is not a JSON scalar (an application
    validator's ``ValueError`` lands there) is rendered with ``str`` and masked
    the same way.
    """
    clean: list[dict[str, Any]] = []
    for error in errors:
        submitted = error.get("input")
        field = _loc_field_name(error.get("loc"))
        item: dict[str, Any] = {
            "type": str(error.get("type", "")),
            # ``loc`` keeps FastAPI's shape: callers index it. A ``dict[str, ...]``
            # field puts the caller's own keys in it, so an e-mail used as a key
            # is masked like one anywhere else.
            "loc": [
                p if isinstance(p, int) and not isinstance(p, bool)
                else _EMAIL_IN_TEXT.sub(_REDACTED, str(p))
                for p in error.get("loc", ())
            ],
            "msg": _mask_echo(str(error.get("msg", "")), submitted, field),
        }
        raw_ctx = error.get("ctx")
        if isinstance(raw_ctx, Mapping):
            ctx: dict[str, Any] = {}
            for key, value in raw_ctx.items():
                if value is None or isinstance(value, bool | int | float):
                    ctx[str(key)] = value
                else:
                    ctx[str(key)] = _mask_echo(str(value), submitted, field)
            item["ctx"] = ctx
        clean.append(item)
    return clean


def validation_error_message(errors: Sequence[Mapping[str, Any]]) -> str:
    """One sentence describing the FIRST problem, for showing to a person.

    Only the first: a form shows one message, and fixing the first problem is
    what moves the person forward. Pass errors through
    :func:`sanitise_validation_errors` first; this function does not mask.
    """
    if not errors:
        return _REQUEST_UNREADABLE
    return _describe(errors[0])


def install_validation_error_handler(app: Starlette) -> None:
    """Register the readable, non-echoing 422 for FastAPI request validation.

    The response keeps FastAPI's shape, ``{"detail": [...]}`` with the same
    ``type``/``loc``/``msg``/``ctx`` keys, and adds ``"message"``::

        {"detail": [{"type": "missing", "loc": ["body", "title"],
                     "msg": "Field required"}],
         "message": "Title is required."}

    Imported lazily because this module deliberately depends on Starlette, not
    FastAPI (see the module docstring); only a FastAPI app can raise
    ``RequestValidationError`` in the first place. Call it next to
    :func:`install_http_observability`, at import time.
    """
    from fastapi.exceptions import RequestValidationError

    async def request_validation_handler(request: Request, exc: Exception) -> Response:
        raw = exc.errors() if isinstance(exc, RequestValidationError) else []
        detail = sanitise_validation_errors(raw)
        return JSONResponse(
            status_code=422,
            content={"detail": detail, "message": validation_error_message(detail)},
        )

    app.add_exception_handler(RequestValidationError, request_validation_handler)
