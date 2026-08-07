"""Unit tests for the resume ATS scorer — HR workflow Phase 1.

Gemini HTTP is mocked so these run offline (no network, no key).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.resume_scorer import ResumeScoringError, score_resume

_SETTINGS = SimpleNamespace(
    gemini_api_base_url="https://example.test/v1beta",
    gemini_model="gemini-flash-lite-latest",
    gemini_api_key="test-key",
)


def _gemini_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


class _FakeResp:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeClient:
    last_url: str | None = None
    last_headers: dict[str, str] | None = None

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, url: str, *_: Any, **kwargs: Any) -> _FakeResp:
        _FakeClient.last_url = url
        _FakeClient.last_headers = kwargs.get("headers")
        return self._resp


def _patch_gemini(monkeypatch: pytest.MonkeyPatch, resp: _FakeResp) -> None:
    _FakeClient.last_url = None
    _FakeClient.last_headers = None
    monkeypatch.setattr(
        "shared.llm.gemini.httpx.AsyncClient", lambda *a, **k: _FakeClient(resp)
    )


@pytest.mark.asyncio
async def test_score_resume_parses_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "overall": 150,  # out of range -> clamps to 100
        "breakdown": {
            "skills_match": 80,
            "experience_relevance": -5,  # clamps to 0
            "education_fit": 70,
            "role_alignment": 60,
        },
        "strengths": ["a", "b"],
        "concerns": ["c"],
        "recommendation": "strong_fit",
        "summary": "Strong candidate.",
    }
    _patch_gemini(monkeypatch, _FakeResp(200, _gemini_envelope(payload)))

    result = await score_resume(
        resume_text="resume", job_title="Engineer", level="mid", settings=_SETTINGS
    )
    assert result["overall"] == 100
    assert result["breakdown"]["experience_relevance"] == 0
    assert result["breakdown"]["skills_match"] == 80
    assert result["recommendation"] == "strong_fit"
    assert result["strengths"] == ["a", "b"]


@pytest.mark.asyncio
async def test_score_resume_sends_api_key_via_header_not_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Gemini key must travel as x-goog-api-key, never ?key=<...> in the URL."""
    payload = {
        "overall": 70,
        "breakdown": {
            "skills_match": 70, "experience_relevance": 70,
            "education_fit": 70, "role_alignment": 70,
        },
        "strengths": [], "concerns": [],
        "recommendation": "moderate_fit",
        "summary": "ok",
    }
    _patch_gemini(monkeypatch, _FakeResp(200, _gemini_envelope(payload)))

    await score_resume(resume_text="resume", job_title="Engineer", settings=_SETTINGS)

    assert "key=" not in (_FakeClient.last_url or "")
    assert _FakeClient.last_headers == {"x-goog-api-key": _SETTINGS.gemini_api_key}


@pytest.mark.asyncio
async def test_invalid_recommendation_defaults_to_moderate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "overall": 50,
        "breakdown": {"skills_match": 50, "experience_relevance": 50,
                      "education_fit": 50, "role_alignment": 50},
        "strengths": [], "concerns": [],
        "recommendation": "definitely_hire",  # invalid enum
        "summary": "ok",
    }
    _patch_gemini(monkeypatch, _FakeResp(200, _gemini_envelope(payload)))
    result = await score_resume(resume_text="r", job_title="Dev", settings=_SETTINGS)
    assert result["recommendation"] == "moderate_fit"


@pytest.mark.asyncio
async def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gemini(monkeypatch, _FakeResp(400, {"error": "bad request"}))
    with pytest.raises(ResumeScoringError):
        await score_resume(resume_text="r", job_title="Dev", settings=_SETTINGS)


def _raw_envelope(raw_text: str) -> dict[str, Any]:
    """Envelope carrying RAW (possibly invalid) JSON text, not json.dumps()'d."""
    return {"candidates": [{"content": {"parts": [{"text": raw_text}]}}]}


@pytest.mark.asyncio
async def test_score_resume_tolerates_trailing_commas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Gemini occasionally emits a trailing comma before } or ] (invalid JSON);
    # the scorer must recover instead of raising / 502-ing.
    raw = (
        '{"candidate_name": "Jane", "candidate_email": "j@x.com", "overall": 82, '
        '"breakdown": {"skills_match": 80, "experience_relevance": 78, '
        '"education_fit": 85, "role_alignment": 84,}, '
        '"strengths": ["a", "b",], "concerns": ["c",], '
        '"recommendation": "strong_fit", "summary": "Solid.",}'
    )
    _patch_gemini(monkeypatch, _FakeResp(200, _raw_envelope(raw)))
    result = await score_resume(resume_text="r", job_title="Dev", settings=_SETTINGS)
    assert result["overall"] == 82
    assert result["breakdown"]["role_alignment"] == 84
    assert result["recommendation"] == "strong_fit"


# ---------------------------------------------------------------------------
# Recovery inherited from the shared caller (FB-2)
# ---------------------------------------------------------------------------

_VALID_PAYLOAD: dict[str, Any] = {
    "candidate_name": "Jane",
    "candidate_email": "j@x.com",
    "overall": 82,
    "breakdown": {
        "skills_match": 80,
        "experience_relevance": 78,
        "education_fit": 85,
        "role_alignment": 84,
    },
    "strengths": ["a"],
    "concerns": ["b"],
    "recommendation": "strong_fit",
    "summary": "Solid.",
}


@pytest.mark.asyncio
async def test_score_resume_repairs_a_raw_newline_inside_a_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resume bullet quoted back with a raw newline makes the JSON invalid.

    This module had no json_repair rung before it moved onto the shared caller
    — the same response the exam generator salvaged raised here.
    """
    broken = json.dumps(_VALID_PAYLOAD).replace("Solid.", "Sol\nid.", 1)
    _patch_gemini(monkeypatch, _FakeResp(200, _raw_envelope(broken)))

    result = await score_resume(resume_text="r", job_title="Dev", settings=_SETTINGS)
    assert result["overall"] == 82


@pytest.mark.asyncio
async def test_score_resume_truncation_error_names_the_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAX_TOKENS must be named: raising the budget and fixing the prompt are
    different tickets, and the old error told them apart for neither."""
    truncated = '{"overall": 82, "breakdown": {"skills_ma'
    envelope = {
        "candidates": [
            {
                "content": {"parts": [{"text": truncated}]},
                "finishReason": "MAX_TOKENS",
            }
        ]
    }
    _patch_gemini(monkeypatch, _FakeResp(200, envelope))

    with pytest.raises(ResumeScoringError) as excinfo:
        await score_resume(resume_text="r", job_title="Dev", settings=_SETTINGS)
    assert "MAX_TOKENS" in excinfo.value.message
    assert "cut off by maxOutputTokens" in excinfo.value.message
