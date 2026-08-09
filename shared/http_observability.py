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

import time
from collections.abc import Callable, Iterable
from typing import NamedTuple

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
    "install_http_observability",
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
        a fixed body with no exception text and the detail goes to the log
        stream, which every service has already fitted with the PII-redacting
        processor from ``shared/observability/pii.py``.

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
            exc_msg=str(exc),
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    app.add_exception_handler(Exception, unhandled_exception_handler)
