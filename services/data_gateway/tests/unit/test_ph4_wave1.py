"""PH4 Wave 1 — scorecards (A1), interview kits (A5), decision reason codes (O4).

The end-to-end behaviour is proven against real Postgres in
``tests/integration/smoke_ph4_scorecards.py`` and the database guarantees in
``tests/integration/test_ph4_scorecard_guarantees.py``. This file is the fast
layer: every validation branch, and the STRUCTURAL properties that would
otherwise only be caught by reading the code — which routes sit behind which
gate, and that nothing in this wave can move a candidate's status.
"""

from __future__ import annotations

import inspect
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _mapping_result(row: dict[str, Any] | None) -> MagicMock:
    res = MagicMock()
    res.mappings.return_value.first.return_value = row
    res.mappings.return_value.all.return_value = [row] if row else []
    return res


# ===========================================================================
# A1 — derived state
# ===========================================================================
@pytest.mark.parametrize(
    ("status", "due", "expected"),
    [
        ("assigned", NOW - timedelta(minutes=1), "late"),
        ("in_progress", NOW - timedelta(days=2), "late"),
        ("assigned", NOW + timedelta(days=1), "assigned"),
        ("in_progress", None, "in_progress"),
        # Submitted late is SUBMITTED — lateness is on the audit row, not held
        # against the evidence.
        ("submitted", NOW - timedelta(days=5), "submitted"),
        ("withdrawn", NOW - timedelta(days=5), "withdrawn"),
    ],
)
def test_late_is_derived_at_read_time(status: str, due: datetime | None, expected: str) -> None:
    from app.interviewer_scorecards import derived_state

    assert derived_state(status, due, NOW) == expected


# ===========================================================================
# A1 — payload validation
# ===========================================================================
CRIT = {"problem_solving", "system_design"}


@pytest.mark.parametrize(
    ("scores", "summary", "fragment"),
    [
        ([{"competency_id": "invented", "score": 3}], None, "does not assess"),
        ([{"competency_id": "problem_solving", "score": 3},
          {"competency_id": "problem_solving", "score": 4}], None, "once"),
        ([{"competency_id": "problem_solving", "score": True}], None, "whole numbers"),
        ([{"competency_id": "problem_solving", "score": 3.5}], None, "whole numbers"),
        ([{"competency_id": "problem_solving", "score": 0}], None, "whole numbers"),
        ([{"competency_id": "problem_solving", "score": 6}], None, "whole numbers"),
        ([{"competency_id": "problem_solving", "score": 3, "not_assessed": True}], None,
         "not both"),
        ([{"competency_id": "problem_solving", "evidence": "x" * 4001}], None, "Evidence"),
        ([], "x" * 4001, "summary"),
    ],
)
def test_a_bad_scorecard_payload_is_refused_in_words(
    scores: list[dict[str, Any]], summary: str | None, fragment: str
) -> None:
    from app.interviewer_scorecards import ScorecardError, _validate_payload

    with pytest.raises(ScorecardError) as exc:
        _validate_payload(CRIT, scores, summary)
    assert exc.value.status_code == 422
    assert fragment in exc.value.detail


