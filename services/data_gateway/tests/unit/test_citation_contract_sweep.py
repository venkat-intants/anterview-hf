"""PH5 Wave 3 (E1) — the citation contract, swept across the real registry.

The rule (design §3.1(a)): a read tool that returns ROW-LEVEL data returns at
least one ``Citation``, every citation has a non-empty ``label`` and a
``kind`` from the closed vocabulary, and ``href`` is a relative app path or
``None``. This is the test that catches a tool citing only some of its rows
(``get_exam_question_stats``' old 5-row cap) or naming a record but citing an
aggregate (``get_hr_workload``) — the two the design calls out by name.

Each tool needs its own fake DB shape (they are not uniform), so this is a
table of (tool name, invoker) rather than one generic loop — a generic loop
over ``registry._tools`` that could not actually construct valid arguments or
rows for most of them would not be testing anything. ``get_decision_trace``
(the evidence graph) and the workflow-builder tools (`.mappings()`-shaped
rows, a different query helper) are exercised end-to-end instead, in
``tests/integration/smoke_ph5_e5_evidence_graph.py`` and
``tests/integration/smoke_group_d_copilot.py`` respectively — duplicating
their DB shape here with mocks would test the mock, not the tool.
"""

from __future__ import annotations

import typing
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from shared.agents import ToolContext
from shared.agents.schema import CitationKind

from app.agents import tools as tools_module
from app.agents.tools import registry
from app.metrics.compute import FunnelGroup, FunnelResult

VALID_KINDS = set(typing.get_args(CitationKind))


def _assert_contract(citations: list[Any], *, tool: str) -> None:
    assert citations, f"{tool} returned row-level data but no citations"
    for c in citations:
        assert c.label, f"{tool} produced a citation with an empty label"
        assert c.kind in VALID_KINDS, f"{tool} produced an unknown kind {c.kind!r}"
        assert c.href is None or (c.href.startswith("/") and not c.href.startswith("//")), (
            f"{tool} produced a non-relative href {c.href!r}"
        )


def _row(**fields: Any) -> MagicMock:
    row = MagicMock()
    for key, value in fields.items():
        setattr(row, key, value)
    return row


def _sequence_db(sequence: list[list[Any]]) -> MagicMock:
    calls = list(sequence)
    db = MagicMock()

    async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
        rows = calls.pop(0) if calls else []
        result = MagicMock()
        result.all.return_value = rows
        result.first.return_value = rows[0] if rows else None
        return result

    db.execute = _execute
    return db


def _ctx(db: Any, role: str, company: str | None = None) -> ToolContext:
    # A real UUID string, not "co-1": get_funnel_analytics and
    # get_company_overview both do ``uuid.UUID(ctx.company_id)`` before calling
    # compute_funnel.
    return ToolContext(
        actor_id="u-1", role=role, company_id=company or str(uuid.uuid4()),
        resources={"db": db},
    )


async def _fake_compute_funnel(
    _db: Any, *, company_id: Any, cohort: Any, filters: Any, group_by: str | None = None
) -> FunnelResult:
    metrics = {"applications": {"value": 10, "metric": "applications", "version": 1}}
    if group_by == "requisition":
        groups = [FunnelGroup(key="req-1", label="Welder", in_progress=0, metrics=metrics)]
    else:
        groups = [FunnelGroup(key=None, label="overall", in_progress=0, metrics=metrics)]
    return FunnelResult(
        registry_hash="fake", cohort=cohort, filters=filters, group_by=group_by, groups=groups,
    )


# ---------------------------------------------------------------------------
# Per-tool invokers — each returns the ToolResult
# ---------------------------------------------------------------------------


async def _invoke_list_applicants() -> Any:
    db = _sequence_db(
        [
            [
                _row(
                    id=uuid.uuid4(), enrolment_id=uuid.uuid4(), full_name="Asha K",
                    target_job_title="Welder", opening_title="Welder", target_level="mid",
                    ats_overall=80, ats_recommendation="strong", updated_at=datetime.now(tz=UTC),
                    best_exam_percent=70, exam_passed=True, interview_score=8, scorecard_id=None,
                    status="shortlisted", days_since_update=1,
                )
            ]
        ]
    )
    return await registry.invoke("list_applicants", {}, _ctx(db, "hr_manager"), call_id="c1")


async def _invoke_get_applicant_detail() -> Any:
    aid = uuid.uuid4()
    applicant_row = _row(
        id=aid, full_name="Asha K", target_job_title="Welder", target_level="mid",
        status="shortlisted", ats_overall=80, ats_breakdown={}, ats_strengths=[],
        ats_concerns=[], ats_recommendation="strong", ats_summary="Good fit",
        resume_text="5 years welding.", created_at=datetime.now(tz=UTC),
    )
    scorecard_row = _row(
        scorecard_id=uuid.uuid4(), scores={"technical": 8}, composite_score=8.0,
        summary="Strong", rationale="...", created_at=datetime.now(tz=UTC),
    )
    db = _sequence_db([[applicant_row], [], [scorecard_row]])
    return await registry.invoke(
        "get_applicant_detail", {"applicant_id": str(aid)}, _ctx(db, "hr_manager"), call_id="c1"
    )


