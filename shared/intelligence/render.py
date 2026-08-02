"""Rendering — turning a ``RoleProfile`` into prompt text and score weights.

Consumers differ in shape, so there are three renderers rather than one:

* ``render_role_model_block`` — the ``[ROLE MODEL]`` block for the interviewer's
  system prompt. Used by both interview paths.
* ``render_plan_block`` — the whole question plan as one block, for the live
  LiveKit worker, which builds a single instruction string for the entire
  session (it has no per-turn hook).
* ``render_turn_directive`` — one turn's instruction, for the LangGraph
  ``follow_up`` node, which does have a per-turn hook and can be steered
  turn by turn.

Plus ``axis_weights`` / ``render_scoring_rubric_block`` for the scorer.

Language note: these blocks are English meta-instructions to the model, not
user-visible copy — the same convention the existing per-turn prompts follow
(``graph/prompts.py``). The system prompt still pins the OUTPUT language, so an
English role model produces Hindi or Telugu questions exactly as before.
Translating them would triple the eval surface without improving the questions.
"""

from __future__ import annotations

from shared.intelligence.archetypes import CANONICAL_AXES, affinity_for
from shared.intelligence.schema import RoleProfile, TurnPlan

# Default axis weights — the pre-existing LLD §10 blend, and the anchor that
# per-role weights are pulled back towards.
DEFAULT_AXIS_WEIGHTS: dict[str, float] = {
    "communication": 0.30,
    "technical": 0.30,
    "problem_solving": 0.25,
    "confidence": 0.15,
}

# How far a role is allowed to move the axis weights away from the defaults.
# 0.0 = always the defaults; 1.0 = purely role-derived. 0.6 lets a welding role
# genuinely outweigh technical over communication, while stopping a role whose
# competencies happen to be all-behavioural from collapsing 'technical' to
# near-zero and making its composite incomparable with every other scorecard in
# the analytics dashboard.
ROLE_WEIGHT_BLEND: float = 0.6

# No axis may fall below this. All four are persisted and charted per-axis;
# an axis at 0.02 is still displayed at full size to a human reader, who then
# over-reads a number that barely moved the composite.
MIN_AXIS_WEIGHT: float = 0.10


def axis_weights(profile: RoleProfile | None) -> dict[str, float]:
    """Per-role weights for the four canonical scorecard axes.

    The axes themselves are a frozen contract (persisted in
    ``scorecards.scores``, aggregated by the admin analytics SQL, typed in the
    frontend). What the role engine changes is their WEIGHTING — so a CNC
    operator's composite is driven by technical and practical evidence, and a
    customer-support composite by communication, without either scorecard
    becoming a different shape from the other.

    Returns the defaults unchanged when ``profile`` is None, so every caller
    can pass an optional profile and stay backwards-compatible.
    """
    if profile is None:
        return dict(DEFAULT_AXIS_WEIGHTS)

    # 1. Project competency weights onto axes via archetype affinity.
    raw: dict[str, float] = dict.fromkeys(CANONICAL_AXES, 0.0)
    for comp in profile.competencies:
        affinity = affinity_for(comp.id, comp.kind)
        affinity_total = sum(affinity.values()) or 1.0
        for axis, share in affinity.items():
            if axis in raw:
                raw[axis] += comp.weight * (share / affinity_total)

    total = sum(raw.values())
    if total <= 0:  # defensive: an affinity table with no canonical axes
        return dict(DEFAULT_AXIS_WEIGHTS)
    role_weights = {axis: value / total for axis, value in raw.items()}

    # 2. Blend towards the defaults, then apply the floor.
    blended = {
        axis: ROLE_WEIGHT_BLEND * role_weights[axis]
        + (1.0 - ROLE_WEIGHT_BLEND) * DEFAULT_AXIS_WEIGHTS[axis]
        for axis in CANONICAL_AXES
    }
    floored = {axis: max(MIN_AXIS_WEIGHT, w) for axis, w in blended.items()}

    # 3. Renormalise (the floor may have added mass) and remove rounding drift
    #    from the heaviest axis, so the weights sum to exactly 1.0 and two
    #    identical interviews cannot produce different composites.
    total = sum(floored.values())
    final = {axis: round(w / total, 4) for axis, w in floored.items()}
    drift = round(1.0 - sum(final.values()), 4)
    if drift:
        heaviest = max(final, key=lambda a: final[a])
        final[heaviest] = round(final[heaviest] + drift, 4)
    return final


