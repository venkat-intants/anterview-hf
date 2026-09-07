"""The Groq JSON caller, and the dispatcher that chooses between providers.

``test_shared_gemini.py`` covers the recovery ladder in depth; both providers
now share that code, so this file does not re-test it. What it does test is
everything Groq owns on its own — the wire format, the envelope, and the
vocabulary differences that are silent when they are wrong:

* JSON mode is a hard mode on Groq: the request is refused outright unless the
  prompt mentions JSON, so the nudge has to be there;
* truncation is called ``length``, not ``MAX_TOKENS`` — and if that is not
  normalised, ``json_repair`` runs on a cut-off payload and returns a
  half-empty object that parses cleanly and reads like a real answer;
* an unknown provider must fail loudly, because a typo that quietly resolved to
  Gemini would send traffic and cost to the provider the operator believed they
  had switched away from.

No network: the transport is replaced inside ``shared.llm._recovery``, which
owns it for both providers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from shared.llm import call_groq_json, call_llm_json


class _CallerError(Exception):
    """Stands in for ScoringError / ExamGenerationError / EmbeddingError."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []


class _FakeClient:
    def __init__(self, recorder: _Recorder, script: list[Any]) -> None:
        self._recorder = recorder
        self._script = list(script)

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, json: Any = None, headers: Any = None) -> Any:  # noqa: A002
        self._recorder.calls.append({"url": url, "body": json, "headers": headers})
        result = self._script.pop(0) if self._script else self._script
        if isinstance(result, Exception):
            raise result
        return result


class _FakeHttpx:
    """Replaces the ``httpx`` name inside ``_recovery`` — never the real one."""

    import httpx as _real

    RequestError = _real.RequestError

    def __init__(self, recorder: _Recorder, script: list[Any]) -> None:
        self._recorder = recorder
        self._script = script

    def AsyncClient(self, **kwargs: Any) -> _FakeClient:  # noqa: N802 — mirrors httpx
        return _FakeClient(self._recorder, self._script)


class _NoSleep:
    async def sleep(self, seconds: float) -> None:
        return None


def _ok(content: str, finish_reason: str = "stop") -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "choices": [
                {"message": {"role": "assistant", "content": content},
                 "finish_reason": finish_reason}
            ]
        },
    )


def _patch(monkeypatch: pytest.MonkeyPatch, *script: Any) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr("shared.llm._recovery.httpx", _FakeHttpx(recorder, list(script)))
    monkeypatch.setattr("shared.llm._recovery.asyncio", _NoSleep())
    return recorder


async def _call(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "api_base_url": "https://api.groq.test/openai/v1",
        "model": "openai/gpt-oss-120b",
        "api_key": "gsk-test",
        "temperature": 0.2,
        "max_output_tokens": 1024,
        "timeout": 30.0,
        "error_cls": _CallerError,
    }
    kwargs.update(overrides)
    return await call_groq_json("score this candidate", **kwargs)


# ---------------------------------------------------------------------------
# The wire format
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_posts_to_the_openai_compatible_path(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(monkeypatch, _ok('{"ok": true}'))
    await _call()
    assert recorder.calls[0]["url"] == "https://api.groq.test/openai/v1/chat/completions"


@pytest.mark.asyncio
async def test_the_key_travels_as_a_bearer_header_not_in_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key in a query string lands in proxy access logs and exception text."""
    recorder = _patch(monkeypatch, _ok('{"ok": true}'))
    await _call()
    assert recorder.calls[0]["headers"] == {"Authorization": "Bearer gsk-test"}
    assert "gsk-test" not in recorder.calls[0]["url"]


@pytest.mark.asyncio
async def test_json_mode_is_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(monkeypatch, _ok('{"ok": true}'))
    await _call()
    assert recorder.calls[0]["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_the_prompt_mentions_json_because_groq_demands_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Groq rejects response_format=json_object outright when the word "json"
    appears nowhere in the prompt — a 400 on a prompt Gemini would have served
    fine. The nudge is appended here so no caller has to remember."""
    recorder = _patch(monkeypatch, _ok('{"ok": true}'))
    await _call()
    content = recorder.calls[0]["body"]["messages"][0]["content"]
    assert content.startswith("score this candidate")
    assert "json" in content.lower()


@pytest.mark.asyncio
async def test_the_budget_is_sent_as_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(monkeypatch, _ok('{"ok": true}'))
    await _call(max_output_tokens=4096)
    assert recorder.calls[0]["body"]["max_tokens"] == 4096
    # Gemini's knob must not leak into an OpenAI-shaped body.
    assert "generationConfig" not in recorder.calls[0]["body"]


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_returns_the_parsed_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _ok('{"overall": 72, "summary": "solid"}'))
    assert await _call() == {"overall": 72, "summary": "solid"}


