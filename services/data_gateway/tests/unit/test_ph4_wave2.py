"""PH4 Wave 2 — workflow review (O6), branching (O3), dry run (O2), stage SLAs (O1).

The fast layer: every rule that can be checked without a database, and the
STRUCTURAL properties that would otherwise only be caught by reading the code —
that the dry run and the SLA/exception code cannot write a candidate outcome,
send an email or move a stage. The database halves (the publish gate, the
review lock, append-only history) are in
``tests/integration/test_ph4_wave2_guarantees.py``; end to end in
``tests/integration/smoke_ph4_wave2.py``.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _round(rid: str, pos: int, kind: str = "mcq", **over: Any) -> dict[str, Any]:
    base = {"id": rid, "position": pos, "title": f"R{pos}", "kind": kind,
            "pass_threshold": 60.0 if kind != "human_review" else None,
            "time_limit_seconds": None, "deadline_days": 7,
            "on_pass_next_round_id": None, "exam_round_id": "ex" if kind in ("mcq", "coding") else None,
            "on_fail_next_round_id": None, "fast_track_min_percent": None,
            "on_fast_track_next_round_id": None}
    return {**base, **over}


def _chain(*rounds: dict[str, Any]) -> list[dict[str, Any]]:
    """Link the pass branch in order, as add_round does."""
    out = [dict(r) for r in rounds]
    for a, b in zip(out, out[1:], strict=False):
        a["on_pass_next_round_id"] = a.get("on_pass_next_round_id") or b["id"]
    return out


def _calls(module: Any) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
        if isinstance(n, ast.ImportFrom):
            names.update(a.name for a in n.names)
    return names


def _sql_strings(module: Any) -> str:
    tree = ast.parse(inspect.getsource(module))
    return " ".join(
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


# ===========================================================================
# O3 — routing: the one function a result goes through
# ===========================================================================
def test_a_pass_goes_down_the_chain_or_to_the_final_decision() -> None:
    from app.workflows import route_after_result

    r = _round("a", 0, on_pass_next_round_id="b")
    assert route_after_result(r, passed=True, percent=70).next_round_id == "b"
    last = _round("z", 3)
    route = route_after_result(last, passed=True, percent=70)
    assert (route.kind, route.branch) == ("complete", "pass")


def test_a_high_score_takes_the_fast_track_and_only_at_or_above_its_bar() -> None:
    from app.workflows import route_after_result

    r = _round("a", 0, on_pass_next_round_id="b", fast_track_min_percent=85,
               on_fast_track_next_round_id="d")
    assert route_after_result(r, passed=True, percent=85).next_round_id == "d"
    assert route_after_result(r, passed=True, percent=84.9).next_round_id == "b"
    assert route_after_result(r, passed=True, percent=None).next_round_id == "b"


def test_below_the_threshold_holds_unless_a_fail_branch_routes() -> None:
    from app.workflows import route_after_result

    held = route_after_result(_round("a", 0), passed=False, percent=10)
    assert (held.kind, held.branch) == ("hold", "fail")
    routed = route_after_result(_round("a", 0, on_fail_next_round_id="rv"), passed=False, percent=10)
    assert (routed.kind, routed.next_round_id) == ("advance", "rv")


def test_no_route_can_ever_be_a_rejection() -> None:
    """D-05. Every combination of result and branch lands in one of three places."""
    from app.workflows import route_after_result

    r = _round("a", 0, on_pass_next_round_id="b", on_fail_next_round_id="c",
               fast_track_min_percent=90, on_fast_track_next_round_id="d")
    kinds = {
        route_after_result(r, passed=p, percent=pc).kind
        for p in (True, False) for pc in (None, 0, 59, 60, 89, 90, 100)
    }
    kinds |= {route_after_result(_round("x", 0), passed=p, percent=pc).kind
              for p in (True, False) for pc in (None, 50)}
    assert kinds <= {"advance", "complete", "hold"}
    src = inspect.getsource(__import__("app.workflows", fromlist=["x"]).route_after_result)
    assert "reject" not in src.lower()


def test_the_runner_and_the_simulation_share_the_routing() -> None:
    import app.workflow_runner as runner
    import app.workflow_simulation as sim

    assert "route_after_result" in _calls(runner)
    assert "route_after_result" in _calls(sim)


# ===========================================================================
# O3 — validation across every branch
# ===========================================================================
def test_a_loop_through_a_fail_branch_is_refused() -> None:
    from app.workflows import validate_chain

    rounds = _chain(_round("a", 0), _round("b", 1, on_fail_next_round_id="a"))
    errors = validate_chain(rounds)
    assert any("loop" in e for e in errors), errors


def test_a_round_reached_only_by_a_branch_is_reachable() -> None:
    from app.workflows import validate_chain

    a = _round("a", 0, on_pass_next_round_id="b", on_fail_next_round_id="c")
    b = _round("b", 1)
    c = _round("c", 2, kind="human_review")
    assert validate_chain([a, b, c]) == []


def test_a_round_no_branch_reaches_is_unreachable() -> None:
    from app.workflows import validate_chain

    a = _round("a", 0)
    b = _round("b", 1)
    errors = validate_chain([a, b])
    assert any("Unreachable" in e and "R1" in e for e in errors), errors


def test_a_loop_and_an_unrelated_orphan_are_both_reported() -> None:
    """One validation pass names every problem, so HR fixes them in one go."""
    from app.workflows import validate_chain

    a = _round("a", 0, on_pass_next_round_id="b")
    b = _round("b", 1, on_fail_next_round_id="a")
    orphan = _round("c", 2)
    errors = validate_chain([a, b, orphan])
    assert any("loop" in e for e in errors), errors
    assert any("Unreachable" in e and "R2" in e for e in errors), errors


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"on_fail_next_round_id": "nowhere"}, "not in this workflow"),
        ({"on_fast_track_next_round_id": "b", "fast_track_min_percent": 50}, "must be above"),
        ({"on_fast_track_next_round_id": "b", "fast_track_min_percent": None}, "both a score"),
    ],
)
def test_bad_branches_are_named_in_words(over: dict[str, Any], fragment: str) -> None:
    from app.workflows import validate_chain

    rounds = _chain(_round("a", 0, **over), _round("b", 1))
    errors = validate_chain(rounds)
    assert any(fragment in e for e in errors), errors


def test_a_human_review_cannot_fast_track() -> None:
    from app.workflows import branch_errors

    r = _round("a", 0, kind="human_review", on_fast_track_next_round_id="b",
               fast_track_min_percent=90)
    assert any("no score" in e for e in branch_errors([r, _round("b", 1)]))


def test_a_linear_workflow_validates_exactly_as_before() -> None:
    """Existing non-branching workflows keep working unchanged."""
    from app.workflows import validate_chain

    assert validate_chain(_chain(_round("a", 0), _round("b", 1), _round("c", 2))) == []


@pytest.mark.asyncio
async def test_a_branch_cannot_point_outside_its_workflow() -> None:
    from app.workflows import WorkflowError, update_round

    db = AsyncMock()
    res = MagicMock()
    res.first.return_value = ("draft", "draft")
    db.execute = AsyncMock(return_value=res)
    db.scalar = AsyncMock(return_value=None)  # target not found in this workflow
    with pytest.raises(WorkflowError, match="another round of this workflow"):
        await update_round(db, workflow_id=uuid.uuid4(), round_id=uuid.uuid4(),
                           fields={"on_fail_next_round_id": uuid.uuid4()})


@pytest.mark.asyncio
async def test_a_branch_cannot_point_at_its_own_round() -> None:
    from app.workflows import WorkflowError, update_round

    db = AsyncMock()
    res = MagicMock()
    res.first.return_value = ("draft", "draft")
    db.execute = AsyncMock(return_value=res)
    rid = uuid.uuid4()
    with pytest.raises(WorkflowError, match="its own round"):
        await update_round(db, workflow_id=uuid.uuid4(), round_id=rid,
                           fields={"on_fail_next_round_id": rid})


# ===========================================================================
# O2 — the dry run
# ===========================================================================
def test_every_branch_is_exercised_by_some_scenario() -> None:
    from app.workflow_simulation import build_scenarios

    a = _round("a", 0, on_pass_next_round_id="b", on_fail_next_round_id="rv",
               fast_track_min_percent=90, on_fast_track_next_round_id="c")
    b = _round("b", 1, on_pass_next_round_id="c")
    c = _round("c", 2, on_pass_next_round_id="rv")
    rv = _round("rv", 3, kind="human_review")
    scenarios = build_scenarios([a, b, c, rv])
    taken = {(s["round_id"], s["branch"]) for sc in scenarios for s in sc["steps"]}
    assert {("a", "pass"), ("a", "fast_track"), ("a", "fail"), ("b", "fail"), ("rv", "fail"),
            ("rv", "pass")} <= taken
    ends = {sc["end"] for sc in scenarios}
    assert ends <= {"decision", "held"}
    assert all(sc["id"].startswith("SIM-") for sc in scenarios)


def test_scenario_count_stays_bounded() -> None:
    from app.workflow_simulation import MAX_SCENARIOS, build_scenarios

    rounds = _chain(*[_round(f"r{i}", i, on_fail_next_round_id=None) for i in range(12)])
    assert len(build_scenarios(rounds)) <= MAX_SCENARIOS


def test_a_loop_is_reported_as_a_path_that_cannot_finish() -> None:
    from app.workflow_simulation import evaluate

    rounds = _chain(_round("a", 0), _round("b", 1, on_fail_next_round_id="a"))
    ctx = {"workflow": {"auto_advance_rounds": True}, "rounds": rounds, "criteria": {},
           "readiness": {"ex": {"status": "published", "questions": 3, "exam_id": "e"}},
           "kits": set(), "stage_settings": {}, "interviewers": 1}
    out = evaluate(ctx, None)
    assert out["status"] == "failed"
    messages = " ".join(f["message"] for f in out["workflow_findings"])
    assert "loop" in messages


def _ctx(rounds: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    base = {"workflow": {"auto_advance_rounds": True}, "rounds": rounds, "criteria": {},
            "readiness": {"ex": {"status": "published", "questions": 3, "exam_id": "e"}},
            "kits": set(), "stage_settings": {}, "interviewers": 1}
    return {**base, **over}


def test_errors_and_warnings_are_told_apart_and_attributed_to_rounds() -> None:
    from app.workflow_simulation import evaluate

    ai = _round("ai", 0, kind="ai_interview", on_pass_next_round_id="hr")
    hr = _round("hr", 1, kind="human_review")
    out = evaluate(_ctx([ai, hr]), None)
    by_id = {r["round_id"]: r for r in out["rounds"]}
    assert by_id["ai"]["state"] == "error"  # an AI interview with nothing to assess
    assert by_id["hr"]["state"] == "warning"  # no criteria, no kit
    assert out["status"] == "failed" and out["errors"] >= 1 and out["warnings"] >= 1


def test_a_clean_workflow_passes() -> None:
    from app.workflow_simulation import evaluate

    a = _round("a", 0, on_pass_next_round_id="hr")
    hr = _round("hr", 1, kind="human_review")
    out = evaluate(
        _ctx([a, hr], criteria={"hr": [{"competency_id": "x"}]}, kits={"hr"}), None
    )
    assert out["status"] == "passed", out


def test_a_draft_exam_is_an_execution_blocking_error() -> None:
    from app.workflow_simulation import evaluate

    out = evaluate(_ctx([_round("a", 0)], readiness={"ex": {"status": "draft", "questions": 3,
                                                           "exam_id": "e"}}), None)
    assert out["rounds"][0]["state"] == "error"


def test_the_dry_run_cannot_write_a_candidate_record_or_send_anything() -> None:
    """Not a flag — the module simply never calls anything that does."""
    import app.workflow_simulation as sim

    forbidden = {"record_transition", "record_round_move", "record_result", "enqueue_email",
                 "create_notification", "_assign_round", "enrol_applicant", "on_shortlisted",
                 "record_final_decision", "advance_applicant_to_interview", "publish"}
    assert not (_calls(sim) & forbidden)
    sql = _sql_strings(sim).upper()
    for table in ("ENROLMENTS", "APPLICANTS", "STAGE_TRANSITIONS", "EMAIL_EVENTS",
                  "NOTIFICATIONS", "EXAM_ASSIGNMENTS", "INTERVIEW_INVITES", "ROUND_RESULTS"):
        assert f"INSERT INTO {table}" not in sql and f"UPDATE {table}" not in sql, table
    inserts = set(sql.split("INSERT INTO ")[1:])
    assert all(w.split()[0].startswith("WORKFLOW_SIMULATIONS") for w in inserts)


@pytest.mark.asyncio
async def test_a_dry_run_records_its_result_and_an_audit_row() -> None:
    import app.workflow_simulation as sim

    wf_id = uuid.uuid4()
    rounds = [_round(str(uuid.uuid4()), 0, kind="human_review")]

    async def _ctx_fn(_db: object, **_kw: object) -> dict[str, Any]:
        return _ctx(rounds, workflow={"auto_advance_rounds": True, "version": 3})

    async def _fp(_db: object, _w: object) -> str:
        return "fp"

    db = AsyncMock()
    db.add = MagicMock()
    orig_ctx, orig_fp = sim._context, sim.workflow_fingerprint
    sim._context, sim.workflow_fingerprint = _ctx_fn, _fp  # type: ignore[assignment]
    try:
        out = await sim.simulate(db, company_id=uuid.uuid4(), workflow_id=wf_id,
                                 actor=uuid.uuid4())
    finally:
        sim._context, sim.workflow_fingerprint = orig_ctx, orig_fp  # type: ignore[assignment]
    sql = str(db.execute.await_args.args[0])
    assert "INSERT INTO workflow_simulations" in sql
    audit = db.add.call_args.args[0]
    assert audit.action == "workflow.simulated"
    assert audit.details["status"] == out["status"] and audit.details["version"] == 3


# ===========================================================================
# O6 — review
# ===========================================================================
def _wf(**over: Any) -> dict[str, Any]:
    base = {"id": uuid.uuid4(), "company_id": uuid.uuid4(), "requisition_id": uuid.uuid4(),
            "version": 2, "status": "draft", "review_status": "draft",
            "submitted_by_user_id": None, "review_fingerprint": None,
            "created_by_user_id": uuid.uuid4(), "opening_title": "Engineer"}
    return {**base, **over}


def _review_db(row: dict[str, Any]) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=res)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["in_review", "approved"])
async def test_only_a_draft_can_be_submitted(state: str) -> None:
    from app.workflow_review import ReviewError, submit

    with pytest.raises(ReviewError) as exc:
        await submit(_review_db(_wf(review_status=state)), company_id=uuid.uuid4(),
                     workflow_id=uuid.uuid4(), actor=uuid.uuid4(), note=None,
                     profile_competencies=None)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_an_author_cannot_approve_their_own_version() -> None:
    from app.workflow_review import ReviewError, approve

    me = uuid.uuid4()
    with pytest.raises(ReviewError) as exc:
        await approve(_review_db(_wf(review_status="in_review", submitted_by_user_id=me)),
                      company_id=uuid.uuid4(), workflow_id=uuid.uuid4(), reviewer=me,
                      note=None, profile_competencies=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_approval_is_of_the_submitted_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workflow_review as rv

    async def _fp(_db: object, _w: object) -> str:
        return "changed"

    monkeypatch.setattr(rv, "workflow_fingerprint", _fp)
    with pytest.raises(rv.ReviewError) as exc:
        await rv.approve(_review_db(_wf(review_status="in_review", submitted_by_user_id=uuid.uuid4(),
                                        review_fingerprint="submitted")),
                         company_id=uuid.uuid4(), workflow_id=uuid.uuid4(),
                         reviewer=uuid.uuid4(), note=None, profile_competencies=None)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_asking_for_changes_needs_a_real_note() -> None:
    from app.workflow_review import ReviewError, request_changes

    with pytest.raises(ReviewError) as exc:
        await request_changes(_review_db(_wf(review_status="in_review")),
                              company_id=uuid.uuid4(), workflow_id=uuid.uuid4(),
                              reviewer=uuid.uuid4(), note="no")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(("fn", "state"), [("withdraw", "draft"), ("reopen", "in_review")])
async def test_withdraw_and_reopen_apply_to_one_state_each(fn: str, state: str) -> None:
    import app.workflow_review as rv

    with pytest.raises(rv.ReviewError) as exc:
        await getattr(rv, fn)(_review_db(_wf(review_status=state)), company_id=uuid.uuid4(),
                              workflow_id=uuid.uuid4(), actor=uuid.uuid4())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["in_review", "approved"])
async def test_a_version_under_review_cannot_be_edited(state: str) -> None:
    from app.workflows import WorkflowError, _assert_draft

    db = AsyncMock()
    res = MagicMock()
    res.first.return_value = ("draft", state)
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(WorkflowError):
        await _assert_draft(db, uuid.uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "in_review", "changes_requested"])
async def test_only_an_approved_version_publishes(state: str) -> None:
    from app.workflows import WorkflowError, publish

    db = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = {
        "status": "draft", "review_status": state, "requisition_id": uuid.uuid4(),
    }
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(WorkflowError, match="not been approved"):
        await publish(db, company_id=uuid.uuid4(), workflow_id=uuid.uuid4())


def test_review_actions_are_audited_and_recorded_as_events() -> None:
    import app.workflow_review as rv

    src = inspect.getsource(rv._record)
    assert "INSERT INTO workflow_review_events" in src and "AuditLog(" in src
    for fn in (rv.submit, rv.withdraw, rv.reopen, rv.approve, rv.request_changes):
        assert "_record(" in inspect.getsource(fn), fn.__name__


def test_the_super_admin_approves_and_hr_submits() -> None:
    """D4-2, and that no second route to 'published' or 'approved' exists."""
    source = (APP / "routers/workflow_ops.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gates: dict[str, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
            gates[fn.name] = ann
    assert "SuperAdminCtxDep" in gates["post_approve"]
    assert "SuperAdminCtxDep" in gates["post_request_changes"]
    assert "HrCtxDep" in gates["post_submit_review"]
    app_src = "\n".join(p.read_text(encoding="utf-8") for p in APP.rglob("*.py"))
    assert app_src.count("review_status = 'approved'") == 1  # only approve() sets it


# ===========================================================================
# O1 — SLAs and exceptions
# ===========================================================================
@pytest.mark.parametrize(
    ("hours_in", "state"), [(1, "on_track"), (7.5, "due_soon"), (10.1, "overdue")],
)
def test_sla_state_is_derived_from_the_stage_entry(hours_in: float, state: str) -> None:
    from app.stage_sla import sla_state

    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    out = sla_state(now - timedelta(hours=hours_in), 10, now)
    assert out is not None and out["state"] == state


def test_no_sla_means_no_sla_state() -> None:
    from app.stage_sla import sla_state

    assert sla_state(datetime.now(tz=UTC), None) is None


def test_exceptions_and_slas_cannot_move_a_candidate() -> None:
    import app.stage_sla as sla

    assert not (_calls(sla) & {"record_transition", "record_round_move", "record_result",
                               "record_final_decision", "_hold", "release_hold"})
    sql = _sql_strings(sla).upper()
    assert "UPDATE ENROLMENTS" not in sql and "INSERT INTO STAGE_TRANSITIONS" not in sql


def test_the_sla_clock_ignores_notes_that_move_nothing() -> None:
    migration = next((APP.parent / "alembic" / "versions").glob("*d5e7f9a1b3c4*")).read_text(
        encoding="utf-8"
    )
    assert "st.from_status IS DISTINCT FROM st.to_status" in migration
    assert "st.from_round_id IS DISTINCT FROM st.to_round_id" in migration


@pytest.mark.asyncio
async def test_an_exception_needs_a_real_reason() -> None:
    from app.stage_sla import StageError, raise_exception

    with pytest.raises(StageError) as exc:
        await raise_exception(AsyncMock(), company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(),
                              reason="short", owner_user_id=None, actor=uuid.uuid4())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_an_exception_reason_stays_out_of_the_audit_log(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.stage_sla as sla

    async def _ok(_db: object, **_kw: object) -> None:
        return None

    async def _note(_db: object, **_kw: object) -> bool:
        return True

    monkeypatch.setattr(sla, "_assert_owner_eligible", _ok)
    monkeypatch.setattr(sla, "create_notification", _note)
    row = {"id": uuid.uuid4(), "status": "held", "current_round_id": None,
           "requisition_id": uuid.uuid4(), "stage": "Final decision", "full_name": "Asha",
           "candidate_erased": False}
    db = AsyncMock()
    db.add = MagicMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=res)
    reason = "Asha asked to reschedule — travelling to Pune until Friday"
    await sla.raise_exception(db, company_id=uuid.uuid4(), enrolment_id=row["id"],
                              reason=reason, owner_user_id=None, actor=uuid.uuid4())
    details = db.add.call_args.args[0].details
    assert "reason" not in details and "Pune" not in str(details)
    assert details["reason_chars"] == len(reason)


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [{"status": "hired"}, {"candidate_erased": True}])
async def test_no_exception_on_a_decided_or_erased_application(over: dict[str, Any]) -> None:
    from app.stage_sla import StageError, raise_exception

    row = {"id": uuid.uuid4(), "status": "held", "current_round_id": None,
           "requisition_id": uuid.uuid4(), "stage": "Final decision", "full_name": "A",
           "candidate_erased": False, **over}
    db = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(StageError) as exc:
        await raise_exception(db, company_id=uuid.uuid4(), enrolment_id=row["id"],
                              reason="A reason that is long enough", owner_user_id=None,
                              actor=uuid.uuid4())
    assert exc.value.status_code == 409


def test_the_overdue_sweep_notifies_and_does_nothing_else() -> None:
    import app.reminders as rem
    from app.stage_sla import notify_overdue_stages

    assert "notify_overdue_stages" in inspect.getsource(rem._stage_sla)
    calls = _calls(notify_overdue_stages)
    assert "create_notification" in calls
    assert "dedupe_key" in inspect.getsource(notify_overdue_stages)


def test_stage_routes_sit_behind_the_hr_gate() -> None:
    source = (APP / "routers/workflow_ops.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef) and any(
            isinstance(d, ast.Call) and getattr(d.func, "value", None) is not None
            and getattr(d.func.value, "id", "") == "hr_router" for d in fn.decorator_list
        ):
            ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
            assert "HrCtxDep" in ann, fn.name


def test_new_tables_are_in_the_erasure_inventory() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(
        encoding="utf-8"
    )
    for table in ("workflow_review_events", "workflow_simulations", "workflow_stage_settings",
                  "stage_exceptions"):
        assert f'"{table}"' in inv, table
    assert "UPDATE stage_exceptions SET reason = '[redacted]'" in inv


# ===========================================================================
# Security review follow-ups (L1, L2, board ordering)
# ===========================================================================
@pytest.mark.asyncio
async def test_a_version_that_changed_after_approval_does_not_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1: an exam round a version uses stays editable, so publish re-checks."""
    import app.workflows as wf

    async def _moved(_db: object, _wid: object) -> str:
        return "after"

    monkeypatch.setattr(wf, "workflow_fingerprint", _moved)
    db = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = {
        "status": "draft", "review_status": "approved", "review_fingerprint": "before",
        "requisition_id": uuid.uuid4(),
    }
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(wf.WorkflowError, match="changed after it was approved"):
        await wf.publish(db, company_id=uuid.uuid4(), workflow_id=uuid.uuid4())