def render_role_model_block(profile: RoleProfile) -> str:
    """The ``[ROLE MODEL]`` block for an interviewer system prompt.

    Deliberately describes the role in plain terms before listing competencies:
    the model interviews far better when it has a picture of the job than when
    handed a bare taxonomy. Probes are labelled as shapes-of-question, never as
    a script to read out — the interview must still follow the candidate.
    """
    lines: list[str] = [
        "[ROLE MODEL]",
        f"Role: {profile.job_title}",
        f"Occupational family: {profile.domain_label}",
        f"Level: {profile.seniority}",
    ]
    if profile.summary:
        lines.append(f"What this person does: {profile.summary}")

    lines.append("")
    lines.append(
        "Assess the candidate against these competencies. The weight is how "
        "much this role depends on each one — spend your questions "
        "accordingly. The examples are SHAPES of question, not a script: "
        "phrase them naturally and follow what the candidate actually says."
    )
    for i, comp in enumerate(profile.competencies, start=1):
        lines.append(f"  {i}. {comp.name} ({comp.kind}, weight {comp.weight:.2f})")
        for probe in comp.probes[:3]:
            lines.append(f"       - {probe}")

    if profile.avoid_topics:
        lines.append("")
        lines.append(
            "Do NOT raise these topics for this role (on top of the standard "
            "ban on personal data): " + "; ".join(profile.avoid_topics) + "."
        )

    return "\n".join(lines)


def render_plan_block(plans: list[TurnPlan]) -> str:
    """The full question plan, for a single-instruction interviewer.

    The live worker cannot re-prompt per turn, so the whole plan goes in up
    front. This replaces the hardcoded 'Q2-Q6 technical, Q7-Q9 behavioural'
    structure, which assumed every role splits that way — it does not: a
    customer-support role is mostly behavioural, a machinist mostly practical.
    """
    lines: list[str] = [
        "[QUESTION PLAN]",
        (
            f"Ask exactly {len(plans)} questions, one per turn, in this order. "
            "The plan tells you what each turn is FOR — you still write the "
            "question, adapt it to what the candidate just said, and may ask a "
            "brief clarifier within the same turn."
        ),
    ]
    for plan in plans:
        if plan.kind == "intro":
            lines.append(
                f"  Q{plan.turn_index} — Opening: invite the candidate to "
                "introduce themselves and walk through their background."
            )
        elif plan.kind == "wrap":
            lines.append(
                f"  Q{plan.turn_index} — Close: a warm wrap-up question (their "
                "goals, or anything they want to ask)."
            )
        else:
            hint = f" e.g. {plan.probe_hint}" if plan.probe_hint else ""
            lines.append(f"  Q{plan.turn_index} — {plan.competency_name}.{hint}")

    lines.append("")
    lines.append(
        "Do not announce which competency you are testing, and do not read the "
        "plan aloud."
    )
    return "\n".join(lines)


def render_turn_directive(plan: TurnPlan, *, turn_index: int, max_turns: int) -> str:
    """One turn's instruction, for a per-turn-steerable interviewer.

    Used by the LangGraph ``follow_up`` node in place of the old "inspect the
    history and pick the least-covered competency" instruction. The competency
    is now chosen in code, so the model spends its attention on phrasing a good
    question instead of on bookkeeping it cannot verify.
    """
    if plan.kind == "intro":
        body = (
            "This turn is the OPENING. Invite the candidate to introduce "
            "themselves and walk through their background. Do not jump into a "
            "technical or behavioural question yet."
        )
    elif plan.kind == "wrap":
        body = (
            "This turn CLOSES the interview. Ask one warm wrap-up question — "
            "their goals, or whether they have anything to ask. Do not open a "
            "new assessment topic."
        )
    else:
        hint = f"\nA question of roughly this shape would evidence it: {plan.probe_hint}" if plan.probe_hint else ""
        body = (
            f"This turn must probe ONE competency: {plan.competency_name}."
            f"{hint}\n"
            "Phrase it naturally, connect it to what the candidate just said "
            "where that is possible, and do NOT announce the competency."
        )

    return (
        f"{body}\n"
        "Ask ONE concise question (1-2 sentences). Acknowledge the previous "
        "answer only if it would feel unnatural not to — do not pad.\n"
        f"(turn {turn_index} of {max_turns})"
    )


