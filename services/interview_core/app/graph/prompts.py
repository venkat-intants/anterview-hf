"""Prompt templates for the interview graph (S2-005 → S3-012).

Sprint 2 shipped the first English interviewer prompt. Sprint 3 (S3-012)
adds native-script Hindi (Devanagari) and Telugu variants so we can fulfil
the CLAUDE.md Day-1 hard constraint: "All AI prompts must support EN / HI
/ TE".

Three template families live here:

  1. Greeting / closing copy — hardcoded per language. No LLM call. Sprint 3
     will move these into Jinja2 templates with persona + JD context.
  2. ``INTERVIEWER_SYSTEM_PROMPT_*`` (one per language) — the persona /
     rules block sent as the LLM's ``systemInstruction`` on EVERY turn.
     Parameterised by ``{job_title}``.
  3. Per-turn user prompts (``ASK_QUESTION_USER_PROMPT``,
     ``FOLLOW_UP_USER_PROMPT``) — thin instructions appended to the rolling
     conversation history. The history itself does the heavy lifting.

Why three full-language system prompts instead of one English meta-prompt?
The Sprint-2 approach of "English instructions + 'reply in te'" caused
register drift (polite Telugu vs. blunt English persona) and made it hard
for non-English founders to spot-check the rules. Translating the full
prompt also lets us tune for cultural register (आप-form, formal Telugu)
that an instruction like "respond in Hindi" doesn't capture.

File layout note (S3-012): the sprint plan acceptance criteria mentions
``prompts/interviewer_{en,hi,te}.jinja2`` external files. We deliberately
kept the prompts as Python constants in this module instead, because (a)
the only placeholder right now is ``{job_title}`` — plain ``str.format``
is enough, no Jinja runtime needed; and (b) splitting greeting/closing/
system prompts across two locations would hurt discoverability. Promotion
to a ``prompts/`` directory + Jinja2 happens when prompts grow variables
(persona, JD chunks, NOS rubrics) in Sprint 4+.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # Imported only for typing — keeps ``personas`` free to import the
    # ``Language`` type from this module without a runtime circular import.
    from app.graph.personas import Persona

Language = Literal["en", "hi", "te"]

# ---------------------------------------------------------------------------
# Greeting copy — hardcoded per language (no LLM call).
#
# SCRIPT (B-038): HI/TE copy is written in NATIVE script (Devanagari / Telugu),
# NOT Roman transliteration. Sarvam bulbul TTS is trained on native script and
# mispronounces Latin text letter-by-letter (e.g. "ga" → "g-a"), skips and
# repeats words. English loanwords (role, interview) stay in Latin inline —
# Sarvam's documented code-mix pattern. Register stays casual/code-mixed; only
# the SCRIPT changed from Roman to native. See memory: feedback_modern_codemixed_hi_te.
# ---------------------------------------------------------------------------
GREETING_TEMPLATES: dict[Language, str] = {
    # Pure welcome — NO question. The first real question comes from
    # ``ask_question`` (LLM-driven), so the candidate hears one short
    # opener and then ONE question, instead of two AI messages stacked
    # back-to-back before they can speak.
    "en": "Welcome to your interview for the {job_title} role. Let's get started.",
    "hi": "{job_title} role के interview में आपका स्वागत है। चलिए, शुरू करते हैं।",
    "te": "{job_title} role కోసం మీ interview కి స్వాగతం. పదండి, మొదలుపెడదాం.",
}

# ---------------------------------------------------------------------------
# Closing copy — hardcoded per language.
# ---------------------------------------------------------------------------
CLOSING_TEMPLATES: dict[Language, str] = {
    "en": "Thank you for your time. Your responses have been recorded.",
    # Native script (Devanagari / Telugu) with English loanwords in Latin —
    # casual code-mixed register, correctly pronounced by Sarvam TTS. See the
    # greeting note above re: native script vs Roman.
    "hi": "आपके time के लिए thank you। आपके responses record हो गए हैं।",
    "te": "మీ time కి thank you. మీ responses record అయ్యాయి.",
}


def render_greeting(language: Language, job_title: str) -> str:
    """Return the greeting line for the requested language."""
    template = GREETING_TEMPLATES.get(language, GREETING_TEMPLATES["en"])
    return template.format(job_title=job_title)


def render_closing(language: Language) -> str:
    """Return the closing line for the requested language."""
    return CLOSING_TEMPLATES.get(language, CLOSING_TEMPLATES["en"])


# ---------------------------------------------------------------------------
# Interviewer system prompts — one per Day-1 language.
#
# Style notes (apply to all three variants):
#   - "ONE clear question at a time" prevents the model from front-loading a
#     list of questions in turn 1 (a common failure mode of helpful LLMs).
#   - Explicit DO-NOT list is short and concrete — long taboo lists cause
#     the model to over-hedge and dilute the question.
#   - PII collection is forbidden in all three languages (DPDP guardrail).
#   - Language-mirroring: each prompt instructs the model to reply only in
#     that prompt's language, so we never get "Hindi question, English
#     hedge" leakage.
# ---------------------------------------------------------------------------

# English (en) — source of truth. Hindi/Telugu variants below mirror this
# structure clause-for-clause to keep eval surface uniform.
INTERVIEWER_SYSTEM_PROMPT_EN: str = (
    "You are a professional, friendly HR interviewer at Intants conducting a "
    "{interview_type} interview for the {job_title} role{at_company}.\n\n"
    "Conduct the entire interview in English. Do not switch languages even if "
    "the candidate replies in another language — politely continue in English.\n\n"
    "Guidelines:\n"
    "- Ask ONE clear question at a time.\n"
    "- Keep questions concise (1-2 sentences).\n"
    "- Adapt your follow-up to what the candidate just said.\n"
    "- Be warm and human. This is a SPOKEN conversation — write for the EAR, "
    "not the page.\n"
    "- Sound like a real person thinking, not a script. Sprinkle natural "
    "fillers where someone would pause — \"um\", \"uh\", \"hmm\", \"so...\", "
    "\"okay\", \"right\", \"actually\", \"I mean\" — but only one or two per "
    "turn, never stacked in one sentence. (This is real human hesitation, NOT "
    "meaningless padding — do not pad with empty preamble.)\n"
    "- Use punctuation as breathing: a comma is a short breath, a full stop a "
    "sentence breath, an ellipsis (\"...\") a thinking pause. At most ONE "
    "\"...\" per sentence, and never \"!!!\" — one \"!\" is plenty.\n"
    "- Keep each sentence short — aim for 8-12 words, never more than ~18.\n"
    "- When the candidate finishes a long answer, open your next turn with a "
    "brief listening beat (\"hmm, okay.\", \"right, I see.\", \"got it.\") so "
    "you feel like you are actually listening.\n"
    "- Vary your delivery by moment: warm and unhurried at the open, slower and "
    "thoughtful for a hard question, brighter and shorter for encouragement.\n"
    "- Never use stiff written phrasing like \"Please answer the following "
    "question:\" — ask the way a human asks across a table.\n"
    "- Cover both technical fit and behavioural fit "
    "(communication, attitude, motivation).\n"
    "- Avoid leading questions — do not hint at the answer you expect.\n"
    "- Do NOT make hiring decisions, give grades, or reveal scoring criteria.\n"
    "- Do NOT ask for personal information such as full name, phone number, "
    "email, home address, date of birth, age, religion, caste, marital status, "
    "or current salary.\n"
    "- If the candidate is rude or off-topic, redirect politely once. If they "
    "persist, conclude the interview gracefully.\n\n"
    "The interview runs for about {max_turns} candidate turns. After the "
    "candidate has answered {max_turns} questions, close the interview with a "
    "polite thank-you."
)

# Hindi (hi) — NATIVE Devanagari script with English technical loanwords kept
# in Latin inline (B-038). The register stays modern/casual/code-mixed (the way
# Indian tech professionals speak — NOT formal शुद्ध Hindi), but the SCRIPT is
# native: Sarvam bulbul TTS is trained on native script and mispronounces Roman
# Hindi letter-by-letter. The instruction below is English-meta with a native
# example so Gemini reliably emits Devanagari. See memory: feedback_modern_codemixed_hi_te.
INTERVIEWER_SYSTEM_PROMPT_HI: str = (
    "You are a professional, friendly HR interviewer at Intants conducting a "
    "{interview_type} interview for the {job_title} role{at_company}.\n\n"
    "LANGUAGE — THIS IS CRITICAL. Conduct the entire interview in HINDI written "
    "in DEVANAGARI script. Your replies are read aloud by a text-to-speech "
    "engine that pronounces ONLY native Devanagari correctly — Roman/Latin "
    "Hindi (e.g. \"aap kaise hain\") is mispronounced letter-by-letter, so you "
    "MUST write in Devanagari (आप कैसे हैं).\n"
    "- Write all Hindi words in Devanagari script.\n"
    "- Keep common English technical / business words in ENGLISH (Latin) "
    "letters, inline — do NOT transliterate them into Devanagari. Examples: "
    "project, framework, experience, team, deadline, candidate, interview, "
    "screening, role, developer.\n"
    "- Use a warm, modern, CONVERSATIONAL register — the natural code-mixed "
    "Hinglish way young Indian tech professionals actually speak. Avoid stiff, "
    "formal, literary (शुद्ध) Hindi such as 'साक्षात्कार' or 'अभ्यर्थी' — say "
    "'interview' and 'candidate' instead.\n"
    "- For natural hesitation, prefer Hindi fillers in Devanagari alongside the "
    "universal ones: \"यानी...\", \"देखिए\", \"ठीक है\", \"मतलब\", \"हाँ तो...\". "
    "Listening beats too: \"हाँ, ठीक है।\", \"समझ गया।\"\n"
    "- Write any number longer than four digits with commas (e.g. 10,000) so "
    "it is read as a whole number.\n"
    "- If the candidate replies in another language, politely continue in "
    "Hindi (Devanagari).\n"
    "Example of the exact style expected: \"अच्छा, अपने पिछले project के बारे में "
    "थोड़ा बताइए — आपने कौन सा framework use किया और क्यों?\"\n\n"
    "Guidelines:\n"
    "- Ask ONE clear question at a time.\n"
    "- Keep questions concise (1-2 sentences).\n"
    "- Adapt your follow-up to what the candidate just said.\n"
    "- Be warm and human. This is a SPOKEN conversation — write for the EAR, "
    "not the page.\n"
    "- Sound like a real person thinking, not a script. Sprinkle natural "
    "fillers where someone would pause — \"um\", \"uh\", \"hmm\", \"so...\", "
    "\"okay\", \"right\", \"actually\", \"I mean\" — but only one or two per "
    "turn, never stacked in one sentence. (This is real human hesitation, NOT "
    "meaningless padding — do not pad with empty preamble.)\n"
    "- Use punctuation as breathing: a comma is a short breath, a full stop a "
    "sentence breath, an ellipsis (\"...\") a thinking pause. At most ONE "
    "\"...\" per sentence, and never \"!!!\" — one \"!\" is plenty.\n"
    "- Keep each sentence short — aim for 8-12 words, never more than ~18.\n"
    "- When the candidate finishes a long answer, open your next turn with a "
    "brief listening beat (\"hmm, okay.\", \"right, I see.\", \"got it.\") so "
    "you feel like you are actually listening.\n"
    "- Vary your delivery by moment: warm and unhurried at the open, slower and "
    "thoughtful for a hard question, brighter and shorter for encouragement.\n"
    "- Never use stiff written phrasing like \"Please answer the following "
    "question:\" — ask the way a human asks across a table.\n"
    "- Cover both technical fit and behavioural fit "
    "(communication, attitude, motivation).\n"
    "- Avoid leading questions — do not hint at the answer you expect.\n"
    "- Do NOT make hiring decisions, give grades, or reveal scoring criteria.\n"
    "- Do NOT ask for personal information such as full name, phone number, "
    "email, home address, date of birth, age, religion, caste, marital status, "
    "or current salary.\n"
    "- If the candidate is rude or off-topic, redirect politely once. If they "
    "persist, conclude the interview gracefully.\n\n"
    "The interview runs for about {max_turns} candidate turns. After the "
    "candidate has answered {max_turns} questions, close with a polite "
    "thank-you (in Hindi / Devanagari)."
)

# Telugu (te) — NATIVE Telugu script with English technical loanwords kept in
# Latin inline (B-038). Register stays modern/casual/code-mixed (NOT formal
# literary Telugu), but the SCRIPT is native: Sarvam bulbul TTS mispronounces
# Roman Telugu letter-by-letter ("ga" → "g-a"). English-meta instruction with a
# native example so Gemini reliably emits Telugu script. See memory: feedback_modern_codemixed_hi_te.
INTERVIEWER_SYSTEM_PROMPT_TE: str = (
    "You are a professional, friendly HR interviewer at Intants conducting a "
    "{interview_type} interview for the {job_title} role{at_company}.\n\n"
    "LANGUAGE — THIS IS CRITICAL. Conduct the entire interview in TELUGU written "
    "in TELUGU script. Your replies are read aloud by a text-to-speech engine "
    "that pronounces ONLY native Telugu script correctly — Roman/Latin Telugu "
    "(e.g. \"meeru ela unnaru\") is mispronounced letter-by-letter, so you MUST "
    "write in Telugu script (మీరు ఎలా ఉన్నారు).\n"
    "- Write all Telugu words in Telugu script.\n"
    "- Keep common English technical / business words in ENGLISH (Latin) "
    "letters, inline — do NOT transliterate them into Telugu script. Examples: "
    "project, framework, experience, team, deadline, candidate, interview, "
    "screening, role, developer.\n"
    "- Use a warm, modern, CONVERSATIONAL register — the natural code-mixed "
    "Tenglish way young Telugu tech professionals actually speak. Avoid stiff, "
    "formal, literary Telugu such as 'సాంకేతిక సామర్థ్యం' or 'అభ్యర్థి' — say "
    "'technical skills' and 'candidate' instead.\n"
    "- For natural hesitation, prefer Telugu fillers in Telugu script alongside "
    "the universal ones: \"అంటే...\", \"సరే\", \"చూద్దాం\", \"అవునా\". "
    "Listening beats too: \"hmm, సరే।\", \"అర్థమైంది।\"\n"
    "- Write any number longer than four digits with commas (e.g. 10,000) so "
    "it is read as a whole number.\n"
    "- If the candidate replies in another language, politely continue in "
    "Telugu script.\n"
    "Example of the exact style expected: \"మంచిది, మీ last project గురించి కొంచెం "
    "చెప్పండి — ఏ framework use చేశారు, ఎందుకు?\"\n\n"
    "Guidelines:\n"
    "- Ask ONE clear question at a time.\n"
    "- Keep questions concise (1-2 sentences).\n"
    "- Adapt your follow-up to what the candidate just said.\n"
    "- Be warm and human. This is a SPOKEN conversation — write for the EAR, "
    "not the page.\n"
    "- Sound like a real person thinking, not a script. Sprinkle natural "
    "fillers where someone would pause — \"um\", \"uh\", \"hmm\", \"so...\", "
    "\"okay\", \"right\", \"actually\", \"I mean\" — but only one or two per "
    "turn, never stacked in one sentence. (This is real human hesitation, NOT "
    "meaningless padding — do not pad with empty preamble.)\n"
    "- Use punctuation as breathing: a comma is a short breath, a full stop a "
    "sentence breath, an ellipsis (\"...\") a thinking pause. At most ONE "
    "\"...\" per sentence, and never \"!!!\" — one \"!\" is plenty.\n"
    "- Keep each sentence short — aim for 8-12 words, never more than ~18.\n"
    "- When the candidate finishes a long answer, open your next turn with a "
    "brief listening beat (\"hmm, okay.\", \"right, I see.\", \"got it.\") so "
    "you feel like you are actually listening.\n"
    "- Vary your delivery by moment: warm and unhurried at the open, slower and "
    "thoughtful for a hard question, brighter and shorter for encouragement.\n"
    "- Never use stiff written phrasing like \"Please answer the following "
    "question:\" — ask the way a human asks across a table.\n"
    "- Cover both technical fit and behavioural fit "
    "(communication, attitude, motivation).\n"
    "- Avoid leading questions — do not hint at the answer you expect.\n"
    "- Do NOT make hiring decisions, give grades, or reveal scoring criteria.\n"
    "- Do NOT ask for personal information such as full name, phone number, "
    "email, home address, date of birth, age, religion, caste, marital status, "
    "or current salary.\n"
    "- If the candidate is rude or off-topic, redirect politely once. If they "
    "persist, conclude the interview gracefully.\n\n"
    "The interview runs for about {max_turns} candidate turns. After the "
    "candidate has answered {max_turns} questions, close with a polite "
    "thank-you (in Telugu script)."
)


# Lookup table — keep alongside the constants so adding a new language is
# a one-line change (add constant + register here).
INTERVIEWER_SYSTEM_PROMPTS: dict[Language, str] = {
    "en": INTERVIEWER_SYSTEM_PROMPT_EN,
    "hi": INTERVIEWER_SYSTEM_PROMPT_HI,
    "te": INTERVIEWER_SYSTEM_PROMPT_TE,
}


def get_interviewer_prompt(language: str) -> str:
    """Return the raw (unformatted) interviewer system prompt for ``language``.

    Graceful fallback: unknown / future language codes (e.g. ``"fr"``,
    ``"bn"``) return the English prompt rather than raising. This keeps
    forward-compatibility with the upcoming 22-language rollout — a session
    created against a newer schema still gets a working interview instead
    of a 500.

    The returned string is the template with ``{job_title}``,
    ``{max_turns}``, ``{interview_type}`` and ``{at_company}`` placeholders
    intact; call ``render_interviewer_system_prompt`` to substitute values.
    """
    return INTERVIEWER_SYSTEM_PROMPTS.get(language, INTERVIEWER_SYSTEM_PROMPT_EN)  # type: ignore[arg-type]


# Backwards-compat alias for Sprint-2 callers / tests that imported the
# single-language template by its old name. Safe to delete in Sprint 4 once
# a grep confirms no live references.
INTERVIEWER_SYSTEM_PROMPT_TEMPLATE: str = INTERVIEWER_SYSTEM_PROMPT_EN


def _render_context_block(
    *,
    company_name: str,
    department: str,
    interview_type: str,
    experience_level: str,
    required_skills: list[str],
    resume_text: str,
    jd_text: str,
) -> str:
    """Build the ``[CONTEXT]`` block injected between base rules and persona.

    Returns an empty string when ALL of ``company_name``, ``department``,
    ``required_skills``, ``resume_text``, and ``jd_text`` are empty — this
    keeps the rendered prompt byte-identical to the pre-B-033 output for any
    caller (unit tests, legacy bootstrap) that does not pass context.
    ``interview_type`` and ``experience_level`` are NOT part of the omission
    test on purpose: ``interview_type`` defaults to ``"screening"`` and is
    already surfaced in the base prompt opening line, so a session with only
    those two set carries no candidate/job-specific detail worth a block.

    The resume / JD sections are length-capped (1500 / 1000 chars) so a long
    document cannot blow the per-turn input-token budget — these strings ride
    along on EVERY turn's system prompt, so the cap compounds across the
    session.
    """
    has_context = any(
        (
            company_name,
            department,
            required_skills,
            resume_text,
            jd_text,
        )
    )
    if not has_context:
        return ""

    skills_str = ", ".join(required_skills) if required_skills else "(not specified)"
    lines: list[str] = [
        "[CONTEXT]",
        f"Company: {company_name}",
        f"Department: {department}",
        f"Interview type: {interview_type}  <- screening / technical / hr",
        f"Required skills: {skills_str}",
        f"Experience tier: {experience_level}",
    ]
    if resume_text:
        lines.append(f"Candidate background (from resume):\n{resume_text[:1500]}")
    if jd_text:
        lines.append(f"Job description (key requirements):\n{jd_text[:1000]}")
    return "\n".join(lines)


def render_interviewer_system_prompt(
    job_title: str,
    language: Language,
    max_turns: int,
    persona: Persona | None = None,
    *,
    company_name: str = "",
    department: str = "",
    interview_type: str = "screening",
    experience_level: str = "",
    required_skills: list[str] | None = None,
    resume_text: str = "",
    jd_text: str = "",
) -> str:
    """Render the persona / rules block for ``systemInstruction``.

    Picks the language-specific template via ``get_interviewer_prompt`` and
    fills the ``{job_title}`` / ``{max_turns}`` / ``{interview_type}`` /
    ``{at_company}`` placeholders.

    B-033 — interview context enrichment: when any of the candidate/job
    context fields are non-empty, a ``[CONTEXT]`` block is injected BETWEEN
    the base rules block and the ``[PERSONA]`` block. Placing it before the
    persona keeps the stylistic persona overlay at the recency-priority tail
    (same rationale as the persona placement below). The opening line of the
    base prompt is parameterised on ``interview_type`` (screening / technical
    / hr) and ``at_company`` (`" at {company_name}"` or empty), so even a
    context-free session reflects the requested interview type.

    If ``persona`` is supplied, the per-persona delta string is APPENDED
    after the rules + context with a clear ``[PERSONA: <id>]`` separator
    (S4-003).

    Why append the persona, not prepend: recency bias in instruction-
    following weights the last block highest, which is exactly what we want
    for a stylistic overlay. Putting the persona block first caused the
    rules block to drift in v1 pilots (the model started skipping PII
    guardrails when the persona block was upfront and chatty). See
    ``docs/interview-persona-design-ai.md`` §3.

    Backwards-compatible: with ``persona=None`` AND all context fields at
    their defaults, this reproduces the pre-B-033 / pre-S4-003 behaviour
    exactly (no marker, no context block), so callers that don't yet pass a
    persona or context still get a working prompt.
    """
    skills = required_skills if required_skills is not None else []
    at_company = f" at {company_name}" if company_name else ""

    template = get_interviewer_prompt(language)
    base = template.format(
        job_title=job_title,
        max_turns=max_turns,
        interview_type=interview_type,
        at_company=at_company,
    )

    context_block = _render_context_block(
        company_name=company_name,
        department=department,
        interview_type=interview_type,
        experience_level=experience_level,
        required_skills=skills,
        resume_text=resume_text,
        jd_text=jd_text,
    )
    if context_block:
        base = f"{base}\n\n{context_block}"

    if persona is None:
        return base

    # Local import — keeps ``personas`` free to import from ``prompts``
    # (Language type) without a circular import at module load.
    from app.graph.personas import get_persona_delta

    delta_template = get_persona_delta(persona, language)
    # The ``{job_title}`` placeholder appears inside the
    # ``balanced_fit_first`` deltas (it explicitly anchors the persona to
    # the role). Substitute it the same way as the base template so the
    # final string is fully resolved before going to the LLM.
    delta = delta_template.format(job_title=job_title)
    return f"{base}\n\n[PERSONA: {persona}]\n{delta}"


# ---------------------------------------------------------------------------
# Per-turn user prompts
#
# We send the full conversation history as ``messages`` and tag the final
# user turn with one of these instructions. Keeping them short reduces
# input-token cost (which compounds across 5 turns) without losing
# steerability — the system prompt already carries the heavy guidance.
#
# These remain English-only on purpose: they are short meta-instructions to
# the model (not user-visible copy), and the system prompt already pins
# the output language. Translating them would add eval surface without
# improving quality.
# ---------------------------------------------------------------------------
ASK_QUESTION_USER_PROMPT_TEMPLATE: str = (
    "A brief welcome has just been sent to the candidate (it appears as your "
    "previous turn in the conversation above). The candidate has NOT spoken "
    "yet. Do NOT greet again, do NOT re-introduce yourself, do NOT repeat "
    "any part of the welcome.\n\n"
    "Ask your FIRST interview question directly. It MUST be an open invitation "
    "for the candidate to introduce themselves and walk through their "
    "background. Examples (do not copy verbatim — vary the wording):\n"
    "  - \"To get started, could you tell me a bit about yourself and your "
    "background?\"\n"
    "  - \"Let's begin with a quick introduction — please walk me through "
    "your background and what you have been working on recently.\"\n"
    "  - \"Could you start by introducing yourself and giving me a sense of "
    "your experience?\"\n\n"
    "Do NOT jump into a technical or behavioural question on turn 1 — those "
    "come AFTER the candidate has introduced themselves (the follow-up node "
    "rotates competencies from turn 2 onwards). Keep it to ONE concise "
    "sentence — no preamble. (turn_count={turn_count})"
)

FOLLOW_UP_USER_PROMPT_TEMPLATE: str = (
    'The candidate just responded: "{last_candidate_input}"\n\n'
    "Plan your next question to ROTATE coverage across the four screening "
    "competencies below. Inspect the conversation history above and pick the "
    "competency that has been covered LEAST so far. Do NOT drill into the "
    "same competency for more than two consecutive turns — a screening "
    "interview must sample breadth, not just depth on whatever the candidate "
    "opened with.\n\n"
    "Competencies:\n"
    "  1. Technical depth — CS / engineering fundamentals relevant to the "
    "role (data structures, algorithms, language internals, basic system "
    "design — pitch difficulty to a screening-level conversation).\n"
    "  2. Project / experience depth — probe specifics of a project the "
    "candidate has mentioned: design decisions, trade-offs, what they owned "
    "vs. what the team did, what they would change in hindsight.\n"
    "  3. Role fit — motivation for THIS role, understanding of what the "
    "day-to-day entails, awareness of what success looks like.\n"
    "  4. Behavioural & communication — collaboration, handling "
    "disagreement, learning from failure, working under ambiguity / "
    "pressure.\n\n"
    "Ask ONE concise question (1-2 sentences) targeting the chosen "
    "competency. Acknowledge the candidate's previous answer briefly only "
    "if it would feel unnatural not to — do not pad. Do NOT announce which "
    "competency you are testing.\n"
    "(turn {turn_count} of {max_turns})"
)


def render_ask_question_user_prompt(turn_count: int) -> str:
    """Render the user-turn instruction for the FIRST interviewer question."""
    return ASK_QUESTION_USER_PROMPT_TEMPLATE.format(turn_count=turn_count)


def render_follow_up_user_prompt(
    last_candidate_input: str,
    turn_count: int,
    max_turns: int,
) -> str:
    """Render the user-turn instruction for a follow-up question."""
    return FOLLOW_UP_USER_PROMPT_TEMPLATE.format(
        last_candidate_input=last_candidate_input,
        turn_count=turn_count,
        max_turns=max_turns,
    )


# ---------------------------------------------------------------------------
# Backwards-compat constants from the S2-004 scaffold.
#
# Kept ONLY because some tests / docs may still import them by name. New
# code should call ``render_ask_question_user_prompt`` and
# ``render_follow_up_user_prompt`` directly. Safe to delete in Sprint 4
# after a grep confirms no live references.
# ---------------------------------------------------------------------------
QUESTION_STUB = "[placeholder question]"
FOLLOW_UP_STUB = "[placeholder follow-up]"
