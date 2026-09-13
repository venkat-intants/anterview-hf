"""D4 — starter templates are built against the role, not just its shape."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class _Anchors:
    low: str = "low"
    mid: str = "mid"
    high: str = "high"


@dataclass
class _Comp:
    id: str
    name: str
    kind: str
    weight: float
    anchors: _Anchors = field(default_factory=_Anchors)
    probes: list[str] = field(default_factory=lambda: ["Tell me about…"])


@dataclass
class _Profile:
    competencies: list[_Comp]
    domain_family: str = "software_it"


def _python_role() -> _Profile:
    return _Profile([
        _Comp("python", "Python", "technical", 0.25),
        _Comp("problem_solving", "Problem Solving", "practical", 0.20),
        _Comp("version_control", "Version Control", "practical", 0.10),
        _Comp("sdlc", "Software Lifecycle", "domain", 0.15),
        _Comp("communication", "Communication", "communication", 0.15),
        _Comp("ownership", "Ownership", "behavioural", 0.15),
    ])


def _covered(payload: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in payload["rounds"]:
        for c in r["criteria"]:
            counts[c["id"]] = counts.get(c["id"], 0) + 1
    return counts


def test_the_three_templates_have_the_specified_shapes() -> None:
    from app.workflow_templates import build_template

    shape = {k: [r["kind"] for r in build_template(k, _python_role())["rounds"]]
             for k in ("technical", "non_technical", "interview_only")}
    assert shape == {
        "technical": ["mcq", "coding", "ai_interview", "human_review"],
        "non_technical": ["mcq", "ai_interview", "human_review"],
        "interview_only": ["ai_interview", "human_review"],
    }


@pytest.mark.parametrize("key", ["technical", "non_technical", "interview_only"])
def test_every_template_ends_at_a_human(key: str) -> None:
    """The old "Screen, then interview" preset had no human gate at all."""
    from app.workflow_templates import build_template

    assert build_template(key, _python_role())["rounds"][-1]["kind"] == "human_review"


@pytest.mark.parametrize("key", ["technical", "non_technical", "interview_only"])
def test_a_fresh_template_leaves_no_competency_unassessed(key: str) -> None:
    from app.workflow_templates import build_template

    covered = _covered(build_template(key, _python_role()))
    assert set(covered) == {c.id for c in _python_role().competencies}


@pytest.mark.parametrize("key", ["technical", "non_technical", "interview_only"])
def test_no_competency_starts_over_assessed(key: str) -> None:
    """The coverage check flags 3+ rounds. A template should not start with a
    warning HR did not cause."""
    from app.workflow_templates import build_template

    assert max(_covered(build_template(key, _python_role())).values()) <= 2


def test_rounds_assess_what_they_can_observe() -> None:
    from app.workflow_templates import build_template

    rounds = {r["title"]: {c["kind"] for c in r["criteria"]}
              for r in build_template("technical", _python_role())["rounds"]}
    assert rounds["Aptitude"] <= {"technical", "domain"}
    assert rounds["Coding"] == {"practical"}
    assert rounds["Human Review"] == {"behavioural"}


def test_the_full_rubric_travels_not_just_the_label() -> None:
    """Freezing what is measured without how would move the standard later (C8)."""
    from app.workflow_templates import build_template

    crit = build_template("technical", _python_role())["rounds"][0]["criteria"][0]
    assert set(crit) == {"id", "name", "kind", "weight", "anchors", "probes"}
    assert crit["anchors"] == {"low": "low", "mid": "mid", "high": "high"}


def test_timings_thresholds_and_settings_are_prefilled() -> None:
    from app.workflow_templates import build_template

    payload = build_template("technical", _python_role())
    by_kind = {r["kind"]: r for r in payload["rounds"]}
    assert by_kind["mcq"]["pass_threshold"] == 60 and by_kind["mcq"]["time_limit_seconds"] == 1800
    assert by_kind["coding"]["pass_threshold"] == 50
    assert by_kind["ai_interview"]["pass_threshold"] == 60
    assert by_kind["human_review"]["pass_threshold"] is None
    assert all(r["deadline_days"] > 0 for r in payload["rounds"])
    assert payload["settings"] == {
        "auto_score_on_apply": True, "auto_assign_first_round": True,
        "auto_advance_rounds": True, "reminders_enabled": True,
    }


def test_no_setting_can_reject_anyone() -> None:
    """D-05, structurally."""
    from app.workflow_templates import DEFAULT_SETTINGS

    assert not any("reject" in k for k in DEFAULT_SETTINGS)


def test_a_role_with_none_of_a_rounds_kinds_still_gets_criteria() -> None:
    """A welder's role model may have no 'practical'-only split that suits an
    MCQ. An empty test round generates nothing useful."""
    from app.workflow_templates import build_template

    only_behaviour = _Profile([
        _Comp("teamwork", "Teamwork", "behavioural", 0.4),
        _Comp("safety", "Safety", "behavioural", 0.35),
        _Comp("reliability", "Reliability", "behavioural", 0.25),
    ], domain_family="skilled_trades")
    rounds = build_template("non_technical", only_behaviour)["rounds"]
    assert all(r["criteria"] for r in rounds if r["kind"] in ("mcq", "ai_interview"))


def test_rounds_are_capped_heaviest_first() -> None:
    from app.workflow_templates import MAX_CRITERIA_PER_ROUND, build_template

    many = _Profile([_Comp(f"c{i}", f"C{i}", "technical", 0.05 + i / 1000) for i in range(12)])
    rounds = build_template("interview_only", many)["rounds"]
    interview = rounds[0]["criteria"]
    assert len(interview) == MAX_CRITERIA_PER_ROUND
    assert [c["id"] for c in interview] == [f"c{i}" for i in range(11, 3, -1)]


@pytest.mark.parametrize(("family", "expected"), [
    ("software_it", "technical"), ("data_analytics", "technical"),
    ("healthcare", "non_technical"), (None, "non_technical"),
])
def test_the_recommendation_follows_the_occupational_family(family: str | None,
                                                            expected: str) -> None:
    from app.workflow_templates import recommended_template

    assert recommended_template(family) == expected


def test_summaries_preview_what_would_be_created() -> None:
    from app.workflow_templates import template_summaries

    s = template_summaries(_python_role())
    assert [t["key"] for t in s] == ["technical", "non_technical", "interview_only"]
    assert [t["recommended"] for t in s] == [True, False, False]
    assert s[0]["rounds"][1]["competencies"] == ["Problem Solving", "Version Control"]
