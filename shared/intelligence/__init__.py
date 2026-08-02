"""Intelligence layer — the platform's role-competency engine.

One place that knows what a role IS. Every AI feature (interview question
planning, scoring rubric, and next exam/coding generation) reads the same
``RoleProfile`` instead of re-guessing the job from a free-text title.

Typical use::

    from shared.intelligence import derive_role_profile, plan_interview, render_plan_block

    profile = await derive_role_profile(
        job_title="CNC Turning Operator",
        jd_text=job.jd_text or "",
        required_skills=["CNC", "Fanuc", "micrometer"],
        experience_level="entry",
        llm=my_async_llm_caller,   # optional — omit for the deterministic baseline
        cache=my_redis_cache,      # optional
    )
    plans = plan_interview(profile, max_turns=10)
    system_prompt += "\\n\\n" + render_plan_block(plans)

Guarantees:

* ``derive_role_profile`` never raises — it degrades to a deterministic
  taxonomy baseline and records that in ``profile.source``.
* Planning is pure and deterministic: same profile + same turn budget always
  produces the same plan.
* Nothing here decides anything. Profiles describe what a role requires and
  what counts as evidence; hire/reject stays with a human.
"""

from shared.intelligence.archetypes import CANONICAL_AXES
from shared.intelligence.coverage import (
    allocate_by_weight,
    coverage_report,
    plan_for_turn,
    plan_interview,
)
from shared.intelligence.derive import (
    InMemoryProfileCache,
    LLMCaller,
    ProfileCache,
    cache_key,
    compute_profile_id,
    derive_role_profile,
)
from shared.intelligence.render import (
    DEFAULT_AXIS_WEIGHTS,
    axis_weights,
    render_competency_output_spec,
    render_exam_blueprint,
    render_plan_block,
    render_role_model_block,
    render_scoring_rubric_block,
    render_turn_directive,
)
from shared.intelligence.schema import (
    SCHEMA_VERSION,
    Anchors,
    Competency,
    CoverageReport,
    CoverageRow,
    RoleProfile,
    TurnPlan,
)
from shared.intelligence.taxonomy import FAMILIES, baseline_profile, classify

__all__ = [
    "CANONICAL_AXES",
    "DEFAULT_AXIS_WEIGHTS",
    "FAMILIES",
    "SCHEMA_VERSION",
    "Anchors",
    "Competency",
    "CoverageReport",
    "CoverageRow",
    "InMemoryProfileCache",
    "LLMCaller",
    "ProfileCache",
    "RoleProfile",
    "TurnPlan",
    "allocate_by_weight",
    "axis_weights",
    "baseline_profile",
    "cache_key",
    "classify",
    "compute_profile_id",
    "coverage_report",
    "derive_role_profile",
    "plan_for_turn",
    "plan_interview",
    "render_competency_output_spec",
    "render_exam_blueprint",
    "render_plan_block",
    "render_role_model_block",
    "render_scoring_rubric_block",
    "render_turn_directive",
]
