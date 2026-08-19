"""Per-console data isolation, checked against the REAL registry.

shared/agents/tests/test_access_matrix.py proves the mechanism. This proves the
wiring: that the nine-plus tools this service actually registers land in the
consoles they were meant to, and that a company super admin cannot reach a
named candidate through any of them.

The distinction matters. The matrix could be perfect while a tool declared
itself ``company_scoped`` and then returned resumes — so the last test here
reads the handler source and insists that anything company-scoped filters on
``ctx.company_id``.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.agents import tools as tools_module
from app.agents.tools import registry
from app.routers.agent import _agent_context

# What each console is expected to hold. Written out in full rather than
# derived from the registry: a test that recomputes the thing it is checking
# passes no matter how the thing changes, which is the opposite of what this
# file is for. If you add a tool, add it here deliberately.
EXPECTED: dict[str, set[str]] = {
    "hr_manager": {
        "list_applicants",
        "get_applicant_detail",
        "get_funnel_analytics",
        "get_exam_question_stats",
        "get_role_model",
        "draft_interview_invites",
        "draft_shortlist",
    },
    "super_admin": {
        "get_funnel_analytics",
        "get_exam_question_stats",
        "get_role_model",
        "get_company_overview",
        "get_hr_workload",
    },
    "platform_owner": {
        "get_platform_overview",
        "get_score_distribution",
    },
    "admin": {
        "get_score_distribution",
    },
}

CANDIDATE_TOOLS = {
    "list_applicants",
    "get_applicant_detail",
    "draft_interview_invites",
    "draft_shortlist",
}


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_each_console_holds_exactly_its_intended_toolset(role: str) -> None:
    assert {s.name for s in registry.specs_for(role)} == EXPECTED[role]


@pytest.mark.parametrize("role", ["super_admin", "platform_owner", "admin"])
def test_no_console_but_hr_can_reach_a_named_candidate(role: str) -> None:
    """The restriction, stated against the concrete tools.

    A super admin asking "show me Asha's interview transcript" must find no
    tool that can answer, not a tool that answers and is later told not to.
    """
    visible = {s.name for s in registry.specs_for(role)}
    assert not (visible & CANDIDATE_TOOLS)


def test_candidate_tools_are_declared_as_such() -> None:
    """A candidate tool that mislabelled itself would slip past the matrix."""
    for spec, _ in registry._tools.values():
        if spec.name in CANDIDATE_TOOLS:
            assert spec.data_class == "candidate_pii", spec.name


def test_the_super_admin_console_is_not_a_superset_of_hr() -> None:
    """Guards the decision itself.

    "More senior therefore sees more" is the intuitive default and the reason
    this separation would quietly erode. Super admin trades candidate depth for
    company breadth; it is not a bigger HR manager.
    """
    hr = {s.name for s in registry.specs_for("hr_manager")}
    sa = {s.name for s in registry.specs_for("super_admin")}
    assert not sa.issuperset(hr)
    assert sa - hr == {"get_company_overview", "get_hr_workload"}
    assert hr - sa == CANDIDATE_TOOLS


def test_staff_tools_stay_inside_one_company() -> None:
    """get_hr_workload returns employee staff records (names, activity counts).

    That is fine for the super admin who administers those employees, and never
    fine across a tenant boundary — so the class must not be cross-tenant.
    """
    for spec, _ in registry._tools.values():
        if spec.data_class == "company_staff":
            assert set(spec.allowed_roles) == {"super_admin"}, spec.name


def test_every_tenant_scoped_query_filters_on_the_context_company() -> None:
    """Declaring a data class does not by itself scope a query.

    A tool could claim ``company_scoped`` and then select the whole table. Every
    handler that reads the database and is not a platform aggregate must
    mention ``ctx.company_id`` — crude, but it catches the omission that
    matters, and the omission is silent in every other way.

    Handlers that never touch the database are exempt because there is no row
    to scope; ``test_a_handler_that_reads_no_rows_needs_no_company`` pins the
    one tool in that position so the exemption cannot quietly widen.
    """
    for spec, handler in registry._tools.values():
        if spec.data_class == "platform_aggregate":
            continue
        source = inspect.getsource(handler)
        if "_db(ctx)" not in source and "db.execute" not in source:
            continue
        assert "ctx.company_id" in source, (
            f"{spec.name} is {spec.data_class} and queries the database, but its "
            "handler never references ctx.company_id — it may be reading across "
            "tenants"
        )


def test_a_handler_that_reads_no_rows_needs_no_company() -> None:
    """get_role_model derives a competency framework from a job title.

    It is pure computation over ``shared.intelligence`` — no query, no tenant
    data, nothing to scope. Naming it here means adding a second database-free
    tool has to come past this test rather than inheriting the exemption.
    """
    db_free = {
        spec.name
        for spec, handler in registry._tools.values()
        if "_db(ctx)" not in inspect.getsource(handler)
        and "db.execute" not in inspect.getsource(handler)
    }
    assert db_free == {"get_role_model"}


def test_platform_aggregates_never_take_a_company_argument() -> None:
    """A cross-tenant tool that accepted a company id would let the model pick
    a tenant — which is precisely the decision the context is supposed to own."""
    for spec, _ in registry._tools.values():
        if spec.data_class != "platform_aggregate":
            continue
        params = set(spec.parameters.get("properties", {}))
        assert not (params & {"company_id", "company", "company_name", "tenant_id"}), (
            f"{spec.name} lets the model name a company"
        )


# ---------------------------------------------------------------------------
# Router: who is entitled to a cross-tenant console
# ---------------------------------------------------------------------------


def _user(*roles: str) -> MagicMock:
    user = MagicMock()
    user.user_id = "11111111-1111-1111-1111-111111111111"
    user.roles = list(roles)
    return user


@pytest.mark.parametrize("role", ["platform_owner", "admin"])
async def test_a_platform_role_on_a_company_account_is_refused(role: str) -> None:
    """The `admin` analytics tools carry no company filter at all.

    So the thing that entitles an account to them is having no company — and
    that has to be verified, not assumed. A company user who also picked up
    `admin` (one grant script away) previously resolved to company_id=None and
    read every tenant's score data.
    """
    db = MagicMock()
    db.scalar = AsyncMock(return_value="co-1")

    with pytest.raises(HTTPException) as exc:
        await _agent_context(_user(role), db)

    assert exc.value.status_code == 403


async def test_a_company_account_holding_admin_still_gets_its_own_console() -> None:
    """Most-privileged-wins picks the tenant console before `admin`, so the
    ordinary case — an HR manager who also holds the analytics role — keeps
    working and stays company-scoped."""
    db = MagicMock()
    db.scalar = AsyncMock(return_value="co-1")

    ctx = await _agent_context(_user("hr_manager", "admin"), db)

    assert ctx.role == "hr_manager"
    assert ctx.company_id == "co-1"


def test_the_router_and_the_matrix_name_the_same_cross_tenant_roles() -> None:
    """Two copies of "who is cross-tenant" would eventually disagree, and the
    disagreement would be an un-scoped toolset on a scoped account."""
    from shared.agents import CROSS_TENANT_ROLES

    from app.routers.agent import PLATFORM_ROLES

    assert PLATFORM_ROLES == CROSS_TENANT_ROLES


def test_the_super_admin_prompt_states_the_restriction() -> None:
    """The gate is structural, but the copilot also has to SAY so.

    Without this the super admin gets "I'm unable to find that tool", which
    reads as a bug and generates a support ticket, rather than "that is outside
    this console".
    """
    from shared.agents.roster import SUPER_ADMIN_COPILOT_PROMPT

    lowered = SUPER_ADMIN_COPILOT_PROMPT.lower()
    assert "no tool that returns an individual candidate" in lowered
    assert "hr manager" in lowered


def test_module_exports_no_stale_role_constant() -> None:
    """ADMIN_ROLES used to sit in __all__ gating nothing at all — a constant
    that reads like a permission and enforces none is worse than absent."""
    assert not hasattr(tools_module, "ADMIN_ROLES")
    assert not hasattr(tools_module, "HR_ROLES")


async def test_the_panel_is_closed_to_the_super_admin_console() -> None:
    """The panel returns resume + exam + coding + transcript in one response.

    It is the densest candidate record the platform produces, so leaving it
    open to super_admin would have made every candidate_pii tool gate cosmetic:
    the console could not call list_applicants, but could pull a fuller record
    than list_applicants returns.
    """
    from app.routers.agent import agent_panel

    db = MagicMock()
    db.scalar = AsyncMock(return_value="co-1")

    with pytest.raises(HTTPException) as exc:
        await agent_panel(
            uuid.UUID("22222222-2222-2222-2222-222222222222"),
            _user("super_admin"),
            db,
        )

    assert exc.value.status_code == 403