@pytest.mark.asyncio
async def test_a_fenced_object_is_still_recovered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared ladder applies to both providers — spot-checked, not re-tested."""
    _patch(monkeypatch, _ok('```json\n{"overall": 5}\n```'))
    assert await _call() == {"overall": 5}


@pytest.mark.asyncio
async def test_no_choices_is_a_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeResponse(200, {"choices": []}))
    with pytest.raises(_CallerError, match="no choices"):
        await _call()


@pytest.mark.asyncio
async def test_a_200_carrying_an_error_object_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading it is the difference between a useful message and "no choices"."""
    _patch(monkeypatch, _FakeResponse(200, {"error": {"message": "model_decommissioned"}}))
    with pytest.raises(_CallerError, match="model_decommissioned"):
        await _call()


@pytest.mark.asyncio
async def test_empty_content_is_an_error_not_an_empty_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _ok(""))
    with pytest.raises(_CallerError, match="no message content"):
        await _call()


# ---------------------------------------------------------------------------
# Truncation — the vocabulary difference that is silent when wrong
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_truncation_is_recognised_under_openais_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``length`` must normalise to the shared truncation marker. Left as-is it
    would not match, json_repair would run on a cut-off payload, and the caller
    would get a half-empty object that parses cleanly and looks like a result."""
    _patch(monkeypatch, _ok('{"summary": "the candidate expl', finish_reason="length"))
    with pytest.raises(_CallerError) as excinfo:
        await _call()
    assert "MAX_TOKENS" in excinfo.value.message


@pytest.mark.asyncio
async def test_the_truncation_error_names_groqs_own_knob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Raise the budget" is only actionable if it names the field to raise —
    and on Groq that is max_tokens, not Gemini's maxOutputTokens."""
    _patch(monkeypatch, _ok('{"summary": "cut', finish_reason="length"))
    with pytest.raises(_CallerError) as excinfo:
        await _call()
    assert "max_tokens" in excinfo.value.message
    assert "maxOutputTokens" not in excinfo.value.message


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_429_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(
        monkeypatch, _FakeResponse(429, {}, text="rate limited"), _ok('{"ok": 1}')
    )
    assert await _call() == {"ok": 1}
    assert len(recorder.calls) == 2


@pytest.mark.asyncio
async def test_a_401_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad key will still be bad on the fourth attempt; retrying only
    multiplies the latency of a failure that was never going to succeed."""
    recorder = _patch(monkeypatch, _FakeResponse(401, {}, text="invalid api key"))
    with pytest.raises(_CallerError):
        await _call()
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_the_error_names_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator reading a log needs to know which provider failed, and after
    the switch that is not inferable from the call site."""
    _patch(monkeypatch, _FakeResponse(400, {}, text="bad request"))
    with pytest.raises(_CallerError, match="Groq"):
        await _call()


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expect_path"),
    [
        ("groq", "/chat/completions"),
        ("Groq", "/chat/completions"),
        ("  GROQ  ", "/chat/completions"),
        ("gemini", ":generateContent"),
    ],
)
async def test_the_provider_string_picks_the_endpoint(
    monkeypatch: pytest.MonkeyPatch, provider: str, expect_path: str
) -> None:
    gemini_ok = _FakeResponse(
        200, {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}
    )
    recorder = _patch(monkeypatch, _ok('{"ok": 1}') if "roq" in provider.lower() else gemini_ok)
    await call_llm_json(
        "prompt",
        provider=provider,
        api_base_url="https://provider.test/v1",
        model="m",
        api_key="k",
        temperature=0.2,
        max_output_tokens=512,
        timeout=10.0,
        error_cls=_CallerError,
    )
    assert expect_path in recorder.calls[0]["url"]


@pytest.mark.asyncio
async def test_an_unknown_provider_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo that quietly fell back to Gemini would keep sending traffic — and
    cost — to the provider the operator believed they had switched away from,
    with nothing in the response to say so."""
    recorder = _patch(monkeypatch, _ok('{"ok": 1}'))
    with pytest.raises(_CallerError, match="Unknown LLM_PROVIDER"):
        await call_llm_json(
            "prompt",
            provider="grok",  # the common misspelling
            api_base_url="https://provider.test/v1",
            model="m",
            api_key="k",
            temperature=0.2,
            max_output_tokens=512,
            timeout=10.0,
            error_cls=_CallerError,
        )
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# A blank key
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_missing_key_says_so_instead_of_failing_in_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found by running it: an empty key built `Authorization: Bearer ` and
    httpx refused it as an illegal header value — surfaced to the user, after
    four retries, as "Illegal header value b'Bearer '". That reads like a bug
    in the transport rather than a blank environment variable."""
    recorder = _patch(monkeypatch, _ok('{"ok": 1}'))
    with pytest.raises(_CallerError, match="GROQ_API_KEY"):
        await _call(api_key="")
    # And not a single attempt was spent on a call that could never succeed.
    assert recorder.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
async def test_a_whitespace_key_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    _patch(monkeypatch, _ok('{"ok": 1}'))
    with pytest.raises(_CallerError, match="GROQ_API_KEY"):
        await _call(api_key=blank)
