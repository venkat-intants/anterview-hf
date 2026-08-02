"""Tests for data_gateway's concrete agent tools and router wiring.

The runtime itself is covered in shared/agents/tests. These cover the seams
that are specific to this service: that no registered tool can mutate, that
every proposal points at an endpoint that actually exists, that scope comes
from the context, and that panel evidence is normalised onto one scale.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from shared.agents import AgentMessage, ToolCall, ToolContext, ToolResult

from app.agents.evidence import MAX_DETAIL_CHARS
from app.agents.llm import _sanitise_schema, _to_gemini_contents
from app.agents.tools import registry
from app.routers.agent import _primary_role

# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


def test_no_registered_tool_can_mutate() -> None:
    """The guarantee, checked against the real registry rather than a fixture."""
    for spec in registry.specs_for("hr_manager"):
        assert spec.effect in ("read", "draft"), f"{spec.name} declares {spec.effect}"


def test_every_tool_is_scoped_to_a_role() -> None:
    """An unscoped tool would be visible to every console, including analytics."""
    for spec in registry.specs_for("hr_manager"):
        assert spec.allowed_roles, f"{spec.name} has no allowed_roles"


def test_every_console_has_at_least_one_tool() -> None:
    """A copilot with no tools can only chat, which is worse than not shipping it."""
    for role in ("hr_manager", "super_admin", "platform_owner", "admin"):
        assert registry.specs_for(role), f"{role} has no tools"


def test_analytics_console_sees_no_candidate_level_tools() -> None:
    """The analytics role gets aggregates, never an individual's record."""
    names = {s.name for s in registry.specs_for("admin")}
    assert names == {"get_score_distribution"}
    assert "get_applicant_detail" not in names
    assert "list_applicants" not in names


def test_platform_owner_gets_aggregates_not_candidate_records() -> None:
    """platform_owner is cross-tenant, so its tools must be aggregate-only —
    otherwise one unscoped read reaches every company's applicants."""
    names = {s.name for s in registry.specs_for("platform_owner")}
    assert names == {"get_platform_overview", "get_score_distribution"}
    assert not (names & {"list_applicants", "get_applicant_detail", "draft_shortlist"})


def test_cross_tenant_tools_are_never_draft_tools() -> None:
    """A cross-tenant draft could propose a write against another company."""
    for role in ("platform_owner", "admin"):
        for spec in registry.specs_for(role):
            assert spec.effect == "read", f"{spec.name} is {spec.effect} for {role}"


def test_hr_console_gets_both_read_and_draft_tools() -> None:
    names = {s.name for s in registry.specs_for("hr_manager")}
    assert "list_applicants" in names
    assert "get_applicant_detail" in names
    assert "draft_interview_invites" in names
    assert names >= {"get_funnel_analytics", "get_role_model"}


def test_tool_descriptions_tell_the_model_drafts_are_not_actions() -> None:
    """A draft tool whose description implies it acts will be misreported."""
    for spec in registry.specs_for("hr_manager"):
        if spec.effect == "draft":
            assert "does NOT" in spec.description or "Does NOT" in spec.description


# ---------------------------------------------------------------------------
# Proposals must target endpoints that exist
# ---------------------------------------------------------------------------


def _fake_db(rows: list[Any]) -> MagicMock:
    """A db whose execute() returns the given rows for .all() and .first()."""
    result = MagicMock()
    result.all.return_value = rows
    result.first.return_value = rows[0] if rows else None
    db = MagicMock()

    async def _execute(*args: Any, **kwargs: Any) -> MagicMock:
        return result

    db.execute = _execute
    return db


def _row(**fields: Any) -> MagicMock:
    row = MagicMock()
    for key, value in fields.items():
        setattr(row, key, value)
    return row


def _ctx(db: Any, role: str = "hr_manager", company: str = "co-1") -> ToolContext:
    return ToolContext(actor_id="u-1", role=role, company_id=company, resources={"db": db})


