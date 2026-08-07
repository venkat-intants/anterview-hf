"""Regression suite for ``shared.llm.gemini`` — the single Gemini JSON caller.

The bug this guards (code review 2026-08-07, FB-1/FB-2): the same retry / auth /
JSON-recovery scaffolding was written once per call site and had already
drifted. The exam generator could salvage a prose-wrapped or slightly malformed
response; the interview scorer — the path that produces the candidate's
scorecard — could not, and 502s on a response its sibling recovers. So the tests
below are written as *recovery* behaviour ("this response still yields an
object") rather than as unit assertions on the parser, because that is the
property that was missing, and asserting it once here is what stops it from
being present in one caller and absent in another again.

No network: ``httpx.AsyncClient`` is replaced inside the module under test, so
these run offline with no key.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from typing import Any

import httpx
import pytest

from shared.llm import call_gemini_json
from shared.llm.gemini import MAX_ATTEMPTS

_BASE = "https://example.test/v1beta"
_MODEL_25 = "gemini-2.5-flash"
_MODEL_LITE = "gemini-flash-lite-latest"
_KEY = "test-key-not-a-real-credential"

_PAYLOAD = {"scores": {"communication": 7}, "summary": "ok"}


class _CallerError(Exception):
    """Stands in for ScoringError / ResumeScoringError / ExamGenerationError.

    Deliberately the same shape as all three (one message argument, kept on
    ``.message``): the helper must work with the callers' *existing* exception
    types, since those are what their routers map to HTTP responses.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _OtherCallerError(Exception):
    """A second, unrelated caller type — used to prove the error class is not
    unified inside the helper."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------
# Fake transport
# --------------------------------------------------------------------------


class _FakeResponse:
    """Just enough of ``httpx.Response``: ``status_code``, ``.text``, ``.json()``."""

    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        *,
        body_text: str | None = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = body_text if body_text is not None else json.dumps(payload)

    def json(self) -> Any:
        if self._json_error:
            raise json.JSONDecodeError("Expecting value", self.text, 0)
        return self._payload


class _Recorder:
    """Scripted responses plus a log of what the caller actually sent."""

    def __init__(self, script: tuple[Any, ...]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.client_kwargs: dict[str, Any] = {}

    def next_response(self) -> Any:
        # The last entry repeats, so a test that only cares about "always 503"
        # does not have to spell it out once per attempt.
        return self._script.pop(0) if len(self._script) > 1 else self._script[0]

    @property
    def attempts(self) -> int:
        return len(self.requests)

    @property
    def last_body(self) -> dict[str, Any]:
        return self.requests[-1]["json"]

    @property
    def generation_config(self) -> dict[str, Any]:
        config: dict[str, Any] = self.last_body["generationConfig"]
        return config


class _FakeClient:
    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self._recorder.requests.append({"url": url, **kwargs})
        result = self._recorder.next_response()
        if isinstance(result, Exception):
            raise result
        return result


class _FakeHttpx:
    """Replaces the ``httpx`` name *inside the module under test* rather than
    patching ``httpx.AsyncClient`` globally — the module's ``import httpx``
    binds the real, process-wide module object, so setattr on it would swap the
    client out for every other test in the session too."""

    RequestError = httpx.RequestError

    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    def AsyncClient(self, **kwargs: Any) -> _FakeClient:  # noqa: N802 — mirrors httpx's name
        self._recorder.client_kwargs = kwargs
        return _FakeClient(self._recorder)


class _FakeAsyncio:
    """Same trick for ``asyncio``: the module only uses ``asyncio.sleep``, and
    the retry schedule is 1s + 2s + 4s of real time otherwise."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _patch(monkeypatch: pytest.MonkeyPatch, *script: Any) -> _Recorder:
    recorder = _Recorder(script)
    monkeypatch.setattr("shared.llm.gemini.httpx", _FakeHttpx(recorder))
    monkeypatch.setattr("shared.llm.gemini.asyncio", _FakeAsyncio())
    return recorder


def _slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    fake = _FakeAsyncio()
    monkeypatch.setattr("shared.llm.gemini.asyncio", fake)
    return fake.slept


def _envelope(text: str, *, finish_reason: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}]
    }


def _ok(payload: Any = None, *, finish_reason: str = "STOP") -> _FakeResponse:
    body = json.dumps(_PAYLOAD if payload is None else payload)
    return _FakeResponse(200, _envelope(body, finish_reason=finish_reason))


def _text_response(text: str, *, finish_reason: str = "STOP") -> _FakeResponse:
    return _FakeResponse(200, _envelope(text, finish_reason=finish_reason))


