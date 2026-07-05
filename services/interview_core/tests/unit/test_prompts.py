"""Unit tests for multilingual interviewer system prompts (S3-012,
revised post-founder-review).

Register decision (B-038, founder): HI/TE prompts instruct the model to
emit **NATIVE script** (Devanagari / Telugu) with English technical
loanwords kept in Latin inline. The register stays modern/casual/
code-mixed (NOT formal literary script), but the SCRIPT must be native:
Sarvam bulbul TTS is trained on native script and mispronounces Roman
text letter-by-letter ("ga" → "g-a"). The gate below therefore asserts
the prompt CONTAINS native script (Devanagari for hi, Telugu for te).
See memory: feedback_modern_codemixed_hi_te.

Validates that ``get_interviewer_prompt`` returns the correct language
variant for ``en`` / ``hi`` / ``te``, gracefully falls back to English for
unknown language codes, and that every variant:

  - contains the ``{job_title}`` placeholder (so ``render_*`` can fill it),
  - is written in the right register (English / Hinglish / Tenglish),
  - includes at least one PII-collection prohibition clause.

These tests are deterministic and offline — no LLM call is ever made.

S3-012 acceptance: "EN session → EN template loaded; HI session → HI
template loaded; TE session → TE template loaded."
"""

from __future__ import annotations

import re

import pytest

from app.graph.personas import PERSONA_ORDER, Persona
from app.graph.prompts import (
    INTERVIEWER_SYSTEM_PROMPT_EN,
    INTERVIEWER_SYSTEM_PROMPT_HI,
    INTERVIEWER_SYSTEM_PROMPT_TE,
    get_interviewer_prompt,
    render_interviewer_system_prompt,
)

# ---------------------------------------------------------------------------
# Script gate (B-038) — HI/TE prompts MUST contain native script so the model
# is anchored to emit Devanagari / Telugu (not Roman transliteration, which
# Sarvam TTS mispronounces). We match the Unicode block for each script.
#   - Devanagari: U+0900–U+097F
#   - Telugu:     U+0C00–U+0C7F
# A regression to pure-Roman (the old Hinglish/Tenglish style) or pure-English
# leaves the prompt with no native characters and trips this gate.
# ---------------------------------------------------------------------------
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TELUGU = re.compile(r"[ఀ-౿]")


def _contains_script(text: str, pattern: re.Pattern[str]) -> bool:
    """Return True if ``text`` contains at least one char in the script block."""
    return pattern.search(text) is not None


# ---------------------------------------------------------------------------
# Per-language retrieval
# ---------------------------------------------------------------------------


def test_get_prompt_en() -> None:
    """``get_interviewer_prompt('en')`` returns the English template."""
    prompt = get_interviewer_prompt("en")

    assert prompt is INTERVIEWER_SYSTEM_PROMPT_EN, (
        "expected the canonical EN constant; got a different object"
    )
    # Anchor word — confirms we got a real prompt, not a stub or empty
    # string. "interviewer" appears in the persona line.
    lowered = prompt.lower()
    assert "interviewer" in lowered or "interview" in lowered, (
        "EN prompt missing the 'interview(er)' anchor word"
    )


def test_get_prompt_hi() -> None:
    """``get_interviewer_prompt('hi')`` returns the HI template in Devanagari.

    Script gate (B-038): the prompt MUST contain Devanagari characters so the
    model is anchored to emit native script. Catches a regression to pure
    English or to the old Roman-transliterated Hinglish, both of which leave no
    Devanagari and cause Sarvam TTS to mispronounce. See memory:
    feedback_modern_codemixed_hi_te.
    """
    prompt = get_interviewer_prompt("hi")

    assert prompt is INTERVIEWER_SYSTEM_PROMPT_HI, (
        "expected the canonical HI constant; got a different object"
    )
    assert _contains_script(prompt, _DEVANAGARI), (
        "HI prompt contains no Devanagari characters — was it reverted to "
        "pure English or Roman-transliterated Hinglish? Native script is "
        "required for correct Sarvam TTS pronunciation."
    )