async def test_interview_invite_proposal_matches_the_real_endpoint() -> None:
    """POST /hr/interviews with {applicant_id, language} — see InviteCreateIn."""
    db = _fake_db([_row(id="a-1", full_name="Asha", email="a@x.com", target_job_title="Welder")])
    result = await registry.invoke(
        "draft_interview_invites",
        {"applicant_ids": ["a-1"], "language": "hi", "reason": "top ATS"},
        _ctx(db),
        call_id="c1",
    )

    assert result.ok
    assert len(result.proposals) == 1
    commit = result.proposals[0].commit
    assert commit.method == "POST"
    assert commit.path == "/hr/interviews"
    assert set(commit.body) == {"applicant_id", "language"}
    assert commit.body["language"] == "hi"


async def test_invite_draft_skips_a_candidate_with_no_email() -> None:
    """An invite is delivered by email — drafting one without an address would
    render a button that fails on click."""
    db = _fake_db([_row(id="a-1", full_name="Bala", email=None, target_job_title="Welder")])
    result = await registry.invoke(
        "draft_interview_invites", {"applicant_ids": ["a-1"]}, _ctx(db), call_id="c1"
    )
    assert result.proposals == []
    assert "no email on file" in result.content


async def test_invite_draft_warns_that_email_is_irreversible() -> None:
    db = _fake_db([_row(id="a-1", full_name="Asha", email="a@x.com", target_job_title="Welder")])
    result = await registry.invoke(
        "draft_interview_invites", {"applicant_ids": ["a-1"]}, _ctx(db), call_id="c1"
    )
    assert "cannot be unsent" in (result.proposals[0].risk_note or "")


async def test_shortlist_proposal_warns_it_emails_the_candidate() -> None:
    """PATCH /hr/applicants/{id} calls email_applicant_decision on a real
    transition, so a shortlist is NOT a silent internal change."""
    db = _fake_db([_row(id="a-1", full_name="Asha", status="new")])
    result = await registry.invoke(
        "draft_shortlist", {"applicant_ids": ["a-1"]}, _ctx(db), call_id="c1"
    )
    proposal = result.proposals[0]
    assert proposal.commit.method == "PATCH"
    assert proposal.commit.path == "/hr/applicants/a-1"
    assert proposal.commit.body == {"status": "shortlisted"}
    assert proposal.risk_note and "Emails the candidate" in proposal.risk_note


async def test_shortlist_draft_skips_already_decided_candidates() -> None:
    db = _fake_db([_row(id="a-1", full_name="Asha", status="hired")])
    result = await registry.invoke(
        "draft_shortlist", {"applicant_ids": ["a-1"]}, _ctx(db), call_id="c1"
    )
    assert result.proposals == []


async def test_draft_tools_reject_an_empty_id_list() -> None:
    db = _fake_db([])
    for tool in ("draft_interview_invites", "draft_shortlist"):
        result = await registry.invoke(tool, {"applicant_ids": []}, _ctx(db), call_id="c1")
        assert "non-empty list" in result.content


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------


async def test_applicant_detail_hides_the_difference_between_absent_and_foreign() -> None:
    """Distinguishing them would let a caller probe other tenants for record ids."""
    result = await registry.invoke(
        "get_applicant_detail", {"applicant_id": "a-1"}, _ctx(_fake_db([])), call_id="c1"
    )
    assert "no such applicant in this company" in result.content


async def test_role_model_tool_needs_no_database_and_follows_the_role() -> None:
    """Explaining a rubric must not cost a DB hit or an LLM call."""
    result = await registry.invoke(
        "get_role_model", {"job_title": "Staff Nurse", "level": "entry"}, _ctx(None), call_id="c1"
    )
    assert result.ok
    assert "Healthcare & Nursing" in result.content
    assert "Patient Safety" in result.content


async def test_role_model_tool_normalises_a_bogus_level() -> None:
    result = await registry.invoke(
        "get_role_model", {"job_title": "Welder", "level": "wizard"}, _ctx(None), call_id="c1"
    )
    assert '"level": "mid"' in result.content


# ---------------------------------------------------------------------------
# Console selection
# ---------------------------------------------------------------------------