async def _call(prompt: str = "Score this transcript.", **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "api_base_url": _BASE,
        "model": _MODEL_LITE,
        "api_key": _KEY,
        "temperature": 0.2,
        "max_output_tokens": 1024,
        "timeout": 30.0,
        "error_cls": _CallerError,
    }
    kwargs.update(overrides)
    return await call_gemini_json(prompt, **kwargs)


# --------------------------------------------------------------------------
# Request shape — the settings that had to be applied three times
# --------------------------------------------------------------------------


async def test_api_key_travels_in_a_header_not_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key must never reach a request URL or a proxy access log. Each call
    site had to learn this separately; now there is one place to get it wrong."""
    recorder = _patch(monkeypatch, _ok())

    await _call()

    assert recorder.requests[0]["headers"] == {"x-goog-api-key": _KEY}
    assert "key=" not in recorder.requests[0]["url"]
    assert _KEY not in recorder.requests[0]["url"]


async def test_url_targets_generate_content_for_the_requested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(monkeypatch, _ok())

    await _call(model=_MODEL_25)

    assert recorder.requests[0]["url"] == f"{_BASE}/models/{_MODEL_25}:generateContent"


async def test_json_mode_and_the_callers_budget_are_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON mode is what stops fence-wrapped output; the budget and temperature
    differ per caller (0.2 for scoring, 0.7 for authoring) and must pass
    through rather than being fixed by the shared helper."""
    recorder = _patch(monkeypatch, _ok())

    await _call(temperature=0.7, max_output_tokens=8192)

    config = recorder.generation_config
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0.7
    assert config["maxOutputTokens"] == 8192


async def test_thinking_is_disabled_on_25_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hidden reasoning tokens count against maxOutputTokens and truncated the
    JSON mid-string in production (the char-41 bug)."""
    recorder = _patch(monkeypatch, _ok())

    await _call(model=_MODEL_25)

    assert recorder.generation_config["thinkingConfig"] == {"thinkingBudget": 0}


async def test_thinking_config_is_absent_on_pre_25_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-2.5 models reject the field with HTTP 400, so the guard is not
    cosmetic — sending it unconditionally breaks the default demo model."""
    recorder = _patch(monkeypatch, _ok())

    await _call(model=_MODEL_LITE)

    assert "thinkingConfig" not in recorder.generation_config


async def test_the_callers_timeout_reaches_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation needs a longer ceiling than scoring (90s vs 60s), which is why
    the timeout is a parameter and not a module constant."""
    recorder = _patch(monkeypatch, _ok())

    await _call(timeout=90.0)

    assert recorder.client_kwargs["timeout"] == 90.0


async def test_the_prompt_is_sent_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Framing of untrusted text belongs to the caller; this helper must not
    rewrite a prompt it cannot reason about."""
    recorder = _patch(monkeypatch, _ok())

    await _call("BEGIN UNTRUSTED\nhello\nEND UNTRUSTED")

    parts = recorder.last_body["contents"][0]["parts"]
    assert parts[0]["text"] == "BEGIN UNTRUSTED\nhello\nEND UNTRUSTED"


# --------------------------------------------------------------------------
# Retry — a momentary hiccup must not cost a candidate their scorecard
# --------------------------------------------------------------------------


async def test_a_transient_status_is_retried_and_the_result_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 "high demand" is routine on the free tier. It must cost latency, not
    the result."""
    recorder = _patch(monkeypatch, _FakeResponse(503, {"error": "overloaded"}), _ok())

    assert await _call() == _PAYLOAD
    assert recorder.attempts == 2


async def test_a_non_transient_status_fails_on_the_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400/403 is a bad prompt or a bad key. Retrying it quadruples the
    latency of a failure that was never going to succeed."""
    recorder = _patch(monkeypatch, _FakeResponse(400, {"error": "bad request"}))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert recorder.attempts == 1
    assert "HTTP 400" in excinfo.value.message


async def test_transport_errors_are_retried_and_then_given_up_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying must not become hanging: a genuinely unreachable endpoint has
    to surface after a bounded number of attempts."""
    recorder = _patch(monkeypatch, httpx.ConnectError("connection refused"))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert recorder.attempts == MAX_ATTEMPTS
    assert "connection refused" in excinfo.value.message
    assert f"{MAX_ATTEMPTS} attempt(s)" in excinfo.value.message


async def test_backoff_is_bounded_exponential(monkeypatch: pytest.MonkeyPatch) -> None:
    """1s, 2s, 4s between four attempts — long enough to outlast a rate-limit
    window, short enough that the caller's own timeout still governs."""
    _patch(monkeypatch, _FakeResponse(429, {"error": "rate limited"}))
    slept = _slept(monkeypatch)

    with pytest.raises(_CallerError):
        await _call()

    assert slept == [1.0, 2.0, 4.0]


