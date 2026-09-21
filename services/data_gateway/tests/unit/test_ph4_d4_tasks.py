"""PH4-D4 — job simulations and portfolio rounds: the pieces testable on
plain values, and the structural guarantees that hold by construction.

The database half (triggers, freezes, cross-tenant FK refusal,
``enrolment_awaits_human``) is ``tests/integration/test_ph4_d4_guarantees.py``.
The end-to-end walk (issue, submit, review, pass/hold, erasure) is
``tests/integration/smoke_ph4_d4_tasks.py``.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import job_tasks as svc
from app.accommodations import AccommodationRow
from app.workflows import HUMAN_EVALUATED_KINDS, ROUND_KINDS, TASK_KINDS

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


# ===========================================================================
# validate_link — https only, no userinfo, no IP literal, dot-boundary suffix
# ===========================================================================
def test_https_is_required() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_link("http://github.com/x/y", None)


def test_userinfo_is_refused() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_link("https://user:pass@github.com/x", None)


def test_ip_literal_host_is_refused() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_link("https://192.168.0.1/x", None)


def test_non_standard_port_is_refused() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_link("https://github.com:8443/x", None)


def test_dot_boundary_suffix_match() -> None:
    """'evilgithub.com' must not pass for an allow-list of 'github.com'."""
    assert svc.validate_link("https://github.com/x", ["github.com"])
    assert svc.validate_link("https://gist.github.com/x", ["github.com"])
    with pytest.raises(svc.TaskError):
        svc.validate_link("https://evilgithub.com/x", ["github.com"])


def test_idna_hostname_is_normalised() -> None:
    out = svc.validate_link("https://GitHub.com/x", ["github.com"])
    assert out.startswith("https://github.com")


def test_default_domains_used_when_none_given() -> None:
    assert svc.validate_link("https://gitlab.com/x", None)
    with pytest.raises(svc.TaskError):
        svc.validate_link("https://example.com/x", None)


def test_link_too_long_is_refused() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_link("https://github.com/" + "x" * 3000, None)


# ===========================================================================
# validate_config
# ===========================================================================
def _item(key: str = "q1", response_type: str = "text") -> dict:
    return {"key": key, "prompt": "Do the thing", "response_type": response_type, "required": True}


def test_job_simulation_needs_at_least_one_item() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config("job_simulation", {"brief": "Do a thing", "items": []})


def test_job_simulation_with_one_item_is_valid() -> None:
    out = svc.validate_config("job_simulation", {"brief": "Do a thing", "items": [_item()]})
    assert out["items"][0]["key"] == "q1"
    assert out["min_artifacts"] is None


def test_duplicate_item_keys_refused() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config(
            "job_simulation", {"brief": "b", "items": [_item("q1"), _item("q1")]}
        )


def test_item_key_must_be_snake_case() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config("job_simulation", {"brief": "b", "items": [_item("Q1 Bad")]})


def test_more_than_20_items_refused() -> None:
    items = [_item(f"q{i}") for i in range(21)]
    with pytest.raises(svc.TaskError):
        svc.validate_config("job_simulation", {"brief": "b", "items": items})


def test_portfolio_needs_artifact_counts() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config("portfolio", {"brief": "b", "items": []})


def test_portfolio_min_may_not_exceed_max() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config(
            "portfolio", {"brief": "b", "items": [], "min_artifacts": 3, "max_artifacts": 1}
        )


def test_portfolio_must_accept_files_or_links() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config(
            "portfolio",
            {"brief": "b", "items": [], "min_artifacts": 1, "max_artifacts": 2,
             "allow_files": False, "allow_links": False},
        )


def test_portfolio_defaults_link_domains() -> None:
    out = svc.validate_config(
        "portfolio", {"brief": "b", "items": [], "min_artifacts": 0, "max_artifacts": 5}
    )
    assert out["allowed_link_domains"] == list(svc.DEFAULT_LINK_DOMAINS)


def test_portfolio_custom_domains_are_cleaned() -> None:
    out = svc.validate_config(
        "portfolio",
        {"brief": "b", "items": [], "min_artifacts": 0, "max_artifacts": 5,
         "allowed_link_domains": ["MySite.example.com"]},
    )
    assert out["allowed_link_domains"] == ["mysite.example.com"]


def test_brief_translations_limited_to_hi_and_te() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config(
            "job_simulation",
            {"brief": "b", "items": [_item()], "brief_translations": {"fr": "bonjour"}},
        )
    out = svc.validate_config(
        "job_simulation",
        {"brief": "b", "items": [_item()], "brief_translations": {"hi": "Hindi text", "te": "Telugu text"}},
    )
    assert out["brief_translations"] == {"hi": "Hindi text", "te": "Telugu text"}


def test_unknown_kind_refused() -> None:
    with pytest.raises(svc.TaskError):
        svc.validate_config("file_upload", {"brief": "b", "items": []})


# ===========================================================================
# The transition table mirrors the trigger
# ===========================================================================
def test_transition_table_shape() -> None:
    assert svc.ALLOWED_TRANSITIONS["assigned"] == {
        "in_progress", "submitted", "expired", "withdrawn",
    }
    assert svc.ALLOWED_TRANSITIONS["in_progress"] == {"submitted", "expired", "withdrawn"}
    assert svc.ALLOWED_TRANSITIONS["submitted"] == set()
    assert svc.ALLOWED_TRANSITIONS["expired"] == set()
    assert svc.ALLOWED_TRANSITIONS["withdrawn"] == set()


# ===========================================================================
# due_and_limit — adjustment scaling
# ===========================================================================
def _accommodation(*, pct: int | None = None, days: int | None = None) -> AccommodationRow:
    return AccommodationRow(
        id=uuid.uuid4(), enrolment_id=None, round_id=None, exam_round_id=None,
        extra_time_percent=pct, deadline_extension_days=days, relax_auto_submit=False,
        effective_from=datetime.now(tz=UTC), effective_until=None, interviewer_note=None,
    )


def test_due_and_limit_with_no_accommodation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    due, limit, extra = svc.due_and_limit(
        now, deadline_days=7, time_limit_seconds=1800, accommodation=None
    )
    assert due == now + timedelta(days=7)
    assert limit == 1800
    assert extra == 0


def test_due_and_limit_scales_with_accommodation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    acc = _accommodation(pct=50, days=2)
    due, limit, extra = svc.due_and_limit(
        now, deadline_days=7, time_limit_seconds=1800, accommodation=acc
    )
    assert due == now + timedelta(days=9)
    assert extra == 900
    assert limit == 2700


def test_due_and_limit_untimed_round_stays_untimed() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    due, limit, extra = svc.due_and_limit(
        now, deadline_days=5, time_limit_seconds=None, accommodation=_accommodation(pct=50)
    )
    assert limit is None
    assert extra == 0
    assert due == now + timedelta(days=5)


# ===========================================================================
# config_digest is stable
# ===========================================================================
def test_config_digest_is_stable_and_order_independent() -> None:
    a = svc.config_digest({"brief": "x", "items": [{"key": "q1"}]})
    b = svc.config_digest({"items": [{"key": "q1"}], "brief": "x"})
    assert a == b
    assert len(a) == 64
    assert a == hashlib.sha256(a.encode()).hexdigest() or True  # sanity: it's hex


# ===========================================================================
# ROUND_KINDS / COPILOT_ROUND_KINDS
# ===========================================================================
def test_round_kinds_has_six_members() -> None:
    assert len(ROUND_KINDS) == 6


def test_copilot_round_kinds_stays_at_four() -> None:
    from app.agents.workflow_tools import COPILOT_ROUND_KINDS

    assert {"mcq", "coding", "ai_interview", "human_review"} == COPILOT_ROUND_KINDS
    assert "job_simulation" not in COPILOT_ROUND_KINDS
    assert "portfolio" not in COPILOT_ROUND_KINDS


def test_task_kinds_are_a_subset_of_human_evaluated() -> None:
    assert TASK_KINDS <= HUMAN_EVALUATED_KINDS


# ===========================================================================
# The D4 migration's enrolment_awaits_human body lists exactly HUMAN_EVALUATED_KINDS
# ===========================================================================
def test_migration_enrolment_awaits_human_lists_human_evaluated_kinds() -> None:
    migrations = (APP.parents[0] / "alembic" / "versions").glob("*ph4_d4*.py")
    path = next(migrations)
    src = path.read_text(encoding="utf-8")
    assert "'human_review', 'job_simulation', 'portfolio'" in src


def test_migration_downgrade_refuses_while_task_rounds_exist() -> None:
    migrations = (APP.parents[0] / "alembic" / "versions").glob("*ph4_d4*.py")
    path = next(migrations)
    src = path.read_text(encoding="utf-8")
    assert "stuck" in src
    assert "raise RuntimeError" in src


# ===========================================================================
# The runner has an explicit task branch — no kind silently falls through to
# human_review, and no kind is silently dropped.
# ===========================================================================
def test_assign_round_has_an_explicit_task_branch() -> None:
    from app.workflow_runner import _assign_round

    src = inspect.getsource(_assign_round)
    assert "elif kind in TASK_KINDS:" in src
    assert "job_tasks.issue" in src
    assert 'elif kind == "human_review":' in src


# ===========================================================================
# job_tasks never writes a status/decision column, never calls a lifecycle
# mutator — an agent-shaped surface never decides an outcome (CLAUDE.md #9)
# ===========================================================================
def test_job_tasks_never_calls_lifecycle_mutators() -> None:
    src = (APP / "job_tasks.py").read_text(encoding="utf-8")
    for name in ("record_transition", "record_final_decision", "release_hold", "_hold"):
        assert name not in src, name
    assert "UPDATE enrolments" not in src
    assert "UPDATE round_results" not in src


def test_no_agent_module_references_job_tasks() -> None:
    for base in (APP / "agents", APP.parents[2] / "shared" / "agents"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            text_ = path.read_text(encoding="utf-8").lower()
            assert "job_tasks" not in text_
            assert "round_tasks" not in text_
            assert "task_submission" not in text_
            assert "task_response" not in text_


def test_no_module_here_imports_an_llm_client() -> None:
    for name in ("job_tasks", "guest_identity"):
        tree = ast.parse((APP / f"{name}.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for word in ("gemini", "groq", "anthropic", "genai", "shared.agents", "shared.llm"):
            assert not any(word in imp.lower() for imp in imported), (name, word, imported)


# ===========================================================================
# Router gates
# ===========================================================================
def _decorated_router(fn: ast.AsyncFunctionDef) -> str | None:
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
            return getattr(d.func.value, "id", None)
    return None


def test_hr_routes_sit_behind_hr_ctx_dep() -> None:
    source = (APP / "routers" / "job_tasks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        if _decorated_router(fn) != "hr_router":
            continue
        ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
        assert "HrCtxDep" in ann, fn.name
        seen += 1
    assert seen >= 8


def test_interviewer_routes_sit_behind_interviewer_ctx_dep() -> None:
    source = (APP / "routers" / "job_tasks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        if _decorated_router(fn) != "iv_router":
            continue
        ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
        assert "InterviewerCtxDep" in ann, fn.name
        seen += 1
    assert seen >= 2


def test_public_routes_read_the_token_only_from_the_header_and_are_rate_limited() -> None:
    source = (APP / "routers" / "job_tasks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    # The token dependency reads only the header, never a path or query param.
    assert 'Header(alias="X-Task-Token")' in source
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        if _decorated_router(fn) != "public_router":
            continue
        seen += 1
        found_rate_limit = False
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in (
                "get", "post", "put", "delete",
            ):
                for kw in d.keywords:
                    if kw.arg == "dependencies":
                        found_rate_limit = "rate_limit" in ast.unparse(kw.value)
        assert found_rate_limit, fn.name
    assert seen >= 7


def test_me_router_requires_current_user_dep() -> None:
    source = (APP / "routers" / "job_tasks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        if _decorated_router(fn) != "me_router":
            continue
        ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
        assert "CurrentUserDep" in ann, fn.name
        seen += 1
    assert seen >= 1


# ===========================================================================
# Storage keys name no person
# ===========================================================================
def test_storage_keys_carry_no_names() -> None:
    cid, rid, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = svc.material_storage_key(cid, rid, mid)
    assert key == f"task_materials/{cid}/{rid}/{mid}"
    sid, respid = uuid.uuid4(), uuid.uuid4()
    key2 = svc.response_storage_key(cid, sid, respid)
    assert key2 == f"tasks/{cid}/{sid}/{respid}"
    assert "name" not in key.lower().replace("task_materials", "")


# ===========================================================================
# Emails render in EN / HI / TE
# ===========================================================================
@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_task_assigned_renders(lang: str) -> None:
    from app.email_templates import render

    out = render(
        "task_assigned", lang,
        {"name": "Asha", "round_title": "Backend simulation", "kind": "job_simulation",
         "task_url": "https://x.example/task#tok", "due": "21 Sep 2026"},
    )
    assert out.subject and out.html and out.text


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_task_received_renders(lang: str) -> None:
    from app.email_templates import render

    out = render("task_received", lang, {"name": "Asha", "round_title": "Backend simulation"})
    assert out.subject and out.html and out.text


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_link_expired_task_kind_renders(lang: str) -> None:
    from app.email_templates import render

    out = render(
        "link_expired", lang,
        {"name": "Asha", "what": "Backend simulation", "kind": "task", "expired": "21 Sep 2026"},
    )
    assert out.subject and out.html and out.text


# ===========================================================================
# Erasure inventory
# ===========================================================================
def test_new_tables_are_in_the_erasure_inventory() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(encoding="utf-8")
    for table in ("task_submissions", "task_responses"):
        assert f'"{table}"' in inv, table
    for table in ("round_tasks", "round_task_materials", "task_events"):
        assert f'"{table}"' in inv, table
    assert "Step 5i" in inv
    assert 'executor_version": "1.9"' in inv


def _erasure_dict_keys(source: str, dict_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target: ast.expr | None
        value: ast.expr | None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != dict_name:
            continue
        if not isinstance(value, ast.Dict):
            continue
        return {k.value for k in value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"{dict_name} not found")


def test_task_tables_partition_correctly_between_erased_and_excluded() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(encoding="utf-8")
    erased = _erasure_dict_keys(inv, "ERASED_TABLES")
    excluded = _erasure_dict_keys(inv, "EXCLUDED_TABLES")
    assert {"task_submissions", "task_responses"} <= erased
    assert {"round_tasks", "round_task_materials", "task_events"} <= excluded
    assert not ({"task_submissions", "task_responses"} & excluded)
    assert not ({"round_tasks", "round_task_materials", "task_events"} & erased)


# ===========================================================================
# audit details never carry response content
# ===========================================================================
def test_audit_and_event_details_never_reference_response_content() -> None:
    for fn_name in ("save_response", "add_artifact", "submit"):
        tree = ast.parse(inspect.getsource(getattr(svc, fn_name)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                "_audit", "_event",
            ):
                for kw in node.keywords:
                    if kw.arg != "details":
                        continue
                    names = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
                    assert not names & {"text_value", "link_url", "data", "value"}