def test_get_prompt_te() -> None:
    """``get_interviewer_prompt('te')`` returns the TE template in Telugu script.

    Script gate — see test_get_prompt_hi for rationale.
    """
    prompt = get_interviewer_prompt("te")

    assert prompt is INTERVIEWER_SYSTEM_PROMPT_TE, (
        "expected the canonical TE constant; got a different object"
    )
    assert _contains_script(prompt, _TELUGU), (
        "TE prompt contains no Telugu characters — was it reverted to pure "
        "English or Roman-transliterated Tenglish? Native script is required "
        "for correct Sarvam TTS pronunciation."
    )


def test_get_prompt_unknown_falls_back_to_en() -> None:
    """Unknown language codes (e.g. 'fr') graceful-fallback to English.

    We deliberately do NOT raise: a session created against a newer schema
    (future 22-language rollout) should still get a functional interview
    instead of a 500 at runtime.
    """
    prompt_fr = get_interviewer_prompt("fr")
    prompt_bn = get_interviewer_prompt("bn")
    prompt_empty = get_interviewer_prompt("")

    assert prompt_fr is INTERVIEWER_SYSTEM_PROMPT_EN
    assert prompt_bn is INTERVIEWER_SYSTEM_PROMPT_EN
    assert prompt_empty is INTERVIEWER_SYSTEM_PROMPT_EN


# ---------------------------------------------------------------------------
# Structural invariants — every prompt must support templating + PII guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "prompt"),
    [
        ("en", INTERVIEWER_SYSTEM_PROMPT_EN),
        ("hi", INTERVIEWER_SYSTEM_PROMPT_HI),
        ("te", INTERVIEWER_SYSTEM_PROMPT_TE),
    ],
)
def test_prompt_has_job_title_placeholder(language: str, prompt: str) -> None:
    """Every variant must expose the ``{job_title}`` placeholder.

    ``render_interviewer_system_prompt`` calls ``str.format(job_title=...)``
    — if a translation accidentally dropped the placeholder, candidates
    would see the literal string "{job_title}" or get a KeyError.
    """
    assert "{job_title}" in prompt, (
        f"{language} prompt missing required {{job_title}} placeholder"
    )
    # Also exercise the render path end-to-end so a mistyped placeholder
    # (e.g. ``{jobtitle}``) is caught here, not at runtime in the WS handler.
    rendered = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language=language,  # type: ignore[arg-type]
        max_turns=5,
    )
    assert "Junior Java Developer" in rendered, (
        f"{language} prompt render did not substitute job_title"
    )
    assert "{job_title}" not in rendered, (
        f"{language} prompt render left an un-substituted placeholder"
    )


@pytest.mark.parametrize(
    ("language", "prompt", "negation_markers"),
    [
        # English negation: "do not" / "do NOT" / "avoid" / "not ask".
        (
            "en",
            INTERVIEWER_SYSTEM_PROMPT_EN,
            ("do not", "avoid", "not ask"),
        ),
        # Hinglish negation: transliterated "mat" (don't, e.g. "mat
        # pucho") OR explicit English "do not" embedded in the rule list.
        # Lower-case substring match — we just want to detect the absence
        # of a prohibition entirely, not parse grammar.
        (
            "hi",
            INTERVIEWER_SYSTEM_PROMPT_HI,
            ("mat ", "do not"),
        ),
        # Tenglish negation: transliterated "cheyaku" / "adagaku" / "ivvaku"
        # (don't do / don't ask / don't give) OR explicit English "do not".
        (
            "te",
            INTERVIEWER_SYSTEM_PROMPT_TE,
            ("cheyaku", "adagaku", "ivvaku", "teesukoku", "do not"),
        ),
    ],
)
def test_prompt_pii_collection_forbidden_clause(
    language: str,
    prompt: str,
    negation_markers: tuple[str, ...],
) -> None:
    """Each prompt must contain at least one prohibition marker.

    Coarse guard — confirms the DPDP-aligned PII guardrail wasn't lost in
    the register switch from native script to code-mixed Hinglish/
    Tenglish. A failure here means a candidate could be asked for their
    phone number in HI/TE, even though the EN prompt forbids it.
    """
    haystack = prompt.lower()
    assert any(marker in haystack for marker in negation_markers), (
        f"{language} prompt has no prohibition marker from {negation_markers!r} — "
        "PII-collection guardrail may have been lost in the register switch"
    )


