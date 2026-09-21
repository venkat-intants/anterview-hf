"""PH4-D3 — static code quality analysis. PURE: stdlib + Pygments only. NEVER
executes candidate code — ``ast.parse`` and Pygments tokenising only inspect
syntax, never run it.

Python gets REAL per-function metrics from the AST: McCabe cyclomatic
complexity, nesting depth, length and parameter count per function; a
Halstead volume/difficulty/effort and a maintainability index derived from
the whole submission; and smells (bare ``except``, a mutable default
argument, an unused import or local, ``eval``/``exec``, ``global``/
``nonlocal``, an over-long or over-nested function).

Every other language in ``SUPPORTED_LANGUAGES`` — and any Python source
``ast.parse`` cannot parse (Python 2 syntax, most often) — gets a TOKEN-LEVEL
APPROXIMATION from a Pygments lexer: decision-point density instead of a true
per-function cyclomatic count, brace-depth (or, for Ruby, keyword-depth)
nesting, an approximate Halstead/maintainability index, a comment ratio and
long lines, and a short list of pattern smells. ``analyser`` on the stored
report says which kind a given result is — the UI must label the token path
an approximation, not present it as equivalent to the AST path.

Test COVERAGE is not available for ANY language — it needs instrumented
execution, and this analyser is static by design. ``analyse()`` always
returns ``coverage={"available": False, "reason": ...}``; the HR console
shows the coding round's existing sandbox TEST RESULTS instead, labelled as
results, not coverage.
"""

from __future__ import annotations

import ast
import math
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pygments
from pygments.token import Comment, Keyword, Name, Number, Operator, Punctuation, String

from app.code_similarity import (
    K_GRAM,
    PYGMENTS_LANGUAGE_ALIASES,
    UnsupportedLanguageError,
    get_lexer,
    tokenize_with_lines,
)

ANALYSER_VERSION = f"cq-1.0.0+pygments-{pygments.__version__}"

_NO_COVERAGE: dict[str, Any] = {
    "available": False,
    "reason": (
        "Coverage requires instrumented execution. This analyser is pure static "
        "analysis and never runs candidate code."
    ),
}

