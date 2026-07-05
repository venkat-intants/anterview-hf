"""Anthropic adapter placeholder — S4-003 cache-block split design.

This module is intentionally a stub. It exists so the cache-block split
strategy for the four-persona system prompt is documented in code, not
buried in a design doc, before the Anthropic adapter actually lands
(planned for the production-tier migration per ``Final_stack.md``).

The Gemini REST adapter in ``app.llm.gemini`` is the only live LLM
implementation today. Tier-2 production swaps to Anthropic on AWS Bedrock
Mumbai; at that point this module gets filled in.

Cache-block split contract (S4-003, see docs/interview-persona-design-ai.md §5)
==============================================================================

The system prompt rendered by
``app.graph.prompts.render_interviewer_system_prompt`` is a deterministic
concatenation of two logical blocks, separated by the literal marker line
``\\n\\n[PERSONA: <persona_id>]\\n``:

    Block 1: BASE prompt (~600 tokens, 3 variants total — one per
             {en, hi, te}). Authored in ``INTERVIEWER_SYSTEM_PROMPT_*``.
             Owns: PII guardrails, language pinning, "one question at a
             time", scoring secrecy, max-turns rule. Hit by ALL sessions
             in the same language regardless of persona.

    Block 2: PERSONA delta (~120 tokens, 12 variants total — 4 personas
             × 3 languages). Authored in ``app.graph.personas``. Owns:
             opening register, probing style, acknowledgement rhythm,
             tie-break competency preference. Hit by ~8% of sessions
             (4 personas × 3 languages, uniform).

When this adapter is implemented, the Anthropic ``messages.create`` call
MUST split the system prompt into two ``cache_control=ephemeral`` blocks:

    system=[
        {"type": "text", "text": block1, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": block2, "cache_control": {"type": "ephemeral"}},
    ]

Splitting on the literal ``"\\n\\n[PERSONA: "`` marker is the simplest
implementation; the marker is generated unconditionally by
``render_interviewer_system_prompt`` whenever a persona is supplied
(which is always, in production — ``build_initial_state`` picks one
deterministically).

If we collapse to a single block, the cache key includes the persona
delta and ~75% of cache hits are lost — economic impact: per-session
cost rises from ~₹10-11 toward the ~₹12 hard ceiling. The split is
load-bearing for the L1 bid economics.
"""

from __future__ import annotations

# Intentionally no implementation yet. ``build_default_adapter`` in
# ``app.llm.__init__`` still raises ValueError for ``LLM_PROVIDER=anthropic``
# until this module ships.