# ---------------------------------------------------------------------------
# S4-003 — Persona delta injection into the rendered system prompt.
#
# These tests guard the renderer contract — they do NOT test the contents
# of the persona deltas themselves (that lives in test_personas.py). The
# contract here is purely structural:
#
#   1. The persona marker ``[PERSONA: <id>]`` appears in the rendered string.
#   2. The marker appears AFTER the base rules block (recency-bias
#      placement per docs/interview-persona-design-ai.md §3).
#   3. Omitting ``persona=`` reproduces the pre-S4-003 output verbatim —
#      backwards-compat for any code path that hasn't migrated yet.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona", list(PERSONA_ORDER))
@pytest.mark.parametrize("language", ["en", "hi", "te"])
def test_render_includes_persona_delta_marker(
    persona: Persona,
    language: str,
) -> None:
    """Rendered system prompt MUST contain ``[PERSONA: <persona_id>]`` marker.

    The marker is what the (future) Anthropic adapter splits on to build
    the two cache_control blocks (per S4-003 §5). If the marker is missing
    or malformed, the cache split silently collapses into one block and
    per-session cost rises ~25%. Catch that here, not in production.
    """
    rendered = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language=language,  # type: ignore[arg-type]
        max_turns=5,
        persona=persona,
    )
    expected_marker = f"[PERSONA: {persona}]"
    assert expected_marker in rendered, (
        f"persona={persona!r} language={language!r}: marker "
        f"{expected_marker!r} missing from rendered prompt"
    )


@pytest.mark.parametrize("persona", list(PERSONA_ORDER))
@pytest.mark.parametrize(
    ("language", "rules_anchor"),
    [
        # Each anchor is a SHORT phrase taken from late in the rules
        # block of its language's base prompt. The persona marker MUST
        # appear AFTER this anchor — i.e. the persona block is appended,
        # never prepended. (Recency-bias placement.)
        ("en", "polite thank-you"),
        ("hi", "polite thank-you"),
        ("te", "polite thank-you"),
    ],
)
def test_persona_delta_appears_after_rules_block(
    persona: Persona,
    language: str,
    rules_anchor: str,
) -> None:
    """The persona marker MUST appear strictly AFTER the rules-block anchor.

    Catches a regression where someone refactors ``render_*`` to prepend
    the persona block — which the v1 pilots showed causes PII-rule drift
    (the model deprioritises the PII guardrail when chatty persona text
    sits in front of it).
    """
    rendered = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language=language,  # type: ignore[arg-type]
        max_turns=5,
        persona=persona,
    )
    anchor_idx = rendered.find(rules_anchor)
    marker_idx = rendered.find(f"[PERSONA: {persona}]")

    assert anchor_idx != -1, (
        f"language={language!r}: rules-block anchor {rules_anchor!r} not "
        "found in rendered prompt — base prompt may have been refactored "
        "without updating this test"
    )
    assert marker_idx != -1, (
        f"persona={persona!r} language={language!r}: persona marker missing "
        "(covered separately by test_render_includes_persona_delta_marker)"
    )
    assert marker_idx > anchor_idx, (
        f"persona={persona!r} language={language!r}: persona marker landed "
        f"BEFORE the rules-block anchor (marker_idx={marker_idx}, "
        f"anchor_idx={anchor_idx}). Persona deltas MUST be appended after "
        "the rules block — see docs/interview-persona-design-ai.md §3."
    )


