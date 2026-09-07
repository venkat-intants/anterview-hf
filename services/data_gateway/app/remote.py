"""Turning a dead sibling service into a message someone can act on.

Every AI-backed feature in data_gateway — exam generation, coding-question
generation, resume scoring, semantic search — is a call to ``feedback_billing``.
When that service is not running, httpx raises ``ConnectError`` and the
handlers wrap it as a 502. The wrapping was accurate and useless:

    AI generation failed: exam generator unreachable:
    All connection attempts failed

Nothing in that names the service to start, and the reader is left deciding
between a missing API key, a bad prompt, a network problem and a stopped
process — which is a slow way to discover that the answer was ``uvicorn`` in
another terminal. This cost real time during local testing, twice.

Written once and shared by all seven call sites rather than improved in the one
that happened to be hit: seven near-identical error strings are seven strings
that drift, and the next person debugging a different feature would get the
old, unhelpful version.

A CONNECT failure is separated from every other transport error on purpose.
"Nothing is listening" has one obvious fix and is by far the most common case
in development; a timeout or a read error during a long generation is a
different problem and must not be described as a stopped service.
"""

from __future__ import annotations

import httpx


def describe_unreachable(exc: httpx.RequestError, *, what: str, url: str) -> str:
    """A one-line explanation of a failed call to another service.

    ``what`` names the capability in the user's terms ("exam generation"), not
    the endpoint — the person reading this is looking at a feature that did not
    work, and the route is in the URL anyway.
    """
    origin = _origin(url)
    if isinstance(exc, httpx.ConnectError):
        return (
            f"{what} needs the feedback_billing service, and nothing is "
            f"listening on {origin}. Start it:  cd services/feedback_billing "
            f"&& .venv/Scripts/python -m uvicorn app.main:app --port "
            f"{_port(origin)}"
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"{what} timed out waiting for feedback_billing at {origin}. The "
            "service is up but the model call did not finish — try fewer items, "
            "or check that service's logs."
        )
    return f"{what} could not reach feedback_billing at {origin}: {exc}"


def _origin(url: str) -> str:
    """Scheme, host and port — never the path, and never a query string.

    These messages reach an HTTP response body. A full URL would be fine today,
    but keeping it to the origin means a call that ever carries something
    identifying in its path cannot leak it into an error a browser displays.
    """
    try:
        parsed = httpx.URL(url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.host}{port}"
    except Exception:  # noqa: BLE001 — a malformed URL must not replace the real error
        return url


def _port(origin: str) -> str:
    tail = origin.rsplit(":", 1)[-1]
    return tail if tail.isdigit() else "8003"
