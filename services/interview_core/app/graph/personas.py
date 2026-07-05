"""Interviewer personas — S4-003.

Four interviewer personas layered on top of the base system prompt in
``app.graph.prompts`` so that two sessions for the same ``{job_title}`` on
the same day feel like two different humans — without touching LangGraph
nodes, without a second LLM call per turn, and without breaking the
competency-rotation contract in ``FOLLOW_UP_USER_PROMPT_TEMPLATE``.

Design source: ``docs/interview-persona-design-ai.md`` (this agent) and
``docs/interview-style-variety-pm.md`` (product-manager).

Key invariants enforced by this module:

  1. Personas are STYLE OVERLAYS, not rule overrides. The base prompt still
     owns: PII guardrails, language pinning, "one question at a time",
     scoring secrecy, and the max-turns wind-down.
  2. Personas only bias the TIE-BREAK inside the four-competency rotation
     rule — they never override the rotation itself. The user prompt that
     carries the rotation contract is untouched.
  3. HI/TE deltas are authored DIRECTLY in modern code-mixed Hinglish /
     Tenglish per the founder-chosen register (see memory
     ``feedback_modern_codemixed_hi_te``). They are NOT translated by the
     LLM. Pure shudh Devanagari / Telugu script reads robotic in the
     screening register.
  4. Persona selection is DETERMINISTIC on ``session_id``: the same
     ``session_id`` always reproduces the same persona, which is critical
     for debugging candidate complaints and for offline eval reproduction.
  5. The delta strings are short on purpose (≤ ~6 lines each). The whole
     persona block sits in its OWN Anthropic prompt-cache slot (see
     ``app.llm.anthropic`` — to land in a later sprint), so the big base
     prompt cache hit (~600 tokens) is preserved across persona variants.
"""

from __future__ import annotations

import hashlib
from typing import Literal, get_args

from app.graph.prompts import Language

# ---------------------------------------------------------------------------
# Persona identifier vocabulary.
#
# Order matters for deterministic selection — adding/removing a persona
# changes the hash-modulo distribution. If you change the tuple in
# ``PERSONA_ORDER`` below, refresh any persona-pinned eval fixtures.
# ---------------------------------------------------------------------------
Persona = Literal[
    "warm_screener",
    "direct_technical",
    "scenario_led",
    "balanced_fit_first",
]

# Canonical persona iteration order — used for deterministic selection.
# ``get_args(Persona)`` already returns the Literal members in declaration
# order, but pinning the tuple explicitly here makes the hash-bucket
# contract independent of typing internals.
PERSONA_ORDER: tuple[Persona, ...] = (
    "warm_screener",
    "direct_technical",
    "scenario_led",
    "balanced_fit_first",
)

# Defensive check at import time — if the Literal members and the tuple
# ever drift apart (e.g. someone added a fifth persona to the Literal but
# forgot the tuple), the selection bucket-count would silently mismatch.
assert tuple(get_args(Persona)) == PERSONA_ORDER, (
    "Persona Literal members and PERSONA_ORDER tuple drifted apart — "
    "update both together to keep the hash-bucket distribution stable."
)