async def test_the_error_body_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider's body is echoed into the raised message, which is usually
    logged — an unbounded echo is a log-flood risk on a bad day."""
    _patch(monkeypatch, _FakeResponse(400, None, body_text="x" * 5000))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert len(excinfo.value.message) < 400


# --------------------------------------------------------------------------
# JSON recovery — the half that had drifted (FB-2)
# --------------------------------------------------------------------------


async def test_markdown_fences_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _text_response("```json\n" + json.dumps(_PAYLOAD) + "\n```"))

    assert await _call() == _PAYLOAD


async def test_prose_around_the_object_is_recovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE regression this module exists for: the exam generator recovers this
    response by taking the outermost brace span; the scorer raises on it, losing
    a scorecard that was sitting in the response all along."""
    wrapped = "Here is the assessment:\n" + json.dumps(_PAYLOAD) + "\nHope that helps!"
    _patch(monkeypatch, _text_response(wrapped))

    assert await _call() == _PAYLOAD


async def test_a_trailing_comma_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid JSON that Gemini still emits occasionally in JSON mode."""
    _patch(monkeypatch, _text_response('{"summary": "ok", "scores": {"communication": 7,},}'))

    assert await _call() == {"summary": "ok", "scores": {"communication": 7}}


async def test_truncated_output_points_at_the_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Raise maxOutputTokens" and "the model returned prose" are different
    tickets. Before this, both produced the same opaque parse error."""
    _patch(monkeypatch, _text_response('{"summary": "the candidate expl', finish_reason="MAX_TOKENS"))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "MAX_TOKENS" in excinfo.value.message
    assert "maxOutputTokens" in excinfo.value.message