def render_scoring_rubric_block(profile: RoleProfile) -> str:
    """The role-specific rubric block for the scorer prompt.

    Two jobs. First, it tells the scorer what the four canonical axes MEAN for
    this role — 'technical: 7' has to mean welding fundamentals for a welder
    and query design for a data analyst, and previously it meant whatever the
    model assumed from the job title. Second, it asks for a per-competency
    breakdown, which is stored alongside the canonical axes as extra evidence
    rather than replacing them.
    """
    weights = axis_weights(profile)
    lines: list[str] = [
        "## Role model (assess against THIS, not a generic idea of the job)",
        f"Role   : {profile.job_title}",
        f"Family : {profile.domain_label}",
        f"Level  : {profile.seniority}",
    ]
    if profile.summary:
        lines.append(f"Work   : {profile.summary}")

    lines.append("")
    lines.append(
        "Competencies this role is assessed on, with what weak / adequate / "
        "strong evidence looks like AT THIS LEVEL:"
    )
    for comp in profile.competencies:
        lines.append(f"- {comp.name} (id: {comp.id}, weight {comp.weight:.2f})")
        lines.append(f"    weak (0-3)    : {comp.anchors.low}")
        lines.append(f"    adequate (4-7): {comp.anchors.mid}")
        lines.append(f"    strong (8-10) : {comp.anchors.high}")

    lines.append("")
    lines.append(
        "Axis weighting for this role (already applied by the system — shown "
        "so you calibrate emphasis, do NOT compute a composite yourself): "
        + ", ".join(f"{axis} {weights[axis]:.2f}" for axis in CANONICAL_AXES)
        + "."
    )
    lines.append(
        "Interpret the four axes THROUGH the competencies above. 'Technical "
        "Knowledge' means the technical and domain competencies of THIS role — "
        "never a generic or software-flavoured notion of technical skill."
    )

    if profile.red_flags:
        lines.append("")
        lines.append(
            "Role-specific signals a human reviewer asked to be told about, if "
            "and only if the transcript actually evidences them: "
            + "; ".join(profile.red_flags)
            + ". Report them as observations. Never treat them as grounds for "
            "rejection — you do not make hiring decisions."
        )

    return "\n".join(lines)


def render_exam_blueprint(profile: RoleProfile, num_questions: int) -> str:
    """A per-competency question quota for exam generation.

    Uses the SAME weighted allocator as interview planning
    (``coverage.allocate_by_weight``), so a written test covers the same
    competencies in the same proportion as the conversation. Before this,
    exams were generated from a free-text topic while the interview followed
    the role model — meaning a candidate's two rounds could quietly be
    assessing different jobs, and the pipeline had no way to notice.

    Competencies allocated zero questions are omitted rather than listed with
    a quota of 0, which a model reliably misreads as "ask about this anyway".
    """
    from shared.intelligence.coverage import allocate_by_weight  # avoids a cycle

    allocation = allocate_by_weight(profile, max(1, num_questions))
    lines: list[str] = [
        "## Role model — spread the questions across these competencies",
        f"Role : {profile.job_title} ({profile.domain_label}, {profile.seniority} level)",
    ]
    if profile.summary:
        lines.append(f"Work : {profile.summary}")
    lines.append("")
    lines.append("Question quota per competency (respect these counts):")

    for comp in profile.competencies:
        count = allocation.get(comp.id, 0)
        if count <= 0:
            continue
        lines.append(f"  - {comp.name} ({comp.kind}): {count} question(s)")
        lines.append(f"      target the level of: {comp.anchors.mid}")

    lines.append("")
    lines.append(
        "Questions must be about THIS occupation. Never import vocabulary from "
        "another field — no algorithms or code for a trade or clinical role, no "
        "machinery for an office role."
    )
    return "\n".join(lines)


def render_competency_output_spec(profile: RoleProfile) -> str:
    """The extra JSON the scorer should emit for the per-competency breakdown.

    Kept separate from the rubric block so the scorer can compose it into its
    own output contract, and so the breakdown can be dropped without touching
    the rubric if a caller only wants the canonical axes.
    """
    ids = ", ".join(f'"{c.id}"' for c in profile.competencies)
    return (
        'Additionally include a "competencies" object keyed by competency id '
        f"({ids}), each of the form "
        '{"score": <int 0-10>, "evidence": "<what in the transcript supports '
        'this, or state plainly that the interview did not cover it>"}. '
        "If a competency was never asked about, score it conservatively and "
        "SAY SO in the evidence — do not invent support for it."
    )
