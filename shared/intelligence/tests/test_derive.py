"""Derivation tests — the never-raises guarantee and the fallback path."""

from __future__ import annotations

import json

import pytest

from shared.intelligence.derive import (
    InMemoryProfileCache,
    cache_key,
    compute_profile_id,
    derive_role_profile,
)
from shared.intelligence.prompt import ProfileParseError, parse_profile_response
from shared.intelligence.schema import RoleProfile
from shared.intelligence.taxonomy import baseline_profile

PID = "z" * 32


def _good_llm_json() -> str:
    return json.dumps(
        {
            "domain_family": "mechanical_manufacturing",
            "summary": "Sets up and operates CNC turning centres to drawing.",
            "competencies": [
                {
                    "id": "cnc_setup",
                    "name": "CNC Setup & Operation",
                    "kind": "practical",
                    "weight": 0.35,
                    "probes": ["Ask how they set work and tool offsets."],
                    "anchors": {
                        "low": "Cannot describe a setup.",
                        "mid": "Runs proven jobs.",
                        "high": "Sets up new jobs unaided and optimises cycle time.",
                    },
                },
                {
                    "id": "metrology",
                    "name": "Measurement & Inspection",
                    "kind": "technical",
                    "weight": 0.3,
                    "probes": ["Ask how they verify a diameter to 20 microns."],
                    "anchors": {"low": "No instruments.", "mid": "Uses vernier.", "high": "Selects instrument by tolerance."},
                },
                {
                    "id": "shop_safety",
                    "name": "Machine Safety",
                    "kind": "domain",
                    "weight": 0.2,
                    "probes": ["Ask what they check before starting a cycle."],
                    "anchors": {"low": "Rules are paperwork.", "mid": "Follows when reminded.", "high": "Stops unsafe work."},
                },
                {
                    "id": "teamwork",
                    "name": "Shift Handover",
                    "kind": "behavioural",
                    "weight": 0.15,
                    "probes": ["Ask what they pass on at handover."],
                    "anchors": {"low": "No handover.", "mid": "Verbal only.", "high": "Structured and written."},
                },
            ],
            "red_flags": ["Cannot name a single instrument they have used."],
            "avoid_topics": ["Union membership"],
        }
    )


async def test_no_llm_returns_the_baseline_not_an_error() -> None:
    """A deployment with no LLM key must still get a working role model."""
    profile = await derive_role_profile(job_title="Welder", llm=None)
    assert profile.source == "taxonomy"
    assert profile.domain_family == "mechanical_manufacturing"
    assert len(profile.competencies) >= 3


async def test_llm_path_produces_a_role_specific_profile() -> None:
    async def llm(system: str, user: str) -> str:
        assert "occupational assessment designer" in system
        assert "CNC" in user
        return _good_llm_json()

    profile = await derive_role_profile(job_title="CNC Turning Operator", llm=llm)
    assert profile.source == "llm"
    assert profile.competency("cnc_setup") is not None
    assert abs(sum(c.weight for c in profile.competencies) - 1.0) < 1e-6


@pytest.mark.parametrize(
    "bad_response",
    [
        "not json at all",
        "{}",
        '{"competencies": []}',
        '{"competencies": [{"name": "only one"}]}',
        "",
    ],
)
async def test_bad_llm_output_falls_back_to_baseline(bad_response: str) -> None:
    async def llm(system: str, user: str) -> str:
        return bad_response

    profile = await derive_role_profile(job_title="Staff Nurse", llm=llm)
    assert profile.source == "taxonomy"
    assert profile.domain_family == "healthcare_nursing"


async def test_llm_exception_never_escapes() -> None:
    """The hot path: an LLM outage must degrade, not take the interview down."""

    async def llm(system: str, user: str) -> str:
        raise TimeoutError("gemini 504")

    profile = await derive_role_profile(job_title="Site Engineer - Civil", llm=llm)
    assert profile.source == "taxonomy"
    assert profile.domain_family == "civil_construction"


async def test_cache_failure_never_escapes() -> None:
    class BrokenCache:
        async def get(self, key: str) -> str | None:
            raise ConnectionError("redis down")

        async def set(self, key: str, value: str, ttl_seconds: int) -> None:
            raise ConnectionError("redis down")

    profile = await derive_role_profile(
        job_title="Data Analyst", llm=None, cache=BrokenCache()
    )
    assert profile.domain_family == "data_analytics"


