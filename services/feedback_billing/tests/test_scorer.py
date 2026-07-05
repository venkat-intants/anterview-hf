"""Unit tests for feedback_billing.app.scorer — S5-006.

Tests:
  1. test_score_session_returns_scorecard_id
  2. test_score_session_clamps_out_of_range_scores
  3. test_score_session_raises_on_gemini_error
  4. test_composite_score_calculation
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.scorer import (
    _GEMINI_MAX_ATTEMPTS,
    _WEIGHTS,
    ScoringError,
    _clamp,
    _compute_composite,
    score_session,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_SCORES: dict[str, int] = {
    "communication": 7,
    "technical": 6,
    "problem_solving": 8,
    "confidence": 7,
}

_GOOD_GEMINI_RESPONSE: dict[str, Any] = {
    "scores": _GOOD_SCORES,
    "strengths": ["Clear communication", "Good examples", "Structured thinking"],
    "improvements": [
        {"area": "Technical depth", "suggestion": "Practice system design"},
        {"area": "Confidence", "suggestion": "Speak more slowly"},
        {"area": "Problem solving", "suggestion": "State assumptions first"},
    ],
    "summary": "A solid entry-level candidate. Meets tier expectations on most axes.",
}


def _make_httpx_response(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    text_body: str = "",
) -> MagicMock:
    """Build a minimal mock that looks like an httpx.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if json_body is not None:
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(json_body)}]
                    }
                }
            ]
        }
    mock_resp.text = text_body
    return mock_resp


def _make_settings() -> Settings:
    """Return a Settings object with minimal required fields."""
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        gemini_api_key="test-key",
        gemini_model="gemini-2.5-flash",
        gemini_api_base_url="https://generativelanguage.googleapis.com/v1beta",
        jwt_secret="test-secret-that-is-at-least-32-chars-long!!",
    )


def _make_db_session() -> AsyncMock:
    """Return a minimal AsyncSession mock."""
    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    return mock_db


_SAMPLE_TURNS: list[dict[str, str]] = [
    {"role": "ai", "text": "Tell me about yourself."},
    {"role": "user", "text": "I am a backend developer with 2 years experience."},
    {"role": "ai", "text": "What is a REST API?"},
    {"role": "user", "text": "REST is a stateless architecture using HTTP verbs."},
]