_LONG_FUNCTION_LINES = 60
_DEEP_NESTING = 4
_LONG_LINE_CHARS = 120
_MUTABLE_DEFAULT_CALLS = frozenset({"list", "dict", "set", "Counter", "defaultdict", "OrderedDict"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def analyse(language: str, source: str, starter_code: str | None = None) -> dict[str, Any]:
    """The one function ``app/code_sandbox.py`` submits to the isolated
    worker. ``starter_code`` is accepted for interface symmetry with
    ``app.code_similarity.fingerprint_source`` (which needs it to subtract
    boilerplate); quality metrics never read it.

    Returns a JSON-safe dict shaped for ``code_quality_reports``: ``analyser``,
    ``status``, ``metrics``, ``findings``, ``coverage``. Never raises for
    ordinary inputs — a language outside ``SUPPORTED_LANGUAGES`` comes back
    ``status='unsupported'`` rather than an exception, so the caller's
    try/except is for genuine failures (a pathological input, a bug here).
    """
    del starter_code  # see docstring
    if language not in PYGMENTS_LANGUAGE_ALIASES:
        return {
            "analyser": "pygments-tokens", "status": "unsupported",
            "metrics": {}, "findings": [], "coverage": _NO_COVERAGE,
        }
    if language == "python":
        try:
            metrics, findings = _python_analysis(source)
            return {
                "analyser": "python-ast", "status": "complete",
                "metrics": metrics, "findings": findings, "coverage": _NO_COVERAGE,
            }
        except SyntaxError:
            # Most often Python 2 syntax. Falls back to the token path rather
            # than failing the whole report.
            metrics, findings = _token_analysis(language, source)
            findings = [
                {
                    "rule": "ast_parse_failed", "severity": "info", "line": 1,
                    "message": "Could not parse as Python 3 — falling back to "
                               "token-level (approximate) metrics.",
                },
                *findings,
            ]
            return {
                "analyser": "pygments-tokens", "status": "complete",
                "metrics": metrics, "findings": findings, "coverage": _NO_COVERAGE,
            }
    metrics, findings = _token_analysis(language, source)
    return {
        "analyser": "pygments-tokens", "status": "complete",
        "metrics": metrics, "findings": findings, "coverage": _NO_COVERAGE,
    }


# ---------------------------------------------------------------------------
# Halstead + maintainability index (shared by both analysers)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Halstead:
    n1: int
    n2: int
    total_operators: int
    total_operands: int
    vocabulary: int
    length: int
    volume: float
    difficulty: float
    effort: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "distinct_operators": self.n1, "distinct_operands": self.n2,
            "total_operators": self.total_operators, "total_operands": self.total_operands,
            "vocabulary": self.vocabulary, "length": self.length,
            "volume": round(self.volume, 2), "difficulty": round(self.difficulty, 2),
            "effort": round(self.effort, 2),
        }


def _halstead(operators: list[str], operands: list[str]) -> Halstead:
    n1, n2 = len(set(operators)), len(set(operands))
    big_n1, big_n2 = len(operators), len(operands)
    vocabulary = n1 + n2
    length = big_n1 + big_n2
    volume = length * math.log2(vocabulary) if vocabulary > 1 else 0.0
    difficulty = (n1 / 2) * (big_n2 / n2) if n1 > 0 and n2 > 0 else 0.0
    return Halstead(n1, n2, big_n1, big_n2, vocabulary, length, volume, difficulty, difficulty * volume)


def maintainability_index(volume: float, complexity: int, loc: int) -> float:
    """The classic SEI formula, normalised to 0-100 and clamped. ``complexity``
    and ``loc`` are aggregate figures for the WHOLE submission (see
    ``python_metrics``'s ``complexity_total`` and ``lines_of_code``), not a
    per-function value."""
    v = max(volume, 1.0)
    lines = max(loc, 1)
    raw = 171 - 5.2 * math.log(v) - 0.23 * complexity - 16.2 * math.log(lines)
    return round(max(0.0, min(100.0, raw * 100.0 / 171.0)), 2)


def _duplication_ratio(language: str, source: str) -> float:
    """Share of this submission's OWN k-grams that recur inside it — a
    within-file copy-paste signal, independent of ``code_similarity``'s
    cross-submission comparison. 0.0 for source shorter than one k-gram."""
    try:
        tokens = [t for t, _ in tokenize_with_lines(language, source)]
    except UnsupportedLanguageError:
        return 0.0
    if len(tokens) < K_GRAM:
        return 0.0
    grams = [tuple(tokens[i : i + K_GRAM]) for i in range(len(tokens) - K_GRAM + 1)]
    counts = Counter(grams)
    repeated = sum(c for c in counts.values() if c > 1)
    return round(repeated / len(grams), 4)


# ---------------------------------------------------------------------------
# Python — the AST path
# ---------------------------------------------------------------------------
_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_DECISION_TYPES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.Assert, ast.IfExp)
_NESTING_TYPES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)


