"""Application-layer auth for ``GET /metrics``, shared by all four services.

Security-audit finding M-6 (CWE-497, exposure of sensitive information): every
service registers ``/metrics`` with no application-layer check. Today that is
covered at both edges (``Caddyfile:46``, ``space/Caddyfile:60``), but
``render.yaml`` describes a topology with **no** proxy in front — each backend
gets its own public hostname. In that shape the scrape endpoint is world
readable, and Prometheus exposition is not innocuous: per-endpoint request
counts, status-code distribution and latency histograms map the whole route
table, expose which admin paths exist, and leak traffic volume and error spikes.
An edge-only control that one deploy target does not have is not a control.

The gate is deliberately shaped so that turning it on cannot take a running
service down:

* ``METRICS_TOKEN`` set — the request must carry ``Authorization: Bearer
  <token>``. Anything else is a 401.
* ``METRICS_TOKEN`` unset **in production** — 401/404 rather than open. Fail
  closed, but as a *response*, not a startup crash: a forgotten token must never
  turn a rolling deploy into an outage, and the operator sees the failure in the
  scrape rather than in a crash loop. 404 (not 403) is chosen so an
  unauthenticated prober learns nothing — the reply is indistinguishable from a
  service that never registered the route.
* ``METRICS_TOKEN`` unset outside production — allowed. Local ``dev-up.ps1``,
  docker-compose and CI keep scraping with no config change.

Why an exception type instead of ``fastapi.HTTPException``: nothing under
``shared/`` imports fastapi or starlette, and ``shared/pyproject.toml`` declares
only pydantic, python-jose and structlog. That dependency-light rule is what
lets all four service images (and the agents/intelligence packages) import
``shared`` freely. Adding a web framework to it for one helper would be a real
cost for a cosmetic gain, so this module raises :class:`MetricsAuthError` and the
caller translates — one line in each service's ``/metrics`` handler::

    from shared.metrics_auth import MetricsAuthError, check_metrics_auth

    @app.get("/metrics", ...)
    async def metrics(authorization: str | None = Header(default=None)) -> Response:
        try:
            check_metrics_auth(
                authorization=authorization,
                metrics_token=settings.metrics_token,
                app_env=settings.app_env,
            )
        except MetricsAuthError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail, headers=exc.headers
            ) from exc
        ...
"""

from __future__ import annotations

import hmac

from shared.security import normalise_app_env


class MetricsAuthError(Exception):
    """Raised when a ``/metrics`` request must be refused.

    Carries the HTTP shape of the refusal so callers can translate it without
    re-deriving the policy: ``raise HTTPException(status_code=exc.status_code,
    detail=exc.detail, headers=exc.headers)``. Deciding 401-vs-404 belongs to
    this module (see the module docstring), not to four copies of a route
    handler that would inevitably drift.
    """

    def __init__(
        self,
        status_code: int,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers


def check_metrics_auth(
    *,
    authorization: str | None,
    metrics_token: str | None,
    app_env: str,
) -> None:
    """Authorise a ``/metrics`` scrape. Returns ``None`` if allowed.

    :param authorization: raw ``Authorization`` request header, or ``None``.
    :param metrics_token: the configured ``METRICS_TOKEN`` (``None``/blank = unset).
    :param app_env: raw ``APP_ENV``; normalised here, so ``"Production"`` cannot
        slip past the production gate.
    :raises MetricsAuthError: 401 on a bad/missing bearer token, 404 when no
        token is configured in production.
    """
    # ``.env.example`` ships ``METRICS_TOKEN=`` (empty), and pydantic hands that
    # through as ``""`` rather than ``None``. Treating an empty string as "set"
    # would demand the literal header ``"Bearer "`` and lock every environment
    # out of its own scrape endpoint, so blank means unset.
    token = (metrics_token or "").strip()

    if not token:
        if normalise_app_env(app_env) == "production":
            raise MetricsAuthError(
                status_code=404,
                detail="Not Found",
            )
        # Dev/test/staging with no token configured: unchanged, open behaviour.
        return

    # Compare as bytes, not str. hmac.compare_digest raises TypeError on str
    # arguments containing non-ASCII characters, and starlette decodes raw header
    # bytes as latin-1 — so a header of ``Bearer \xff`` would turn an
    # unauthorised request into a 500 (and a trivially reachable one). Encoding
    # first removes that whole class of failure; surrogateescape covers any lone
    # surrogate a decoder upstream may have produced.
    supplied = (authorization or "").encode("utf-8", "surrogateescape")
    expected = f"Bearer {token}".encode()

    # compare_digest still leaks the length of the supplied header, which is
    # public information anyway; what matters is that it does not leak how many
    # leading bytes of the *token* were guessed correctly.
    if not hmac.compare_digest(supplied, expected):
        raise MetricsAuthError(
            status_code=401,
            detail="Invalid or missing metrics credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
