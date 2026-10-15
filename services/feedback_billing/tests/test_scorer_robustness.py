"""Scorer hardening: malformed model replies and the PDF background task.

* ``int(raw_scores[axis])`` raised TypeError/ValueError on "7/10" or null, which
  is not a ScoringError, so it surfaced as an unhandled 500.
* ``improvements`` as plain strings crashed ``i.get("area")`` AFTER the scorecard
  committed, so the scorecard existed and its PDF never did.
* the PDF ``create_task`` kept no reference and nobody read its exception.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import scorer
from app.scorer import ScoringError, _coerce_score, _normalise_improvements
from tests.test_scorer import (
    _GOOD_GEMINI_RESPONSE,
    _SAMPLE_TURNS,
    _make_db_session,
    _make_httpx_response,
    _make_settings,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(7, 7), (7.9, 7), ("7", 7), ("7/10", 7), (" 8 / 10 ", 8), ("70/100", 7),
     ("3.5", 3), (15, 10), (-2, 0), ("14/10", 10)],
)
def test_coerce_score_accepts_the_shapes_a_model_returns(raw: Any, expected: int) -> None:
    assert _coerce_score("technical", raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, True, "", "seven", "7 out of 10", "7/0", [7], {"v": 7}, float("nan"), float("inf")],
)
def test_coerce_score_rejects_garbage_with_scoring_error(raw: Any) -> None:
    with pytest.raises(ScoringError):
        _coerce_score("technical", raw)


def test_normalise_improvements() -> None:
    out = _normalise_improvements(
        ["Practise system design", {"area": "Pace", "suggestion": "Slow down"},
         {"area": None}, 42, "  ", None]
    )
    assert out == [
        {"area": "", "suggestion": "Practise system design"},
        {"area": "Pace", "suggestion": "Slow down"},
        {"area": "", "suggestion": ""},
    ]
    assert _normalise_improvements(None) == []
    assert _normalise_improvements("not a list") == []


async def _score(body: dict[str, Any], **kw: Any) -> tuple[str, Any, Any]:
    with patch("shared.llm._recovery.httpx.AsyncClient") as cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=_make_httpx_response(json_body=body))
        cls.return_value = client
        return await scorer.score_session(
            session_id=str(uuid.uuid4()), job_title="Welder", experience_level="entry",
            language="en", turns=_SAMPLE_TURNS, db_session=kw.pop("db", _make_db_session()),
            settings=kw.pop("settings", _make_settings()), **kw,
        )


async def test_unparseable_score_is_a_scoring_error_and_writes_nothing() -> None:
    db = _make_db_session()
    body = {**_GOOD_GEMINI_RESPONSE, "scores": {**_GOOD_GEMINI_RESPONSE["scores"], "technical": None}}
    with pytest.raises(ScoringError):
        await _score(body, db=db)
    db.execute.assert_not_called()
    db.commit.assert_not_called()


async def test_scores_object_missing_or_wrong_type_is_a_scoring_error() -> None:
    with pytest.raises(ScoringError):
        await _score({**_GOOD_GEMINI_RESPONSE, "scores": None})


async def test_string_improvements_are_stored_normalised_and_do_not_crash_pdf() -> None:
    db = _make_db_session()
    pdf = AsyncMock()
    settings = _make_settings().model_copy(update={"s3_access_key_id": "k"})
    body = {**_GOOD_GEMINI_RESPONSE, "improvements": ["Be concise", "Give examples"]}
    with patch("app.pdf_render.render_scorecard_pdf", pdf):
        await _score(body, db=db, settings=settings, candidate_name="A B")
        await asyncio.sleep(0)
    import json as _json
    stored = _json.loads(db.execute.await_args.args[1]["improvements"])
    assert stored == [
        {"area": "", "suggestion": "Be concise"},
        {"area": "", "suggestion": "Give examples"},
    ]
    pdf.assert_awaited_once()


async def test_pdf_task_is_held_then_released_and_its_failure_logged() -> None:
    gate = asyncio.Event()
    seen_held: list[int] = []

    async def failing_pdf(*_a: Any, **_k: Any) -> None:
        await gate.wait()
        raise RuntimeError("s3 down")

    settings = _make_settings().model_copy(update={"s3_access_key_id": "k"})
    logger = MagicMock()
    with patch("app.pdf_render.render_scorecard_pdf", failing_pdf), \
            patch.object(scorer, "log", logger):
        await _score(_GOOD_GEMINI_RESPONSE, settings=settings, candidate_name="A B")
        seen_held.append(len(scorer._background_tasks))  # strong reference kept
        gate.set()
        for _ in range(5):
            await asyncio.sleep(0)

    assert seen_held == [1]
    assert scorer._background_tasks == set()  # discarded by the done-callback
    events = [c.args[0] for c in logger.error.call_args_list]
    assert "scorer.pdf_task_failed" in events
