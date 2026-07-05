"""InterviewBrain — LangGraph policy as a per-turn STREAMING function.

ARCHITECTURE (docs/ARCH-realtime-interview.md v0.2, cto-architect review):
    The compiled LangGraph loop (build.py) owns turn *cadence* via its
    conditional-edge loop + ``await_candidate_input`` pause. That collides with
    the real-time transport (LiveKit agent), which ALSO wants to own cadence —
    two state machines fighting over "whose turn is it", which breaks barge-in.

    The fix is subtractive: keep the graph's *policy* (greeting, first question,
    competency-rotating follow-ups, max-turns close, persona, language, context
    enrichment) but drop its *loop control*. This module exposes that policy as a
    small per-turn API the agent drives:

        brain, greeting = InterviewBrain.start(...)   # greeting: static, no LLM
        async for chunk in brain.first_question():    # Q1, streamed
            ...
        async for chunk in brain.respond(candidate_text):  # follow-up OR closing
            ...
        if brain.is_complete: ...

    The agent (LiveKit) owns: VAD/turn-final detection, the candidate-input
    pause, barge-in cancellation, and persistence. The brain owns: WHAT to say.

WHY STREAMING (the C2 build task): the kept graph nodes call the BLOCKING
``adapter.generate()``. The whole real-time overlap thesis (TTS fires per
sentence while the LLM still streams later sentences) needs
``adapter.generate_stream()``. This module uses the streaming surface. NOTE
that only ``GeminiAdapter`` streams token-by-token today; ``GroqAdapter``'s
``generate_stream`` is a single-chunk shim — functionally correct here (we still
get one chunk), just without mid-response overlap until Groq streaming lands.

This module reuses the existing prompt/persona/history machinery verbatim
(``render_*`` from prompts.py, ``_build_history_messages`` from nodes.py,
``build_initial_state`` from state.py) so there is ONE source of truth for
interviewer behaviour. The compiled graph in build.py is retained for the
offline eval harness / unit tests; production drives THIS class.

PII: never log candidate text or interviewer text here (DPDP). Log only event
names, turn counters, latency, token counts.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import structlog

from app.graph.nodes import _build_history_messages
from app.graph.personas import Persona
from app.graph.prompts import (
    render_ask_question_user_prompt,
    render_closing,
    render_follow_up_user_prompt,
    render_greeting,
    render_interviewer_system_prompt,
)
from app.graph.state import (
    PHASE_CLOSING,
    PHASE_DONE,
    PHASE_IN_PROGRESS,
    InterviewState,
    Language,
    TurnRecord,
    build_initial_state,
)
from app.llm.base import LLMAdapter, LLMMessage

log = structlog.get_logger(__name__)


class InterviewBrain:
    """Per-turn interview policy driver. Transport-agnostic, streaming-first.

    Holds the ``InterviewState`` and the bound ``LLMAdapter``. Mirrors the
    node topology of build.py (greeting -> ask_question -> [follow_up]* ->
    closing) but as an externally-driven, streaming API rather than a
    self-looping compiled graph.
    """

    def __init__(self, state: InterviewState, adapter: LLMAdapter) -> None:
        self._state = state
        self._adapter = adapter

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def start(
        cls,
        *,
        adapter: LLMAdapter,
        session_id: str,
        job_id: str,
        job_title: str,
        language: Language = "en",
        max_turns: int = 5,
        persona: Persona | None = None,
        company_name: str = "",
        department: str = "",
        interview_type: str = "screening",
        experience_level: str = "",
        required_skills: list[str] | None = None,
        resume_text: str = "",
        jd_text: str = "",
    ) -> tuple[InterviewBrain, str]:
        """Create a brain for a new session and emit the greeting.

        Returns ``(brain, greeting_line)``. The greeting is STATIC per-language
        copy (no LLM call) — the agent ships it to TTS immediately while the
        first question is being generated. Mirrors ``nodes.greeting``.
        """
        state = build_initial_state(
            session_id=session_id,
            job_id=job_id,
            job_title=job_title,
            language=language,
            max_turns=max_turns,
            persona=persona,
            company_name=company_name,
            department=department,
            interview_type=interview_type,
            experience_level=experience_level,
            required_skills=required_skills,
            resume_text=resume_text,
            jd_text=jd_text,
        )
        greeting_line = render_greeting(state["language"], state["job_title"])
        state["phase"] = PHASE_IN_PROGRESS
        state["next_interviewer_message"] = greeting_line
        state["turns"].append(
            TurnRecord(turn_number=0, speaker="interviewer", text=greeting_line)
        )
        log.info("brain.start", session_id=session_id, language=language)
        brain = cls(state, adapter)
        return brain, greeting_line

    # ------------------------------------------------------------------
    # Properties the agent reads
    # ------------------------------------------------------------------
    @property
    def is_complete(self) -> bool:
        """True once the closing line has been emitted (phase == done)."""
        return self._state["phase"] == PHASE_DONE

    @property
    def language(self) -> Language:
        return self._state["language"]

    @property
    def turn_count(self) -> int:
        """Number of completed candidate-input rounds."""
        return self._state["turn_count"]

    @property
    def state(self) -> InterviewState:
        """Live state — the agent reads ``turns`` for persistence."""
        return self._state

    @property
    def last_llm_latency_ms(self) -> int | None:
        return self._state["last_llm_latency_ms"]

    # ------------------------------------------------------------------
    # Turn API
    # ------------------------------------------------------------------
    async def first_question(self) -> AsyncIterator[str]:
        """Stream the opening interview question (mirrors ``nodes.ask_question``).

        Call once, immediately after ``start``. The candidate has not spoken
        yet. Yields text chunks; the full text is committed to ``turns`` after
        the stream completes.
        """
        system_prompt = self._system_prompt()
        history = _build_history_messages(self._state)
        history.append(
            LLMMessage.user(render_ask_question_user_prompt(self._state["turn_count"]))
        )
        async for chunk in self._stream_and_commit(system_prompt, history, "first_question"):
            yield chunk

    async def respond(self, candidate_text: str) -> AsyncIterator[str]:
        """Ingest the candidate's answer, then stream the next interviewer line.

        Records the candidate turn, bumps ``turn_count`` (mirrors
        ``nodes.await_candidate_input``), then routes (mirrors
        ``build._route_after_candidate_input``):
          - ``turn_count < max_turns`` -> stream a follow-up (``nodes.follow_up``)
          - ``turn_count >= max_turns`` -> emit the STATIC closing line and mark
            the session done (``nodes.closing`` — no LLM call).

        Yields text chunks of the interviewer's next line.
        """
        # 1. Record candidate input + advance the counter.
        self._state["turns"].append(
            TurnRecord(
                turn_number=self._state["turn_count"] + 1,
                speaker="candidate",
                text=candidate_text,
            )
        )
        self._state["turn_count"] += 1
        self._state["last_candidate_input"] = None
        log.info(
            "brain.respond.ingested",
            session_id=self._state["session_id"],
            turn_count=self._state["turn_count"],
        )

        # 2. Route: follow-up while turns remain, else close.
        if self._state["turn_count"] < self._state["max_turns"]:
            async for chunk in self._follow_up():
                yield chunk
        else:
            yield self._closing()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _system_prompt(self) -> str:
        s = self._state
        return render_interviewer_system_prompt(
            job_title=s["job_title"],
            language=s["language"],
            max_turns=s["max_turns"],
            persona=s["persona"],
            company_name=s.get("company_name", ""),
            department=s.get("department", ""),
            interview_type=s.get("interview_type", "screening"),
            experience_level=s.get("experience_level", ""),
            required_skills=s.get("required_skills", []),
            resume_text=s.get("resume_text", ""),
            jd_text=s.get("jd_text", ""),
        )

    async def _follow_up(self) -> AsyncIterator[str]:
        """Stream a competency-rotating follow-up (mirrors ``nodes.follow_up``)."""
        last_candidate_input = ""
        for turn in reversed(self._state["turns"]):
            if turn["speaker"] == "candidate":
                last_candidate_input = turn["text"]
                break

        system_prompt = self._system_prompt()
        history = _build_history_messages(self._state)
        history.append(
            LLMMessage.user(
                render_follow_up_user_prompt(
                    last_candidate_input=last_candidate_input,
                    turn_count=self._state["turn_count"],
                    max_turns=self._state["max_turns"],
                )
            )
        )
        async for chunk in self._stream_and_commit(system_prompt, history, "follow_up"):
            yield chunk

    def _closing(self) -> str:
        """Emit the static closing line and mark the session done.

        Mirrors ``nodes.closing`` — two-phase (closing -> done) so the agent's
        ``is_complete`` flips after this call. No LLM call.
        """
        message = render_closing(self._state["language"])
        self._state["phase"] = PHASE_CLOSING
        self._state["next_interviewer_message"] = message
        self._state["turns"].append(
            TurnRecord(
                turn_number=self._state["turn_count"] + 1,
                speaker="interviewer",
                text=message,
            )
        )
        self._state["phase"] = PHASE_DONE
        log.info(
            "brain.closing",
            session_id=self._state["session_id"],
            turn_count=self._state["turn_count"],
        )
        return message

    async def _stream_and_commit(
        self,
        system_prompt: str,
        history: list[LLMMessage],
        event: str,
    ) -> AsyncIterator[str]:
        """Stream the LLM response, yield each chunk, then commit the full text.

        Accumulates yielded chunks so the complete interviewer line is appended
        to ``turns`` exactly once after the stream finishes — keeps transcript
        persistence identical to the blocking-node behaviour, while the agent
        gets sentence-by-sentence chunks for early TTS.

        Note on barge-in: if the agent stops consuming this iterator (candidate
        interrupted), the ``async for`` simply ends early and we never reach the
        commit — so an interrupted interviewer turn is NOT persisted, exactly as
        the architecture requires.
        """
        t_start = time.monotonic()
        parts: list[str] = []
        async for chunk in self._adapter.generate_stream(system_prompt, history):
            if chunk:
                parts.append(chunk)
                yield chunk

        latency_ms = int((time.monotonic() - t_start) * 1000)
        self._state["last_llm_latency_ms"] = latency_ms
        full_text = "".join(parts)

        self._state["next_interviewer_message"] = full_text
        self._state["turns"].append(
            TurnRecord(
                turn_number=self._state["turn_count"] + 1,
                speaker="interviewer",
                text=full_text,
            )
        )
        log.info(
            f"brain.{event}",
            session_id=self._state["session_id"],
            turn_count=self._state["turn_count"],
            persona=self._state["persona"],
            latency_ms=latency_ms,
        )