def _user(*roles: str) -> Any:
    user = MagicMock()
    user.user_id = "u-1"
    user.roles = list(roles)
    return user


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (("hr_manager",), "hr_manager"),
        (("super_admin",), "super_admin"),
        (("platform_owner", "admin"), "platform_owner"),
        (("admin",), "admin"),
        # Most-privileged wins, matching how the frontend picks a console.
        (("hr_manager", "super_admin"), "super_admin"),
    ],
)
def test_primary_role_picks_the_most_privileged_console(
    roles: tuple[str, ...], expected: str
) -> None:
    assert _primary_role(_user(*roles)) == expected


def test_a_candidate_has_no_copilot() -> None:
    with pytest.raises(HTTPException) as exc:
        _primary_role(_user("candidate"))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Gemini wire format
# ---------------------------------------------------------------------------


def test_tool_results_go_back_as_function_responses() -> None:
    """Gemini pairs functionResponse to functionCall by name on a user turn.

    Getting this wrong makes the model ignore tool output and re-request the
    same tool until the step budget fires.
    """
    wire = _to_gemini_contents(
        [
            AgentMessage(role="user", text="who is top?"),
            AgentMessage(
                role="assistant", tool_calls=[ToolCall(id="c1", name="list_applicants")]
            ),
            AgentMessage(
                role="tool",
                tool_results=[ToolResult(call_id="c1", name="list_applicants", content="{}")],
            ),
        ]
    )
    assert wire[1]["role"] == "model"
    assert "functionCall" in wire[1]["parts"][0]
    assert wire[2]["role"] == "user"
    assert wire[2]["parts"][0]["functionResponse"]["name"] == "list_applicants"


def test_schema_sanitiser_drops_keywords_gemini_rejects() -> None:
    """An unsupported keyword returns an opaque 400 that is painful to trace."""
    cleaned = _sanitise_schema(
        {
            "type": "object",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
            "properties": {
                "ids": {"type": "array", "items": {"type": "string", "default": "x"}}
            },
            "required": ["ids"],
        }
    )
    assert "$schema" not in cleaned
    assert "additionalProperties" not in cleaned
    assert "default" not in cleaned["properties"]["ids"]["items"]
    assert cleaned["required"] == ["ids"]


def test_every_registered_tool_schema_survives_sanitising() -> None:
    """A tool whose schema empties out would be uncallable at runtime."""
    for spec in registry.specs_for("hr_manager"):
        cleaned = _sanitise_schema(spec.parameters)
        assert cleaned.get("type") == "object"


# ---------------------------------------------------------------------------
# Panel evidence
# ---------------------------------------------------------------------------


def test_detail_cap_is_bounded() -> None:
    """Four specialists each carrying an unbounded transcript would blow both
    the context window and the per-assessment cost."""
    assert 0 < MAX_DETAIL_CHARS <= 8000


async def test_interview_composite_is_rescaled_onto_the_panel_scale() -> None:
    """Interview composites are 0-10; every other signal is 0-100.

    Comparing them raw would manufacture contradictions out of unit error.
    """
    from app.agents.evidence import _interview_signal

    db = _fake_db(
        [
            _row(
                scorecard_id="sc-1",
                scores={"technical": 8},
                composite_score=7.4,
                summary="Solid",
                rationale={"technical": "good", "_role_profile_id": "abc"},
            )
        ]
    )
    signal = await _interview_signal(db, "co-1", "a-1", "Asha")

    assert signal.available is True
    assert signal.score_0_100 == pytest.approx(74.0)
    # Intelligence-layer metadata keys must not leak in as per-axis prose.
    assert "_role_profile_id" not in signal.detail


async def test_a_missing_round_is_absent_not_zero() -> None:
    """'Did not sit the exam' and 'failed the exam' are different facts."""
    from app.agents.evidence import _exam_signal, _interview_signal

    for builder in (_exam_signal, _interview_signal):
        kwargs = {"name": "Asha"} if builder is _interview_signal else {}
        signal = await builder(_fake_db([]), "co-1", "a-1", **kwargs)  # type: ignore[operator]
        assert signal.available is False
        assert signal.score_0_100 is None