# ---------------------------------------------------------------------------
# Persona delta clauses — EN
#
# Each delta is appended VERBATIM after the base system prompt with a
# ``[PERSONA: <id>]`` header (see ``render_interviewer_system_prompt``).
# The placement is intentionally AFTER the rules block, so recency bias in
# instruction-following weights the persona overlay highest — without
# letting the persona override the upstream PII / scoring / language rules.
# Pilots that placed the persona block BEFORE the rules caused PII-rule
# drift; do not reorder.
#
# Hard rule preserved across all four personas: the four-competency
# rotation in ``FOLLOW_UP_USER_PROMPT_TEMPLATE`` keeps full authority. Each
# delta only nudges the TIE-BREAK inside it.
# ---------------------------------------------------------------------------
PERSONA_DELTAS_EN: dict[Persona, str] = {
    "warm_screener": (
        "Your interviewing style for this session:\n"
        "- Open turn 1 with a single welcoming sentence before the question "
        "(still one question, still 1-2 sentences in total).\n"
        "- On every follow-up from turn 2 onwards, lead with a short "
        "acknowledgement (max 8 words) of what the candidate just said "
        "before asking your next question.\n"
        "- Be warm and conversational; low-stakes tone, not chatty.\n"
        "- When the four-competency rotation rule leaves a free choice, "
        "prefer Behavioural & communication or Role fit.\n"
        "The four-competency rotation rule above STILL applies — the "
        "persona only influences tie-breaks within it."
    ),
    "direct_technical": (
        "Your interviewing style for this session:\n"
        "- Be brisk and on-task. Skip small-talk preamble.\n"
        "- Do NOT acknowledge or paraphrase the candidate before your next "
        "question — go directly to the question.\n"
        "- When the candidate uses a concrete technical term, your next "
        "question should test that term at one level of depth deeper.\n"
        "- When the four-competency rotation rule leaves a free choice, "
        "prefer Technical depth or Project / experience depth.\n"
        "The four-competency rotation rule above STILL applies — the "
        "persona only influences tie-breaks within it."
    ),
    "scenario_led": (
        "Your interviewing style for this session:\n"
        "- Prefer follow-ups that add a constraint or twist to what the "
        "candidate just described (\"what if the data was 10x larger?\", "
        "\"what if the customer pushed back?\") instead of pivoting to a "
        "new topic.\n"
        "- Acknowledge only when the candidate names a specific artefact "
        "(project, tool, customer, metric). Otherwise go straight to the "
        "next question.\n"
        "- Frame the first probe as a small hypothetical the candidate has "
        "to reason through.\n"
        "- When the four-competency rotation rule leaves a free choice, "
        "prefer Project / experience depth or Behavioural & communication.\n"
        "The four-competency rotation rule above STILL applies — the "
        "persona only influences tie-breaks within it."
    ),
    "balanced_fit_first": (
        "Your interviewing style for this session:\n"
        "- Frame this explicitly as a fit conversation for the "
        "{job_title} role.\n"
        "- At least one in every two follow-ups should explicitly connect "
        "what the candidate said back to the {job_title} role specifically.\n"
        "- Use a brief reassuring acknowledgement only when the candidate's "
        "previous answer hedged (\"I think\", \"maybe\", \"not sure\"). "
        "Otherwise skip acknowledgements.\n"
        "- When the four-competency rotation rule leaves a free choice, "
        "prefer Role fit or Behavioural & communication.\n"
        "The four-competency rotation rule above STILL applies — the "
        "persona only influences tie-breaks within it."
    ),
}


