"""Classifier + baseline tests.

The point of these is regression protection on the exact failure the role
engine exists to fix: non-IT roles being interviewed as if they were software
roles. ``test_no_role_is_classified_as_software_by_accident`` is the canary.
"""

from __future__ import annotations

import pytest

from shared.intelligence.archetypes import ARCHETYPES
from shared.intelligence.schema import MAX_COMPETENCIES, MIN_COMPETENCIES
from shared.intelligence.taxonomy import (
    FAMILIES,
    baseline_profile,
    classify,
)


@pytest.mark.parametrize(
    ("title", "expected_family"),
    [
        ("Senior Backend Developer (Python)", "software_it"),
        ("Full Stack Engineer", "software_it"),
        ("Data Analyst", "data_analytics"),
        ("Machine Learning Engineer", "data_analytics"),
        ("Electrical Maintenance Technician", "electronics_electrical"),
        ("ITI Electrician", "electronics_electrical"),
        ("CNC Machine Operator", "mechanical_manufacturing"),
        ("Welder - MIG/TIG", "mechanical_manufacturing"),
        ("Site Engineer - Civil", "civil_construction"),
        ("Staff Nurse (GNM)", "healthcare_nursing"),
        ("Field Sales Executive", "sales_bd"),
        ("Customer Support Associate - Voice Process", "customer_support_bpo"),
        ("Accounts Executive - GST & Tally", "finance_accounting"),
        ("HR Generalist", "hr_admin"),
        ("Assistant Professor - Physics", "education_training"),
        ("Warehouse Supervisor", "logistics_supply_chain"),
        ("Hotel Front Desk Executive", "retail_hospitality"),
        ("Agriculture Extension Officer", "agriculture_allied"),
        ("UI/UX Designer", "design_creative"),
        ("Compliance Officer - AML", "legal_compliance"),
    ],
)
def test_classify_by_title(title: str, expected_family: str) -> None:
    family, score = classify(job_title=title)
    assert family.id == expected_family, f"{title!r} scored {score}"


def test_unknown_role_falls_back_to_generic_not_a_wrong_guess() -> None:
    """An unrecognised role must return ``generic``, not the nearest guess.

    Confidently filing an unknown role under a wrong family is worse than
    admitting ignorance — the whole interview would be miscalibrated.
    """
    family, score = classify(job_title="Chief Vibes Officer")
    assert family.id == "generic"
    assert score < 5.0


def test_no_role_is_classified_as_software_by_accident() -> None:
    """Regression canary for the IT-bias bug this engine exists to fix.

    Each of these JDs contains software-adjacent noise words ('system',
    'Excel', 'reports', 'technical') that an unweighted keyword classifier
    happily files under Software & IT.
    """
    cases = [
        (
            "Quality Inspector",
            "Inspect components against drawings. Maintain inspection reports in "
            "Excel. Report technical non-conformance to the production system owner.",
            "mechanical_manufacturing",
        ),
        (
            "Staff Nurse",
            "Maintain patient records in the hospital information system. "
            "Generate shift reports. Technical proficiency with monitoring equipment.",
            "healthcare_nursing",
        ),
        (
            "Store Keeper",
            "Update stock in the ERP system daily, generate MIS reports in Excel, "
            "coordinate with the technical team for material issue.",
            "logistics_supply_chain",
        ),
    ]
    for title, jd, expected in cases:
        family, _ = classify(job_title=title, jd_text=jd)
        assert family.id == expected, f"{title!r} misfiled as {family.id!r}"
        assert family.id != "software_it"


def test_title_outweighs_jd_noise() -> None:
    """A mechanical title beats a JD that name-drops software repeatedly."""
    jd = "software " * 30
    family, _ = classify(job_title="CNC Machine Operator", jd_text=jd)
    assert family.id == "mechanical_manufacturing"


def test_skills_disambiguate_a_vague_title() -> None:
    vague = "Technical Executive"
    family, _ = classify(
        job_title=vague,
        required_skills=["Tally", "GST filing", "accounts payable"],
    )
    assert family.id == "finance_accounting"


def test_every_family_builds_a_valid_profile() -> None:
    """Static data integrity: every family must produce a valid profile.

    Catches typo'd archetype ids and weight sets that violate the schema at
    test time rather than in a live interview.
    """
    for family_id, family in FAMILIES.items():
        profile = baseline_profile(profile_id="t" * 32, job_title=family.label)
        assert MIN_COMPETENCIES <= len(profile.competencies) <= MAX_COMPETENCIES
        assert abs(sum(c.weight for c in profile.competencies) - 1.0) < 1e-6
        assert profile.source == "taxonomy"
        assert family_id in FAMILIES

        for spec in family.specs:
            assert spec.archetype_id in ARCHETYPES, (
                f"family {family_id!r} references unknown archetype "
                f"{spec.archetype_id!r}"
            )


def test_baseline_is_deterministic() -> None:
    a = baseline_profile(profile_id="x" * 32, job_title="Welder", jd_text="MIG welding")
    b = baseline_profile(profile_id="x" * 32, job_title="Welder", jd_text="MIG welding")
    assert a.model_dump(exclude={"generated_at"}) == b.model_dump(exclude={"generated_at"})


def test_seniority_is_carried_onto_the_profile() -> None:
    profile = baseline_profile(
        profile_id="y" * 32, job_title="Data Analyst", seniority="senior"
    )
    assert profile.seniority == "senior"