# ---------------------------------------------------------------------------
# test_score_session_returns_scorecard_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_session_returns_scorecard_id() -> None:
    """Happy path: Gemini returns valid JSON → scorecard_id (UUID string) returned."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    mock_response = _make_httpx_response(json_body=_GOOD_GEMINI_RESPONSE)

    with patch("app.scorer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        scorecard_id, scores, composite = await score_session(
            session_id=str(uuid.uuid4()),
            job_title="Junior Java Developer",
            experience_level="entry",
            language="en",
            turns=_SAMPLE_TURNS,
            db_session=mock_db,
            settings=mock_settings,
        )

    # Must return a valid UUID string.
    parsed_uuid = uuid.UUID(scorecard_id)
    assert str(parsed_uuid) == scorecard_id

    # Scores must be the clamped values from the Gemini response.
    assert scores == dict(_GOOD_SCORES)
    # Composite must be a positive float.
    assert isinstance(composite, float)
    assert 0.0 <= composite <= 10.0

    # DB must have been written and committed.
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_score_session_tolerates_trailing_commas() -> None:
    """Gemini emits a trailing comma before } / ] (invalid JSON) → must recover,
    not 502. Regression for the score.gemini_error seen in production logs."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    raw = (
        '{"scores": {"communication": 7, "technical": 6, "problem_solving": 8, '
        '"confidence": 7,}, '
        '"strengths": ["Clear", "Good", "Structured",], '
        '"improvements": [{"area": "Tech", "suggestion": "Practice",},], '
        '"summary": "Solid candidate.",}'
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": raw}]}}]
    }
    mock_response.text = ""

    with patch("app.scorer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        scorecard_id, scores, _ = await score_session(
            session_id=str(uuid.uuid4()),
            job_title="Junior Java Developer",
            experience_level="entry",
            language="en",
            turns=_SAMPLE_TURNS,
            db_session=mock_db,
            settings=mock_settings,
        )

    assert uuid.UUID(scorecard_id)  # parsed successfully → no ScoringError
    assert scores == dict(_GOOD_SCORES)
    mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# test_score_session_includes_jd_in_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_session_includes_jd_in_prompt() -> None:
    """jd_text is appended to the Gemini prompt sent in the request body."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    mock_response = _make_httpx_response(json_body=_GOOD_GEMINI_RESPONSE)

    jd = "Must know Spring Boot and Kubernetes."

    with patch("app.scorer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await score_session(
            session_id=str(uuid.uuid4()),
            job_title="Junior Java Developer",
            experience_level="entry",
            language="en",
            jd_text=jd,
            turns=_SAMPLE_TURNS,
            db_session=mock_db,
            settings=mock_settings,
        )

    # Capture the JSON payload posted to Gemini and inspect the prompt text.
    mock_client.post.assert_called_once()
    _, post_kwargs = mock_client.post.call_args
    prompt_text: str = post_kwargs["json"]["contents"][0]["parts"][0]["text"]

    assert jd in prompt_text
    assert "Job Description (use to calibrate technical depth expectations)" in prompt_text


# ---------------------------------------------------------------------------
# test_score_session_clamps_out_of_range_scores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_session_clamps_out_of_range_scores() -> None:
    """Gemini returns score 11 → clamped to 10; score -1 → clamped to 0."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    out_of_range_response: dict[str, Any] = {
        **_GOOD_GEMINI_RESPONSE,
        "scores": {
            "communication": 11,  # should clamp to 10
            "technical": -1,      # should clamp to 0
            "problem_solving": 8,
            "confidence": 7,
        },
    }

    mock_response = _make_httpx_response(json_body=out_of_range_response)

    # Capture what was actually written to the DB.
    execute_calls: list[Any] = []

    async def _capture_execute(stmt: Any, params: Any = None) -> AsyncMock:
        execute_calls.append(params)
        return AsyncMock()

    mock_db.execute = _capture_execute  # type: ignore[method-assign]

    with patch("app.scorer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        _, returned_scores, _ = await score_session(
            session_id=str(uuid.uuid4()),
            job_title="Junior Java Developer",
            experience_level="entry",
            language="en",
            turns=_SAMPLE_TURNS,
            db_session=mock_db,
            settings=mock_settings,
        )

    # The return value must carry the clamped scores.
    assert returned_scores["communication"] == 10  # clamped from 11
    assert returned_scores["technical"] == 0       # clamped from -1
    assert returned_scores["problem_solving"] == 8
    assert returned_scores["confidence"] == 7

    # The DB INSERT must also carry the clamped scores.
    assert len(execute_calls) == 1
    params = execute_calls[0]
    written_scores: dict[str, int] = json.loads(params["scores"])
    assert written_scores["communication"] == 10  # clamped from 11
    assert written_scores["technical"] == 0       # clamped from -1
    assert written_scores["problem_solving"] == 8
    assert written_scores["confidence"] == 7


