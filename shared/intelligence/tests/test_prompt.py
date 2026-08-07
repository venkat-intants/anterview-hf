"""Assertions on the derivation prompt STRING itself.

Why this file exists (code-review finding INT-1): ``build_derivation_prompt``
was executed on every LLM-path test in ``test_derive.py``, but nothing asserted
anything about what it produced — the only check was ``"CNC" in user``. So the
prompt that shapes every interview question plan and every scorecard rubric
could be edited freely and the suite stayed green. Worse, ``jd_text`` — the one
argument carrying attacker-controllable text, since a JD can be pasted by any
HR user and a resume-derived JD is worse still — was empty in every single test,
so its whole branch was dead code as far as CI was concerned.

These tests pin the two properties that make that branch safe rather than the
exact wording, which must stay free to tune:

* the JD is **fenced** — wrapped in delimiters and introduced by a label, so the
  model reads it as quoted material rather than as instructions addressed to it;
* the JD is **truncated at the cap** — an unbounded JD pushes the response past
  the output limit, and truncation on THIS call silently costs the whole role
  model (the caller then falls back to the generic baseline, and the only signal
  is ``profile.source``).

They drive the real entry point, ``derive_role_profile``, rather than calling
``build_derivation_prompt`` directly, so the wiring between the two is covered
too: a caller that stopped passing ``jd_text`` through would fail here.
"""

from __future__ import annotations

from shared.intelligence.derive import derive_role_profile
from shared.intelligence.prompt import (
    _JD_CHAR_CAP,
    _SKILLS_CAP,
    build_derivation_prompt,
)
from shared.intelligence.taxonomy import baseline_profile

PID = "p" * 32

# The fence the JD block is wrapped in. Named once so a deliberate change to the
# delimiter is a one-line edit here and an obvious diff, not a silent drift.
_FENCE = '"""'


class _PromptCapture:
    """An LLM caller that records the prompts and then fails.

    Failing is deliberate: ``derive_role_profile`` never raises, so raising here
    exercises the real fallback path while still handing the test the exact
    strings that would have gone to Gemini.
    """

    def __init__(self) -> None:
        self.system: str = ""
        self.user: str = ""

    async def __call__(self, system: str, user: str) -> str:
        self.system = system
        self.user = user
        raise RuntimeError("captured; no real call")


# ---------------------------------------------------------------------------
# The untrusted-input branch: jd_text
# ---------------------------------------------------------------------------


async def test_jd_text_reaches_the_prompt_fenced_and_labelled() -> None:
    """A JD must arrive as quoted material, not as free-floating instructions."""
    capture = _PromptCapture()
    jd = "Operate and set up CNC turning centres to drawing. Report to the shift lead."

    await derive_role_profile(
        job_title="CNC Turning Operator",
        jd_text=jd,
        llm=capture,
    )

    assert jd in capture.user, "the JD never reached the prompt at all"

    # Labelled: the model is told what the block is before it reads it.
    assert "Job description" in capture.user

    # Fenced on BOTH sides — an opening delimiter with no closing one would let
    # everything after the JD read as part of it.
    start = capture.user.index(jd)
    before, after = capture.user[:start], capture.user[start + len(jd) :]
    assert before.rstrip().endswith(_FENCE), "JD is not opened with a fence"
    assert after.lstrip().startswith(_FENCE), "JD is not closed with a fence"


async def test_jd_text_is_truncated_at_the_documented_cap() -> None:
    """Truncation on this call costs the whole role model, so the cap must hold.

    Uses a distinctive tail marker so the assertion proves the *end* was cut,
    not merely that some length limit exists somewhere.
    """
    capture = _PromptCapture()
    filler = "x" * (_JD_CHAR_CAP + 500)
    jd = filler + "TAIL_MARKER_MUST_NOT_APPEAR"

    await derive_role_profile(job_title="Welder", jd_text=jd, llm=capture)

    assert "TAIL_MARKER_MUST_NOT_APPEAR" not in capture.user
    # Exactly the cap survives — an off-by-a-lot here (e.g. a cap applied to the
    # wrong string) would still pass the marker check above.
    assert "x" * _JD_CHAR_CAP in capture.user
    assert "x" * (_JD_CHAR_CAP + 1) not in capture.user


async def test_whitespace_only_jd_takes_the_none_supplied_branch() -> None:
    """``jd_text.strip()`` guards the branch; blanks must not emit an empty fence.

    An empty fenced block reads to the model as "here is the JD: <nothing>",
    which is a worse instruction than saying none was supplied.
    """
    capture = _PromptCapture()

    await derive_role_profile(job_title="Staff Nurse", jd_text="   \n\t  ", llm=capture)

    assert "(none supplied)" in capture.user
    assert _FENCE not in capture.user


