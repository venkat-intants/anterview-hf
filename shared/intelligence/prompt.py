"""LLM derivation of a ``RoleProfile`` — prompt construction and parsing.

The taxonomy gets the *family* right. The LLM's job is to make the profile
specific to THIS posting: a "CNC Operator (Turning) — 3 years, Sungrace Auto"
should end up with competencies about turning tolerances and setup time, not
the generic mechanical baseline.

Design decisions worth knowing:

* **Seeded, not blank.** The baseline profile is included in the prompt and the
  model is told to refine it. Asking cold for "the competencies for this role"
  reliably produced four restatements of "communication" for unfamiliar roles.
* **No transport code here.** This module builds a string and parses a string.
  The HTTP call is the caller's (``derive.py`` takes an injected callable), so
  ``shared/`` stays free of httpx and of any provider SDK — see the dependency
  rule in ``schema.py``.
* **Repair, don't reject.** Parsing is deliberately forgiving: fences stripped,
  trailing commas removed, slugs repaired, missing anchors backfilled from the
  baseline. A profile that is 90% right is far better than falling back to the
  generic baseline over a formatting slip. Anything unrecoverable raises
  ``ProfileParseError`` and the caller falls back cleanly.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from shared.intelligence.schema import (
    MAX_COMPETENCIES,
    MIN_COMPETENCIES,
    Anchors,
    Competency,
    RoleProfile,
    Seniority,
)

log = structlog.get_logger(__name__)

# Same repair the scorer needs against Gemini: a trailing comma before a
# closing brace/bracket is invalid JSON but trivially recoverable.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# Input caps. These strings go into a one-off derivation call (not every turn),
# but an unbounded JD still risks a truncated response — and truncation on this
# call silently costs the whole role model.
_JD_CHAR_CAP: int = 3000
_SKILLS_CAP: int = 40

_VALID_KINDS = {"technical", "practical", "domain", "behavioural", "communication"}


class ProfileParseError(Exception):
    """Raised when an LLM response cannot be turned into a ``RoleProfile``."""


DERIVATION_SYSTEM_PROMPT: str = (
    "You are an occupational assessment designer. You build interview "
    "competency frameworks for ANY job — trades, healthcare, agriculture, "
    "retail, teaching, finance, construction, software. You are not a software "
    "specialist: never assume a role is technical/IT unless the evidence says "
    "so, and never import software vocabulary (algorithms, debugging, code) "
    "into a role that has nothing to do with it.\n\n"
    "You return STRICT JSON only — no markdown, no code fences, no commentary."
)


def build_derivation_prompt(
    *,
    job_title: str,
    baseline: RoleProfile,
    jd_text: str = "",
    required_skills: list[str] | None = None,
    department: str = "",
    company_name: str = "",
    seniority: Seniority = "mid",
    interview_type: str = "screening",
) -> str:
    """Build the user prompt that asks the model to refine ``baseline``."""
    skills = [s.strip() for s in (required_skills or []) if s and s.strip()][:_SKILLS_CAP]
    skills_line = ", ".join(skills) if skills else "(none supplied)"
    jd_block = (
        f"\nJob description (verbatim, may be partial):\n\"\"\"\n{jd_text[:_JD_CHAR_CAP]}\n\"\"\"\n"
        if jd_text.strip()
        else "\nJob description: (none supplied)\n"
    )

    baseline_json = json.dumps(
        {
            "domain_family": baseline.domain_family,
            "competencies": [
                {
                    "id": c.id,
                    "name": c.name,
                    "kind": c.kind,
                    "weight": c.weight,
                    "probes": c.probes,
                    "anchors": {
                        "low": c.anchors.low,
                        "mid": c.anchors.mid,
                        "high": c.anchors.high,
                    },
                }
                for c in baseline.competencies
            ],
        },
        indent=2,
        ensure_ascii=False,
    )

    return (
        "Build the competency framework used to interview and assess ONE "
        "candidate for the role below.\n\n"
        "## Role\n"
        f"Title      : {job_title}\n"
        f"Level      : {seniority}  (entry | mid | senior)\n"
        f"Department : {department or '(not specified)'}\n"
        f"Company    : {company_name or '(not specified)'}\n"
        f"Interview  : {interview_type}\n"
        f"Required skills: {skills_line}\n"
        f"{jd_block}\n"
        "## Starting point\n"
        "This is the generic framework for the occupational family we matched. "
        "Refine it — rename competencies to what this specific job actually "
        "requires, adjust the weights to this job's priorities, and rewrite the "
        "probes and anchors so they are about THIS work. Drop anything "
        "irrelevant and add what is missing. Keep the same JSON shape.\n"
        f"{baseline_json}\n\n"
        "## Rules\n"
        f"- Return {MIN_COMPETENCIES}-{MAX_COMPETENCIES} competencies, ordered "
        "most important first.\n"
        "- 'weight' is relative importance; the weights must sum to about 1.0.\n"
        "- 'kind' is exactly one of: technical, practical, domain, behavioural, "
        "communication.\n"
        "- 'id' is a lowercase_snake_case slug.\n"
        "- 'probes' are 2-4 INSTRUCTIONS to an interviewer describing what to "
        "ask about (e.g. \"Ask how they verify a weld before sign-off.\"), not "
        "questions to read aloud verbatim.\n"
        "- 'anchors' describe what weak (low), adequate (mid) and strong (high) "
        "evidence looks like FOR THIS ROLE at the stated level. Be concrete and "
        "observable — no 'good understanding'.\n"
        "- Calibrate to the level: an entry candidate is assessed on "
        "foundations and trainability, a senior on judgement and ownership.\n"
        "- 'red_flags' are up to 5 role-specific warning signs a human reviewer "
        "should look at. They are OBSERVATIONS for a human, never grounds for "
        "automatic rejection.\n"
        "- 'avoid_topics' are up to 5 subjects an interviewer must not raise "
        "for this role beyond the standard ban on personal data (name, phone, "
        "email, address, age, religion, caste, marital status, salary).\n"
        "- Never ask for or reference protected personal characteristics "
        "anywhere in your output.\n\n"
        "## Output — STRICT JSON, this exact shape\n"
        "{\n"
        '  "domain_family": "<keep the given family id unless it is clearly wrong>",\n'
        '  "summary": "<1-2 sentences: what this person actually does day to day>",\n'
        '  "competencies": [\n'
        "    {\n"
        '      "id": "<snake_case>",\n'
        '      "name": "<short display name>",\n'
        '      "kind": "<technical|practical|domain|behavioural|communication>",\n'
        '      "weight": <number>,\n'
        '      "probes": ["<interviewer instruction>", "..."],\n'
        '      "anchors": {"low": "<...>", "mid": "<...>", "high": "<...>"}\n'
        "    }\n"
        "  ],\n"
        '  "red_flags": ["<...>"],\n'
        '  "avoid_topics": ["<...>"]\n'
        "}"
    )


def _clean_json_text(raw: str) -> str:
    """Strip fences and trailing commas from a model response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Some models prepend a sentence before the object despite instructions.
    # Slicing to the outermost braces is a no-op when there is no stray text.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return _TRAILING_COMMA_RE.sub(r"\1", cleaned)