def test_a_good_payload_passes() -> None:
    from app.interviewer_scorecards import _validate_payload

    _validate_payload(
        CRIT,
        [{"competency_id": "problem_solving", "score": 5, "evidence": "clear"},
         {"competency_id": "system_design", "not_assessed": True}],
        "Strong.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], [uuid.uuid4() for _ in range(11)]])
async def test_assignment_panel_size_is_bounded(ids: list[uuid.UUID]) -> None:
    from app.interviewer_scorecards import RequestMeta, ScorecardError, assign

    with pytest.raises(ScorecardError) as exc:
        await assign(
            AsyncMock(), company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(),
            round_id=uuid.uuid4(), interviewer_user_ids=ids, assigned_by=uuid.uuid4(),
            due_at=None, meta=RequestMeta(),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_application_is_not_found() -> None:
    from app.interviewer_scorecards import RequestMeta, ScorecardError, assign

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mapping_result(None))
    with pytest.raises(ScorecardError) as exc:
        await assign(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), round_id=uuid.uuid4(),
            interviewer_user_ids=[uuid.uuid4()], assigned_by=uuid.uuid4(), due_at=None,
            meta=RequestMeta(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["hired", "rejected"])
async def test_nothing_is_assigned_after_a_final_decision(status: str) -> None:
    from app.interviewer_scorecards import RequestMeta, ScorecardError, assign

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mapping_result({
        "id": uuid.uuid4(), "workflow_id": uuid.uuid4(), "status": status,
        "requisition_id": uuid.uuid4(), "full_name": "C", "job_title": "Eng",
    }))
    with pytest.raises(ScorecardError) as exc:
        await assign(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), round_id=uuid.uuid4(),
            interviewer_user_ids=[uuid.uuid4()], assigned_by=uuid.uuid4(), due_at=None,
            meta=RequestMeta(),
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_a_correction_needs_a_real_reason() -> None:
    from app.interviewer_scorecards import RequestMeta, ScorecardError, open_correction

    with pytest.raises(ScorecardError) as exc:
        await open_correction(
            AsyncMock(), scorecard_id=uuid.uuid4(), interviewer_user_id=uuid.uuid4(),
            company_id=uuid.uuid4(), reason="too short", meta=RequestMeta(),
        )
    assert exc.value.status_code == 422


# ===========================================================================
# A1 — structural: a scorecard never decides
# ===========================================================================
@pytest.mark.parametrize(
    "module", ["interviewer_scorecards.py", "interview_kits.py", "routers/interviewer.py",
               "routers/hr_scorecards.py"],
)
def test_scorecards_and_kits_cannot_move_a_candidate(module: str) -> None:
    """Evidence is gathered by many and decided by a person. If any of these
    ever learns to write an outcome, "a scorecard informs the decision" has
    quietly become "a scorecard makes it".

    Checked against CODE, not text. The first version grepped the source and
    failed on the module docstring — which names record_final_decision
    precisely to say scorecards never call it. A test that cannot tell an
    explanation from a call either cries wolf or gets loosened until it cannot.
    """
    import ast

    tree = ast.parse((APP / module).read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    identifiers: set[str] = set()
    sql: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.asname or node.name)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings):
            sql.append(node.value)

    for writer in ("record_transition", "record_result", "record_final_decision",
                   "record_round_move", "release_hold"):
        assert writer not in identifiers, f"{module} references {writer}"
    assert not any("UPDATE enrolments" in s for s in sql), f"{module} updates enrolments"


def test_no_agent_code_can_reach_scorecards() -> None:
    """Agents hold no write tools by construction; this makes sure nothing in the
    agent layer even imports the scorecard writers to wrap one."""
    for path in (APP / "agents").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "interviewer_scorecards" not in source, path.name
        assert "/interviewer/scorecards" not in source, path.name


# ===========================================================================
# Structural: which gate every new route sits behind
# ===========================================================================
def _calls(dependant: Any) -> set[Any]:
    found = {dependant.call} if dependant.call else set()
    for sub in dependant.dependencies:
        found |= _calls(sub)
    return found


def _routes() -> list[Any]:
    from app.main import app

    return [r for r in app.routes if hasattr(r, "dependant")]


def test_every_interviewer_route_goes_through_the_interviewer_gate() -> None:
    from app.dependencies import get_interviewer_company

    routes = [r for r in _routes() if r.path.startswith("/interviewer/")]
    assert len(routes) >= 8, [r.path for r in routes]
    for route in routes:
        assert get_interviewer_company in _calls(route.dependant), route.path


