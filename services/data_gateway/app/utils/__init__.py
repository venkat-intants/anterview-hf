"""Infrastructure-level helpers shared by routers AND by non-router modules.

Everything here must be importable by ``app.rate_limit``, ``app.main`` and the
routers alike, so nothing in this package may import from ``app.routers``.
That one rule is the whole point of the package: the request-IP extractor used
to live in ``app/routers/consent.py``, which forced the rate limiter — a piece
of infrastructure that runs before any route is chosen — to import a route
module.
"""
