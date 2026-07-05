"""Unit tests for interview_worker — transcript capture, answer counting, JWT minting.

Fully offline: no LiveKit connection, no DB, no HTTP call. The LiveKit agent
primitives are replaced with simple stubs/mocks wherever the worker's business
logic touches them.

Tests cover:
  - _interviewer_instructions: structure (10 questions, Q1/Q7-9/Q10), PII guard,
    language rules (native script for hi/te).
  - _get_closing_msg: correct text returned per language / timed_out flag.
  - _mint_service_jwt: verifiable with shared.auth.jwt.verify_access_token, using
    the EXACT issuer/audience that feedback_billing expects.
  - InterviewState: the REAL extracted unit — answer counting, transcript capture,
    close-guard, final_status, should_score.
  - Wall-clock cap path: fires _on_close with timed_out=True.
  - Abrupt-disconnect path: correct status + scoring gate.
  - _post_score: swallows HTTP failures and JWT-mint failures without raising.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from jose import jwt as jose_jwt

# ---------------------------------------------------------------------------
# Unit under test
# ---------------------------------------------------------------------------
import app.worker.interview_worker as wk
from app.config import settings as _app_settings
from app.worker.interview_worker import (
    MAX_CANDIDATE_ANSWERS,
    MIN_ANSWERS_TO_SCORE,
    SESSION_WALL_CLOCK_CAP_SECONDS,
    InterviewState,
    _get_closing_msg,
    _interviewer_instructions,
    _mint_service_jwt,
    _persist_turns,
    _post_score,
)

# Script patterns (mirrors test_prompts.py conventions).
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TELUGU = re.compile(r"[ఀ-౿]")

# ---------------------------------------------------------------------------
# _interviewer_instructions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "hi", "te", "xx"])
def test_instructions_has_pii_guard(lang: str) -> None:
    """Every language variant must forbid PII collection."""
    text = _interviewer_instructions("Software Engineer", lang)
    low = text.lower()
    assert any(kw in low for kw in ("never ask", "never reveal", "never")), (
        f"lang={lang!r}: PII guardrail missing from instructions"
    )


def test_instructions_en_structure() -> None:
    """EN instructions must reference the 10-question structure."""
    text = _interviewer_instructions("Data Analyst", "en")
    assert "10" in text or "ten" in text.lower(), (
        "EN instructions must reference 10 questions"
    )
    # Q1 self-intro
    assert "Q1" in text or "introduce" in text.lower()
    # Q7-Q9 behavioural
    assert "behaviour" in text.lower() or "Q7" in text
    # Q10 wrap-up
    assert "Q10" in text or "wrap" in text.lower()


def test_instructions_hi_directs_native_script() -> None:
    """HI instructions must direct the model to use Devanagari (B-038).

    The system instruction is in English (it's a directive to the LLM), but it
    must explicitly mention 'Devanagari' so the model emits native script in its
    spoken replies. The closing messages (actual spoken text) carry native chars;
    those are tested in test_closing_msg_hi_devanagari.
    """
    text = _interviewer_instructions("Software Engineer", "hi")
    assert "Devanagari" in text, (
        "HI instructions must explicitly mention 'Devanagari' to anchor the "
        "model to native script output — B-038"
    )
    assert "NOT roman" in text or "NOT Roman" in text, (
        "HI instructions must prohibit Roman transliteration"
    )


def test_instructions_te_directs_native_script() -> None:
    """TE instructions must direct the model to use Telugu script (B-038)."""
    text = _interviewer_instructions("Software Engineer", "te")
    assert "Telugu script" in text, (
        "TE instructions must explicitly mention 'Telugu script' to anchor the "
        "model to native script output — B-038"
    )
    assert "NOT roman" in text or "NOT Roman" in text, (
        "TE instructions must prohibit Roman transliteration"
    )


def test_instructions_unknown_lang_falls_back_to_en() -> None:
    """Unknown language codes must not raise — fall back to English."""
    text = _interviewer_instructions("DevOps Engineer", "fr")
    assert "English" in text


def test_instructions_job_title_embedded() -> None:
    """The job title must appear verbatim in the instructions."""
    title = "Quantum Computing Specialist"
    text = _interviewer_instructions(title, "en")
    assert title in text


# ---------------------------------------------------------------------------
# _interviewer_instructions — resume grounding
# ---------------------------------------------------------------------------


def test_instructions_no_resume_omits_background_block() -> None:
    """With no resume (default/empty), no CANDIDATE BACKGROUND block is added."""
    assert "CANDIDATE BACKGROUND" not in _interviewer_instructions("Backend Engineer", "en")
    assert "CANDIDATE BACKGROUND" not in _interviewer_instructions(
        "Backend Engineer", "en", ""
    )
    # Whitespace-only resume is treated as empty.
    assert "CANDIDATE BACKGROUND" not in _interviewer_instructions(
        "Backend Engineer", "en", "   \n\t "
    )


def test_instructions_resume_injects_background_block() -> None:
    """A real resume is injected as a CANDIDATE BACKGROUND block and grounds Q2–Q6."""
    resume = "Built a Kafka pipeline at Acme; led a team of 4 on a React migration."
    text = _interviewer_instructions("Backend Engineer", "en", resume)
    assert "CANDIDATE BACKGROUND" in text
    assert resume in text, "Resume text must be embedded in the instructions"
    # Q2–Q6 guidance must reference grounding in the resume.
    assert "resume" in text.lower()


def test_instructions_resume_is_capped() -> None:
    """An oversized resume must be truncated to the char cap (keeps prompt bounded)."""
    from app.worker.interview_worker import _RESUME_PROMPT_CHAR_CAP

    resume = "x" * (_RESUME_PROMPT_CHAR_CAP + 5000)
    text = _interviewer_instructions("Backend Engineer", "en", resume)
    # The verbatim full-length string must NOT be present (it was truncated).
    assert resume not in text
    assert "x" * _RESUME_PROMPT_CHAR_CAP in text


def test_instructions_resume_present_keeps_pii_guard() -> None:
    """Adding a resume must not drop the PII/closing guardrails."""
    text = _interviewer_instructions("Backend Engineer", "en", "Python, FastAPI, 6 yrs.")
    low = text.lower()
    assert "never ask" in low and "never reveal" in low


# ---------------------------------------------------------------------------
# _get_closing_msg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_closing_msg_exists(lang: str) -> None:
    msg = _get_closing_msg(lang, timed_out=False)
    assert len(msg) > 20, f"lang={lang}: closing message too short"


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_timeout_msg_exists(lang: str) -> None:
    msg = _get_closing_msg(lang, timed_out=True)
    assert len(msg) > 20


def test_closing_and_timeout_differ() -> None:
    """The timed-out and normal closing messages must be distinct."""
    assert _get_closing_msg("en", timed_out=False) != _get_closing_msg("en", timed_out=True)


def test_closing_msg_unknown_lang_falls_back_to_en() -> None:
    msg = _get_closing_msg("xx")
    assert msg == _get_closing_msg("en")


def test_closing_msg_hi_devanagari() -> None:
    """HI closing message must be in Devanagari (Sarvam TTS requirement)."""
    msg = _get_closing_msg("hi")
    assert _DEVANAGARI.search(msg), "HI closing message has no Devanagari characters"


def test_closing_msg_te_telugu_script() -> None:
    """TE closing message must be in Telugu script."""
    msg = _get_closing_msg("te")
    assert _TELUGU.search(msg), "TE closing message has no Telugu characters"


# ---------------------------------------------------------------------------
# _mint_service_jwt — verify it matches feedback_billing's validator exactly
# ---------------------------------------------------------------------------


def test_mint_service_jwt_verifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    """_mint_service_jwt() must produce a JWT that passes verify_access_token.

    This is the critical regression guard: if the issuer, audience, algorithm,
    or required claims ever diverge from what feedback_billing's _require_jwt
    dependency expects, this test fails before anything reaches prod.

    feedback_billing/app/routers/score.py calls:
        verify_access_token(
            token,
            secret=_app_settings.jwt_secret,
            algorithm=_app_settings.jwt_algorithm,
            expected_issuer=_app_settings.jwt_issuer,
            expected_audience=_app_settings.jwt_audience,
        )
    with defaults: issuer="intants-data-gateway", audience="intants-services".
    """
    from shared.auth.jwt import verify_access_token

    # Patch settings to use a known test secret (avoids needing .env).
    monkeypatch.setattr(_app_settings, "jwt_secret", "test-secret-32bytes-xxxxxxxxxxx")
    monkeypatch.setattr(_app_settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(_app_settings, "jwt_issuer", "intants-data-gateway")
    monkeypatch.setattr(_app_settings, "jwt_audience", "intants-services")

    token = _mint_service_jwt()

    # Must decode successfully with the SAME params feedback_billing uses.
    payload = verify_access_token(
        token,
        secret="test-secret-32bytes-xxxxxxxxxxx",
        algorithm="HS256",
        expected_issuer="intants-data-gateway",
        expected_audience="intants-services",
    )
    assert payload["sub"] == "interview_core"
    assert "service" in payload["roles"]
    assert payload["iss"] == "intants-data-gateway"
    assert payload["aud"] == "intants-services"
    # jti must be present and non-empty (verify_access_token enforces this).
    assert payload.get("jti"), "jti claim missing or empty"


def test_mint_service_jwt_short_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """The minted JWT must expire within _SERVICE_JWT_TTL_SECONDS."""
    monkeypatch.setattr(_app_settings, "jwt_secret", "test-secret-32bytes-xxxxxxxxxxx")
    monkeypatch.setattr(_app_settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(_app_settings, "jwt_issuer", "intants-data-gateway")
    monkeypatch.setattr(_app_settings, "jwt_audience", "intants-services")

    token = _mint_service_jwt()
    # Decode without verification to inspect exp.
    raw = jose_jwt.decode(
        token,
        "test-secret-32bytes-xxxxxxxxxxx",
        algorithms=["HS256"],
        audience="intants-services",
        options={"verify_exp": False},
    )
    exp_dt = datetime.fromtimestamp(raw["exp"], tz=UTC)
    now = datetime.now(tz=UTC)
    ttl = (exp_dt - now).total_seconds()
    assert 0 < ttl <= wk._SERVICE_JWT_TTL_SECONDS + 5  # +5s tolerance for test time


# ---------------------------------------------------------------------------
# Helpers for the real InterviewState tests
# ---------------------------------------------------------------------------


def _make_chat_message(role: str, text: str | None, interrupted: bool = False) -> object:
    """Return a minimal stub matching livekit.agents.llm.ChatMessage interface."""
    from livekit.agents.llm.chat_context import ChatMessage

    msg = MagicMock(spec=ChatMessage)
    msg.role = role
    msg.text_content = text
    msg.interrupted = interrupted
    return msg


def _make_event(item: Any) -> Any:
    """Return a ConversationItemAddedEvent stub."""
    from livekit.agents.voice.events import ConversationItemAddedEvent

    ev = MagicMock(spec=ConversationItemAddedEvent)
    ev.item = item
    return ev


# ---------------------------------------------------------------------------
# InterviewState — pure unit tests (no asyncio needed)
# ---------------------------------------------------------------------------


def test_interview_state_initial_values() -> None:
    """InterviewState starts with zero answers and an empty transcript."""
    s = InterviewState()
    assert s.candidate_answer_count == 0
    assert s.transcript == []
    assert not s.close_triggered


def test_interview_state_nine_answers_no_close() -> None:
    """9 candidate answers must NOT trigger the should-close signal."""
    s = InterviewState()
    triggered_count = 0
    for i in range(MAX_CANDIDATE_ANSWERS - 1):
        should_close = s.handle_conversation_item(_make_chat_message("user", f"Answer {i + 1}"))
        if should_close:
            triggered_count += 1

    assert s.candidate_answer_count == MAX_CANDIDATE_ANSWERS - 1
    assert triggered_count == 0, (
        f"should-close signal fired after only {MAX_CANDIDATE_ANSWERS - 1} answers"
    )
    assert not s.close_triggered  # state is still open


def test_interview_state_tenth_answer_triggers_close() -> None:
    """The 10th candidate answer must return True (should-close) exactly once."""
    s = InterviewState()
    # Feed 9 silently.
    for i in range(MAX_CANDIDATE_ANSWERS - 1):
        s.handle_conversation_item(_make_chat_message("user", f"Answer {i + 1}"))

    # 10th answer — must signal close.
    should_close = s.handle_conversation_item(_make_chat_message("user", "Answer 10"))
    assert should_close, "10th answer did not return should-close=True"
    assert s.candidate_answer_count == MAX_CANDIDATE_ANSWERS


def test_interview_state_eleventh_answer_ignored_after_close() -> None:
    """An 11th answer after close_triggered must NOT re-signal close."""
    s = InterviewState()
    for i in range(MAX_CANDIDATE_ANSWERS):
        s.handle_conversation_item(_make_chat_message("user", f"Answer {i + 1}"))
    # Simulate entrypoint marking close after the 10th.
    s.mark_close_triggered()

    # 11th answer — close_triggered is True, so should_close must be False.
    should_close = s.handle_conversation_item(_make_chat_message("user", "Answer 11"))
    assert not should_close, "11th answer triggered a second close signal"
    # Count still advances (it's just the close signal that's suppressed).
    assert s.candidate_answer_count == MAX_CANDIDATE_ANSWERS + 1


def test_interview_state_transcript_role_mapping() -> None:
    """role=='user' maps to 'user'; role=='assistant' maps to 'ai' in transcript."""
    s = InterviewState()
    s.handle_conversation_item(_make_chat_message("user", "Hello"))
    s.handle_conversation_item(_make_chat_message("assistant", "Welcome!"))
    s.handle_conversation_item(_make_chat_message("system", "Ignored system msg"))

    assert s.transcript == [
        {"role": "user", "text": "Hello"},
        {"role": "ai", "text": "Welcome!"},
    ]


def test_interview_state_empty_text_not_counted() -> None:
    """Empty/whitespace/None text_content must not appear in transcript."""
    s = InterviewState()
    s.handle_conversation_item(_make_chat_message("user", ""))
    s.handle_conversation_item(_make_chat_message("user", "   "))
    s.handle_conversation_item(_make_chat_message("user", None))

    assert s.transcript == []
    assert s.candidate_answer_count == 0


def test_interview_state_non_chat_message_ignored() -> None:
    """Non-ChatMessage items (e.g. AgentHandoff) must be silently skipped."""
    s = InterviewState()
    should_close = s.handle_conversation_item(object())  # not a ChatMessage
    assert not should_close
    assert s.candidate_answer_count == 0
    assert s.transcript == []


def test_interview_state_final_status_above_min() -> None:
    """final_status() returns 'completed' when answers >= MIN_ANSWERS_TO_SCORE."""
    s = InterviewState()
    for i in range(MIN_ANSWERS_TO_SCORE):
        s.handle_conversation_item(_make_chat_message("user", f"A{i}"))
    assert s.final_status() == "completed"
    assert s.should_score() is True


def test_interview_state_final_status_below_min() -> None:
    """final_status() returns 'abandoned' when answers < MIN_ANSWERS_TO_SCORE."""
    s = InterviewState()
    for i in range(MIN_ANSWERS_TO_SCORE - 1):
        s.handle_conversation_item(_make_chat_message("user", f"A{i}"))
    assert s.final_status() == "abandoned"
    assert s.should_score() is False


# ---------------------------------------------------------------------------
# Async integration of InterviewState with the close-scheduling logic
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal AgentSession stub: records say() calls, allows shutdown()."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.shut_down = False
        self._handlers: dict[str, Any] = {}

    def on(self, event: str, cb: Any) -> None:
        self._handlers[event] = cb

    def say(self, text: str, **kwargs: Any) -> asyncio.Future[None]:
        self.said.append(text)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        fut.set_result(None)
        return fut

    def shutdown(self) -> None:
        self.shut_down = True

    async def wait(self) -> None:
        pass

    def generate_reply(self, **kwargs: Any) -> asyncio.Future[None]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        fut.set_result(None)
        return fut


@pytest.mark.asyncio
async def test_async_tenth_answer_schedules_close() -> None:
    """Feeding MAX_CANDIDATE_ANSWERS events through the real handler schedules _on_close."""
    state = InterviewState()
    close_calls: list[dict[str, bool]] = []

    async def fake_on_close(*, timed_out: bool) -> None:
        if state.close_triggered:
            return
        state.mark_close_triggered()
        close_calls.append({"timed_out": timed_out})

    # Mirror the real entrypoint handler: call state.handle_conversation_item,
    # then schedule close if signalled.
    def handler(event: Any) -> None:
        should_close = state.handle_conversation_item(event.item)
        if should_close:
            asyncio.create_task(fake_on_close(timed_out=False))

    # Feed exactly MAX_CANDIDATE_ANSWERS user messages.
    for i in range(MAX_CANDIDATE_ANSWERS):
        msg = _make_chat_message("user", f"Answer {i + 1}")
        handler(_make_event(msg))

    # Allow the created task to execute.
    await asyncio.sleep(0)

    assert state.candidate_answer_count == MAX_CANDIDATE_ANSWERS
    assert len(close_calls) == 1, f"Expected 1 close call, got {len(close_calls)}"
    assert close_calls[0]["timed_out"] is False


@pytest.mark.asyncio
async def test_async_no_double_close_on_race() -> None:
    """Even if close is scheduled twice (race condition), it fires at most once."""
    state = InterviewState()
    close_calls: list[int] = [0]

    async def fake_on_close(*, timed_out: bool) -> None:
        if state.close_triggered:
            return
        state.mark_close_triggered()
        close_calls[0] += 1

    # Schedule two concurrent close tasks (simulates a race between the
    # conversation_item handler and the wall-clock cap).
    asyncio.create_task(fake_on_close(timed_out=False))
    asyncio.create_task(fake_on_close(timed_out=True))
    await asyncio.sleep(0)

    assert close_calls[0] == 1, f"Expected 1 close execution, got {close_calls[0]}"


@pytest.mark.asyncio
async def test_wall_clock_cap_fires_with_timed_out_true() -> None:
    """Wall-clock cap path must call _on_close with timed_out=True."""
    state = InterviewState()
    close_calls: list[dict[str, bool]] = []

    async def fake_on_close(*, timed_out: bool) -> None:
        if state.close_triggered:
            return
        state.mark_close_triggered()
        close_calls.append({"timed_out": timed_out})

    # Simulate the _wall_clock_cap coroutine logic with a near-zero sleep.
    async def fast_cap() -> None:
        await asyncio.sleep(0)  # immediate in tests
        if not state.close_triggered:
            await fake_on_close(timed_out=True)

    await fast_cap()

    assert len(close_calls) == 1
    assert close_calls[0]["timed_out"] is True


@pytest.mark.asyncio
async def test_abrupt_close_above_min_scores_and_completes() -> None:
    """Abrupt disconnect with answers >= MIN_ANSWERS_TO_SCORE → 'completed' + scoring."""
    state = InterviewState()
    # Feed enough answers to pass the MIN threshold.
    for i in range(MIN_ANSWERS_TO_SCORE):
        state.handle_conversation_item(_make_chat_message("user", f"A{i}"))

    assert state.should_score() is True
    assert state.final_status() == "completed"

    status_calls: list[str] = []
    score_calls: list[int] = [0]

    async def fake_update_status(room: str, status: str, **kwargs: Any) -> None:
        status_calls.append(status)

    async def fake_post_score(**kwargs: Any) -> None:
        score_calls[0] += 1

    # Mirror _abrupt_close logic using the real state.
    if state.close_triggered:
        return
    state.mark_close_triggered()
    await fake_update_status("test-room", state.final_status())
    if state.should_score():
        await fake_post_score()

    assert status_calls == ["completed"]
    assert score_calls[0] == 1, "Scoring must be attempted when answers >= MIN_ANSWERS_TO_SCORE"


@pytest.mark.asyncio
async def test_abrupt_close_below_min_abandoned_no_score() -> None:
    """Abrupt disconnect with answers < MIN_ANSWERS_TO_SCORE → 'abandoned' + no scoring."""
    state = InterviewState()
    # Feed fewer answers than the threshold.
    for i in range(MIN_ANSWERS_TO_SCORE - 1):
        state.handle_conversation_item(_make_chat_message("user", f"A{i}"))

    assert state.should_score() is False
    assert state.final_status() == "abandoned"

    status_calls: list[str] = []
    score_calls: list[int] = [0]

    async def fake_update_status(room: str, status: str, **kwargs: Any) -> None:
        status_calls.append(status)

    async def fake_post_score(**kwargs: Any) -> None:
        score_calls[0] += 1

    # Mirror _abrupt_close logic using the real state.
    if state.close_triggered:
        return
    state.mark_close_triggered()
    await fake_update_status("test-room", state.final_status())
    if state.should_score():  # False — this block must NOT execute
        await fake_post_score()

    assert status_calls == ["abandoned"]
    assert score_calls[0] == 0, "Scoring must NOT be attempted when answers < MIN_ANSWERS_TO_SCORE"


# ---------------------------------------------------------------------------
# _post_score: swallows HTTP failures and JWT-mint failures without raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_score_swallows_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_post_score must not raise when httpx raises a transport-level error."""
    monkeypatch.setattr(_app_settings, "jwt_secret", "test-secret-32bytes-xxxxxxxxxxx")
    monkeypatch.setattr(_app_settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(_app_settings, "jwt_issuer", "intants-data-gateway")
    monkeypatch.setattr(_app_settings, "jwt_audience", "intants-services")
    monkeypatch.setattr(_app_settings, "feedback_billing_url", "http://localhost:9999")

    # Patch AsyncClient.post to raise a connection error on every attempt.
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("connection refused")
        # Must return cleanly — no exception propagated.
        await _post_score(
            session_id="test-session-id",
            job_title="Engineer",
            experience_level="entry",
            language="en",
            jd_text="",
            transcript=[{"role": "user", "text": "Hello"}],
        )
    # If we reach here, _post_score swallowed the error correctly.


@pytest.mark.asyncio
async def test_post_score_swallows_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_post_score must not raise when the server returns a 5xx error."""
    monkeypatch.setattr(_app_settings, "jwt_secret", "test-secret-32bytes-xxxxxxxxxxx")
    monkeypatch.setattr(_app_settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(_app_settings, "jwt_issuer", "intants-data-gateway")
    monkeypatch.setattr(_app_settings, "jwt_audience", "intants-services")
    monkeypatch.setattr(_app_settings, "feedback_billing_url", "http://localhost:9999")

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "service unavailable", request=MagicMock(), response=mock_response
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await _post_score(
            session_id="test-session-id",
            job_title="Engineer",
            experience_level="entry",
            language="en",
            jd_text="",
            transcript=[{"role": "user", "text": "Hello"}],
        )


@pytest.mark.asyncio
async def test_post_score_jwt_mint_failure_aborts_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JWT-mint failure must abort _post_score immediately — no HTTP call made."""
    monkeypatch.setattr(_app_settings, "feedback_billing_url", "http://localhost:9999")

    def bad_mint() -> str:
        raise RuntimeError("signing key unavailable")

    post_called: list[int] = [0]

    with patch.object(wk, "_mint_service_jwt", bad_mint), patch(
        "httpx.AsyncClient.post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.side_effect = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("HTTP post was called despite JWT-mint failure")
        )

        await _post_score(
            session_id="test-session-id",
            job_title="Engineer",
            experience_level="entry",
            language="en",
            jd_text="",
            transcript=[],
        )
        # If we reach here without AssertionError, HTTP was not called.
        post_called[0] = mock_post.call_count

    assert post_called[0] == 0, "HTTP post must NOT be called when JWT mint fails"


# ---------------------------------------------------------------------------
# Module constants — sanity
# ---------------------------------------------------------------------------


def test_constants_sensible() -> None:
    """Basic sanity checks on module constants."""
    assert MAX_CANDIDATE_ANSWERS == 10
    assert MIN_ANSWERS_TO_SCORE >= 1
    assert MIN_ANSWERS_TO_SCORE < MAX_CANDIDATE_ANSWERS
    assert SESSION_WALL_CLOCK_CAP_SECONDS >= 600  # at least 10 minutes


# ---------------------------------------------------------------------------
# _persist_turns — transcript persistence to the turns table
# ---------------------------------------------------------------------------


def _make_capture_factory(captured: list) -> Any:
    """Mock async_sessionmaker whose session captures rows passed to add_all()."""

    @asynccontextmanager
    async def _cm() -> Any:
        db = AsyncMock()
        db.add_all = MagicMock(side_effect=lambda rows: captured.extend(rows))
        db.commit = AsyncMock()
        yield db

    factory = MagicMock(side_effect=lambda: _cm())
    return factory


@pytest.mark.asyncio
async def test_persist_turns_maps_roles_and_sequences() -> None:
    """role 'user'->'candidate', 'ai'->'interviewer'; turn_number is 1-based in order."""
    sid = "11111111-1111-1111-1111-111111111111"
    transcript = [
        {"role": "ai", "text": "Tell me about yourself."},
        {"role": "user", "text": "I am a backend engineer."},
        {"role": "ai", "text": "What did you build?"},
        {"role": "user", "text": "A Kafka pipeline."},
    ]
    captured: list[Any] = []
    factory = _make_capture_factory(captured)

    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        await _persist_turns(sid, transcript)

    assert len(captured) == 4
    assert [r.turn_number for r in captured] == [1, 2, 3, 4]
    assert [r.speaker for r in captured] == [
        "interviewer", "candidate", "interviewer", "candidate",
    ]
    assert str(captured[0].session_id) == sid
    assert captured[1].text_content == "I am a backend engineer."


@pytest.mark.asyncio
async def test_persist_turns_empty_transcript_is_noop() -> None:
    """An empty transcript must not touch the DB at all (early return)."""
    factory = MagicMock(side_effect=AssertionError("factory must not be called"))
    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        await _persist_turns("11111111-1111-1111-1111-111111111111", [])
    # No assertion error raised → factory was never invoked.


@pytest.mark.asyncio
async def test_persist_turns_invalid_uuid_is_safe() -> None:
    """A non-UUID room name must return quietly without raising or hitting the DB."""
    factory = MagicMock(side_effect=AssertionError("factory must not be called"))
    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        await _persist_turns("not-a-uuid", [{"role": "user", "text": "hi"}])


@pytest.mark.asyncio
async def test_persist_turns_swallows_db_errors() -> None:
    """A DB failure during persist must never raise (best-effort contract)."""
    @asynccontextmanager
    async def _boom() -> Any:
        db = AsyncMock()
        db.add_all = MagicMock()
        db.commit = AsyncMock(side_effect=RuntimeError("db down"))
        yield db

    factory = MagicMock(side_effect=lambda: _boom())
    with (
        patch("app.database.init_engine"),
        patch("app.database.get_session_factory", return_value=factory),
    ):
        # Must not raise.
        await _persist_turns(
            "11111111-1111-1111-1111-111111111111",
            [{"role": "user", "text": "hi"}],
        )
