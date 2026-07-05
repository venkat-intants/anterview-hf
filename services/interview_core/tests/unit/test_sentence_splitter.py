"""Unit tests for SentenceBuffer (S4-005).

Tests verify the state-machine sentence boundary detector works correctly
for all expected input shapes: single sentence, partial accumulation,
multiple boundaries per feed, empty input, whitespace-only boundaries,
and the flush() drain.
"""

from __future__ import annotations

from app.speech.sentence_splitter import SentenceBuffer


class TestSentenceBufferFeed:
    """Tests for SentenceBuffer.feed()."""

    def test_single_complete_sentence(self) -> None:
        """A single sentence ending in '.' is returned immediately."""
        buf = SentenceBuffer()
        result = buf.feed("Hello world.")
        assert result == ["Hello world."]
        # Buffer must be empty after the boundary was consumed.
        assert buf.flush() == ""

    def test_partial_sentence_buffered_then_completed(self) -> None:
        """Partial text without a boundary stays buffered; boundary completes it."""
        buf = SentenceBuffer()
        result1 = buf.feed("Hello")
        assert result1 == []  # no boundary → nothing returned

        result2 = buf.feed(" world.")
        assert result2 == ["Hello world."]
        assert buf.flush() == ""

    def test_three_sentences_in_one_feed(self) -> None:
        """Multiple boundaries in a single feed call return all completed sentences."""
        buf = SentenceBuffer()
        result = buf.feed("First. Second! Third?")
        # All three sentences have boundaries — all three are returned immediately.
        assert result == ["First.", "Second!", "Third?"]
        assert buf.flush() == ""

    def test_empty_feed_returns_empty_list(self) -> None:
        """An empty string feed returns an empty list without error."""
        buf = SentenceBuffer()
        result = buf.feed("")
        assert result == []
        assert buf.flush() == ""

    def test_whitespace_boundary_handling(self) -> None:
        """A newline boundary triggers sentence completion."""
        buf = SentenceBuffer()
        result = buf.feed("Line one\nLine two\n")
        assert result == ["Line one", "Line two"]
        assert buf.flush() == ""

    def test_flush_returns_remaining_text(self) -> None:
        """Trailing partial text without boundary is returned by flush()."""
        buf = SentenceBuffer()
        buf.feed("No boundary here")
        tail = buf.flush()
        assert tail == "No boundary here"
        # Buffer cleared — second flush returns empty.
        assert buf.flush() == ""

    def test_flush_clears_buffer_after_partial_boundary_split(self) -> None:
        """After a partial accumulation + one complete sentence, flush gets the rest."""
        buf = SentenceBuffer()
        buf.feed("First sentence. Still going")
        buf.feed(" strong")
        tail = buf.flush()
        assert tail == "Still going strong"

    def test_boundary_char_is_included_in_sentence(self) -> None:
        """Boundary character (., !, ?) is preserved at the end of the sentence."""
        for boundary, text in [(".", "Done."), ("!", "Go!"), ("?", "Really?")]:
            b = SentenceBuffer()
            result = b.feed(text)
            assert result == [text], f"boundary {boundary!r}: expected [{text!r}]"

    def test_multiple_feeds_accumulate_correctly(self) -> None:
        """Many small feeds accumulate and flush correctly."""
        buf = SentenceBuffer()
        tokens = ["Th", "is ", "is ", "a ", "test", "."]
        all_results: list[str] = []
        for token in tokens:
            all_results.extend(buf.feed(token))
        assert all_results == ["This is a test."]
        assert buf.flush() == ""

    def test_lone_punctuation_not_emitted_as_sentence(self) -> None:
        """A lone boundary character produces no sentence (strip() makes it empty)."""
        buf = SentenceBuffer()
        result = buf.feed(".")
        # The stripped sentence is empty → not added to output list.
        assert result == []

    def test_exclamation_boundary(self) -> None:
        """Exclamation mark terminates a sentence correctly."""
        buf = SentenceBuffer()
        result = buf.feed("Wow! Amazing!")
        assert result == ["Wow!", "Amazing!"]

    def test_question_mark_boundary(self) -> None:
        """Question mark terminates a sentence correctly."""
        buf = SentenceBuffer()
        result = buf.feed("Are you ready? Yes I am. Let's go!")
        assert result == ["Are you ready?", "Yes I am.", "Let's go!"]
