"""Errors for a dead sibling service.

The 502 that reaches a browser when feedback_billing is not running used to say
"unreachable: All connection attempts failed", which is accurate and leaves the
reader choosing between a missing API key, a bad prompt, a network fault and a
stopped process. It was the stopped process, twice, and both times it cost real
time. These pin the part that made it slow: naming the service and the command.
"""

from __future__ import annotations

import httpx
import pytest

from app.remote import describe_unreachable

_URL = "http://localhost:8003/internal/generate-exam"


def test_a_stopped_service_names_itself_and_the_command() -> None:
    msg = describe_unreachable(
        httpx.ConnectError("All connection attempts failed"),
        what="Exam generation", url=_URL,
    )
    assert "feedback_billing" in msg
    assert "nothing is listening" in msg
    assert "uvicorn" in msg and "8003" in msg


def test_it_leads_with_the_capability_not_the_endpoint() -> None:
    """The reader is looking at a feature that did not work, not a route."""
    msg = describe_unreachable(httpx.ConnectError("x"), what="Resume scoring", url=_URL)
    assert msg.startswith("Resume scoring")


def test_a_timeout_is_not_described_as_a_stopped_service() -> None:
    """Opposite fixes. Telling someone to start a service that is already
    running sends them to look in the one place the problem is not."""
    msg = describe_unreachable(httpx.ReadTimeout("slow"), what="Exam generation", url=_URL)
    assert "timed out" in msg
    assert "nothing is listening" not in msg
    assert "uvicorn" not in msg


def test_any_other_transport_error_still_says_which_service() -> None:
    msg = describe_unreachable(httpx.RequestError("boom"), what="Match explanation", url=_URL)
    assert "feedback_billing" in msg and "boom" in msg


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8003/internal/generate-exam?applicant=ada@example.com",
        "https://fb.internal:9443/internal/score-resume/abc-123",
    ],
)
def test_only_the_origin_is_echoed_never_the_path(url: str) -> None:
    """These strings reach an HTTP response body a browser displays. Nothing
    identifying that ever ends up in a path or query should ride along."""
    msg = describe_unreachable(httpx.ConnectError("x"), what="Scoring", url=url)
    assert "internal/" not in msg
    assert "ada@example.com" not in msg and "abc-123" not in msg


def test_a_malformed_url_does_not_replace_the_real_error() -> None:
    """Formatting the diagnosis must never become the failure being diagnosed."""
    msg = describe_unreachable(httpx.ConnectError("x"), what="Scoring", url="::::not a url")
    assert "Scoring" in msg