def _coerce_competency(raw: Any, fallback: Competency) -> Competency | None:
    """Turn one raw dict into a ``Competency``, backfilling from ``fallback``.

    Returns None only when the entry is unusable (not a dict, or no name/id) —
    a single bad entry drops that competency rather than the whole profile.
    """
    if not isinstance(raw, dict):
        return None

    name = str(raw.get("name") or "").strip()
    cid = str(raw.get("id") or "").strip() or name
    if not name and not cid:
        return None

    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in _VALID_KINDS:
        kind = fallback.kind

    try:
        weight = float(raw.get("weight", fallback.weight))
    except (TypeError, ValueError):
        weight = fallback.weight
    # Non-positive weights would fail validation; a competency the model
    # thought worth listing gets a small share rather than being dropped.
    if weight <= 0:
        weight = 0.01

    raw_probes = raw.get("probes")
    probes = (
        [str(p).strip() for p in raw_probes if str(p).strip()]
        if isinstance(raw_probes, list)
        else []
    )
    if not probes:
        probes = list(fallback.probes)

    raw_anchors = raw.get("anchors")
    if isinstance(raw_anchors, dict) and all(
        str(raw_anchors.get(k) or "").strip() for k in ("low", "mid", "high")
    ):
        anchors = Anchors(
            low=str(raw_anchors["low"]).strip(),
            mid=str(raw_anchors["mid"]).strip(),
            high=str(raw_anchors["high"]).strip(),
        )
    else:
        anchors = fallback.anchors

    try:
        return Competency(
            id=cid or name,
            name=name or cid,
            kind=kind,  # type: ignore[arg-type]  # narrowed against _VALID_KINDS
            weight=min(weight, 1.0),
            probes=probes,
            anchors=anchors,
        )
    except ValueError:
        return None


