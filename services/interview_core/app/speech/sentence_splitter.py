"""Stream-friendly sentence boundary detector (S4-005).

Accepts incremental text chunks (token deltas from the LLM stream), yields
completed sentences when a boundary character is encountered, and buffers any
trailing unfinished text for the next chunk.

Boundary characters: ``. ! ? \\n``

Design notes:

- No NLP, no regex magic — a simple state machine over boundary characters.
  False positives (e.g. "Dr.", "v2.5", "etc.") are acceptable for interview
  screening responses; the TTS adapter handles short fragments gracefully.
- Optimised for the Day-1 language set (EN / Hinglish / Tenglish).  All three
  use Roman punctuation per the ``feedback_modern_codemixed_hi_te`` memory
  entry, so the same boundary set works for all three.
- Thread-safety: ``SentenceBuffer`` is NOT thread-safe.  Each WebSocket
  session instantiates its own buffer and it is only accessed by the single
  coroutine running the streaming TTS pipeline.
"""

from __future__ import annotations

# Characters that mark the END of a sentence.
# A period alone is ambiguous ("Dr.", "v2.5") but for screening interviews the
# false-positive rate is low enough to accept. Capture as Sprint 5+ follow-up.
_BOUNDARY_CHARS: frozenset[str] = frozenset(".!?\n")


class SentenceBuffer:
    """Stateful accumulator that yields complete sentences from a text stream.

    Usage::

        buf = SentenceBuffer()
        async for chunk in llm_stream:
            for sentence in buf.feed(chunk):
                await tts(sentence)
        tail = buf.flush()
        if tail.strip():
            await tts(tail)
    """

    def __init__(self) -> None:
        self._buf: str = ""

    def feed(self, text: str) -> list[str]:
        """Append ``text`` to the internal buffer and return completed sentences.

        A sentence is complete when a boundary character (``. ! ? \\n``) is
        encountered.  The boundary character is INCLUDED at the end of the
        returned sentence so the TTS adapter receives natural punctuation.
        Any text after the last boundary stays buffered for the next call.

        Args:
            text: Incremental text chunk from the LLM stream.  May contain
                zero, one, or many boundary characters.  May be empty — in
                that case an empty list is returned.

        Returns:
            A list of complete sentences (possibly empty) in order of
            occurrence within ``text``.  Each sentence includes its trailing
            boundary character and has leading/trailing whitespace stripped.
        """
        if not text:
            return []

        self._buf += text
        sentences: list[str] = []

        while True:
            # Find the first boundary in the current buffer.
            boundary_pos: int = -1
            for i, ch in enumerate(self._buf):
                if ch in _BOUNDARY_CHARS:
                    boundary_pos = i
                    break

            if boundary_pos == -1:
                # No boundary found — everything stays buffered.
                break

            # Slice up to and including the boundary character.
            sentence = self._buf[: boundary_pos + 1].strip()
            # Advance buffer past the boundary.
            self._buf = self._buf[boundary_pos + 1 :]

            # Skip sentences with no actual words — pure whitespace or
            # lone boundary chars (e.g. a stray "." from "...", or "Dr.")
            # would otherwise fire a no-op TTS call.
            if sentence and any(c.isalnum() for c in sentence):
                sentences.append(sentence)

        return sentences

    def flush(self) -> str:
        """Return (and clear) whatever remains in the buffer.

        Call this after the LLM stream ends to retrieve the final partial
        sentence that had no terminating boundary character.

        Returns:
            The remaining buffered text, which may be an empty string.
        """
        # Strip leading whitespace left over from the previous sentence's
        # boundary char (e.g. ". Still going" → buffer starts with " Still…").
        tail = self._buf.strip()
        self._buf = ""
        return tail