def test_the_fingerprint_covers_the_exams_a_version_uses() -> None:
    import app.workflows as wf

    for table in ("exam_rounds", "exam_sections", "exam_questions", "coding_questions"):
        assert table in wf._EXAM_CONTENT_SQL, table
    assert "_EXAM_CONTENT_SQL" in inspect.getsource(wf.workflow_fingerprint)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "over", [{"redacted_at": datetime(2026, 9, 18, tzinfo=UTC)}, {"candidate_erased": True}],
)
async def test_nothing_more_is_written_about_an_erased_candidate(over: dict[str, Any]) -> None:
    """L2: the exception can still be closed; the note about the person is dropped."""
    import app.stage_sla as sla

    row = {"id": uuid.uuid4(), "enrolment_id": uuid.uuid4(), "status": "open",
           "owner_user_id": None, "stage_label": "Screen", "redacted_at": None,
           "candidate_erased": False, **over}
    db = AsyncMock()
    db.add = MagicMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=res)
    await sla.resolve_exception(db, company_id=uuid.uuid4(), exception_id=row["id"],
                                note="She called back from Pune", actor=uuid.uuid4())
    update = next(c for c in db.execute.call_args_list
                  if "UPDATE stage_exceptions" in str(c.args[0]))
    assert update.args[1]["note"] is None
    assert db.add.call_args.args[0].details["note_chars"] == 0


def test_erasure_closes_open_exceptions_as_it_redacts_them() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(
        encoding="utf-8"
    )
    step = inv[inv.index("UPDATE stage_exceptions SET reason = '[redacted]'"):]
    step = step[: step.index('{"uid": uid_str}')]
    assert "status = 'resolved'" in step and "COALESCE(resolved_at, now())" in step


def test_the_at_risk_board_orders_before_it_caps() -> None:
    """With more than the cap open, the MOST overdue must be the ones shown."""
    from app.stage_sla import _BOARD_SQL

    sql = " ".join(_BOARD_SQL.split())
    assert sql.index("ORDER BY b.entered_at + b.sla_hours") < sql.index("LIMIT 2000")


def test_the_decision_queue_counts_exceptions_on_a_stage_without_an_sla() -> None:
    """An exception can be raised on any stage; its count must not need an SLA."""
    import app.stage_sla as sla

    assert '"  LEFT JOIN workflow_stage_settings s"' in inspect.getsource(sla.sla_for_enrolments)