async def _invoke_get_funnel_analytics(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(tools_module, "compute_funnel", _fake_compute_funnel)
    db = _sequence_db([])
    return await registry.invoke("get_funnel_analytics", {}, _ctx(db, "hr_manager"), call_id="c1")


async def _invoke_get_exam_question_stats() -> Any:
    exam_a, exam_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(exam_id=exam_a, title="Safety exam", question_id=uuid.uuid4(), position=1,
             attempts=10, correct=1),
        _row(exam_id=exam_a, title="Safety exam", question_id=uuid.uuid4(), position=2,
             attempts=10, correct=9),
        _row(exam_id=exam_b, title="Welding exam", question_id=uuid.uuid4(), position=1,
             attempts=8, correct=2),
    ]
    db = _sequence_db([rows])
    return await registry.invoke(
        "get_exam_question_stats", {}, _ctx(db, "hr_manager"), call_id="c1"
    )


async def _invoke_get_role_model() -> Any:
    return await registry.invoke(
        "get_role_model", {"job_title": "Welder", "level": "mid"},
        _ctx(None, "hr_manager"), call_id="c1",
    )


async def _invoke_get_platform_overview() -> Any:
    db = _sequence_db(
        [
            [_row(id=uuid.uuid4(), name="Acme", applicants=5)],
            [_row(role="hr_manager", n=2)],
            [_row(total=9, completed=7)],
        ]
    )
    return await registry.invoke(
        "get_platform_overview", {}, _ctx(db, "platform_owner"), call_id="c1"
    )


async def _invoke_get_score_distribution() -> Any:
    db = _sequence_db(
        [
            [_row(n=5, avg_composite=7.5, avg_communication=7, avg_technical=8,
                  avg_problem_solving=7, avg_confidence=7)],
            [_row(lang="en", n=5)],
        ]
    )
    return await registry.invoke("get_score_distribution", {}, _ctx(db, "admin"), call_id="c1")


async def _invoke_get_company_overview(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(tools_module, "compute_funnel", _fake_compute_funnel)
    db = _sequence_db(
        [
            [_row(role="hr_manager", n=2)],
            [_row(status="published", n=3)],
        ]
    )
    return await registry.invoke(
        "get_company_overview", {}, _ctx(db, "super_admin"), call_id="c1"
    )


async def _invoke_get_hr_workload() -> Any:
    db = _sequence_db(
        [
            [
                _row(id=uuid.uuid4(), full_name="Priya", email="priya@x.com", is_active=True,
                     applicants_added=10, interviews_invited=4, exams_created=1),
                _row(id=uuid.uuid4(), full_name="Ravi", email="ravi@x.com", is_active=True,
                     applicants_added=3, interviews_invited=1, exams_created=0),
            ]
        ]
    )
    return await registry.invoke("get_hr_workload", {}, _ctx(db, "super_admin"), call_id="c1")


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def test_list_applicants_satisfies_the_citation_contract() -> None:
    result = await _invoke_list_applicants()
    assert result.ok
    _assert_contract(result.citations, tool="list_applicants")


async def test_get_applicant_detail_satisfies_the_citation_contract() -> None:
    """Also the regression test for the scorecard citation's href — it used to
    have none at all (design §1.1)."""
    result = await _invoke_get_applicant_detail()
    assert result.ok
    _assert_contract(result.citations, tool="get_applicant_detail")
    by_kind = {c.kind: c for c in result.citations}
    assert "scorecard" in by_kind
    assert by_kind["scorecard"].href is not None, "the scorecard citation is unopenable"


async def test_get_funnel_analytics_satisfies_the_citation_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _invoke_get_funnel_analytics(monkeypatch)
    assert result.ok
    _assert_contract(result.citations, tool="get_funnel_analytics")


async def test_get_exam_question_stats_cites_every_exam_not_just_the_first_five() -> None:
    """The regression test for the design's named gap: the old code capped
    citations at ``rows[:5]``, so a query spanning more than five exams left
    questions 6+ uncited even though they were right there in the answer."""
    result = await _invoke_get_exam_question_stats()
    assert result.ok
    _assert_contract(result.citations, tool="get_exam_question_stats")
    # Two distinct exams in the fake rows -> two citations, deduped by exam,
    # not one per question and not capped before every exam is covered.
    assert len(result.citations) == 2


async def test_get_role_model_satisfies_the_citation_contract() -> None:
    result = await _invoke_get_role_model()
    assert result.ok
    _assert_contract(result.citations, tool="get_role_model")


async def test_get_platform_overview_satisfies_the_citation_contract() -> None:
    result = await _invoke_get_platform_overview()
    assert result.ok
    _assert_contract(result.citations, tool="get_platform_overview")


async def test_get_score_distribution_satisfies_the_citation_contract() -> None:
    result = await _invoke_get_score_distribution()
    assert result.ok
    _assert_contract(result.citations, tool="get_score_distribution")


async def test_get_company_overview_satisfies_the_citation_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _invoke_get_company_overview(monkeypatch)
    assert result.ok
    _assert_contract(result.citations, tool="get_company_overview")


async def test_get_hr_workload_satisfies_the_citation_contract_and_carries_a_locator() -> None:
    """The regression test for the design's other named gap: this tool names
    staff by full_name but used to cite a bare aggregate with nothing saying
    what it spans. There is no per-staff route to link to (§9 Q10) — the fix
    is a locator, not an invented citation kind."""
    result = await _invoke_get_hr_workload()
    assert result.ok
    _assert_contract(result.citations, tool="get_hr_workload")
    assert result.citations[0].kind == "analytics"
    assert result.citations[0].locator is not None
    assert "2" in result.citations[0].locator  # two HR managers in the fixture
