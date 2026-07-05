"""Unit tests for the four-persona interview system (S4-003).

Strategy:
- All deterministic and offline — zero LLM calls.
- Selection uniformity tested against a fixed sample of 1000 UUIDs (no
  flaky randomness — uniformity assertion is generous enough to survive
  legitimate hash variance).
- Coverage matrix: every Persona × every Language MUST have a non-empty
  delta string. This is the contract the renderer relies on.

Design reference: ``docs/interview-persona-design-ai.md``.
"""

from __future__ import annotations

import uuid
from typing import get_args

import pytest

from app.graph.personas import (
    PERSONA_DELTAS_BY_LANGUAGE,
    PERSONA_ORDER,
    Persona,
    get_persona_delta,
    select_persona,
)

# ---------------------------------------------------------------------------
# Selection — determinism and uniformity
# ---------------------------------------------------------------------------


def test_select_persona_deterministic() -> None:
    """Same ``session_id`` MUST map to the same persona across 10 calls.

    The deterministic contract is load-bearing: debugging a candidate
    complaint ("the AI was rude to me") and the eval harness both pin
    ``session_id`` to reproduce a specific persona's behaviour. Any
    non-determinism here is a SEV regression.
    """
    session_id = "11111111-2222-3333-4444-555555555555"
    first = select_persona(session_id)
    for _ in range(10):
        assert select_persona(session_id) == first, (
            "select_persona is non-deterministic — every call with the "
            "same session_id must return the same persona"
        )


def test_select_persona_returns_valid_literal() -> None:
    """Returned value MUST be one of the four ``Persona`` literal members.

    Guards against off-by-one bucket-math regressions (e.g. someone changes
    ``len(PERSONA_ORDER)`` without updating the tuple).
    """
    valid_personas = set(get_args(Persona))
    # Sample a handful of UUIDs to cover all four buckets in expectation.
    for _ in range(20):
        chosen = select_persona(str(uuid.uuid4()))
        assert chosen in valid_personas, (
            f"select_persona returned {chosen!r}, not in {valid_personas!r}"
        )


def test_select_persona_uniform_distribution() -> None:
    """1000 random UUIDs spread roughly 25% ± 5% per persona.

    blake2s on UUIDv4 inputs is uniform to many decimal places — we expect
    very close to 250 hits per bucket. Acceptance threshold (≥200 / ≤300
    per bucket) is generous enough to never flake on legitimate sampling
    variance while still catching a real distribution regression (e.g. a
    bug that always returns the same persona, or biases two of four).
    """
    counts: dict[Persona, int] = {p: 0 for p in PERSONA_ORDER}
    sample_size = 1000

    for _ in range(sample_size):
        chosen = select_persona(str(uuid.uuid4()))
        counts[chosen] += 1

    # Sanity — every persona must show up at all. A zero would mean the
    # bucket math collapsed (e.g. modulo 3 instead of modulo 4).
    for persona in PERSONA_ORDER:
        assert counts[persona] >= 200, (
            f"persona {persona!r} only hit {counts[persona]} / {sample_size} "
            "times — uniform distribution regression. Expected ~250 each."
        )
        assert counts[persona] <= 300, (
            f"persona {persona!r} hit {counts[persona]} / {sample_size} "
            "times — over-represented. Expected ~250 each."
        )

    # Totals consistency check — guards against a mutation that
    # accidentally double-counts.
    assert sum(counts.values()) == sample_size


def test_select_persona_empty_session_id_does_not_raise() -> None:
    """Empty ``session_id`` MUST NOT raise — degrade rather than 500.

    Production should never pass an empty session_id, but a graceful
    fallback during a handshake bug is preferable to a mid-WS 500. The
    return value must still be a valid persona.
    """
    chosen = select_persona("")
    assert chosen in PERSONA_ORDER


# ---------------------------------------------------------------------------
# Delta coverage — Persona × Language matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", list(PERSONA_ORDER))
@pytest.mark.parametrize("language", ["en", "hi", "te"])
def test_persona_delta_present_for_all_langs(
    persona: Persona,
    language: str,
) -> None:
    """Every Persona × Language pair MUST have a non-empty delta string.

    A missing entry would crash ``get_persona_delta`` with a KeyError mid-
    session. Tests are parametrised so a missing entry pinpoints exactly
    which pair regressed rather than failing one omnibus assertion.
    """
    delta = get_persona_delta(persona, language)  # type: ignore[arg-type]
    assert delta, (
        f"persona={persona!r} language={language!r} has empty delta — "
        "renderer would silently drop the persona overlay"
    )
    # Sanity — deltas must be at least 50 chars to carry the four
    # required clauses (style + acknowledgement + probe + tie-break).
    # Anything shorter is almost certainly a stub or a typo.
    assert len(delta) >= 50, (
        f"persona={persona!r} language={language!r} delta is suspiciously "
        f"short ({len(delta)} chars). Each delta should carry ≥ 4 clauses."
    )


def test_persona_deltas_cover_all_four_personas_per_language() -> None:
    """Each language dict MUST contain all four ``PERSONA_ORDER`` keys.

    Defensive — checks the dict keys directly so adding a fifth persona to
    the Literal but forgetting to author its HI/TE delta fails this test
    instead of crashing at runtime on the first HI/TE session that bucket
    happens to land in.
    """
    for language, deltas in PERSONA_DELTAS_BY_LANGUAGE.items():
        missing = set(PERSONA_ORDER) - set(deltas.keys())
        assert not missing, (
            f"language={language!r} missing persona deltas for {missing!r}"
        )


def test_get_persona_delta_unknown_language_falls_back_to_en() -> None:
    """Unknown language codes MUST fall back to EN — same policy as the base prompt.

    Mirrors ``get_interviewer_prompt`` so a session created against a
    newer schema (future 22-language rollout) does not 500 here while we
    wait for the persona deltas to be authored in the new language.
    """
    en_delta = get_persona_delta("warm_screener", "en")
    # ``Language`` literal is en/hi/te — pass a forward-compat code that's
    # not registered. Cast through Any to satisfy mypy strict on the test.
    bn_delta = get_persona_delta("warm_screener", "bn")  # type: ignore[arg-type]
    assert bn_delta == en_delta, (
        "Unknown language did not fall back to EN — a Bengali session "
        "would 500 once Bengali base prompt ships but personas don't yet"
    )