async def test_second_call_hits_the_cache_and_skips_the_llm() -> None:
    calls = {"n": 0}

    async def llm(system: str, user: str) -> str:
        calls["n"] += 1
        return _good_llm_json()

    cache = InMemoryProfileCache()
    kwargs = {"job_title": "CNC Turning Operator", "llm": llm, "cache": cache}

    first = await derive_role_profile(**kwargs)  # type: ignore[arg-type]
    second = await derive_role_profile(**kwargs)  # type: ignore[arg-type]

    assert calls["n"] == 1
    assert first.profile_id == second.profile_id
    assert second.competency("cnc_setup") is not None


async def test_baseline_is_cached_so_a_failing_llm_is_not_retried_per_interview() -> None:
    """Cost guard: a role whose derivation keeps failing must not burn an LLM
    call on every single interview (the ₹12/session cap cannot absorb it)."""
    calls = {"n": 0}

    async def llm(system: str, user: str) -> str:
        calls["n"] += 1
        raise RuntimeError("quota exhausted")

    cache = InMemoryProfileCache()
    for _ in range(3):
        await derive_role_profile(job_title="Welder", llm=llm, cache=cache)

    assert calls["n"] == 1


def test_profile_id_is_stable_and_order_insensitive() -> None:
    a = compute_profile_id(job_title="Welder", required_skills=["MIG", "TIG"])
    b = compute_profile_id(job_title=" welder ", required_skills=["TIG", "MIG"])
    assert a == b
    assert cache_key(a).endswith(a)


def test_profile_id_changes_when_the_role_changes() -> None:
    a = compute_profile_id(job_title="Welder", seniority="entry")
    b = compute_profile_id(job_title="Welder", seniority="senior")
    assert a != b


@pytest.mark.parametrize(
    ("raw_level", "expected"),
    [("fresher", "entry"), ("Junior", "entry"), ("lead", "senior"), ("", "mid"), ("weird", "mid")],
)
async def test_experience_level_is_normalised_to_three_tiers(
    raw_level: str, expected: str
) -> None:
    profile = await derive_role_profile(
        job_title="Data Analyst", experience_level=raw_level, llm=None
    )
    assert profile.seniority == expected


# ---------------------------------------------------------------------------
# Parser repair behaviour
# ---------------------------------------------------------------------------


def test_parser_strips_fences_and_trailing_commas() -> None:
    baseline = baseline_profile(profile_id=PID, job_title="CNC Turning Operator")
    fenced = "```json\n" + _good_llm_json().replace("}\n  ]", "},\n  ]") + "\n```"
    profile = parse_profile_response(fenced, baseline=baseline, profile_id=PID)
    assert profile.competency("cnc_setup") is not None


def test_parser_repairs_a_non_slug_id() -> None:
    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    payload = json.loads(_good_llm_json())
    payload["competencies"][0]["id"] = "CNC Setup & Operation"
    profile = parse_profile_response(
        json.dumps(payload), baseline=baseline, profile_id=PID
    )
    assert profile.competency("cnc_setup_operation") is not None


def test_parser_backfills_missing_anchors_from_the_baseline() -> None:
    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    payload = json.loads(_good_llm_json())
    del payload["competencies"][0]["anchors"]
    profile = parse_profile_response(
        json.dumps(payload), baseline=baseline, profile_id=PID
    )
    comp = profile.competency("cnc_setup")
    assert comp is not None
    assert comp.anchors.low == baseline.competencies[0].anchors.low


def test_parser_rejects_an_invented_domain_family() -> None:
    """The model may correct the family, but only to one we actually know —
    an invented id would break analytics grouping and the fallback rubric."""
    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    payload = json.loads(_good_llm_json())
    payload["domain_family"] = "underwater_basket_weaving"
    profile = parse_profile_response(
        json.dumps(payload), baseline=baseline, profile_id=PID
    )
    assert profile.domain_family == baseline.domain_family


def test_parser_raises_on_unrecoverable_output() -> None:
    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    with pytest.raises(ProfileParseError):
        parse_profile_response("¯\\_(ツ)_/¯", baseline=baseline, profile_id=PID)


def test_duplicate_competency_ids_are_merged_not_rejected() -> None:
    baseline = baseline_profile(profile_id=PID, job_title="Welder")
    payload = json.loads(_good_llm_json())
    payload["competencies"].append(dict(payload["competencies"][0], weight=0.05))
    profile = parse_profile_response(
        json.dumps(payload), baseline=baseline, profile_id=PID
    )
    assert len(profile.competency_ids) == len(set(profile.competency_ids))


def test_profile_round_trips_through_json() -> None:
    """Cache correctness: what we store must validate back into a profile."""
    original = baseline_profile(profile_id=PID, job_title="Staff Nurse")
    restored = RoleProfile.model_validate_json(original.model_dump_json())
    assert restored.competency_ids == original.competency_ids
    assert restored.domain_family == original.domain_family
