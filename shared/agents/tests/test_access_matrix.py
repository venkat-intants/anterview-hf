"""The role/data-class access matrix — the restriction, checked structurally.

``ToolEffect`` guarantees an agent cannot WRITE. This module covers the second
guarantee: an agent cannot READ outside its console's remit. The two are
enforced the same way, and for the same reason — a rule a call site can bend is
not a rule, so both live in the type and are checked at construction.

What must stay true:

* Only ``hr_manager`` is ever offered a tool that returns a named candidate.
* ``super_admin`` runs one company's OPERATIONS and has no route to an
  individual applicant.
* ``platform_owner`` and ``admin`` cross tenants and are therefore
  aggregate-only.
* None of the above can be widened by passing a different argument. Widening
  requires editing ``DATA_CLASS_ROLES``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from shared.agents.registry import ToolContext, ToolOutput, ToolRegistry
from shared.agents.roster import build_agent
from shared.agents.runtime import run_agent
from shared.agents.schema import (
    CROSS_TENANT_ROLES,
    DATA_CLASS_ROLES,
    ToolSpec,
)

OBJ_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
ALL_ROLES = ("hr_manager", "super_admin", "platform_owner", "admin")


def _spec(**kw: Any) -> ToolSpec:
    base: dict[str, Any] = {
        "name": "some_tool",
        "description": "x",
        "parameters": OBJ_SCHEMA,
        "effect": "read",
        "data_class": "company_scoped",
        "allowed_roles": ("hr_manager",),
    }
    base.update(kw)
    return ToolSpec(**base)


# ---------------------------------------------------------------------------
# The matrix itself
# ---------------------------------------------------------------------------


def test_only_the_hr_console_may_read_a_named_candidate() -> None:
    """The single most important line in the matrix.

    If this widens, a company super admin — or worse, a cross-tenant platform
    role — gains a route to resumes and interview transcripts through a chat
    box, which is precisely the exposure DPDP data-minimisation is about.
    """
    assert DATA_CLASS_ROLES["candidate_pii"] == frozenset({"hr_manager"})


def test_no_role_holds_both_a_tenant_and_a_cross_tenant_reach() -> None:
    """A role that could read one company AND across companies is a bridge.

    Whichever direction it is used in, the tenancy boundary stops meaning
    anything — so the two sets must stay disjoint.
    """
    tenant_roles = (
        DATA_CLASS_ROLES["candidate_pii"]
        | DATA_CLASS_ROLES["company_scoped"]
        | DATA_CLASS_ROLES["company_staff"]
    )
    assert not (tenant_roles & DATA_CLASS_ROLES["platform_aggregate"])
    assert not (tenant_roles & CROSS_TENANT_ROLES)


def test_cross_tenant_roles_agree_with_the_platform_data_class() -> None:
    """The router reads CROSS_TENANT_ROLES to decide who may carry a NULL
    company_id; the matrix decides who gets un-scoped tools. Drift between them
    would hand a company-scoped account a cross-tenant toolset."""
    assert DATA_CLASS_ROLES["platform_aggregate"] == CROSS_TENANT_ROLES


def test_every_console_appears_somewhere_in_the_matrix() -> None:
    """A console absent from every data class has no tools and can only chat."""
    covered: set[str] = set()
    for roles in DATA_CLASS_ROLES.values():
        covered |= roles
    assert covered == set(ALL_ROLES)


# ---------------------------------------------------------------------------
# Construction-time enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["super_admin", "platform_owner", "admin"])
def test_a_candidate_tool_cannot_name_a_non_hr_console(role: str) -> None:
    """Widening is refused where it is written, not where it is used.

    ToolSpec validates on construction, so a mis-scoped tool raises at import
    of the tools module and the service fails to start — it never reaches a
    request.
    """
    with pytest.raises(ValidationError, match="does not permit"):
        _spec(data_class="candidate_pii", allowed_roles=("hr_manager", role))


def test_a_staff_tool_cannot_be_handed_to_an_hr_manager() -> None:
    """Staff records are the super admin's remit. An HR manager auditing their
    own peers' throughput is a different product with different consent."""
    with pytest.raises(ValidationError, match="does not permit"):
        _spec(data_class="company_staff", allowed_roles=("hr_manager",))


def test_a_platform_aggregate_cannot_be_handed_to_a_tenant_role() -> None:
    with pytest.raises(ValidationError, match="does not permit"):
        _spec(data_class="platform_aggregate", allowed_roles=("super_admin",))