async def test_no_jd_supplied_still_produces_a_usable_prompt() -> None:
    capture = _PromptCapture()

    await derive_role_profile(job_title="Data Analyst", llm=capture)

    assert "Job description: (none supplied)" in capture.user


# ---------------------------------------------------------------------------
# The other caller-supplied inputs
# ---------------------------------------------------------------------------


async def test_required_skills_survive_into_the_prompt() -> None:
    """Skills are the strongest role signal an HR user gives us — losing them
    silently would produce a plausible-looking rubric for the wrong job."""
    capture = _PromptCapture()

    await derive_role_profile(
        job_title="CNC Turning Operator",
        required_skills=["Fanuc controls", "GD&T", "Micrometer"],
        llm=capture,
    )

    assert "Fanuc controls" in capture.user
    assert "GD&T" in capture.user
    assert "Micrometer" in capture.user


async def test_skills_are_capped_and_blanks_dropped() -> None:
    capture = _PromptCapture()
    skills = ["  ", ""] + [f"skill_{i}" for i in range(_SKILLS_CAP + 10)]

    await derive_role_profile(job_title="Welder", required_skills=skills, llm=capture)

    assert "skill_0" in capture.user
    assert f"skill_{_SKILLS_CAP - 1}" in capture.user
    # The (_SKILLS_CAP)th index is the first one past the cap once blanks are
    # stripped — its absence proves the cap is applied after filtering, not
    # before (filtering first is what keeps two blanks from stealing two slots).
    assert f"skill_{_SKILLS_CAP}" not in capture.user
    # Scoped to the skills line: "(none supplied)" also renders for an absent
    # job description, so an unqualified check here passes for the wrong reason.
    assert "Required skills: (none supplied)" not in capture.user


async def test_role_context_fields_all_reach_the_prompt() -> None:
    """company_name in particular: it is hashed into the profile_id because it
    is fed to the model (see compute_profile_id's tenancy note). If it stopped
    reaching the prompt, the cache key would be splitting on nothing."""
    capture = _PromptCapture()

    await derive_role_profile(
        job_title="Site Engineer - Civil",
        department="Projects",
        company_name="Sungrace Infra",
        experience_level="senior",
        interview_type="technical_deep_dive",
        llm=capture,
    )

    assert "Site Engineer - Civil" in capture.user
    assert "Projects" in capture.user
    assert "Sungrace Infra" in capture.user
    assert "senior" in capture.user
    assert "technical_deep_dive" in capture.user


# ---------------------------------------------------------------------------
# Instructions the prompt must not lose
# ---------------------------------------------------------------------------


def test_prompt_carries_the_baseline_to_refine() -> None:
    """Seeded-not-blank is the documented design decision (prompt.py's header).
    Asking cold produced four restatements of "communication"."""
    baseline = baseline_profile(profile_id=PID, job_title="CNC Turning Operator")
    prompt = build_derivation_prompt(job_title="CNC Turning Operator", baseline=baseline)

    for competency in baseline.competencies:
        assert competency.name in prompt


def test_prompt_keeps_the_protected_characteristics_ban() -> None:
    """This is a DPDP-facing instruction, not a style preference: the derived
    probes are read aloud to a candidate. Losing it in a prompt edit would be
    invisible until a rubric asked about caste or marital status."""
    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    prompt = build_derivation_prompt(job_title="Welder", baseline=baseline)

    assert "Never ask for or reference protected personal characteristics" in prompt
    for banned in ("religion", "caste", "marital status", "age", "salary"):
        assert banned in prompt


def test_prompt_keeps_red_flags_advisory_only() -> None:
    """CLAUDE.md hard constraint #9: AI must never decide hiring outcomes. The
    schema enforces it structurally; this instruction stops the model from
    writing rejection criteria into the rubric text itself."""
    baseline = baseline_profile(profile_id=PID, job_title="Staff Nurse")
    prompt = build_derivation_prompt(job_title="Staff Nurse", baseline=baseline)

    assert "never grounds for" in prompt
    assert "automatic rejection" in prompt


def test_prompt_demands_strict_json_in_both_halves() -> None:
    """The parser is forgiving, but only about formatting slips. A prompt that
    stopped asking for JSON would push every derivation onto the fallback."""
    from shared.intelligence.prompt import DERIVATION_SYSTEM_PROMPT

    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    prompt = build_derivation_prompt(job_title="Welder", baseline=baseline)

    assert "STRICT JSON" in DERIVATION_SYSTEM_PROMPT
    assert "STRICT JSON" in prompt
    for key in ("domain_family", "competencies", "anchors", "red_flags", "avoid_topics"):
        assert key in prompt