# ---------------------------------------------------------------------------
# test_score_session_raises_on_gemini_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_session_raises_on_gemini_error() -> None:
    """Gemini returns HTTP 500 → ScoringError raised, DB not written."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    mock_response = _make_httpx_response(
        status_code=500,
        text_body="Internal Server Error",
    )

    with patch("app.scorer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ScoringError) as exc_info:
            await score_session(
                session_id=str(uuid.uuid4()),
                job_title="Junior Java Developer",
                experience_level="entry",
                language="en",
                turns=_SAMPLE_TURNS,
                db_session=mock_db,
                settings=mock_settings,
            )

    assert "500" in exc_info.value.message
    mock_db.execute.assert_not_called()
    mock_db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# test_composite_score_calculation
# ---------------------------------------------------------------------------


def test_composite_score_calculation() -> None:
    """Known scores → correct composite (weight formula from LLD §10)."""
    scores = {
        "communication": 8,
        "technical": 6,
        "problem_solving": 7,
        "confidence": 9,
    }
    # Expected: 8*0.30 + 6*0.30 + 7*0.25 + 9*0.15
    #         = 2.40 + 1.80 + 1.75 + 1.35 = 7.30
    expected = round(
        scores["communication"] * 0.30
        + scores["technical"] * 0.30
        + scores["problem_solving"] * 0.25
        + scores["confidence"] * 0.15,
        2,
    )
    result = _compute_composite(scores)
    assert result == expected
    assert result == 7.30


def test_composite_score_all_zeros() -> None:
    """All-zero scores → composite is 0.0."""
    scores = {k: 0 for k in _WEIGHTS}
    assert _compute_composite(scores) == 0.0


def test_composite_score_all_tens() -> None:
    """All-10 scores → composite is 10.0."""
    scores = {k: 10 for k in _WEIGHTS}
    assert _compute_composite(scores) == 10.0


def test_clamp_within_range() -> None:
    assert _clamp(5) == 5


def test_clamp_above_max() -> None:
    assert _clamp(11) == 10


def test_clamp_below_min() -> None:
    assert _clamp(-3) == 0


def test_clamp_boundary_values() -> None:
    assert _clamp(0) == 0
    assert _clamp(10) == 10


# ---------------------------------------------------------------------------
# Gemini 503 / transient-error retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_session_retries_on_503_then_succeeds() -> None:
    """A transient 503 is retried; the next 200 succeeds and writes a scorecard."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    resp_503 = _make_httpx_response(status_code=503, text_body="high demand")
    resp_200 = _make_httpx_response(json_body=_GOOD_GEMINI_RESPONSE)

    with (
        patch("app.scorer.httpx.AsyncClient") as mock_client_cls,
        patch("app.scorer.asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        # First call 503, second call 200.
        mock_client.post = AsyncMock(side_effect=[resp_503, resp_200])
        mock_client_cls.return_value = mock_client

        scorecard_id, _scores, _composite = await score_session(
            session_id=str(uuid.uuid4()),
            job_title="Junior Java Developer",
            experience_level="entry",
            language="en",
            turns=_SAMPLE_TURNS,
            db_session=mock_db,
            settings=mock_settings,
        )

    uuid.UUID(scorecard_id)  # valid UUID → success
    assert mock_client.post.await_count == 2  # retried once
    assert mock_sleep.await_count == 1  # backed off once
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_score_session_gives_up_after_max_503() -> None:
    """Persistent 503 → ScoringError after _GEMINI_MAX_ATTEMPTS, no DB write."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    resp_503 = _make_httpx_response(status_code=503, text_body="high demand")

    with (
        patch("app.scorer.httpx.AsyncClient") as mock_client_cls,
        patch("app.scorer.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp_503)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ScoringError):
            await score_session(
                session_id=str(uuid.uuid4()),
                job_title="Junior Java Developer",
                experience_level="entry",
                language="en",
                turns=_SAMPLE_TURNS,
                db_session=mock_db,
                settings=mock_settings,
            )

        assert mock_client.post.await_count == _GEMINI_MAX_ATTEMPTS
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_score_session_does_not_retry_on_403() -> None:
    """A 403 (bad key) is non-transient → fail fast, exactly one attempt."""
    mock_db = _make_db_session()
    mock_settings = _make_settings()

    resp_403 = _make_httpx_response(status_code=403, text_body="permission denied")

    with (
        patch("app.scorer.httpx.AsyncClient") as mock_client_cls,
        patch("app.scorer.asyncio.sleep", new=AsyncMock()),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp_403)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ScoringError):
            await score_session(
                session_id=str(uuid.uuid4()),
                job_title="Junior Java Developer",
                experience_level="entry",
                language="en",
                turns=_SAMPLE_TURNS,
                db_session=mock_db,
                settings=mock_settings,
            )

        assert mock_client.post.await_count == 1  # no retry on 403