async def test_an_unparseable_complete_response_is_not_blamed_on_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same distinction: the model finished (STOP) and
    simply did not produce an object, so pointing at the token budget would
    send the operator to the wrong fix."""
    monkeypatch.setitem(sys.modules, "json_repair", None)
    _patch(monkeypatch, _text_response("I'm sorry, I can't help with that."))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "MAX_TOKENS" not in excinfo.value.message
    assert "not valid JSON" in excinfo.value.message


async def test_a_candidate_with_no_parts_still_names_the_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2.5 model that spends its whole budget thinking returns a candidate
    with a ``content`` and no ``parts`` at all. Subscripting parts[0] made that
    an "IndexError: list index out of range", which names neither the cause nor
    the fix."""
    _patch(
        monkeypatch,
        _FakeResponse(
            200,
            {"candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}]},
        ),
    )

    with pytest.raises(_CallerError) as excinfo:
        await _call(model=_MODEL_25)

    assert "MAX_TOKENS" in excinfo.value.message
    assert "no text part" in excinfo.value.message


async def test_a_blocked_prompt_surfaces_the_block_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safety-blocked prompt returns promptFeedback and NO candidates. The
    reason is the whole diagnosis, and it is one key away from being lost."""
    _patch(monkeypatch, _FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "blockReason=SAFETY" in excinfo.value.message


async def test_a_non_stop_finish_reason_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """RECITATION/SAFETY on the *response* is neither a budget problem nor
    something a retry fixes, so it has to be named rather than swallowed."""
    monkeypatch.setitem(sys.modules, "json_repair", None)
    _patch(monkeypatch, _text_response("truncated by policy", finish_reason="RECITATION"))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "finishReason=RECITATION" in excinfo.value.message


async def test_thought_parts_are_not_parsed_as_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private reasoning is not output. Joining it into the payload would
    corrupt an otherwise perfectly parseable response."""
    _patch(
        monkeypatch,
        _FakeResponse(
            200,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Let me think about this...", "thought": True},
                                {"text": json.dumps(_PAYLOAD)},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        ),
    )

    assert await _call() == _PAYLOAD


async def test_valid_json_that_is_not_an_object_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The return type is a dict or an error, never "whatever parsed". Every
    caller does ``parsed.get(...)``, so an array would reach them as an
    AttributeError raised from inside their own parsing code — which reads like
    a bug in the caller rather than a bad response."""
    _patch(monkeypatch, _text_response("[1, 2, 3]"))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "not an object" in excinfo.value.message


async def test_a_non_json_body_is_reported_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 carrying an HTML error page (proxy/captive portal) is a different
    failure from a model that answered badly."""
    _patch(monkeypatch, _FakeResponse(200, None, body_text="<html>502</html>", json_error=True))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "non-JSON body" in excinfo.value.message


# --------------------------------------------------------------------------
# json_repair — the last rung, and the gate on it
# --------------------------------------------------------------------------


class _StubJsonRepair:
    """Stands in for the optional dependency so the *gate* is what is tested,
    not json_repair's own behaviour (which its own test suite owns, and which
    is exercised for real in the importorskip test below)."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    def repair_json(self, text: str, *, return_objects: bool = False) -> Any:
        self.calls += 1
        return self.result


async def test_json_repair_salvages_an_otherwise_unparseable_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubJsonRepair(_PAYLOAD)
    monkeypatch.setitem(sys.modules, "json_repair", stub)
    _patch(monkeypatch, _text_response('{"summary": "ok", "scores": {bad}}'))

    assert await _call() == _PAYLOAD
    assert stub.calls == 1


async def test_json_repair_is_not_attempted_on_a_truncated_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repairing a cut-off payload closes the braces and yields a half-empty
    object — a scorecard with three of four axes silently missing, which is
    worse than an error because it looks like a real result."""
    stub = _StubJsonRepair({"scores": {}})
    monkeypatch.setitem(sys.modules, "json_repair", stub)
    _patch(monkeypatch, _text_response('{"summary": "the candi', finish_reason="MAX_TOKENS"))

    with pytest.raises(_CallerError):
        await _call()

    assert stub.calls == 0


async def test_an_empty_repair_result_is_not_treated_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """json_repair returns ``{}`` when it cannot make sense of the text. An
    empty scorecard must fail loudly, not be persisted as a real one."""
    monkeypatch.setitem(sys.modules, "json_repair", _StubJsonRepair({}))
    _patch(monkeypatch, _text_response("not json at all"))

    with pytest.raises(_CallerError):
        await _call()


async def test_a_missing_json_repair_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only two of the four service images ship json_repair. Where it is
    absent, the original parse error must surface — not an ImportError from
    inside the recovery path."""
    monkeypatch.setitem(sys.modules, "json_repair", None)
    _patch(monkeypatch, _text_response('{"summary": "ok", "scores": {bad}}'))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "not valid JSON" in excinfo.value.message


async def test_a_repair_that_raises_falls_through_to_the_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Exploding:
        def repair_json(self, text: str, *, return_objects: bool = False) -> Any:
            raise RuntimeError("json_repair blew up")

    monkeypatch.setitem(sys.modules, "json_repair", _Exploding())
    _patch(monkeypatch, _text_response("not json at all"))

    with pytest.raises(_CallerError) as excinfo:
        await _call()

    assert "not valid JSON" in excinfo.value.message


async def test_real_json_repair_recovers_a_raw_newline_inside_a_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live failure: even in JSON mode Gemini emits raw newlines inside
    strings when the payload embeds source code or a quoted transcript line.
    Skipped where the optional dependency is absent — the gate itself is
    covered above with a stub."""
    pytest.importorskip("json_repair")
    valid = json.dumps({"summary": "print(sum(x))", "scores": {"communication": 7}})
    broken = valid.replace("print(sum", "print(\nsum", 1)
    _patch(monkeypatch, _text_response(broken))

    assert "sum" in (await _call())["summary"]


# --------------------------------------------------------------------------
# Contract with callers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("error_cls", [_CallerError, _OtherCallerError])
async def test_the_callers_own_exception_type_is_what_is_raised(
    monkeypatch: pytest.MonkeyPatch, error_cls: type[Exception]
) -> None:
    """The three call sites map three different exception types to three
    different HTTP responses. Unifying them here would be an API change for
    three services smuggled in as a refactor."""
    _patch(monkeypatch, _FakeResponse(400, {"error": "bad request"}))

    with pytest.raises(error_cls) as excinfo:
        await _call(error_cls=error_cls)

    assert type(excinfo.value) is error_cls


async def test_no_other_exception_type_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers catch exactly their own type. A KeyError leaking out of the
    envelope parsing would bypass every one of their handlers and 500."""
    _patch(monkeypatch, _FakeResponse(200, {"candidates": "not a list"}))

    with pytest.raises(_CallerError):
        await _call()


def test_module_stays_importable_from_every_service_image() -> None:
    """shared/ is COPY'd into all four images, so a module-level import of
    anything the other images lack breaks them at container start. json_repair
    is exactly that dependency — it is in two of the four requirements files —
    which is why it must stay a guarded, function-local import. Hoisting it
    would look harmless and pass every test but this one."""
    source = pathlib.Path(__file__).parent.parent / "llm" / "gemini.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    def roots(nodes: list[ast.stmt] | list[ast.AST]) -> set[str]:
        found: set[str] = set()
        for node in nodes:
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        return found

    module_level = roots(tree.body)
    everywhere = roots(list(ast.walk(tree)))

    allowed = {"__future__", "asyncio", "json", "re", "typing", "httpx", "structlog"}
    assert module_level <= allowed, f"disallowed top-level imports: {sorted(module_level - allowed)}"
    assert "json_repair" not in module_level
    assert "json_repair" in everywhere
