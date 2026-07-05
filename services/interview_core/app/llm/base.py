"""LLM adapter protocol — provider-neutral surface used by the LangGraph nodes.

The interview graph never imports a concrete provider. It depends only on
``LLMAdapter`` so that swapping Gemini -> Anthropic -> Bedrock is a
constructor change in ``app.main`` (or a test fixture) — no node code edits.

Each adapter implementation is responsible for:

  - Translating our neutral ``(system_prompt, messages, max_tokens)`` shape
    into its native wire format.
  - Surfacing a uniform ``LLMError`` for any non-success path so callers
    have a single exception type to catch.
  - Returning a ``LLMResponse`` with the fields the graph and observability
    layers care about (text, token counts, finish_reason).

NOTE on ``messages`` shape: roles are normalised to Gemini's vocabulary
(``user`` / ``model``) because Gemini is our primary provider in Sprint 2.
Future Anthropic/Bedrock adapters translate ``model`` -> ``assistant``
internally — callers should not have to care.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# Role vocabulary as it appears in the messages list passed to ``generate``.
# We use Gemini's terms (``model``) rather than Anthropic's (``assistant``)
# so the primary provider needs no translation; other adapters can map.
MessageRole = Literal["user", "model"]


@dataclass(frozen=True)
class LLMMessage:
    """A single conversation turn for the LLM adapter.

    Frozen so it can be safely shared across coroutines without defensive
    copies. Use ``LLMMessage.user(...)`` / ``LLMMessage.model(...)`` for
    construction in tests.
    """

    role: MessageRole
    text: str

    @classmethod
    def user(cls, text: str) -> LLMMessage:
        return cls(role="user", text=text)

    @classmethod
    def model(cls, text: str) -> LLMMessage:
        return cls(role="model", text=text)


@dataclass(frozen=True)
class LLMResponse:
    """Adapter-neutral response shape returned by ``LLMAdapter.generate``."""

    text: str
    prompt_tokens: int
    candidates_tokens: int
    # Gemini 2.5-flash is a "thinking" model — it spends some output budget on
    # private reasoning tokens (``thoughtsTokenCount``) before producing the
    # visible answer. Non-thinking models report ``None`` here.
    thoughts_tokens: int | None
    finish_reason: str


class LLMError(RuntimeError):
    """Single exception type for ANY adapter failure.

    Carries an optional status code (HTTP for REST adapters, ``None`` for
    SDK / network errors) and an optional raw body snippet for debugging.

    Callers should catch ``LLMError`` and surface it as a uniform
    ``{"type":"error","code":"LLM_FAILURE"}`` to the client; the message
    string is for logs only — never echo it to the candidate.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Truncate aggressively — provider bodies can be multi-KB.
        self.body = body[:500] if body else None


@runtime_checkable
class LLMAdapter(Protocol):
    """Provider-neutral async LLM surface used by the LangGraph nodes."""

    async def generate(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Run one synchronous generation.

        Args:
            system_prompt: Persona / instruction block. Adapters route this
                to the provider's native system slot (e.g. Gemini
                ``systemInstruction``, Anthropic ``system``).
            messages: Full conversation history in chronological order.
                Must end with a ``user`` turn for a useful response.
            max_tokens: Per-call override of the adapter default (set at
                construction time). Useful for scoring calls that need a
                larger budget than turn-loop calls.

        Returns:
            ``LLMResponse`` with the visible text + token accounting.

        Raises:
            LLMError: Any non-success path — non-2xx HTTP, MAX_TOKENS
                truncation with no visible output, empty response, network
                timeout. Callers should NOT introspect the failure further;
                the adapter has already classified it.
        """
        ...

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream text deltas as they arrive from the model.

        Each yielded string is an incremental text chunk (token delta) that
        the caller concatenates to form the full response. The caller MUST
        consume the iterator to completion (or break early and discard) —
        not awaiting it wastes the underlying connection.

        Implementations that do not support native streaming yield the full
        response text in a single chunk to preserve the caller's
        sentence-splitting logic.

        Args:
            system_prompt: Persona / instruction block. Same semantics as
                ``generate``.
            messages: Full conversation history. Same semantics as
                ``generate``.
            max_tokens: Per-call override. Same semantics as ``generate``.

        Yields:
            str: Incremental text delta. Empty strings must NOT be yielded;
                 callers assume every yielded value contributes visible text.

        Raises:
            LLMError: Same taxonomy as ``generate`` — raised on the first
                chunk or during iteration if the connection drops.

        Default implementation for adapters that do not yet support native
        streaming: call ``generate()`` and yield the full response as a
        single chunk so the caller's sentence-splitting logic still works.
        Concrete adapters (GeminiAdapter) override this for true token-by-token
        streaming.
        """
        ...