# ---------------------------------------------------------------------------
# Persona delta clauses — HI (Hinglish / code-mixed)
#
# Authored directly in modern Hinglish to match the founder-chosen register
# (memory: feedback_modern_codemixed_hi_te). Each EN delta has a
# register-equivalent — NOT a literal translation — so the *intent* of
# "skip acknowledgements" lands as the locally appropriate "anavashyak
# swikriti mat dijiye" rather than a stiff word-for-word translation that
# reads cold in Indian business Hindi.
# ---------------------------------------------------------------------------
PERSONA_DELTAS_HI: dict[Persona, str] = {
    "warm_screener": (
        "Is session ke liye aapka interviewing style:\n"
        "- Turn 1 par ek warm welcoming sentence ke saath open kijiye, "
        "phir question pucho (total 1-2 sentences mein).\n"
        "- Turn 2 se aage har follow-up par, candidate ne jo kaha usko "
        "ek choti acknowledgement (max 8 words) dijiye, phir next "
        "question pucho.\n"
        "- Warm aur conversational raho; low-stakes tone, but chatty mat "
        "ho jao.\n"
        "- Jab four-competency rotation rule mein free choice ho, toh "
        "Behavioural & communication ya Role fit ko prefer kijiye.\n"
        "Upar wala four-competency rotation rule abhi bhi apply hota hai "
        "— persona sirf tie-break ko influence karta hai."
    ),
    "direct_technical": (
        "Is session ke liye aapka interviewing style:\n"
        "- Brisk aur on-task raho. Small-talk preamble skip kijiye.\n"
        "- Anavashyak acknowledgement ya paraphrase mat dijiye — seedha "
        "next question pucho.\n"
        "- Jab candidate koi concrete technical term use kare, toh aapka "
        "next question us term ko ek level deeper test kare.\n"
        "- Jab four-competency rotation rule mein free choice ho, toh "
        "Technical depth ya Project / experience depth prefer kijiye.\n"
        "Upar wala four-competency rotation rule abhi bhi apply hota hai "
        "— persona sirf tie-break ko influence karta hai."
    ),
    "scenario_led": (
        "Is session ke liye aapka interviewing style:\n"
        "- Naye topic par jaane ke bajaaye, follow-up mein candidate ne jo "
        "describe kiya usme ek constraint ya twist add kijiye (\"agar data "
        "10x bada hota toh?\", \"agar customer disagree karta toh?\").\n"
        "- Acknowledgement sirf tab dijiye jab candidate koi specific "
        "artefact name kare (project, tool, customer, metric). Warna seedha "
        "next question pucho.\n"
        "- Pehla probe ek chote hypothetical scenario jaisa frame kijiye "
        "jisme candidate ko reason karna pade.\n"
        "- Jab four-competency rotation rule mein free choice ho, toh "
        "Project / experience depth ya Behavioural & communication prefer "
        "kijiye.\n"
        "Upar wala four-competency rotation rule abhi bhi apply hota hai "
        "— persona sirf tie-break ko influence karta hai."
    ),
    "balanced_fit_first": (
        "Is session ke liye aapka interviewing style:\n"
        "- Is interview ko explicitly {job_title} role ke liye ek fit "
        "conversation ke jaisa frame kijiye.\n"
        "- Har do follow-ups mein se kam se kam ek follow-up candidate ki "
        "baat ko {job_title} role se explicitly connect kare.\n"
        "- Reassuring acknowledgement sirf tab dijiye jab candidate ka "
        "previous answer hedge kare (\"shayad\", \"mujhe lagta hai\", "
        "\"sure nahi hoon\"). Warna acknowledgement skip kijiye.\n"
        "- Jab four-competency rotation rule mein free choice ho, toh "
        "Role fit ya Behavioural & communication prefer kijiye.\n"
        "Upar wala four-competency rotation rule abhi bhi apply hota hai "
        "— persona sirf tie-break ko influence karta hai."
    ),
}


# ---------------------------------------------------------------------------
# Persona delta clauses — TE (Tenglish / code-mixed)
#
# Authored directly in modern Tenglish — same register strategy as the HI
# block. "Adagaku" / "cheyaku" / "ivvaku" (don't ask / don't do / don't
# give) carry the same imperative-prohibition force as Hindi "mat" without
# sliding into formal/shudh Telugu that reads robotic.
# ---------------------------------------------------------------------------
PERSONA_DELTAS_TE: dict[Persona, str] = {
    "warm_screener": (
        "Ee session kosam mee interviewing style:\n"
        "- Turn 1 lo oka warm welcoming sentence tho open cheyandi, ataruvata "
        "question adagandi (total 1-2 sentences lo).\n"
        "- Turn 2 nundi prati follow-up lo, candidate cheppindi ki oka chinna "
        "acknowledgement (max 8 words) ichi, ataruvata next question "
        "adagandi.\n"
        "- Warm ga and conversational ga undandi; low-stakes tone, kani "
        "chatty ga maraku.\n"
        "- Four-competency rotation rule lo free choice unappudu, "
        "Behavioural & communication leda Role fit prefer cheyandi.\n"
        "Paina unna four-competency rotation rule inka apply avutundi — "
        "persona kevalam tie-break ni influence chestundi."
    ),
    "direct_technical": (
        "Ee session kosam mee interviewing style:\n"
        "- Brisk ga and on-task ga undandi. Small-talk preamble skip "
        "cheyandi.\n"
        "- Adanapu acknowledgement leda paraphrase ivvaku — direct ga next "
        "question adagandi.\n"
        "- Candidate edaina concrete technical term use chesinappudu, mee "
        "next question aa term ni okka level deeper test cheyaali.\n"
        "- Four-competency rotation rule lo free choice unappudu, Technical "
        "depth leda Project / experience depth prefer cheyandi.\n"
        "Paina unna four-competency rotation rule inka apply avutundi — "
        "persona kevalam tie-break ni influence chestundi."
    ),
    "scenario_led": (
        "Ee session kosam mee interviewing style:\n"
        "- Kotta topic ki marakunda, follow-up lo candidate describe chesina "
        "daani ki oka constraint leda twist add cheyandi (\"data 10x peddaga "
        "unte?\", \"customer disagree chesthe?\").\n"
        "- Acknowledgement candidate edaina specific artefact (project, tool, "
        "customer, metric) name chesinappudu matrame ivvandi. Lekapothe "
        "direct ga next question adagandi.\n"
        "- Modati probe ni oka chinna hypothetical scenario laga frame "
        "cheyandi, candidate reason cheyyalsina vidham ga.\n"
        "- Four-competency rotation rule lo free choice unappudu, Project / "
        "experience depth leda Behavioural & communication prefer cheyandi.\n"
        "Paina unna four-competency rotation rule inka apply avutundi — "
        "persona kevalam tie-break ni influence chestundi."
    ),
    "balanced_fit_first": (
        "Ee session kosam mee interviewing style:\n"
        "- Ee interview ni explicitly {job_title} role kosam oka fit "
        "conversation laga frame cheyandi.\n"
        "- Prati rendu follow-ups lo kanisam okati candidate cheppina daanini "
        "explicitly {job_title} role ki connect cheyaali.\n"
        "- Reassuring acknowledgement candidate previous answer hedge "
        "chesinappudu matrame ivvandi (\"bhavistunna\", \"emo\", \"sure ga "
        "ledhu\"). Lekapothe acknowledgement skip cheyandi.\n"
        "- Four-competency rotation rule lo free choice unappudu, Role fit "
        "leda Behavioural & communication prefer cheyandi.\n"
        "Paina unna four-competency rotation rule inka apply avutundi — "
        "persona kevalam tie-break ni influence chestundi."
    ),
}