def _walk_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Like ``ast.walk`` but does not descend into a nested function/lambda/
    class — each of those is measured independently, so its decisions are
    counted once, against it, not folded into the enclosing function too."""
    for child in ast.iter_child_nodes(node):
        yield child
        if isinstance(child, _SCOPE_BOUNDARY):
            continue
        yield from _walk_own_scope(child)


def _cyclomatic_complexity(node: ast.AST, *, stop_at_nested: bool) -> int:
    """1 + one per decision point: ``if``/``elif`` (each ``elif`` is its own
    nested ``ast.If``), ``for``, ``while``, ``except``, ``assert``, a ternary,
    each additional operand of a boolean ``and``/``or`` chain, and each
    comprehension clause (its implicit ``for`` plus every ``if``).

    ``stop_at_nested=True`` scopes the count to THIS function only (used for
    the per-function table); ``False`` walks the whole submission, including
    nested defs, for the aggregate figure the maintainability index uses.
    """
    complexity = 1
    nodes = _walk_own_scope(node) if stop_at_nested else ast.walk(node)
    for child in nodes:
        if isinstance(child, _DECISION_TYPES):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            complexity += 1 + len(child.ifs)
    return complexity


def _max_nesting(node: ast.AST) -> int:
    def depth(n: ast.AST, current: int) -> int:
        best = current
        for child in ast.iter_child_nodes(n):
            if isinstance(child, _SCOPE_BOUNDARY):
                continue
            nxt = current + 1 if isinstance(child, _NESTING_TYPES) else current
            best = max(best, depth(child, nxt))
        return best

    return depth(node, 0)


def _parameter_count(args: ast.arguments) -> int:
    n = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
    if args.vararg is not None:
        n += 1
    if args.kwarg is not None:
        n += 1
    return n


def _function_metrics(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    end = node.end_lineno or node.lineno
    return {
        "name": node.name,
        "line": node.lineno,
        "cyclomatic_complexity": _cyclomatic_complexity(node, stop_at_nested=True),
        "nesting_depth": _max_nesting(node),
        "length_lines": end - node.lineno + 1,
        "parameter_count": _parameter_count(node.args),
    }


_PY_UNARY_KEEP = (ast.Return, ast.Yield, ast.YieldFrom, ast.Raise, ast.Assert, ast.Delete)


def _python_operators_operands(tree: ast.AST) -> tuple[list[str], list[str]]:
    operators: list[str] = []
    operands: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp | ast.BoolOp | ast.UnaryOp):
            operators.append(type(node.op).__name__)
        elif isinstance(node, ast.Compare):
            operators.extend(type(op).__name__ for op in node.ops)
        elif isinstance(node, ast.AugAssign):
            operators.append(f"{type(node.op).__name__}=")
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            operators.append("=")
        elif isinstance(node, ast.Call):
            operators.append("call()")
        elif isinstance(node, ast.Subscript):
            operators.append("[]")
        elif isinstance(node, ast.Attribute):
            operators.append(".")
            operands.append(node.attr)
        elif isinstance(node, ast.If | ast.IfExp):
            operators.append("if")
        elif isinstance(node, ast.For | ast.AsyncFor):
            operators.append("for")
        elif isinstance(node, ast.While):
            operators.append("while")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            operators.append("def")
        elif isinstance(node, ast.ClassDef):
            operators.append("class")
        elif isinstance(node, ast.Lambda):
            operators.append("lambda")
        elif isinstance(node, ast.With | ast.AsyncWith):
            operators.append("with")
        elif isinstance(node, ast.Try):
            operators.append("try")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            operators.append("import")
        elif isinstance(node, ast.Global | ast.Nonlocal):
            operators.append("global")
        elif isinstance(node, _PY_UNARY_KEEP):
            operators.append(type(node).__name__.lower())
        elif isinstance(node, ast.Name):
            operands.append(node.id)
        elif isinstance(node, ast.arg):
            operands.append(node.arg)
        elif isinstance(node, ast.Constant):
            operands.append(f"{type(node.value).__name__}:{node.value!r}"[:60])
    return operators, operands


def python_metrics(source: str) -> dict[str, Any]:
    """Real, per-function metrics from the AST — never executes ``source``."""
    tree = ast.parse(source)
    functions = [
        _function_metrics(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    operators, operands = _python_operators_operands(tree)
    halstead = _halstead(operators, operands)
    loc = max(1, source.count("\n") + 1)
    complexity_total = _cyclomatic_complexity(tree, stop_at_nested=False)
    return {
        "kind": "python-ast",
        "functions": functions,
        "function_count": len(functions),
        "complexity_total": complexity_total,
        "complexity_avg": (
            round(complexity_total / len(functions), 2) if functions else float(complexity_total)
        ),
        "halstead": halstead.to_dict(),
        "maintainability_index": maintainability_index(halstead.volume, complexity_total, loc),
        "lines_of_code": loc,
        "duplication_ratio": _duplication_ratio("python", source),
    }


def python_smells(tree: ast.AST) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append({
                "rule": "bare_except", "severity": "warning", "line": node.lineno,
                "message": "A bare `except:` catches everything, including "
                           "KeyboardInterrupt and SystemExit.",
            })
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("eval", "exec")
        ):
            findings.append({
                "rule": "eval_exec_use", "severity": "warning", "line": node.lineno,
                "message": f"`{node.func.id}()` runs a string as code.",
            })
        elif isinstance(node, ast.Global | ast.Nonlocal):
            findings.append({
                "rule": "global_use", "severity": "info", "line": node.lineno,
                "message": "`global`/`nonlocal` — mutable shared state across calls.",
            })
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            findings.extend(_mutable_default_findings(node))
            findings.extend(_unused_local_findings(node))
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > _LONG_FUNCTION_LINES:
                findings.append({
                    "rule": "long_function", "severity": "info", "line": node.lineno,
                    "message": f"`{node.name}` is {length} lines long.",
                })
            nesting = _max_nesting(node)
            if nesting > _DEEP_NESTING:
                findings.append({
                    "rule": "deep_nesting", "severity": "warning", "line": node.lineno,
                    "message": f"`{node.name}` nests {nesting} levels deep.",
                })
    findings.extend(_unused_import_findings(tree))
    return findings


def _mutable_default_findings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
    for default in defaults:
        is_literal = isinstance(default, ast.List | ast.Dict | ast.Set)
        is_call = (
            isinstance(default, ast.Call)
            and isinstance(default.func, ast.Name)
            and default.func.id in _MUTABLE_DEFAULT_CALLS
        )
        if is_literal or is_call:
            findings.append({
                "rule": "mutable_default_arg", "severity": "warning", "line": default.lineno,
                "message": f"`{node.name}` has a mutable default argument, shared across "
                           "every call that omits it.",
            })
    return findings


def _unused_local_findings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    stored: dict[str, int] = {}
    for child in _walk_own_scope(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store) and not child.id.startswith("_"):
            stored.setdefault(child.id, child.lineno)
    loaded = {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }
    return [
        {
            "rule": "unused_local", "severity": "info", "line": line,
            "message": f"`{name}` is assigned but never used.",
        }
        for name, line in stored.items()
        if name not in loaded
    ]


def _unused_import_findings(tree: ast.AST) -> list[dict[str, Any]]:
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                imported.setdefault(name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.setdefault(alias.asname or alias.name, node.lineno)
    used = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return [
        {
            "rule": "unused_import", "severity": "info", "line": line,
            "message": f"`{name}` is imported but never used.",
        }
        for name, line in imported.items()
        if name not in used
    ]


def _python_analysis(source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tree = ast.parse(source)
    return python_metrics(source), python_smells(tree)


# ---------------------------------------------------------------------------
# Every other language — the Pygments token path (labelled approximate)
# ---------------------------------------------------------------------------
_DECISION_KEYWORDS = frozenset({
    "if", "elif", "for", "foreach", "while", "case", "switch", "catch", "except",
    "when", "unless", "until",
})
_DECISION_OPERATORS = frozenset({"&&", "||", "and", "or", "?"})
_RUBY_NEST_OPEN = frozenset({"def", "class", "module", "if", "unless", "while", "until", "case", "do", "begin"})
_RUBY_NEST_CLOSE = frozenset({"end"})


def _token_operators_operands(language: str, source: str) -> tuple[list[str], list[str]]:
    lexer = get_lexer(language)
    operators: list[str] = []
    operands: list[str] = []
    for ttype, value in lexer.get_tokens(source):
        text = value.strip()
        if not text or ttype in Comment:
            continue
        if ttype in String:
            operands.append("S")
        elif ttype in Number:
            operands.append("N")
        elif ttype in Name:
            operands.append("V")
        elif ttype in Operator or ttype in Keyword or ttype in Punctuation:
            operators.append(text)
    return operators, operands


def _decision_count(tokens: list[tuple[Any, str]]) -> int:
    count = 0
    for ttype, value in tokens:
        text = value.strip().lower()
        if not text:
            continue
        if ttype in Keyword and text in _DECISION_KEYWORDS or ttype in Operator and text in _DECISION_OPERATORS:
            count += 1
    return count


def _nesting_depth(language: str, tokens: list[tuple[Any, str]]) -> int:
    if language == "ruby":
        depth = max_depth = 0
        for ttype, value in tokens:
            text = value.strip().lower()
            if ttype in Keyword and text in _RUBY_NEST_OPEN:
                depth += 1
                max_depth = max(max_depth, depth)
            elif ttype in Keyword and text in _RUBY_NEST_CLOSE:
                depth = max(0, depth - 1)
        return max_depth
    depth = max_depth = 0
    for ttype, value in tokens:
        text = value.strip()
        if ttype in Punctuation and text == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ttype in Punctuation and text == "}":
            depth = max(0, depth - 1)
    return max_depth


def token_metrics(language: str, source: str) -> dict[str, Any]:
    """Token-level APPROXIMATIONS for the nine languages Python's ``ast``
    cannot parse. Every figure here is labelled ``kind='token-approximate'``
    on the returned dict — never presented as equal to ``python_metrics``."""
    lexer = get_lexer(language)
    tokens = list(lexer.get_tokens(source))
    non_comment = [(t, v) for t, v in tokens if v.strip() and t not in Comment]
    total_chars = sum(len(v) for _, v in tokens) or 1
    comment_chars = sum(len(v) for t, v in tokens if t in Comment)
    loc = max(1, source.count("\n") + 1)
    long_lines = sum(1 for ln in source.splitlines() if len(ln) > _LONG_LINE_CHARS)
    decisions = _decision_count(tokens)
    nesting = _nesting_depth(language, tokens)
    operators, operands = _token_operators_operands(language, source)
    halstead = _halstead(operators, operands)
    return {
        "kind": "token-approximate",
        "decision_points": decisions,
        "decision_point_density": round(decisions / max(1, len(non_comment)), 4),
        "max_nesting_depth": nesting,
        "comment_ratio": round(comment_chars / total_chars, 4),
        "long_line_count": long_lines,
        "lines_of_code": loc,
        "halstead": halstead.to_dict(),
        "maintainability_index": maintainability_index(halstead.volume, decisions + 1, loc),
        "duplication_ratio": _duplication_ratio(language, source),
    }


def token_smells(language: str, source: str) -> list[dict[str, Any]]:
    lexer = get_lexer(language)
    stream: list[tuple[int, Any, str]] = []
    line = 1
    for ttype, value in lexer.get_tokens(source):
        start_line = line
        line += value.count("\n")
        text = value.strip()
        if text:
            stream.append((start_line, ttype, text))

    findings: list[dict[str, Any]] = []
    for i, (ln, ttype, text) in enumerate(stream):
        if ttype in Keyword and text.lower() == "catch":
            j = i + 1
            while j < len(stream) and stream[j][2] != "{" and j - i <= 10:
                j += 1
            if j < len(stream) and stream[j][2] == "{" and j + 1 < len(stream) and stream[j + 1][2] == "}":
                findings.append({
                    "rule": "empty_catch", "severity": "warning", "line": ln,
                    "message": "An empty catch block silently swallows the error.",
                })

    if language in ("javascript", "typescript"):
        findings.extend(
            {"rule": "var_usage", "severity": "info", "line": ln,
             "message": "`var` — prefer `let`/`const`."}
            for ln, ttype, text in stream if ttype in Keyword and text == "var"
        )
    if language == "typescript":
        findings.extend(
            {"rule": "any_type", "severity": "info", "line": ln,
             "message": "`any` discards TypeScript's type checking."}
            for ln, ttype, text in stream if text == "any" and (ttype in Keyword or ttype in Name)
        )
    if language == "rust":
        findings.extend(
            {"rule": "unsafe_block", "severity": "warning", "line": ln,
             "message": "`unsafe` bypasses Rust's safety guarantees."}
            for ln, ttype, text in stream if ttype in Keyword and text == "unsafe"
        )
        findings.extend(
            {"rule": "unwrap_call", "severity": "info", "line": ln,
             "message": "`.unwrap()` panics instead of handling the error."}
            for ln, _ttype, text in stream if text == "unwrap"
        )
    if language in ("c", "cpp"):
        findings.extend(
            {"rule": "goto_usage", "severity": "warning", "line": ln,
             "message": "`goto` — unstructured control flow."}
            for ln, ttype, text in stream if ttype in Keyword and text == "goto"
        )

    magic = [
        (ln, text) for ln, ttype, text in stream
        if ttype in Number and text not in ("0", "1", "2", "10", "100")
    ]
    findings.extend(
        {"rule": "magic_number", "severity": "info", "line": ln,
         "message": f"Magic number {text} — consider a named constant."}
        for ln, text in magic[:20]
    )
    return findings


def _token_analysis(language: str, source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return token_metrics(language, source), token_smells(language, source)