def parse_profile_response(
    raw_text: str,
    *,
    baseline: RoleProfile,
    profile_id: str,
) -> RoleProfile:
    """Parse an LLM response into a ``RoleProfile``.

    ``baseline`` supplies both the fallback values for missing fields and the
    identity fields (job title, seniority) that the model is not allowed to
    change — it is describing a role we already know the title of.

    Raises:
        ProfileParseError: the response was not JSON, or yielded fewer than
            ``MIN_COMPETENCIES`` usable competencies. The caller falls back to
            the baseline.
    """
    try:
        parsed = json.loads(_clean_json_text(raw_text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProfileParseError(f"role profile response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ProfileParseError("role profile response was not a JSON object")

    raw_comps = parsed.get("competencies")
    if not isinstance(raw_comps, list) or not raw_comps:
        raise ProfileParseError("role profile response had no competencies")

    # Positional fallback: the Nth returned competency backfills from the Nth
    # baseline competency, which keeps anchors sensible when the model omits
    # them (they arrive in importance order on both sides).
    base_comps = baseline.competencies
    competencies: list[Competency] = []
    for i, raw_comp in enumerate(raw_comps[: MAX_COMPETENCIES + 2]):
        fallback = base_comps[min(i, len(base_comps) - 1)]
        coerced = _coerce_competency(raw_comp, fallback)
        if coerced is not None:
            competencies.append(coerced)

    if len(competencies) < MIN_COMPETENCIES:
        raise ProfileParseError(
            f"only {len(competencies)} usable competencies "
            f"(need {MIN_COMPETENCIES})"
        )

    def _str_list(key: str, limit: int) -> list[str]:
        value = parsed.get(key)
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:limit]

    # The model may correct the family (it sees the JD; the classifier only saw
    # keywords) but only to a family we actually know — an invented id would
    # break analytics grouping and the fallback rubric lookup.
    from shared.intelligence.taxonomy import FAMILIES  # local: avoids import cycle

    family_id = str(parsed.get("domain_family") or "").strip()
    if family_id in FAMILIES and family_id != baseline.domain_family:
        family_label = FAMILIES[family_id].label
    else:
        family_id, family_label = baseline.domain_family, baseline.domain_label

    summary = str(parsed.get("summary") or "").strip() or baseline.summary

    # "hybrid" when we had to lean on the baseline for structure; "llm" when
    # the response stood on its own. Recorded so a reviewer can tell how much
    # of a rubric the model actually wrote.
    source = "llm" if len(competencies) >= MIN_COMPETENCIES and summary else "hybrid"

    try:
        return RoleProfile(
            profile_id=profile_id,
            job_title=baseline.job_title,
            domain_family=family_id,
            domain_label=family_label,
            seniority=baseline.seniority,
            summary=summary[:600],
            competencies=competencies,
            red_flags=_str_list("red_flags", 5),
            avoid_topics=_str_list("avoid_topics", 5),
            source=source,  # type: ignore[arg-type]  # literal from the branch above
        )
    except ValueError as exc:
        raise ProfileParseError(f"role profile failed validation: {exc}") from exc
