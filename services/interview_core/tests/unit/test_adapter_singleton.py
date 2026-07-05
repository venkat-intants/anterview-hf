"""Regression tests for S4-013 — LLM adapter DI invariants.

After the S4-013 refactor, the module-level ``_default_adapter`` singleton
and the ``set_default_adapter`` / ``_require_adapter`` helpers have been
removed from ``app.graph.nodes``. The two invariants this file tests are:

1. ``test_adapter_installed_on_app_state_at_startup``
   FastAPI startup stores the adapter on ``app.state.llm_adapter`` (not on
   a module global). We drive the lifespan via ``with TestClient(app):`` and
   assert the attribute is present on the app object.

2. ``test_ask_question_and_follow_up_require_adapter_kwarg``
   ``ask_question`` and ``follow_up`` now accept an explicit
   ``adapter: LLMAdapter`` keyword argument. Calling them without it should
   raise ``TypeError``. This guards against callers accidentally reverting
   to the old implicit-singleton pattern.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMAdapter

# ---------------------------------------------------------------------------
# Test 1 — startup hook stores the adapter on app.state
# ---------------------------------------------------------------------------


def test_adapter_installed_on_app_state_at_startup() -> None:
    """FastAPI startup stores the LLM adapter on ``app.state.llm_adapter``.

    Uses ``with TestClient(app):`` to trigger the startup lifespan.
    ``build_default_adapter`` is patched so no real Gemini client is built —
    the test stays fully offline and configuration-agnostic.
    """
    from app.main import app

    dummy_adapter = MagicMock(spec=LLMAdapter)

    with patch("app.main.build_default_adapter", return_value=dummy_adapter), TestClient(app):
        installed = app.state.llm_adapter

    assert installed is not None, (
        "Expected app.state.llm_adapter to be set after startup, but it is None. "
        "Check that app.main.startup() sets app.state.llm_adapter = build_default_adapter()."
    )
    assert installed is dummy_adapter, (
        f"Expected app.state.llm_adapter to be the dummy returned by build_default_adapter(), "
        f"got {installed!r} instead."
    )


# ---------------------------------------------------------------------------
# Test 2 — nodes require explicit adapter kwarg
# ---------------------------------------------------------------------------


def test_ask_question_and_follow_up_require_adapter_kwarg() -> None:
    """``ask_question`` / ``follow_up`` must be called with ``adapter=`` keyword.

    Calling without the keyword argument must raise ``TypeError`` — this
    prevents silent regressions where code accidentally calls the node
    without the required dependency.
    """
    from app.graph.nodes import ask_question, follow_up
    from app.graph.state import build_initial_state

    state: Any = build_initial_state(
        session_id="test-sig-001",
        job_id="job-001",
        job_title="Engineer",
        language="en",  # type: ignore[arg-type]
        max_turns=5,
    )

    # Calling ask_question without adapter= must raise TypeError (missing kwarg).
    with pytest.raises(TypeError):
        # We need to actually call the coroutine function in a way that hits
        # the signature check. asyncio.run() on the bare call should do it.
        asyncio.run(ask_question(state))  # type: ignore[call-arg]

    # Same for follow_up.
    with pytest.raises(TypeError):
        asyncio.run(follow_up(state))  # type: ignore[call-arg]
