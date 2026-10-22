"""The copilot routes' input and spend limits (audit CP-F2, CP-L3).

* A long assistant reply replayed as history used to 422 every later message,
  because history turns were held to the same 2,000-character cap as a new
  message while the reply itself has no cap. History is now truncated to that
  cap instead, so the model still never sees more than it did.
* /agent/chat and /agent/panel spend LLM budget (the panel makes four or five
  model calls per click) and carried no rate limit of their own.
* The copilot sends an Idempotency-Key on every commit, which a split-origin
  deploy refuses at preflight unless CORS allows it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.routers.agent import (
    AGENT_CHAT_PER_MINUTE,
    AGENT_PANEL_PER_MINUTE,
    MAX_HISTORY_TEXT_ACCEPTED,
    MAX_MESSAGE_CHARS,
    ChatIn,
)


def test_a_long_history_turn_is_truncated_not_refused() -> None:
    body = ChatIn(
        message="who else should I interview?",
        history=[{"role": "assistant", "text": "x" * (MAX_MESSAGE_CHARS * 3)}],
    )
    assert len(body.history[0].text) == MAX_MESSAGE_CHARS


def test_a_short_history_turn_is_left_alone() -> None:
    body = ChatIn(message="hi", history=[{"role": "user", "text": "hello"}])
    assert body.history[0].text == "hello"


def test_an_absurd_history_payload_is_still_refused() -> None:
    with pytest.raises(ValidationError):
        ChatIn(message="hi", history=[{"role": "user", "text": "x" * (MAX_HISTORY_TEXT_ACCEPTED + 1)}])


def test_the_new_message_itself_keeps_its_hard_cap() -> None:
    """Truncating someone's question would answer a question they did not ask."""
    with pytest.raises(ValidationError):
        ChatIn(message="x" * (MAX_MESSAGE_CHARS + 1))


def _dependency_names(route: Any) -> set[str]:
    out: set[str] = set()
    stack = [route.dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None:
            out.add(getattr(call, "__qualname__", ""))
        stack.extend(node.dependencies)
    return out


@pytest.mark.parametrize(
    ("path", "limit"),
    [("/agent/chat", AGENT_CHAT_PER_MINUTE), ("/agent/panel/{applicant_id}", AGENT_PANEL_PER_MINUTE)],
)
def test_the_llm_spending_routes_are_rate_limited_per_account(path: str, limit: int) -> None:
    from app.main import app

    routes = [r for r in app.routes if getattr(r, "path", "") == path]
    assert routes, f"{path} is not mounted (vacuous pass?)"
    for route in routes:
        assert "rate_limit_actor.<locals>._dep" in _dependency_names(route), path
    assert 0 < limit <= 30


def test_cors_allows_the_commit_idempotency_header() -> None:
    from starlette.middleware.cors import CORSMiddleware

    from app.main import app

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert cors, "CORS middleware not installed"
    allowed = {h.lower() for h in cors[0].kwargs["allow_headers"]}
    assert "idempotency-key" in allowed