@pytest.mark.parametrize(
    "fragment",
    ["/hr/interviewers", "/hr/enrolments/{enrolment_id}/scorecards",
     "/hr/scorecards/{scorecard_id}/withdraw", "/hr/rounds/{round_id}/kit",
     "/hr/decision-reasons"],
)
def test_hr_routes_go_through_the_hr_gate(fragment: str) -> None:
    from app.dependencies import get_hr_company

    matched = [r for r in _routes() if r.path == fragment]
    assert matched, fragment
    for route in matched:
        assert get_hr_company in _calls(route.dependant), route.path


def test_team_and_taxonomy_routes_are_super_admin_only() -> None:
    from app.dependencies import get_super_admin_company
    from app.routers.admin_hr import get_company_admin_ctx

    for route in _routes():
        if route.path.startswith(("/admin/interviewers", "/admin/decision-reasons")):
            calls = _calls(route.dependant)
            assert get_company_admin_ctx in calls or get_super_admin_company in calls, route.path


def test_the_interviewer_gate_admits_interviewers_and_hr_managers_only() -> None:
    from app import dependencies

    source = inspect.getsource(dependencies.get_interviewer_company)
    assert 'require_role_password_ok("interviewer", "hr_manager")' in source


# ===========================================================================
# A5 — kit validation
# ===========================================================================
def test_kit_lists_are_trimmed_and_bounded() -> None:
    from app.interview_kits import ScorecardError, _clean_list

    assert _clean_list(["  a ", "", "  ", "b"], field="probes", name="X") == ["a", "b"]
    with pytest.raises(ScorecardError):
        _clean_list([f"p{i}" for i in range(11)], field="probes", name="X")
    with pytest.raises(ScorecardError):
        _clean_list(["x" * 301], field="probes", name="X")


def test_kit_text_is_bounded_and_blank_means_none() -> None:
    from app.interview_kits import ScorecardError, _clean_text

    assert _clean_text("   ", label="Instructions") is None
    assert _clean_text(None, label="Instructions") is None
    with pytest.raises(ScorecardError):
        _clean_text("x" * 8001, label="Instructions")


def test_no_kit_still_returns_the_frozen_rubric() -> None:
    from app.interview_kits import _shape

    shaped = _shape("Tech", None, [{
        "competency_id": "ps", "competency_name": "Problem Solving", "weight": 0.6,
        "anchors": {"low": "a", "mid": "b", "high": "c"}, "probes": ["Why?"],
    }])
    assert shaped["has_custom_kit"] is False
    crit = shaped["criteria"][0]
    assert crit["frozen_probes"] == ["Why?"]
    assert crit["what_to_evaluate"] == [] and crit["probes"] == []