def test_render_without_persona_preserves_legacy_output() -> None:
    """``persona=None`` (or omitted) MUST reproduce the pre-S4-003 string.

    Backwards-compatibility guard for any test fixture or call site that
    has not yet adopted the persona kwarg. If this regresses, every
    pre-S4-003 caller suddenly sees a ``[PERSONA: ...]`` marker in the
    rendered prompt — silently changing behaviour everywhere.
    """
    rendered_default = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
    )
    rendered_explicit_none = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
        persona=None,
    )
    assert rendered_default == rendered_explicit_none
    assert "[PERSONA:" not in rendered_default, (
        "Persona marker leaked into the no-persona render path — "
        "backwards-compat broken"
    )


# ---------------------------------------------------------------------------
# B-033 — interview context enrichment ([CONTEXT] block injection).
#
# Structural contract:
#   1. A [CONTEXT] block appears when any candidate/job context is supplied.
#   2. NO [CONTEXT] block appears when all context fields are at defaults
#      (backwards compatibility — preserves the legacy prompt byte-for-byte).
#   3. resume_text / jd_text are length-capped so a long document cannot blow
#      the per-turn input-token budget (the block rides every turn).
#   4. The base-prompt opening reflects interview_type (screening vs technical).
# ---------------------------------------------------------------------------


def test_context_block_included_when_company_set() -> None:
    """A [CONTEXT] block is injected when ``company_name`` is provided."""
    rendered = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
        company_name="Acme",
    )
    assert "[CONTEXT]" in rendered, (
        "[CONTEXT] block missing even though company_name was set — "
        "context enrichment did not fire"
    )
    assert "Company: Acme" in rendered, (
        "company_name value did not land in the [CONTEXT] block"
    )


def test_context_block_omitted_when_all_empty() -> None:
    """No [CONTEXT] block when every context field is at its default.

    This is the backwards-compat guard: existing tests / call sites that
    never pass context must see the pre-B-033 prompt unchanged.
    """
    rendered = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
    )
    assert "[CONTEXT]" not in rendered, (
        "[CONTEXT] block leaked into a context-free render — "
        "backwards-compat broken (legacy prompt must be byte-identical)"
    )


def test_resume_text_truncated_to_1500_chars() -> None:
    """A long ``resume_text`` is truncated to 1500 chars in the prompt.

    The system prompt ships on EVERY turn, so an un-capped resume would
    compound the input-token cost across the whole session. We assert the
    first 1500 chars survive and the 1501st+ are dropped.
    """
    # Use a distinctive overflow sentinel that would NEVER occur in the base
    # prompt, so a false positive (a stray letter elsewhere in the rules
    # block) cannot mask a real truncation regression.
    overflow_marker = "ZZ_OVERFLOW_ZZ"
    long_resume = "A" * 1500 + overflow_marker + "C" * 500  # > 1500 chars total
    rendered = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
        resume_text=long_resume,
    )
    assert "[CONTEXT]" in rendered
    # The first 1500 resume chars must survive.
    assert "A" * 1500 in rendered, "first 1500 resume chars were dropped"
    # The overflow sentinel sits at index 1500 — past the cap — and must be gone.
    assert overflow_marker not in rendered, (
        "resume_text was not truncated at 1500 chars — overflow chars leaked "
        "into the prompt and will inflate per-turn token cost"
    )


def test_interview_type_screening_vs_technical() -> None:
    """The base-prompt opening changes between 'screening' and 'technical'.

    interview_type is surfaced in the opening line of the base prompt
    (``conducting a {interview_type} interview for the {job_title} role``),
    so two renders that differ only in interview_type must differ in text.
    """
    screening = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
        interview_type="screening",
    )
    technical = render_interviewer_system_prompt(
        job_title="Junior Java Developer",
        language="en",
        max_turns=5,
        interview_type="technical",
    )
    assert "screening interview" in screening
    assert "technical interview" in technical
    assert screening != technical, (
        "interview_type had no effect on the rendered prompt — the opening "
        "line is not parameterised on interview_type"
    )
