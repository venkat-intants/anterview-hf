"""PH4-D1 — reusable question banks: the pieces that can be tested on plain
values, and the promises that hold by construction.

The database half (T1/T2/T3, the append-only events, the cross-company FKs,
the duplicate-lineage index) is in ``tests/integration/test_ph4_d1_guarantees.py``.
The end-to-end HTTP walk is ``tests/integration/smoke_ph4_d1_question_banks.py``.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from typing import Any

import pytest

from app import question_banks as svc

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


# ===========================================================================
# content_hash — normalisation
# ===========================================================================
def test_content_hash_ignores_case_and_extra_whitespace() -> None:
    a = svc.content_hash(kind="mcq", prompt="What is  2 + 2?", options=["Three", "Four"],
                         correct_index=1)
    b = svc.content_hash(kind="mcq", prompt="what is 2 + 2?  ", options=["three", "four "],
                         correct_index=1)
    assert a == b


def test_content_hash_is_sensitive_to_the_actual_content() -> None:
    base = svc.content_hash(kind="mcq", prompt="2 + 2?", options=["3", "4"], correct_index=1)
    diff_answer = svc.content_hash(kind="mcq", prompt="2 + 2?", options=["3", "4"], correct_index=0)
    diff_prompt = svc.content_hash(kind="mcq", prompt="2 + 3?", options=["3", "4"], correct_index=1)
    diff_options = svc.content_hash(kind="mcq", prompt="2 + 2?", options=["3", "5"], correct_index=1)
    assert len({base, diff_answer, diff_prompt, diff_options}) == 4


def test_content_hash_for_coding_looks_at_test_cases_not_starter_code() -> None:
    cases = [{"stdin": "1 2", "expected_output": "3"}]
    a = svc.content_hash(kind="coding", prompt="Add two numbers", test_cases=cases)
    b = svc.content_hash(kind="coding", prompt="add two numbers", test_cases=cases)
    assert a == b
    other_cases = [{"stdin": "1 2", "expected_output": "4"}]
    assert a != svc.content_hash(kind="coding", prompt="Add two numbers", test_cases=other_cases)


def test_content_hash_mcq_and_coding_never_collide() -> None:
    mcq = svc.content_hash(kind="mcq", prompt="x", options=["a", "b"], correct_index=0)
    coding = svc.content_hash(kind="coding", prompt="x", test_cases=[])
    assert mcq != coding


# ===========================================================================
# The T1 transition table
# ===========================================================================
def test_the_transition_table_matches_the_four_states() -> None:
    assert set(svc.ALLOWED_TRANSITIONS) == {"draft", "in_review", "approved", "retired"}
    assert svc.ALLOWED_TRANSITIONS["draft"] == frozenset({"in_review"})
    assert svc.ALLOWED_TRANSITIONS["in_review"] == frozenset({"draft", "approved"})
    assert svc.ALLOWED_TRANSITIONS["approved"] == frozenset({"retired"})
    assert svc.ALLOWED_TRANSITIONS["retired"] == frozenset()


def test_retired_is_terminal() -> None:
    assert svc.ALLOWED_TRANSITIONS["retired"] == frozenset()
    # Nothing transitions INTO retired except from approved.
    sources = {frm for frm, tos in svc.ALLOWED_TRANSITIONS.items() if "retired" in tos}
    assert sources == {"approved"}


def test_the_migration_s_lifecycle_trigger_encodes_the_same_four_transitions() -> None:
    """Pins the T1 trigger body to the same table this module exposes, so the
    two cannot silently drift apart."""
    path = (
        APP.parent / "alembic" / "versions"
        / "20260921_0001_d2a4c6e8f0b3_ph4_d1_question_banks.py"
    )
    src = path.read_text(encoding="utf-8")
    assert "(OLD.status = 'draft' AND NEW.status = 'in_review')" in src
    assert "(OLD.status = 'in_review' AND NEW.status = 'draft')" in src
    assert "(OLD.status = 'in_review' AND NEW.status = 'approved')" in src
    assert "(OLD.status = 'approved' AND NEW.status = 'retired')" in src


# ===========================================================================
# Competency validation
# ===========================================================================
def test_competencies_accept_a_well_formed_list() -> None:
    out = svc.validate_competencies([{"id": "python", "name": "Python"}])
    assert out == [{"id": "python", "name": "Python"}]


def test_competencies_lowercase_the_id_and_dedupe() -> None:
    out = svc.validate_competencies([{"id": "Python", "name": "Python"}, {"id": "python", "name": "x"}])
    assert out == [{"id": "python", "name": "Python"}]


def test_a_mixed_case_id_is_normalised_not_refused() -> None:
    """Uppercase letters alone are not malformed — they are lowercased before
    the slug pattern is checked, same as the exam-side normalisation."""
    assert svc.validate_competencies([{"id": "UPPER", "name": "x"}]) == [{"id": "upper", "name": "x"}]


def test_at_most_eight_competencies() -> None:
    items = [{"id": f"c{i}", "name": f"C{i}"} for i in range(9)]
    with pytest.raises(svc.QuestionBankError) as exc:
        svc.validate_competencies(items)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("bad_id", ["Has Space", "punctuation!", "", "x" * 81])
def test_a_competency_id_must_match_the_slug_pattern(bad_id: str) -> None:
    with pytest.raises(svc.QuestionBankError) as exc:
        svc.validate_competencies([{"id": bad_id, "name": "Something"}])
    assert exc.value.status_code == 422


def test_a_competency_needs_a_name() -> None:
    with pytest.raises(svc.QuestionBankError):
        svc.validate_competencies([{"id": "python", "name": "  "}])


def test_no_competencies_is_fine() -> None:
    assert svc.validate_competencies(None) == []
    assert svc.validate_competencies([]) == []


# ===========================================================================
# The picker's skip reasons — a pure helper
# ===========================================================================
def _bq(**over: Any) -> Any:
    from types import SimpleNamespace

    defaults = {
        "id": uuid.uuid4(), "root_id": uuid.uuid4(), "status": "approved", "kind": "mcq",
        "content_hash": "h" * 64,
    }
    return SimpleNamespace(**{**defaults, **over})


def test_picker_skips_a_question_that_is_not_approved() -> None:
    bq = _bq(status="draft")
    reason = svc.picker_skip_reason(bq, section_kind="mcq", existing_roots=set(), existing_hashes=set())
    assert reason == "not approved"


def test_picker_skips_a_question_of_the_wrong_kind() -> None:
    bq = _bq(kind="coding")
    reason = svc.picker_skip_reason(bq, section_kind="mcq", existing_roots=set(), existing_hashes=set())
    assert reason is not None and "mcq" in reason and "coding" in reason


def test_picker_skips_the_same_lineage_already_in_the_exam() -> None:
    bq = _bq()
    reason = svc.picker_skip_reason(
        bq, section_kind="mcq", existing_roots={bq.root_id}, existing_hashes=set(),
    )
    assert reason == "already in this exam"


def test_picker_skips_an_identical_question_by_content_hash() -> None:
    bq = _bq()
    reason = svc.picker_skip_reason(
        bq, section_kind="mcq", existing_roots=set(), existing_hashes={bq.content_hash},
    )
    assert reason == "an identical question is already in this exam"


def test_picker_adds_an_approved_matching_unique_question() -> None:
    bq = _bq()
    reason = svc.picker_skip_reason(bq, section_kind="mcq", existing_roots=set(), existing_hashes=set())
    assert reason is None


# ===========================================================================
# Every route sits behind its audience gate
# ===========================================================================
def test_every_route_sits_behind_its_audience_gate() -> None:
    source = (APP / "routers" / "question_banks.py").read_text(encoding="utf-8")
    gate = {"hr_router": "HrCtxDep", "admin_router": "SuperAdminCtxDep"}
    seen = 0
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                router = getattr(d.func.value, "id", "")
                if router not in gate:
                    continue
                ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
                assert gate[router] in ann, fn.name
                seen += 1
    assert seen >= 25


# ===========================================================================
# Every question writer calls the lock check
# ===========================================================================
@pytest.mark.parametrize(
    ("module_name", "functions"),
    [
        ("app.routers.hr_exams", ["add_question", "update_question", "delete_question",
                                  "reorder_questions", "bulk_add_questions", "import_questions"]),
        ("app.routers.hr_coding", ["add_coding_question", "update_coding_question",
                                   "delete_coding_question"]),
        ("app.routers.hr_rounds", ["create_section", "update_section", "delete_section",
                                   "delete_section_question", "delete_section_coding_question",
                                   "add_section_question", "add_section_coding_question",
                                   "delete_round"]),
    ],
)
def test_every_question_writer_calls_the_lock_check(module_name: str, functions: list[str]) -> None:
    import importlib

    module = importlib.import_module(module_name)
    for name in functions:
        src = inspect.getsource(getattr(module, name))
        assert "exam_locks." in src, f"{module_name}.{name} does not call exam_locks"


def test_update_round_checks_the_lock_only_for_grading_fields() -> None:
    from app.routers.hr_rounds import update_round

    src = inspect.getsource(update_round)
    assert "grading_changed" in src
    assert "exam_locks.assert_round_editable(db, company_id, rnd)" in src


# ===========================================================================
# The fingerprint excludes provenance
# ===========================================================================
def test_the_fingerprint_sql_subtracts_the_bank_provenance_columns() -> None:
    from app.workflows import _EXAM_CONTENT_SQL

    for col in ("source_bank_question_id", "source_bank_root_id", "source_bank_version"):
        assert f"- '{col}'" in _EXAM_CONTENT_SQL, col


# ===========================================================================
# No AI anywhere near a question bank
# ===========================================================================
def test_no_agent_module_references_question_banks() -> None:
    words = ("bank_question", "question_bank")
    for base in (APP / "agents", APP.parents[2] / "shared" / "agents"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            text_ = path.read_text(encoding="utf-8").lower()
            for word in words:
                assert word not in text_, f"{path} references {word}"


def test_question_banks_module_never_imports_an_llm_client() -> None:
    """Checked over the AST's import statements, not the raw text — the
    module's own docstring talks ABOUT the AI-draft path in prose, which must
    not make this a false positive."""
    tree = ast.parse(inspect.getsource(svc))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for word in ("gemini", "groq", "anthropic", "genai", "shared.agents", "shared.llm"):
        assert not any(word in name.lower() for name in imported), (word, imported)


def test_the_candidate_take_path_has_no_bank_reference() -> None:
    import app.routers.exam_take as et

    src = inspect.getsource(et)
    assert "bank" not in src.lower()


# ===========================================================================
# Audit rows are facts only
# ===========================================================================
def test_bank_question_events_and_audit_never_carry_question_text() -> None:
    src = inspect.getsource(svc)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in {"_event", "_audit"}):
            continue
        body_src = ast.unparse(node)
        assert "prompt" not in body_src
        assert "options" not in body_src
        assert "test_cases" not in body_src