# Lookup table — keeps the per-language dispatch centralised. Add a new
# language by adding its constant above and registering it here.
PERSONA_DELTAS_BY_LANGUAGE: dict[Language, dict[Persona, str]] = {
    "en": PERSONA_DELTAS_EN,
    "hi": PERSONA_DELTAS_HI,
    "te": PERSONA_DELTAS_TE,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_persona(session_id: str) -> Persona:
    """Pick the persona for this ``session_id``.

    Deterministic: the same ``session_id`` always maps to the same persona,
    which is critical for debugging ("the AI was rude to me") and for
    eval-harness reproducibility (the harness pins ``session_id``s to force
    one session per persona without monkey-patching).

    Implementation: 32-bit ``blake2s`` of the session_id bytes, modulo the
    number of personas. ``blake2s`` is uniform to many decimal places on
    UUIDv4-style inputs, so each persona sees ~25% of sessions at scale.

    Args:
        session_id: The session UUID (str). Empty string still hashes to a
            valid persona bucket (blake2s is defined on empty input) — we
            don't raise, so a handshake bug never 500s mid-WS. Callers
            should still pass a real UUID.

    Returns:
        One of the four ``Persona`` literals.
    """
    digest = hashlib.blake2s(session_id.encode("utf-8"), digest_size=4).digest()
    bucket = int.from_bytes(digest, byteorder="big") % len(PERSONA_ORDER)
    return PERSONA_ORDER[bucket]


def get_persona_delta(persona: Persona, language: Language) -> str:
    """Return the persona delta string for the requested ``(persona, language)``.

    Graceful fallback: unknown language codes fall back to English (same
    policy as ``app.graph.prompts.get_interviewer_prompt``) so a session
    created against a newer schema does not 500 here.

    Unknown persona literals (which the type system already forbids) raise
    ``KeyError`` — that path indicates a programmer bug, not a runtime
    config issue, and silent fallback would hide it.
    """
    deltas = PERSONA_DELTAS_BY_LANGUAGE.get(language, PERSONA_DELTAS_EN)
    return deltas[persona]


__all__ = [
    "PERSONA_DELTAS_BY_LANGUAGE",
    "PERSONA_DELTAS_EN",
    "PERSONA_DELTAS_HI",
    "PERSONA_DELTAS_TE",
    "PERSONA_ORDER",
    "Persona",
    "get_persona_delta",
    "select_persona",
]