def test_allowed_roles_cannot_be_empty() -> None:
    """Empty used to mean "every role" — the worst possible default for a
    surface that reads candidate data, and one a new tool inherits by
    forgetting rather than by deciding."""
    with pytest.raises(ValidationError):
        _spec(allowed_roles=())


def test_data_class_has_no_default() -> None:
    """A default would be inherited silently by the next tool someone adds.

    The safe-looking default ("aggregate") is exactly the one a tool that
    actually returns PII would pick up by omission.
    """
    with pytest.raises(ValidationError):
        # The suppression is the POINT of this test, not an oversight: mypy is
        # right that data_class is missing. We omit it anyway to prove the
        # RUNTIME also refuses, because a value arriving from JSON never met
        # the type checker.
        ToolSpec(  # type: ignore[call-arg]
            name="undeclared",
            description="x",
            parameters=OBJ_SCHEMA,
            effect="read",
            allowed_roles=("hr_manager",),
        )


def test_an_unknown_data_class_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _spec(data_class="whatever_i_like")


# ---------------------------------------------------------------------------
# Runtime enforcement — listing is not the gate
# ---------------------------------------------------------------------------


def _registry_with_a_candidate_tool() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(
        name="read_candidate",
        description="candidate detail",
        parameters=OBJ_SCHEMA,
        data_class="candidate_pii",
        allowed_roles=("hr_manager",),
    )
    async def _read(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        return ToolOutput(data={"name": "Asha", "resume": "..."})

    return reg


async def test_a_denied_role_is_refused_even_when_it_names_the_tool() -> None:
    """Hiding a tool from the tool LIST is presentation, not security.

    A model can emit any function name it likes — from its own training, from a
    replayed history, or because text inside a resume told it to. The gate that
    matters is the one in invoke().
    """
    reg = _registry_with_a_candidate_tool()
    ctx = ToolContext(actor_id="u-1", role="super_admin", company_id="co-1")

    assert not [s for s in reg.specs_for("super_admin") if s.name == "read_candidate"]

    result = await reg.invoke("read_candidate", {}, ctx, call_id="c1")

    assert result.ok is False
    assert result.error == "permission denied"
    # The refusal must not leak what it was protecting.
    assert "Asha" not in result.content
    assert "resume" not in result.content


async def test_a_permitted_role_still_gets_through() -> None:
    """The gate has to be a gate, not a wall."""
    reg = _registry_with_a_candidate_tool()
    ctx = ToolContext(actor_id="u-1", role="hr_manager", company_id="co-1")
    result = await reg.invoke("read_candidate", {}, ctx, call_id="c1")
    assert result.ok is True
    assert "Asha" in result.content


async def test_a_prompt_written_for_one_console_cannot_run_as_another() -> None:
    """The persona and the toolset are chosen independently.

    Nothing else forces them to agree, so a caller that assembled them from
    different places would run one console's instructions against another's
    tools. Refuse rather than pick a winner.
    """
    reg = _registry_with_a_candidate_tool()
    spec = build_agent("hr_manager", reg)
    ctx = ToolContext(actor_id="u-1", role="super_admin", company_id="co-1")

    called = False

    async def _llm(*args: Any, **kw: Any) -> Any:  # pragma: no cover - must not run
        nonlocal called
        called = True
        raise AssertionError("the model must not be reached on a role mismatch")

    run = await run_agent(spec, ctx, "who is the strongest candidate?", llm=_llm)

    assert run.stop_reason == "role_mismatch"
    assert called is False


# ---------------------------------------------------------------------------
# PH5 Wave 3 (E1) — a citation cannot hand a caller a record their role could
# not open, independently of whether the TOOL itself was permitted.
#
# This is the test the design calls out as one that FAILS without §3.1(5):
# a ``company_scoped`` tool is offered to both ``hr_manager`` and
# ``super_admin``, so nothing about ``ToolSpec.allowed_roles`` stops it handing
# a super admin a citation kind — ``applicant`` — that ``DATA_CLASS_ROLES``
# says only ``hr_manager`` may ever be shown a named candidate through.
# ---------------------------------------------------------------------------


def _registry_with_an_overreaching_tool() -> ToolRegistry:
    """A ``company_scoped`` tool, open to both consoles, whose handler returns
    a ``candidate_pii``-class citation. Nothing in ``ToolSpec`` catches this —
    the tool's OWN data class is honestly declared and genuinely aggregate —
    it just also happens to cite a named record, which only ``invoke``'s
    citation filter is positioned to catch.
    """
    reg = ToolRegistry()

    @reg.tool(
        name="funnel_with_an_example",
        description="an aggregate that names one example candidate",
        parameters=OBJ_SCHEMA,
        data_class="company_scoped",
        allowed_roles=("hr_manager", "super_admin"),
    )
    async def _handler(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        from shared.agents.schema import Citation

        return ToolOutput(
            data={"top_candidate": "Asha"},
            citations=[Citation(kind="applicant", id="a1", label="Asha")],
        )

    return reg


async def test_a_citation_outside_the_callers_remit_is_dropped_not_the_call() -> None:
    """The FAILING-TODAY case: invoked as super_admin, the citation must not
    survive — even though the TOOL itself was one super_admin may call."""
    reg = _registry_with_an_overreaching_tool()
    ctx = ToolContext(actor_id="u-1", role="super_admin", company_id="co-1")

    result = await reg.invoke("funnel_with_an_example", {}, ctx, call_id="c1")

    # Dropping the citation must not cost the call (E1-11) — the aggregate
    # data itself was genuinely permitted and still comes back.
    assert result.ok is True
    assert "Asha" in result.content  # the DATA is untouched; only the citation is not
    assert result.citations == []


async def test_the_same_tool_keeps_the_citation_for_the_role_it_belongs_to() -> None:
    reg = _registry_with_an_overreaching_tool()
    ctx = ToolContext(actor_id="u-1", role="hr_manager", company_id="co-1")

    result = await reg.invoke("funnel_with_an_example", {}, ctx, call_id="c1")

    assert result.ok is True
    assert len(result.citations) == 1
    assert result.citations[0].kind == "applicant"


async def test_citation_overreach_is_logged_at_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared.agents import registry as registry_module

    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        registry_module.log, "error", lambda event, **kw: captured.append((event, kw))
    )

    reg = _registry_with_an_overreaching_tool()
    ctx = ToolContext(actor_id="u-1", role="super_admin", company_id="co-1")
    await reg.invoke("funnel_with_an_example", {}, ctx, call_id="c1")

    assert len(captured) == 1
    event, fields = captured[0]
    assert event == "agents.tool.citation_overreach"
    assert fields["role"] == "super_admin"
    assert fields["kind"] == "applicant"
    # NEVER the label — it can be a candidate's name.
    assert "Asha" not in str(fields)


async def test_citation_overreach_raises_under_the_test_flag() -> None:
    """Production always drops (the two tests above); this flag exists only so
    a test can assert on the detection itself rather than only on the length
    of what came back. Never read outside this module's registry logic, and
    reset in a ``finally`` so one test cannot leave the whole suite strict."""
    from shared.agents import registry as registry_module
    from shared.agents.registry import CitationOverreachError

    reg = _registry_with_an_overreaching_tool()
    ctx = ToolContext(actor_id="u-1", role="super_admin", company_id="co-1")

    registry_module.RAISE_ON_CITATION_OVERREACH = True
    try:
        with pytest.raises(CitationOverreachError):
            await reg.invoke("funnel_with_an_example", {}, ctx, call_id="c1")
    finally:
        registry_module.RAISE_ON_CITATION_OVERREACH = False


async def test_an_overreaching_citation_on_a_proposal_is_also_dropped() -> None:
    """The same rule applies to a citation riding on a ``Proposal`` — a draft
    tool's review panel must not smuggle a named candidate past the matrix
    either, even though nothing today actually does this (defence in depth)."""
    from shared.agents.schema import Citation, CommitSpec, Proposal

    reg = ToolRegistry()

    @reg.tool(
        name="draft_with_an_example",
        description="x",
        parameters=OBJ_SCHEMA,
        effect="draft",
        data_class="company_scoped",
        allowed_roles=("hr_manager", "super_admin"),
    )
    async def _handler(args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        return ToolOutput(
            data={"drafted": True},
            proposals=[
                Proposal(
                    kind="note",
                    title="x",
                    summary="x",
                    commit=CommitSpec(method="POST", path="/hr/x", label="x"),
                    citations=[Citation(kind="applicant", id="a1", label="Asha")],
                )
            ],
        )

    ctx = ToolContext(actor_id="u-1", role="super_admin", company_id="co-1")
    result = await reg.invoke("draft_with_an_example", {}, ctx, call_id="c1")

    assert result.ok is True
    assert len(result.proposals) == 1
    assert result.proposals[0].citations == []
