"""Unit tests for the AI exam-question generator — HR workflow (MCQ authoring).

Gemini HTTP is mocked so these run offline (no network, no key). Covers the
2026-07-05 production bug: gemini-2.5-flash spent nearly the whole
maxOutputTokens budget on hidden "thinking", truncating the JSON mid-string
("Unterminated string ... char 41") and 502-ing every AI generation.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.exam_generator import (
    ExamGenerationError,
    generate_coding_questions,
    generate_exam_questions,
)

_SETTINGS_25 = SimpleNamespace(
    gemini_api_base_url="https://example.test/v1beta",
    gemini_model="gemini-2.5-flash",
    gemini_api_key="test-key",
)
_SETTINGS_LITE = SimpleNamespace(
    gemini_api_base_url="https://example.test/v1beta",
    gemini_model="gemini-flash-lite-latest",
    gemini_api_key="test-key",
)


def _question(i: int, correct: int = 1) -> dict[str, Any]:
    return {
        "prompt": f"Question {i}?",
        "options": [f"q{i} opt0", f"q{i} opt1", f"q{i} opt2", f"q{i} opt3"],
        "correct_index": correct,
    }


def _envelope(text: str, finish_reason: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}
        ]
    }


class _FakeResp:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeClient:
    """Records the request body so tests can assert on generationConfig."""

    last_body: dict[str, Any] | None = None

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, *_: Any, **kwargs: Any) -> _FakeResp:
        _FakeClient.last_body = kwargs.get("json")
        return self._resp


def _patch_gemini(monkeypatch: pytest.MonkeyPatch, resp: _FakeResp) -> None:
    _FakeClient.last_body = None
    monkeypatch.setattr(
        "app.exam_generator.httpx.AsyncClient", lambda *a, **k: _FakeClient(resp)
    )


@pytest.mark.asyncio
async def test_generates_and_validates_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"questions": [_question(i) for i in range(3)]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    questions = await generate_exam_questions(
        topic="JavaScript fundamentals", num_questions=3, settings=_SETTINGS_25
    )
    assert len(questions) == 3
    assert questions[0]["correct_index"] == 1
    assert len(questions[0]["options"]) == 4


@pytest.mark.asyncio
async def test_thinking_disabled_on_25_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """2.5 models must get thinkingBudget=0 — hidden reasoning tokens otherwise
    eat maxOutputTokens and truncate the JSON (the char-41 production bug)."""
    payload = {"questions": [_question(0)]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    await generate_exam_questions(topic="t", num_questions=1, settings=_SETTINGS_25)

    config = _FakeClient.last_body["generationConfig"]  # type: ignore[index]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}


@pytest.mark.asyncio
async def test_no_thinking_config_on_non_25_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-2.5 models reject thinkingConfig with HTTP 400 — it must be absent."""
    payload = {"questions": [_question(0)]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    await generate_exam_questions(topic="t", num_questions=1, settings=_SETTINGS_LITE)

    config = _FakeClient.last_body["generationConfig"]  # type: ignore[index]
    assert "thinkingConfig" not in config


@pytest.mark.asyncio
async def test_truncated_json_error_names_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MAX_TOKENS truncation must be called out in the error, not read as a
    generic model failure."""
    truncated = '{\n  "questions": [\n    {\n      "prompt": "Wh'
    _patch_gemini(
        monkeypatch, _FakeResp(200, _envelope(truncated, finish_reason="MAX_TOKENS"))
    )

    with pytest.raises(ExamGenerationError) as excinfo:
        await generate_exam_questions(topic="t", num_questions=5, settings=_SETTINGS_25)
    assert "MAX_TOKENS" in excinfo.value.message
    assert "truncated" in excinfo.value.message


@pytest.mark.asyncio
async def test_invalid_questions_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "questions": [
            _question(0),
            {"prompt": "only two options", "options": ["a", "b"], "correct_index": 0},
            {"prompt": "bad index", "options": ["a", "b", "c", "d"], "correct_index": 9},
            "not-a-dict",
        ]
    }
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    questions = await generate_exam_questions(
        topic="t", num_questions=4, settings=_SETTINGS_25
    )
    assert len(questions) == 1


@pytest.mark.asyncio
async def test_all_questions_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"questions": [{"prompt": "", "options": [], "correct_index": 0}]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    with pytest.raises(ExamGenerationError):
        await generate_exam_questions(topic="t", num_questions=1, settings=_SETTINGS_25)


# ---------------------------------------------------------------------------
# Coding-question generation
# ---------------------------------------------------------------------------


def _coding_case(sample: bool, weight: int = 1) -> dict[str, Any]:
    return {
        "stdin": "3\n1 2 3",
        "expected_output": "6",
        "is_sample": sample,
        "weight": weight,
    }


def _coding_question(**overrides: Any) -> dict[str, Any]:
    q: dict[str, Any] = {
        "prompt": "Read N integers and print their sum. Input: N then N ints.",
        "reference_solution": "n=int(input());print(sum(int(x) for x in input().split()))",
        "test_cases": [
            _coding_case(True),
            _coding_case(True),
            _coding_case(False, weight=2),
            _coding_case(False),
        ],
    }
    q.update(overrides)
    return q


@pytest.mark.asyncio
async def test_generates_coding_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"questions": [_coding_question(), _coding_question()]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    qs = await generate_coding_questions(
        topic="arrays", num_questions=2, allowed_languages=["python"], settings=_SETTINGS_25
    )
    assert len(qs) == 2
    assert qs[0]["reference_solution"].startswith("n=int")
    cases = qs[0]["test_cases"]
    assert any(c["is_sample"] for c in cases)
    assert any(not c["is_sample"] for c in cases)
    assert cases[2]["weight"] == 2


@pytest.mark.asyncio
async def test_coding_question_without_hidden_cases_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-sample questions are gradable from visible cases alone — dropped."""
    bad = _coding_question(test_cases=[_coding_case(True), _coding_case(True)])
    payload = {"questions": [bad, _coding_question()]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    qs = await generate_coding_questions(
        topic="t", num_questions=2, allowed_languages=["python"], settings=_SETTINGS_25
    )
    assert len(qs) == 1


@pytest.mark.asyncio
async def test_coding_question_without_sample_cases_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _coding_question(test_cases=[_coding_case(False), _coding_case(False)])
    payload = {"questions": [bad]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    with pytest.raises(ExamGenerationError):
        await generate_coding_questions(
            topic="t", num_questions=1, allowed_languages=["python"], settings=_SETTINGS_25
        )


@pytest.mark.asyncio
async def test_coding_broken_json_is_repaired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini intermittently emits raw newlines / bad escapes inside code strings
    even in JSON mode (seen live) — json_repair must salvage the payload."""
    valid = json.dumps({"questions": [_coding_question()]})
    # Inject a RAW newline inside the reference_solution string — invalid JSON.
    broken = valid.replace("print(sum", "print(\nsum", 1)
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(broken)))

    qs = await generate_coding_questions(
        topic="t", num_questions=1, allowed_languages=["python"], settings=_SETTINGS_25
    )
    assert len(qs) == 1
    assert "sum" in qs[0]["reference_solution"]


@pytest.mark.asyncio
async def test_coding_thinking_disabled_on_25_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"questions": [_coding_question()]}
    _patch_gemini(monkeypatch, _FakeResp(200, _envelope(json.dumps(payload))))

    await generate_coding_questions(
        topic="t", num_questions=1, allowed_languages=["python"], settings=_SETTINGS_25
    )
    config = _FakeClient.last_body["generationConfig"]  # type: ignore[index]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