def _kit_db(kind: str = "human_review", kit: dict[str, Any] | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def kit_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.interview_kits as ik

    state: dict[str, Any] = {"kind": "human_review", "kit": None}

    async def _round(_db: object, **_kw: object) -> dict[str, Any]:
        return {"id": uuid.uuid4(), "title": "Tech", "kind": state["kind"]}

    async def _criteria(_db: object, ids: list[uuid.UUID]) -> dict[str, Any]:
        return {str(ids[0]): [
            {"competency_id": "ps", "competency_name": "Problem Solving", "weight": 0.6},
            {"competency_id": "sd", "competency_name": "System Design", "weight": 0.4},
        ]}

    async def _load_kit(_db: object, _rid: uuid.UUID, _cid: uuid.UUID) -> dict[str, Any] | None:
        return state["kit"]

    monkeypatch.setattr(ik, "_round", _round)
    monkeypatch.setattr(ik, "load_criteria", _criteria)
    monkeypatch.setattr(ik, "_load_kit", _load_kit)
    return state


async def _update(criteria: list[Any], **kw: Any) -> dict[str, Any]:
    from app.interview_kits import update_kit
    from app.interviewer_scorecards import RequestMeta

    db = kw.pop("db", None) or _kit_db()
    return await update_kit(
        db, company_id=uuid.uuid4(), round_id=uuid.uuid4(), actor=uuid.uuid4(),
        instructions=kw.get("instructions"), interviewer_notes=None, criteria=criteria,
        meta=RequestMeta(),
    )


@pytest.mark.asyncio
async def test_a_kit_cannot_introduce_a_criterion(kit_env: dict[str, Any]) -> None:
    from app.interview_kits import KitCriterionIn, ScorecardError

    with pytest.raises(ScorecardError) as exc:
        await _update([KitCriterionIn("invented", [], [], ["probe"])])
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_a_duplicate_criterion_is_caught_even_when_the_first_is_empty(
    kit_env: dict[str, Any],
) -> None:
    """The bug this pins: duplicates used to be detected against the stored
    guidance, which only keeps non-empty entries."""
    from app.interview_kits import KitCriterionIn, ScorecardError

    with pytest.raises(ScorecardError) as exc:
        await _update([KitCriterionIn("ps", [], [], []), KitCriterionIn("ps", [], [], ["x"])])
    assert exc.value.status_code == 422 and "twice" in exc.value.detail


@pytest.mark.asyncio
async def test_a_kit_is_only_for_human_interview_rounds(kit_env: dict[str, Any]) -> None:
    from app.interview_kits import ScorecardError

    kit_env["kind"] = "mcq"
    with pytest.raises(ScorecardError) as exc:
        await _update([])
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_an_unchanged_kit_writes_nothing_and_audits_nothing(
    kit_env: dict[str, Any],
) -> None:
    from app.interview_kits import KitCriterionIn

    kit_env["kit"] = {
        "instructions": "45 minutes", "interviewer_notes": None,
        "guidance": {"ps": {"what_to_evaluate": [], "look_for": [], "probes": ["Why?"]}},
        "updated_at": NOW,
    }
    db = _kit_db()
    await _update([KitCriterionIn("ps", [], [], ["Why?"])], instructions="45 minutes", db=db)
    assert db.execute.await_count == 0
    assert not db.add.called


@pytest.mark.asyncio
async def test_a_kit_change_is_audited_naming_what_changed(kit_env: dict[str, Any]) -> None:
    from app.interview_kits import KitCriterionIn

    db = _kit_db()
    await _update([KitCriterionIn("sd", ["Trade-offs"], [], [])], instructions="New", db=db)
    audit = db.add.call_args.args[0]
    assert audit.action == "interview_kit.updated"
    assert "instructions" in audit.details["changed"]
    assert "System Design guidance" in audit.details["changed"]


@pytest.mark.asyncio
async def test_notes_are_bounded() -> None:
    from app.interview_kits import ScorecardError, save_notes

    with pytest.raises(ScorecardError):
        await save_notes(
            AsyncMock(), scorecard_id=uuid.uuid4(), interviewer_user_id=uuid.uuid4(),
            company_id=uuid.uuid4(), notes="x" * 20001,
        )


def test_private_notes_are_never_audited() -> None:
    from app.interview_kits import save_notes

    assert "AuditLog" not in inspect.getsource(save_notes)


# ===========================================================================
# O4 — decision reasons
# ===========================================================================
@pytest.mark.parametrize(
    ("label", "code"),
    [("Failed background check", "failed_background_check"),
     ("  Compensation / availability ", "compensation_availability"),
     ("2nd round no-show", "r_2nd_round_no_show")],
)
def test_codes_are_stable_machine_keys(label: str, code: str) -> None:
    from app.decision_reasons import _code_from_label

    assert _code_from_label(label) == code


def test_the_defaults_include_the_documented_categories() -> None:
    from app.decision_reasons import DEFAULTS

    labels = {d[1] for d in DEFAULTS}
    assert {"Skills / competency fit", "Experience", "Role fit", "Interview performance",
            "Compensation / availability", "Position closed", "Other"} <= labels
    other = next(d for d in DEFAULTS if d[0] == "other")
    assert other[3] is True, "'Other' must require an explanation"


def _reason_db(row: dict[str, Any] | None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_mapping_result(row))
    return db


def _reason(**over: Any) -> dict[str, Any]:
    base = {"code": "skills_fit", "label": "Skills / competency fit", "applies_to": "both",
            "requires_explanation": False, "active": True, "is_default": True}
    return {**base, **over}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "row", "decision", "reason", "fragment"),
    [
        (None, _reason(), "rejected", "Not a fit", "Choose"),
        ("made_up", None, "rejected", "Not a fit", "not available"),
        ("skills_fit", _reason(active=False), "rejected", "Not a fit", "not available"),
        ("position_closed", _reason(code="position_closed", label="Position closed",
                                    applies_to="rejected"), "hired", "Great", "not a reason"),
        ("other", _reason(code="other", label="Other", requires_explanation=True),
         "rejected", "Misc", "explanation"),
    ],
)
async def test_an_unusable_category_is_refused(
    code: str | None, row: dict[str, Any] | None, decision: str, reason: str, fragment: str
) -> None:
    from app.decision_reasons import ReasonError, resolve

    with pytest.raises(ReasonError) as exc:
        await resolve(_reason_db(row), company_id=uuid.uuid4(), code=code, decision=decision,
                      reason=reason)
    assert exc.value.status_code == 422
    assert fragment in exc.value.detail


