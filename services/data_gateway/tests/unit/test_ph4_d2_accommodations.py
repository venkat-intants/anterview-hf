"""PH4-D2 — candidate accommodations: the pieces that can be tested on plain
values, and the promises that hold by construction.

The database half (the guard trigger, the allowance freeze, the CHECK
constraints, append-only events, cross-company FKs) is in
``tests/integration/test_ph4_d2_guarantees.py``. The end-to-end HTTP walk,
including the "same answers score the same" proof, is
``tests/integration/smoke_ph4_d2_accommodations.py``.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import accommodations as svc
from app.email_templates import render

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _row(
    *,
    row_id: uuid.UUID | None = None,
    enrolment_id: uuid.UUID | None = None,
    round_id: uuid.UUID | None = None,
    exam_round_id: uuid.UUID | None = None,
    extra_time_percent: int | None = None,
    deadline_extension_days: int | None = None,
    relax_auto_submit: bool = False,
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
    interviewer_note: str | None = None,
) -> svc.AccommodationRow:
    return svc.AccommodationRow(
        id=row_id or uuid.uuid4(), enrolment_id=enrolment_id, round_id=round_id,
        exam_round_id=exam_round_id, extra_time_percent=extra_time_percent,
        deadline_extension_days=deadline_extension_days, relax_auto_submit=relax_auto_submit,
        effective_from=effective_from or datetime(2026, 1, 1, tzinfo=UTC),
        effective_until=effective_until, interviewer_note=interviewer_note,
    )


# ===========================================================================
# pick — precedence, time windows, and rows never add up
# ===========================================================================
def test_a_round_scoped_row_beats_an_application_scoped_row() -> None:
    enrolment_id, round_id = uuid.uuid4(), uuid.uuid4()
    app_wide = _row(enrolment_id=enrolment_id, extra_time_percent=20)
    round_scoped = _row(enrolment_id=enrolment_id, round_id=round_id, extra_time_percent=50)
    picked = svc.pick(
        [app_wide, round_scoped], enrolment_id=enrolment_id, workflow_round_id=round_id,
        exam_round_id=None, at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is round_scoped


def test_an_application_scoped_row_beats_an_applicant_wide_row() -> None:
    enrolment_id = uuid.uuid4()
    applicant_wide = _row(extra_time_percent=20)
    app_scoped = _row(enrolment_id=enrolment_id, extra_time_percent=50)
    picked = svc.pick(
        [applicant_wide, app_scoped], enrolment_id=enrolment_id, workflow_round_id=None,
        exam_round_id=None, at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is app_scoped


def test_an_exam_round_scoped_row_matches_by_exam_round_id() -> None:
    exam_round_id = uuid.uuid4()
    row = _row(exam_round_id=exam_round_id, extra_time_percent=30)
    picked = svc.pick(
        [row], enrolment_id=None, workflow_round_id=None, exam_round_id=exam_round_id,
        at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is row


def test_a_row_scoped_to_a_different_round_never_matches() -> None:
    round_id, other_round_id = uuid.uuid4(), uuid.uuid4()
    enrolment_id = uuid.uuid4()
    row = _row(enrolment_id=enrolment_id, round_id=other_round_id, extra_time_percent=50)
    picked = svc.pick(
        [row], enrolment_id=enrolment_id, workflow_round_id=round_id, exam_round_id=None,
        at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is None


def test_within_the_same_level_the_latest_effective_from_wins() -> None:
    enrolment_id = uuid.uuid4()
    older = _row(
        enrolment_id=enrolment_id, extra_time_percent=20,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = _row(
        enrolment_id=enrolment_id, extra_time_percent=50,
        effective_from=datetime(2026, 3, 1, tzinfo=UTC),
    )
    picked = svc.pick(
        [older, newer], enrolment_id=enrolment_id, workflow_round_id=None, exam_round_id=None,
        at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is newer


def test_rows_are_never_added_together() -> None:
    """Two applicant-wide rows never combine into one bigger adjustment — the
    latest one wins outright, the earlier one is simply not picked."""
    row_a = _row(extra_time_percent=20, effective_from=datetime(2026, 1, 1, tzinfo=UTC))
    row_b = _row(extra_time_percent=30, effective_from=datetime(2026, 2, 1, tzinfo=UTC))
    picked = svc.pick(
        [row_a, row_b], enrolment_id=None, workflow_round_id=None, exam_round_id=None,
        at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is not None
    assert picked.extra_time_percent == 30  # never 50


def test_a_row_not_yet_effective_is_not_picked() -> None:
    row = _row(extra_time_percent=50, effective_from=datetime(2027, 1, 1, tzinfo=UTC))
    picked = svc.pick(
        [row], enrolment_id=None, workflow_round_id=None, exam_round_id=None,
        at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is None


def test_a_row_past_its_effective_until_is_not_picked() -> None:
    row = _row(
        extra_time_percent=50, effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_until=datetime(2026, 2, 1, tzinfo=UTC),
    )
    picked = svc.pick(
        [row], enrolment_id=None, workflow_round_id=None, exam_round_id=None,
        at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert picked is None


def test_no_rows_means_no_adjustment() -> None:
    assert svc.pick([], enrolment_id=None, workflow_round_id=None, exam_round_id=None,
                    at=datetime.now(tz=UTC)) is None


# ===========================================================================
# extra_seconds / scaled — ceil arithmetic, and "no adjustment" is a no-op
# ===========================================================================
def test_extra_seconds_rounds_up() -> None:
    assert svc.extra_seconds(100, 50) == 50
    assert svc.extra_seconds(101, 10) == 11  # ceil(10.1) == 11, never truncated down


def test_extra_seconds_is_zero_with_no_base_or_no_percent() -> None:
    assert svc.extra_seconds(None, 50) == 0
    assert svc.extra_seconds(100, None) == 0
    assert svc.extra_seconds(0, 50) == 0


def test_scaled_adds_the_extra_seconds() -> None:
    assert svc.scaled(900, 50) == 1350
    assert svc.scaled(900, None) == 900  # no adjustment: unchanged


def test_scaled_leaves_an_untimed_limit_untimed() -> None:
    assert svc.scaled(None, 50) is None


# ===========================================================================
# _deadline — no adjustment is EXACTLY the old formula
# ===========================================================================
def test_deadline_with_zero_extra_seconds_matches_the_old_formula() -> None:
    from types import SimpleNamespace

    from app.routers.exam_take import _deadline

    rnd = SimpleNamespace(time_limit_seconds=1800)
    started = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert _deadline(rnd, started) == started + timedelta(seconds=1800)
    assert _deadline(rnd, started, 0) == _deadline(rnd, started)
    assert _deadline(rnd, started, 900) == started + timedelta(seconds=2700)


def test_deadline_stays_none_for_an_untimed_round() -> None:
    from types import SimpleNamespace

    from app.routers.exam_take import _deadline

    rnd = SimpleNamespace(time_limit_seconds=None)
    assert _deadline(rnd, datetime.now(tz=UTC), 900) is None


# ===========================================================================
# The scoring path is not touched
# ===========================================================================
def test_exam_grading_never_references_accommodations() -> None:
    from app import exam_grading

    src = inspect.getsource(exam_grading).lower()
    assert "accommodation" not in src


def test_coding_grader_never_references_accommodations() -> None:
    from app import coding_grader

    src = inspect.getsource(coding_grader).lower()
    assert "accommodation" not in src


def test_grade_and_finalize_only_reaches_deadline_with_accommodation_data() -> None:
    """The one place ``_grade_and_finalize`` touches PH4-D2 data is the
    ``_deadline`` call — never the score, never the pass/fail comparison."""
    from app.routers.exam_take import _grade_and_finalize

    src = inspect.getsource(_grade_and_finalize)
    assert "accommodation" not in src.lower()
    assert src.count("extra_time_seconds") == 1
    line = next(ln for ln in src.splitlines() if "extra_time_seconds" in ln)
    assert "_deadline(" in line


# ===========================================================================
# The interviewer payload's keys are an exact whitelist
# ===========================================================================
def _return_dict_keys(fn: object) -> set[str]:
    tree = ast.parse(inspect.getsource(fn))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for k in node.value.keys:
                assert isinstance(k, ast.Constant)
                keys.add(k.value)
    return keys


def test_get_for_interviewer_returns_exactly_the_expected_keys() -> None:
    from app.interviewer_scorecards import get_for_interviewer

    assert _return_dict_keys(get_for_interviewer) == {
        "scorecard_id", "state", "status", "candidate_name", "job_title", "round_title",
        "due_at", "submitted_at", "summary", "correction_reason", "is_correction",
        "superseded", "can_edit", "can_correct", "adjustments_note", "criteria",
    }


def test_interviewer_note_for_never_reaches_for_internal_note() -> None:
    """Checked over the AST's attribute accesses, not the raw text — the
    function's own docstring explains what it does NOT return, in prose that
    names those very fields, which would make a substring search a false
    positive."""
    tree = ast.parse(inspect.getsource(svc.interviewer_note_for))
    accessed = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not accessed & {"internal_note", "extra_time_percent", "deadline_extension_days"}


# ===========================================================================
# Audit and event details never carry note text or parameter values
# ===========================================================================
def _dict_value_node(fn: object, key: str) -> ast.expr:
    tree = ast.parse(inspect.getsource(fn))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values, strict=True):
            if isinstance(k, ast.Constant) and k.value == key:
                return v
    raise AssertionError(f"key {key!r} not found in {fn}")


@pytest.mark.parametrize("key", ["has_other_adjustment", "has_interviewer_note", "has_internal_note"])
def test_field_facts_wraps_notes_in_bool_never_the_text(key: str) -> None:
    node = _dict_value_node(svc._field_facts, key)  # noqa: SLF001 — the function under test
    assert isinstance(node, ast.Call)
    assert isinstance(node.func, ast.Name) and node.func.id == "bool"


def test_reason_facts_never_carries_the_reason_text() -> None:
    has_reason = _dict_value_node(svc._reason_facts, "has_reason")  # noqa: SLF001
    chars = _dict_value_node(svc._reason_facts, "reason_chars")  # noqa: SLF001
    assert isinstance(has_reason, ast.Call) and has_reason.func.id == "bool"  # type: ignore[union-attr]
    assert isinstance(chars, ast.Call) and chars.func.id == "len"  # type: ignore[union-attr]


def test_scope_facts_never_carries_a_parameter_value() -> None:
    src = inspect.getsource(svc._scope_facts)  # noqa: SLF001
    for word in ("extra_time_percent", "deadline_extension_days", "other_adjustment",
                "interviewer_note", "internal_note"):
        assert word not in src


# ===========================================================================
# Every route sits behind HrCtxDep — no candidate, interviewer, super-admin
# or agent path
# ===========================================================================
def test_every_route_sits_behind_hr_ctx_dep() -> None:
    source = (APP / "routers" / "accommodations.py").read_text(encoding="utf-8")
    seen = 0
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                router = getattr(d.func.value, "id", "")
                if router != "hr_router":
                    continue
                ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
                assert "HrCtxDep" in ann, fn.name
                seen += 1
    assert seen >= 5


def test_no_super_admin_interviewer_or_public_router_in_the_module() -> None:
    src = (APP / "routers" / "accommodations.py").read_text(encoding="utf-8")
    for name in ("SuperAdminCtxDep", "InterviewerCtxDep", "CurrentUserDep"):
        assert name not in src


# ===========================================================================
# No AI anywhere near an accommodation
# ===========================================================================
def test_no_agent_module_references_accommodations() -> None:
    for base in (APP / "agents", APP.parents[2] / "shared" / "agents"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            text_ = path.read_text(encoding="utf-8").lower()
            assert "accommodation" not in text_, path


def test_accommodations_module_never_imports_an_llm_client() -> None:
    tree = ast.parse(inspect.getsource(svc))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for word in ("gemini", "groq", "anthropic", "genai", "shared.agents", "shared.llm"):
        assert not any(word in name.lower() for name in imported), (word, imported)


# ===========================================================================
# The candidate email — EN / HI / TE, parameters only, never a note
# ===========================================================================
@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_accommodation_email_renders_and_never_carries_notes(lang: str) -> None:
    ctx = {
        "name": "Asha", "extra_time_percent": 50, "deadline_extension_days": 3,
        "relax_auto_submit": True, "has_other_adjustment": True,
        # If these ever leaked into the template they would show up verbatim —
        # planted here so the assertion below is a real check, not a tautology.
        "other_adjustment": "SECRET-OTHER-TEXT",
        "interviewer_note": "SECRET-INTERVIEWER-TEXT",
        "internal_note": "SECRET-INTERNAL-TEXT",
    }
    mail = render("accommodation_recorded", lang, ctx)
    assert mail.subject
    for secret in ("SECRET-OTHER-TEXT", "SECRET-INTERVIEWER-TEXT", "SECRET-INTERNAL-TEXT"):
        assert secret not in mail.html
        assert secret not in mail.text
    assert "50" in mail.html or "50" in mail.text  # the parameter IS told


def test_accommodation_email_differs_from_english_in_other_languages() -> None:
    ctx = {"name": "Asha", "extra_time_percent": 50}
    en = render("accommodation_recorded", "en", ctx)
    for lang in ("hi", "te"):
        other = render("accommodation_recorded", lang, ctx)
        assert other.subject != en.subject


def test_accommodation_email_with_no_adjustment_fields_still_renders() -> None:
    mail = render("accommodation_recorded", "en", {"name": "Asha"})
    assert mail.subject and mail.html
