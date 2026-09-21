"""PH4-D3 — code quality + similarity evidence: the pieces testable on plain
values, and the structural guarantees that "a signal is never a finding"
holds by construction.

The database half (append-only/immutable triggers, the submission freeze,
cross-tenant FK refusal) is ``tests/integration/test_ph4_d3_guarantees.py``.
The end-to-end walk (two near-identical submissions producing exactly one
signal, a reference-solution match, tenant isolation over HTTP, erasure) is
``tests/integration/smoke_ph4_d3_code_evidence.py``.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import textwrap
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app import code_evidence as svc
from app import code_quality, code_similarity
from app.code_similarity import PYGMENTS_LANGUAGE_ALIASES

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


# ===========================================================================
# code_similarity — normalisation is renaming-proof, across every language
# ===========================================================================
_SNIPPETS: dict[str, tuple[str, str]] = {
    "python": ("def foo(x):\n    y = x + 1\n    return y\n",
               "def bar(a):\n    b = a + 1\n    return b\n"),
    "javascript": ("function foo(x) {\n  var y = x + 1;\n  return y;\n}\n",
                   "function bar(a) {\n  var b = a + 1;\n  return b;\n}\n"),
    "typescript": ("function foo(x: number): number {\n  let y = x + 1;\n  return y;\n}\n",
                   "function bar(a: number): number {\n  let b = a + 1;\n  return b;\n}\n"),
    "java": ("class Foo {\n  int bar(int x) {\n    int y = x + 1;\n    return y;\n  }\n}\n",
             "class Baz {\n  int qux(int a) {\n    int b = a + 1;\n    return b;\n  }\n}\n"),
    "cpp": ("int foo(int x) {\n  int y = x + 1;\n  return y;\n}\n",
            "int bar(int a) {\n  int b = a + 1;\n  return b;\n}\n"),
    "c": ("int foo(int x) {\n  int y = x + 1;\n  return y;\n}\n",
          "int bar(int a) {\n  int b = a + 1;\n  return b;\n}\n"),
    "go": ("func foo(x int) int {\n  y := x + 1\n  return y\n}\n",
           "func bar(a int) int {\n  b := a + 1\n  return b\n}\n"),
    "csharp": ("class Foo {\n  int Bar(int x) {\n    int y = x + 1;\n    return y;\n  }\n}\n",
               "class Baz {\n  int Qux(int a) {\n    int b = a + 1;\n    return b;\n  }\n}\n"),
    "ruby": ("def foo(x)\n  y = x + 1\n  return y\nend\n",
             "def bar(a)\n  b = a + 1\n  return b\nend\n"),
    "rust": ("fn foo(x: i32) -> i32 {\n  let y = x + 1;\n  y\n}\n",
             "fn bar(a: i32) -> i32 {\n  let b = a + 1;\n  b\n}\n"),
}


def test_every_supported_language_has_a_renaming_fixture() -> None:
    assert set(_SNIPPETS) == set(PYGMENTS_LANGUAGE_ALIASES)


@pytest.mark.parametrize("language", sorted(_SNIPPETS))
def test_renamed_identifiers_produce_the_same_normalised_tokens(language: str) -> None:
    original, renamed = _SNIPPETS[language]
    assert code_similarity.normalised_tokens(language, original) == (
        code_similarity.normalised_tokens(language, renamed)
    )
    # And it is not trivially empty -- a real check, not a vacuous one.
    assert len(code_similarity.normalised_tokens(language, original)) > 5


def test_unsupported_language_raises() -> None:
    with pytest.raises(code_similarity.UnsupportedLanguageError):
        code_similarity.get_lexer("cobol")


# ===========================================================================
# Hashing is stable, never Python's salted hash()
# ===========================================================================
def test_hashes_are_stable_across_calls() -> None:
    tokens = ["def", "V", "(", "V", ")", ":", "return", "V"] * 3
    h1, p1 = code_similarity.fingerprints(tokens)
    h2, p2 = code_similarity.fingerprints(tokens)
    assert h1 == h2
    assert p1 == p2


def test_hash_gram_does_not_use_python_hash() -> None:
    src = inspect.getsource(code_similarity._hash_gram)  # noqa: SLF001
    assert "blake2b" in src
    tree = ast.parse(textwrap.dedent(src))
    calls = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "hash" not in calls


# ===========================================================================
# Winnowing guarantee: a shared run of k + w - 1 = 22 tokens is always caught
# ===========================================================================
def test_winnowing_guarantee_holds_for_a_22_token_shared_run() -> None:
    shared_run = [f"tok{i}" for i in range(22)]
    a = ["prefix_a1", "prefix_a2"] + shared_run + ["suffix_a1"]
    b = ["prefix_b1"] + shared_run + ["suffix_b1", "suffix_b2"]
    hashes_a, _ = code_similarity.fingerprints(a)
    hashes_b, _ = code_similarity.fingerprints(b)
    assert set(hashes_a) & set(hashes_b), "a 22-token shared run must produce a shared fingerprint"


def test_completely_disjoint_token_streams_share_nothing() -> None:
    a = [f"a{i}" for i in range(30)]
    b = [f"b{i}" for i in range(30)]
    hashes_a, _ = code_similarity.fingerprints(a)
    hashes_b, _ = code_similarity.fingerprints(b)
    assert not (set(hashes_a) & set(hashes_b))


# ===========================================================================
# Containment maths
# ===========================================================================
def test_containment_and_jaccard_on_a_known_overlap() -> None:
    sim = code_similarity.compare([1, 2, 3, 4], [3, 4, 5])
    assert sim.shared == 2
    assert sim.containment_a == pytest.approx(2 / 4)
    assert sim.containment_b == pytest.approx(2 / 3)
    assert sim.jaccard == pytest.approx(2 / 5)


def test_compare_on_empty_sets_never_divides_by_zero() -> None:
    sim = code_similarity.compare([], [])
    assert (sim.shared, sim.containment_a, sim.containment_b, sim.jaccard) == (0, 0.0, 0.0, 0.0)


# ===========================================================================
# starter_code fingerprints are subtracted; short submissions are skipped
# ===========================================================================
def test_starter_code_fingerprints_are_excluded() -> None:
    boilerplate = "def solve(n):\n" + "\n".join(f"    x{i} = {i}" for i in range(20)) + "\n    return x0\n"
    fp_with_starter = code_similarity.fingerprint_source("python", boilerplate, starter_code=boilerplate)
    assert fp_with_starter.hashes == []


def test_submission_shorter_than_k_gram_has_no_fingerprints() -> None:
    tiny = ["V", "=", "N"]
    hashes, positions = code_similarity.fingerprints(tiny)
    assert hashes == [] and positions == []


def test_min_tokens_threshold_matches_the_documented_default() -> None:
    from app.config import settings

    assert settings.code_similarity_min_tokens == 50
    assert code_similarity.MIN_TOKENS == 50


# ===========================================================================
# code_quality — Python AST path: known McCabe values on hand-built fixtures
# ===========================================================================
def _first_function_complexity(source: str) -> dict[str, Any]:
    metrics = code_quality.python_metrics(source)
    assert metrics["function_count"] == 1
    return metrics["functions"][0]


def test_mccabe_complexity_no_branches_is_one() -> None:
    fn = _first_function_complexity("def f():\n    return 1\n")
    assert fn["cyclomatic_complexity"] == 1


def test_mccabe_complexity_one_if_is_two() -> None:
    fn = _first_function_complexity("def f(x):\n    if x:\n        return 1\n    return 0\n")
    assert fn["cyclomatic_complexity"] == 2


def test_mccabe_complexity_if_elif_else_is_three() -> None:
    source = (
        "def f(x):\n"
        "    if x == 1:\n        return 1\n"
        "    elif x == 2:\n        return 2\n"
        "    else:\n        return 3\n"
    )
    fn = _first_function_complexity(source)
    assert fn["cyclomatic_complexity"] == 3


def test_mccabe_complexity_for_plus_if_is_three() -> None:
    source = (
        "def f(xs):\n"
        "    total = 0\n"
        "    for x in xs:\n"
        "        if x:\n            total += x\n"
        "    return total\n"
    )
    fn = _first_function_complexity(source)
    assert fn["cyclomatic_complexity"] == 3


def test_mccabe_complexity_boolop_counts_each_extra_operand() -> None:
    source = "def f(a, b, c):\n    if a and b and c:\n        return 1\n    return 0\n"
    fn = _first_function_complexity(source)
    # base 1 + if (1) + BoolOp with 3 operands (+2) = 4
    assert fn["cyclomatic_complexity"] == 4


def test_nested_function_complexity_is_not_folded_into_the_parent() -> None:
    source = (
        "def outer():\n"
        "    def inner(x):\n"
        "        if x:\n            return 1\n"
        "        return 0\n"
        "    return inner\n"
    )
    metrics = code_quality.python_metrics(source)
    by_name = {f["name"]: f for f in metrics["functions"]}
    assert by_name["outer"]["cyclomatic_complexity"] == 1
    assert by_name["inner"]["cyclomatic_complexity"] == 2


def test_nesting_depth_counts_control_structures_only() -> None:
    fn = _first_function_complexity(
        "def f(x):\n    if x:\n        for i in range(x):\n            print(i)\n"
    )
    assert fn["nesting_depth"] == 2


def test_parameter_count_covers_every_argument_kind() -> None:
    fn = _first_function_complexity("def f(a, b, /, c, *args, d, **kwargs):\n    return 1\n")
    assert fn["parameter_count"] == 6


# ===========================================================================
# code_quality — smells (Python)
# ===========================================================================
def _rules(source: str) -> set[str]:
    _, findings = code_quality._python_analysis(source)  # noqa: SLF001
    return {f["rule"] for f in findings}


def test_bare_except_is_flagged() -> None:
    assert "bare_except" in _rules("def f():\n    try:\n        pass\n    except:\n        pass\n")


def test_mutable_default_literal_is_flagged() -> None:
    assert "mutable_default_arg" in _rules("def f(x=[]):\n    return x\n")


def test_mutable_default_call_is_flagged() -> None:
    assert "mutable_default_arg" in _rules("def f(x=list()):\n    return x\n")


def test_immutable_default_is_not_flagged() -> None:
    assert "mutable_default_arg" not in _rules("def f(x=None):\n    return x\n")


def test_unused_import_is_flagged() -> None:
    assert "unused_import" in _rules("import os\n\n\ndef f():\n    return 1\n")


def test_used_import_is_not_flagged() -> None:
    assert "unused_import" not in _rules("import os\n\n\ndef f():\n    return os.getcwd()\n")


def test_unused_local_is_flagged() -> None:
    assert "unused_local" in _rules("def f():\n    unused = 1\n    return 2\n")


def test_eval_and_exec_are_flagged() -> None:
    assert "eval_exec_use" in _rules("def f(s):\n    return eval(s)\n")
    assert "eval_exec_use" in _rules("def f(s):\n    exec(s)\n    return 1\n")


def test_global_is_flagged() -> None:
    assert "global_use" in _rules("x = 0\n\n\ndef f():\n    global x\n    x = 1\n")


def test_long_function_is_flagged() -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(65))
    source = f"def f():\n{body}\n    return x0\n"
    assert "long_function" in _rules(source)


def test_short_function_is_not_flagged_long() -> None:
    assert "long_function" not in _rules("def f():\n    return 1\n")


def test_deeply_nested_function_is_flagged() -> None:
    lines = ["def f():"]
    for i in range(6):
        lines.append("    " * (i + 1) + "if True:")
    lines.append("    " * 7 + "pass")
    source = "\n".join(lines) + "\n"
    assert "deep_nesting" in _rules(source)


# ===========================================================================
# Maintainability index stays in [0, 100]
# ===========================================================================
@pytest.mark.parametrize("source", [
    "def f():\n    return 1\n",
    "def f(x):\n" + "    if x:\n        x += 1\n" * 20 + "    return x\n",
    "",
])
def test_maintainability_index_stays_in_range(source: str) -> None:
    if source:
        metrics = code_quality.python_metrics(source)
        mi = metrics["maintainability_index"]
    else:
        mi = code_quality.maintainability_index(0.0, 1, 0)
    assert 0.0 <= mi <= 100.0


# ===========================================================================
# analyse() — the entry point app/code_sandbox.py submits to the worker
# ===========================================================================
def test_analyse_unsupported_language() -> None:
    result = code_quality.analyse("cobol", "IDENTIFICATION DIVISION.")
    assert result["status"] == "unsupported"
    assert result["coverage"] == {
        "available": False,
        "reason": (
            "Coverage requires instrumented execution. This analyser is pure static "
            "analysis and never runs candidate code."
        ),
    }


def test_analyse_python2_syntax_falls_back_to_token_metrics() -> None:
    result = code_quality.analyse("python", 'print "hello world"\n')
    assert result["analyser"] == "pygments-tokens"
    assert result["status"] == "complete"
    assert any(f["rule"] == "ast_parse_failed" for f in result["findings"])


def test_analyse_python_uses_the_ast_path() -> None:
    result = code_quality.analyse("python", "def f(x):\n    return x + 1\n")
    assert result["analyser"] == "python-ast"
    assert result["status"] == "complete"
    assert result["metrics"]["kind"] == "python-ast"


def test_analyse_other_languages_are_labelled_approximate() -> None:
    result = code_quality.analyse("javascript", "function f(x) {\n  return x + 1;\n}\n")
    assert result["analyser"] == "pygments-tokens"
    assert result["metrics"]["kind"] == "token-approximate"


def test_coverage_is_never_available_for_any_language() -> None:
    for language in ("python", "javascript", "rust"):
        result = code_quality.analyse(language, "x = 1\n" if language == "python" else "let x = 1;\n")
        assert result["coverage"]["available"] is False


# ===========================================================================
# A failure never crashes the sweep -- app/code_evidence.py catches broadly
# ===========================================================================
@pytest.mark.asyncio
async def test_a_timeout_is_stored_as_failed_without_raising() -> None:
    db = AsyncMock()
    with patch.object(svc, "run_isolated", AsyncMock(side_effect=FutureTimeoutError())):
        wrote = await svc._analyse_one(  # noqa: SLF001
            db, company_id=uuid.uuid4(), attempt_id=uuid.uuid4(),
            coding_question_id=uuid.uuid4(), exam_id=uuid.uuid4(),
            language="python", source="x = 1\n",
        )
    assert wrote is True
    params = db.execute.call_args.args[1]
    assert params["status"] == "failed"
    assert params["error_class"] == "TimeoutError"
    assert "x = 1" not in str(params)  # never the source


@pytest.mark.asyncio
async def test_a_pathological_input_is_stored_as_failed_not_raised() -> None:
    """A RecursionError (a deeply nested AST) must not propagate — it is
    caught the same as any other analysis failure."""
    db = AsyncMock()
    with patch.object(svc, "run_isolated", AsyncMock(side_effect=RecursionError("too deep"))):
        wrote = await svc._analyse_one(  # noqa: SLF001
            db, company_id=uuid.uuid4(), attempt_id=uuid.uuid4(),
            coding_question_id=uuid.uuid4(), exam_id=uuid.uuid4(),
            language="python", source="if True:\n" * 5000,
        )
    assert wrote is True
    params = db.execute.call_args.args[1]
    assert params["status"] == "failed"
    assert params["error_class"] == "RecursionError"


# ===========================================================================
# Redaction — pure, and never touches score or status
# ===========================================================================
def test_redact_coding_answers_strips_source_keeps_language() -> None:
    answers = {"mcq": {"q1": 2}, "coding": {"q2": {"language": "python", "source": "print(1)"}}}
    out = svc.redact_coding_answers(answers)
    assert out["coding"]["q2"]["source"] is None
    assert out["coding"]["q2"]["source_redacted"] is True
    assert out["coding"]["q2"]["language"] == "python"
    assert out["mcq"] == {"q1": 2}  # untouched


def test_redact_graded_snapshot_strips_output_keeps_score() -> None:
    snapshot = {
        "coding": {
            "q2": {"points": 100, "raw": 80, "tests": [
                {"index": 0, "passed": True, "actual_output": "3", "stderr": ""},
            ]},
        },
    }
    out = svc.redact_graded_snapshot(snapshot)
    test0 = out["coding"]["q2"]["tests"][0]
    assert test0["actual_output"] is None
    assert test0["stderr"] is None
    assert test0["passed"] is True  # the score survives
    assert out["coding"]["q2"]["raw"] == 80


def test_redact_functions_tolerate_missing_or_malformed_input() -> None:
    assert svc.redact_coding_answers(None) == {}
    assert svc.redact_graded_snapshot(None) == {}
    assert svc.redact_coding_answers({"mcq": {}}) == {"mcq": {}}


# ===========================================================================
# admin_ops's INDEPENDENT copy of the same transform
# ===========================================================================
def test_admin_ops_has_its_own_independently_written_redaction_copy() -> None:
    admin_ops_path = (
        APP.parents[1] / "admin_ops" / "app" / "code_redaction.py"
    )
    assert admin_ops_path.exists()
    src = admin_ops_path.read_text(encoding="utf-8")
    assert "def redact_coding_answers(" in src
    assert "def redact_graded_snapshot(" in src
    assert "from app" not in src.replace("from app.config", "")  # never imports data_gateway's app


# ===========================================================================
# The structural invariant: a signal is never a finding
# ===========================================================================
def test_code_evidence_never_calls_lifecycle_mutators() -> None:
    src = (APP / "code_evidence.py").read_text(encoding="utf-8")
    for name in ("record_transition", "record_final_decision", "release_hold", "_hold"):
        assert name not in src, name
    assert "UPDATE enrolments" not in src
    # The ONE exception is the retention redaction, which the freeze trigger
    # only allows when it touches ONLY answers/graded_snapshot/code_redacted_at
    # in the same statement -- never status or any score column.
    forbidden_fragments = (
        "SET status", "SET passed", "SET score_raw", "SET score_max", "SET score_percent",
    )
    for match in re.finditer(r'UPDATE exam_attempts.*?"', src, flags=re.DOTALL):
        stmt = match.group(0)
        for frag in forbidden_fragments:
            assert frag not in stmt, stmt[:200]


def test_exam_take_never_imports_code_evidence() -> None:
    src = (APP / "routers" / "exam_take.py").read_text(encoding="utf-8")
    assert "code_evidence" not in src


def test_execution_module_signature_is_unchanged() -> None:
    from app.execution import SUPPORTED_LANGUAGES, run_code

    sig = inspect.signature(run_code)
    assert list(sig.parameters) == ["language", "source", "stdin", "time_limit_ms"]
    assert len(SUPPORTED_LANGUAGES) == 10


def test_every_route_sits_behind_hr_ctx_dep() -> None:
    source = (APP / "routers" / "code_evidence.py").read_text(encoding="utf-8")
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
    assert seen >= 7


def test_no_super_admin_interviewer_or_public_router_in_the_module() -> None:
    src = (APP / "routers" / "code_evidence.py").read_text(encoding="utf-8")
    for name in ("SuperAdminCtxDep", "InterviewerCtxDep", "CurrentUserDep", "public_router"):
        assert name not in src


def test_no_agent_module_references_code_evidence() -> None:
    for base in (APP / "agents", APP.parents[2] / "shared" / "agents"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            text_ = path.read_text(encoding="utf-8").lower()
            assert "code_evidence" not in text_
            assert "code_quality_report" not in text_
            assert "code_similarity_signal" not in text_
            assert "code_integrity_finding" not in text_


def test_no_module_here_imports_an_llm_client() -> None:
    for name in ("code_evidence", "code_quality", "code_similarity", "code_sandbox"):
        tree = ast.parse((APP / f"{name}.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for word in ("gemini", "groq", "anthropic", "genai", "shared.agents", "shared.llm"):
            assert not any(word in imp.lower() for imp in imported), (name, word, imported)


# LOW-2: an ALLOW-list, not a deny-list -- a new import outside it fails this
# test loudly instead of a deny-list silently missing something nobody added
# to it. Every root actually used today by the four modules between a
# request and an analysis result: the pure analysers (code_quality,
# code_similarity), the isolation boundary (code_sandbox) and its caller
# (code_evidence). None of the four executes candidate code, so none needs
# subprocess, socket, importlib, runpy or the network stack.
_ALLOWED_IMPORT_ROOTS = {
    "__future__", "ast", "asyncio", "collections", "contextlib", "dataclasses",
    "datetime", "hashlib", "json", "math", "multiprocessing", "os", "platform",
    "resource", "typing", "uuid",
    "pygments", "structlog", "sqlalchemy",
    "app", "shared",
}
_FORBIDDEN_IMPORT_ROOTS = {"subprocess", "importlib", "runpy", "socket", "http", "urllib", "requests"}
# Banned even in ATTRIBUTE form (``os.system(...)``, ``builtins.eval(...)``,
# ``getattr(subprocess, "Popen")(...)``'s target name) -- a bare-name check
# alone misses exactly the call shapes an execution path would actually use.
_FORBIDDEN_CALL_NAMES = {"exec", "eval", "compile", "system", "popen", "__import__"}


def test_pure_modules_never_execute_candidate_code() -> None:
    """AST-level, across code_quality.py / code_similarity.py / code_sandbox.py
    / code_evidence.py (LOW-2 widened this from the first two alone)."""
    for name in ("code_quality", "code_similarity", "code_sandbox", "code_evidence"):
        tree = ast.parse((APP / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in _FORBIDDEN_IMPORT_ROOTS, (name, alias.name)
                    assert root in _ALLOWED_IMPORT_ROOTS, ("not on the allow-list", name, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in _FORBIDDEN_IMPORT_ROOTS, (name, node.module)
                assert root in _ALLOWED_IMPORT_ROOTS, ("not on the allow-list", name, node.module)
            elif isinstance(node, ast.Call):
                called = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else None
                )
                # The one legitimate hit: code_sandbox.py's own Windows check,
                # ``platform.system()`` -- returns the OS name, nothing to do
                # with ``os.system``. Named explicitly rather than dropping
                # "system" from the banned set, which would also stop
                # catching ``os.system(...)``.
                is_platform_system = (
                    isinstance(node.func, ast.Attribute) and node.func.attr == "system"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "platform"
                )
                assert is_platform_system or called not in _FORBIDDEN_CALL_NAMES, (name, called)


def test_code_sandbox_only_imports_resource_on_posix() -> None:
    src = (APP / "code_sandbox.py").read_text(encoding="utf-8")
    assert "import resource" in src
    assert 'platform.system() == "Windows"' in src
    # subprocess/socket/etc. must not appear here either -- isolation, not execution.
    for word in ("subprocess", "socket", "importlib", "runpy"):
        assert word not in src


def test_audit_details_never_reference_the_raw_source_or_entry() -> None:
    for fn in (svc.source_for, svc.compare_view):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_audit":
                for kw in node.keywords:
                    if kw.arg != "details":
                        continue
                    names = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
                    assert "entry" not in names
                    assert "source" not in names
                    assert "text" not in names


# ===========================================================================
# The four new tables are in the erasure inventory, and the executor really
# writes what it claims to
# ===========================================================================
def test_new_tables_are_in_the_erasure_inventory() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(encoding="utf-8")
    for table in ("code_quality_reports", "code_fingerprints", "code_similarity_signals",
                  "code_integrity_findings"):
        assert f'"{table}"' in inv, table
    assert "DELETE FROM code_quality_reports" in inv
    assert "DELETE FROM code_fingerprints" in inv
    assert "DELETE FROM code_similarity_signals" in inv
    assert "UPDATE code_integrity_findings SET rationale = '[redacted]'" in inv
    # 1.8 when D3's step 5h joined; bumped to 1.9 when PH4-D4's step 5i did.
    assert 'executor_version": "1.9"' in inv


def _erasure_dict_keys(source: str, dict_name: str) -> set[str]:
    """Every string key of ``ERASED_TABLES``/``EXCLUDED_TABLES``, read by
    ``ast`` rather than imported — admin_ops's ``app`` package collides with
    data_gateway's own (``tests/test_erasure_inventory.py``'s own docstring
    explains why import is the wrong tool here)."""
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


def test_exam_attempts_moved_from_excluded_to_erased() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(encoding="utf-8")
    erased = _erasure_dict_keys(inv, "ERASED_TABLES")
    excluded = _erasure_dict_keys(inv, "EXCLUDED_TABLES")
    assert "exam_attempts" in erased
    assert "exam_attempts" not in excluded
    for table in ("code_quality_reports", "code_fingerprints", "code_similarity_signals",
                  "code_integrity_findings"):
        assert table in erased
        assert table not in excluded


# ===========================================================================
# MEDIUM-3: excerpts are built from the matched regions, not the whole file
# ===========================================================================
def test_excerpt_from_regions_pads_three_lines_around_a_match() -> None:
    text_ = "\n".join(f"line{i}" for i in range(1, 51))  # lines 1..50
    excerpt = svc._excerpt_from_regions(text_, [(20, 22)])  # noqa: SLF001
    lines = excerpt.splitlines()
    assert lines[0] == "line17"
    assert lines[-1] == "line25"
    assert len(lines) == 9  # 17..25 inclusive


def test_excerpt_from_regions_merges_touching_context() -> None:
    text_ = "\n".join(f"line{i}" for i in range(1, 51))
    # (10,12) padded is (7,15); (14,16) padded is (11,19) -- these overlap,
    # so the merged block is one run with no "..." separator.
    excerpt = svc._excerpt_from_regions(text_, [(10, 12), (14, 16)])  # noqa: SLF001
    assert "..." not in excerpt
    assert excerpt.splitlines()[0] == "line7"
    assert excerpt.splitlines()[-1] == "line19"


def test_excerpt_from_regions_separates_distant_matches_with_an_ellipsis() -> None:
    text_ = "\n".join(f"line{i}" for i in range(1, 51))
    excerpt = svc._excerpt_from_regions(text_, [(1, 2), (40, 41)])  # noqa: SLF001
    assert "..." in excerpt


def test_excerpt_from_regions_is_capped_at_200_lines() -> None:
    text_ = "\n".join(f"line{i}" for i in range(1, 1000))
    excerpt = svc._excerpt_from_regions(text_, [(1, 900)])  # noqa: SLF001
    assert len(excerpt.splitlines()) <= 200


def test_excerpt_from_regions_falls_back_to_a_capped_head_with_no_regions() -> None:
    text_ = "\n".join(f"line{i}" for i in range(1, 300))
    excerpt = svc._excerpt_from_regions(text_, [])  # noqa: SLF001
    lines = excerpt.splitlines()
    assert len(lines) == 200
    assert lines[0] == "line1"


def test_excerpt_from_regions_never_returns_the_rest_of_a_long_program() -> None:
    """MEDIUM-3's own scenario: a match near the top of a long program must
    not pull the rest of the file in alongside it."""
    text_ = "\n".join(f"line{i}" for i in range(1, 1000))
    excerpt = svc._excerpt_from_regions(text_, [(5, 6)])  # noqa: SLF001
    assert "line999" not in excerpt
    assert len(excerpt.splitlines()) < 20


def test_excerpt_from_regions_handles_empty_text() -> None:
    assert svc._excerpt_from_regions(None, [(1, 2)]) == ""  # noqa: SLF001
    assert svc._excerpt_from_regions("", []) == ""  # noqa: SLF001


# ===========================================================================
# MEDIUM-4: the pure comparison-scoring helpers run off the event loop
# ===========================================================================
_THRESHOLDS = {"min_containment": 0.5, "min_shared": 10, "max_df": 0.4, "k": 15, "w": 8}


def test_score_new_attempt_finds_an_overlapping_candidate_above_threshold() -> None:
    new_id, other_id = uuid.uuid4(), uuid.uuid4()
    shared_hashes = set(range(1, 21))  # 20 shared -- clears min_shared=10
    scored = svc._score_new_attempt(  # noqa: SLF001
        new_id=new_id, new_hashes_filtered=shared_hashes, new_tokens=100,
        candidates=[(other_id, list(shared_hashes), 100)], boilerplate=set(), thresholds=_THRESHOLDS,
    )
    assert len(scored) == 1
    assert {scored[0]["low"], scored[0]["high"]} == {new_id, other_id}
    assert scored[0]["shared"] == 20
    assert scored[0]["clow"] == pytest.approx(1.0)


def test_score_new_attempt_drops_a_candidate_below_the_shared_threshold() -> None:
    scored = svc._score_new_attempt(  # noqa: SLF001
        new_id=uuid.uuid4(), new_hashes_filtered={1, 2, 3}, new_tokens=100,
        candidates=[(uuid.uuid4(), [1, 2, 3], 100)], boilerplate=set(), thresholds=_THRESHOLDS,
    )
    assert scored == []


def test_score_new_attempt_subtracts_boilerplate_from_the_candidate_too() -> None:
    shared = set(range(1, 21))
    boilerplate = set(range(1, 15))  # leaves only 15..20 -- six shared, below min_shared
    scored = svc._score_new_attempt(  # noqa: SLF001
        new_id=uuid.uuid4(), new_hashes_filtered=shared - boilerplate, new_tokens=100,
        candidates=[(uuid.uuid4(), list(shared), 100)], boilerplate=boilerplate, thresholds=_THRESHOLDS,
    )
    assert scored == []


def test_score_new_attempt_orders_low_high_the_same_way_postgres_does() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    shared = set(range(1, 21))
    scored = svc._score_new_attempt(  # noqa: SLF001
        new_id=a, new_hashes_filtered=shared, new_tokens=50,
        candidates=[(b, list(shared), 70)], boilerplate=set(), thresholds=_THRESHOLDS,
    )
    low, high = sorted((a, b))
    assert scored[0]["low"] == low
    assert scored[0]["high"] == high
    assert scored[0]["tlow"] == (50 if low == a else 70)
    assert scored[0]["thigh"] == (70 if low == a else 50)


def test_score_against_reference_matches_the_old_containment_semantics() -> None:
    shared = set(range(1, 21))
    result = svc._score_against_reference(  # noqa: SLF001
        hashes_filtered=shared, token_count=80, ref_hashes=shared, ref_token_count=90,
        thresholds=_THRESHOLDS,
    )
    assert result is not None
    assert result["shared"] == 20
    assert result["tokens"] == 80
    assert result["ref_tokens"] == 90
    assert result["containment"] == pytest.approx(1.0)


def test_score_against_reference_drops_below_threshold() -> None:
    assert svc._score_against_reference(  # noqa: SLF001
        hashes_filtered={1, 2}, token_count=60, ref_hashes={1, 2}, ref_token_count=60,
        thresholds=_THRESHOLDS,
    ) is None


def test_score_against_reference_handles_empty_sides() -> None:
    assert svc._score_against_reference(  # noqa: SLF001
        hashes_filtered=set(), token_count=60, ref_hashes={1, 2}, ref_token_count=60,
        thresholds=_THRESHOLDS,
    ) is None


@pytest.mark.asyncio
async def test_compare_question_is_a_noop_with_no_new_attempts() -> None:
    """MEDIUM-4: nothing new this pass means zero DB round trips, not a
    silent full re-scan."""
    db = AsyncMock()
    result = await svc._compare_question(  # noqa: SLF001
        db, company_id=uuid.uuid4(), exam_id=uuid.uuid4(), coding_question_id=uuid.uuid4(),
        new_attempt_ids=[],
    )
    assert result == 0
    db.execute.assert_not_called()


def test_compare_question_uses_the_gin_overlap_index_not_a_full_scan() -> None:
    """Pins that the implementation actually runs the `hashes &&` query the
    migration's docstring promises against ix_code_fingerprints_hashes — it
    used to only be a promise (MEDIUM-4)."""
    src = (APP / "code_evidence.py").read_text(encoding="utf-8")
    assert "hashes && CAST(:new_hashes AS bigint[])" in src
    assert "new_attempt_ids" in src
    assert "code_similarity_max_candidates" in src


def test_compare_question_runs_scoring_off_the_event_loop() -> None:
    src = (APP / "code_evidence.py").read_text(encoding="utf-8")
    assert src.count("await asyncio.to_thread(") == 2
    assert "_score_new_attempt," in src
    assert "_score_against_reference," in src


def test_the_on_demand_analysis_route_is_rate_limited_per_company() -> None:
    """MEDIUM-4."""
    src = (APP / "routers" / "code_evidence.py").read_text(encoding="utf-8")
    assert "rate_limit_company(" in src
    assert "code_analysis_ondemand_per_minute" in src