@pytest.mark.asyncio
async def test_a_usable_category_resolves_with_its_label() -> None:
    from app.decision_reasons import resolve

    out = await resolve(_reason_db(_reason()), company_id=uuid.uuid4(), code="skills_fit",
                        decision="hired", reason="Strong fit")
    assert (out.code, out.label) == ("skills_fit", "Skills / competency fit")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "applies_to", "status"),
    [("x", "both", 422), ("Valid label", "sometimes", 422)],
)
async def test_creating_a_bad_category_is_refused(label: str, applies_to: str, status: int) -> None:
    from app.decision_reasons import ReasonError, create_reason

    with pytest.raises(ReasonError) as exc:
        await create_reason(AsyncMock(), company_id=uuid.uuid4(), actor=uuid.uuid4(),
                            label=label, applies_to=applies_to, requires_explanation=False)
    assert exc.value.status_code == status


@pytest.mark.asyncio
async def test_other_cannot_be_retired() -> None:
    from app.decision_reasons import ReasonError, update_reason

    db = _reason_db(_reason(code="other", label="Other", requires_explanation=True))
    with pytest.raises(ReasonError) as exc:
        await update_reason(db, company_id=uuid.uuid4(), actor=uuid.uuid4(), code="other",
                            active=False, label=None)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_updating_an_unknown_category_is_not_found() -> None:
    from app.decision_reasons import ReasonError, update_reason

    with pytest.raises(ReasonError) as exc:
        await update_reason(_reason_db(None), company_id=uuid.uuid4(), actor=uuid.uuid4(),
                            code="nope", active=False, label=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_rename_is_audited_with_before_and_after() -> None:
    from app.decision_reasons import update_reason

    db = _reason_db(_reason())
    db.scalar = AsyncMock(return_value=0)  # never used in a decision yet
    out = await update_reason(db, company_id=uuid.uuid4(), actor=uuid.uuid4(),
                              code="skills_fit", active=None, label="Skills match")
    assert out["label"] == "Skills match"
    audit = db.add.call_args.args[0]
    assert audit.details["label_before"] == "Skills / competency fit"
    assert audit.details["label_after"] == "Skills match"


@pytest.mark.parametrize("path", ["routers/hr_requisitions.py", "routers/hr_pipeline.py"])
def test_every_decision_path_captures_a_category(path: str) -> None:
    """A category captured on one decision path but not the other would make
    every "why do we reject?" figure quietly incomplete."""
    source = (APP / path).read_text(encoding="utf-8")
    assert "reason_code" in source, path
