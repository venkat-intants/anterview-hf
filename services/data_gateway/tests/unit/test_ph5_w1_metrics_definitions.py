"""PH5 Wave 1 / C2 — the governed metric registry, offline.

Real Postgres coverage (flag semantics, cohorts, tenant isolation, permissions,
the members drill-down, cross-consumer consistency) lives in
``tests/integration/test_ph5_w1_metrics.py``. This file is everything that
does not need a database: registry validation (each invalid shape raises),
the lock file's relationship to the registry, the cross-module text/tuple
pins the design calls for, the check-in governance rules (PH5-C1), and the
no-write structural guarantee.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from datetime import date

import pytest

from app import hire_checkins as checkins_module
from app import interviewer_scorecards as scorecards_module
from app.agents import tools as tools_module
from app.agents import watch_runner as watch_runner_module
from app.application_source import SOURCE_LABELS, SOURCES
from app.metrics import definitions as d

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _flag(name: str, sql: str = "TRUE", version: int = 1, effective: date = date(2026, 9, 22)) -> d.Flag:
    return d.Flag(name, version, effective, "test flag", sql)


# ===========================================================================
# validate_registry — each invalid shape raises
# ===========================================================================
def test_duplicate_flag_key_raises() -> None:
    with pytest.raises(d.MetricDefinitionError, match="duplicate"):
        d._index((_flag("a"), _flag("a")))


def test_duplicate_metric_key_raises() -> None:
    m = d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("application",), (),
                 numerator=("a@1",))
    with pytest.raises(d.MetricDefinitionError, match="duplicate"):
        d._index_metrics((m, m))


def test_unknown_flag_reference_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("application",), (),
                        numerator=("nope@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="unknown flag reference"):
        d.validate_registry(flags, {}, metrics)


def test_unversioned_flag_reference_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("application",), (),
                        numerator=("a",))
    }
    with pytest.raises(d.MetricDefinitionError, match="unversioned flag reference"):
        d.validate_registry(flags, {}, metrics)


def test_unknown_cohort_basis_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("nonsense",), (),
                        numerator=("a@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="cohort_bases"):
        d.validate_registry(flags, {}, metrics)


def test_unknown_dimension_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("application",),
                        ("nonsense",), numerator=("a@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="dimensions"):
        d.validate_registry(flags, {}, metrics)


def test_unknown_kind_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "nonsense", "M", "d", ("application",), (),
                        numerator=("a@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="unknown kind"):
        d.validate_registry(flags, {}, metrics)


def test_count_metric_with_a_denominator_raises() -> None:
    flags = {"a@1": _flag("a"), "b@1": _flag("b")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("application",), (),
                        numerator=("a@1",), denominator=("b@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="count metric"):
        d.validate_registry(flags, {}, metrics)


def test_rate_metric_with_no_denominator_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "rate", "M", "d", ("application",), (),
                        numerator=("a@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="rate metric"):
        d.validate_registry(flags, {}, metrics)


def test_rate_numerator_not_a_superset_of_denominator_raises() -> None:
    """The 175%-conversion fix: this is structural, not a convention."""
    flags = {"a@1": _flag("a"), "b@1": _flag("b"), "c@1": _flag("c")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "rate", "M", "d", ("application",), (),
                        numerator=("a@1",), denominator=("b@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="superset"):
        d.validate_registry(flags, {}, metrics)


def test_median_without_a_measure_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "median", "M", "d", ("hire",), (),
                        numerator=("a@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="median"):
        d.validate_registry(flags, {}, metrics)


def test_mean_with_unknown_measure_reference_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "mean", "M", "d", ("hire",), (),
                        numerator=("a@1",), measure="nope@1")
    }
    with pytest.raises(d.MetricDefinitionError, match="unknown or unversioned measure"):
        d.validate_registry(flags, {}, metrics)


def test_distribution_without_buckets_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "distribution", "M", "d", ("hire",), (),
                        numerator=("a@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="distribution metric"):
        d.validate_registry(flags, {}, metrics)


def test_distribution_bucket_referencing_unknown_flag_raises() -> None:
    flags = {"a@1": _flag("a")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "distribution", "M", "d", ("hire",), (),
                        numerator=("a@1",), buckets=("nope@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="unknown flag reference"):
        d.validate_registry(flags, {}, metrics)


def test_effective_from_running_backwards_raises() -> None:
    flags = {
        "a@1": _flag("a", effective=date(2026, 9, 22)),
        "a@2": _flag("a", version=2, effective=date(2026, 1, 1)),
    }
    with pytest.raises(d.MetricDefinitionError, match="does not come strictly after"):
        d.validate_registry(flags, {}, {})


def test_effective_from_tied_between_versions_raises() -> None:
    flags = {
        "a@1": _flag("a", effective=date(2026, 9, 22)),
        "a@2": _flag("a", version=2, effective=date(2026, 9, 22)),
    }
    with pytest.raises(d.MetricDefinitionError, match="does not come strictly after"):
        d.validate_registry(flags, {}, {})


def test_checkin_metric_with_an_unsafe_dimension_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """PH5-C1 "check-in-safe grouping": a metric reading a check-in flag may
    only be grouped by CHECKIN_SAFE_DIMENSIONS. DIMENSIONS is widened here (a
    hypothetical future dimension) so the test isolates THIS rule from the
    ordinary "dimensions must be a known dimension" check, which would
    otherwise fire first for a made-up dimension name.
    """
    monkeypatch.setattr(d, "DIMENSIONS", d.DIMENSIONS | {"future_dimension"})
    flags = {"checkin_recorded@1": _flag("checkin_recorded")}
    metrics = {
        "m@1": d.Metric("m", 1, date(2026, 9, 22), "count", "M", "d", ("hire",),
                        ("future_dimension",), numerator=("checkin_recorded@1",))
    }
    with pytest.raises(d.MetricDefinitionError, match="check-in data"):
        d.validate_registry(flags, {}, metrics)


def test_the_real_registry_validates() -> None:
    """The module already ran this at import; re-running confirms it is not
    a fluke of import order."""
    d.validate_registry(d.FLAGS, d.MEASURES, d.METRICS)


# ===========================================================================
# The lock file
# ===========================================================================
def test_every_registry_entry_matches_its_lock_entry() -> None:
    lock = d.load_lock()
    entries = d.build_lock_entries()
    assert set(lock) == set(entries), set(lock) ^ set(entries)
    for key, entry in entries.items():
        assert lock[key]["sha256"] == entry["sha256"], f"{key} hash drifted from its lock entry"


def test_every_lock_entry_has_effective_from_on_or_after_added_on() -> None:
    for key, entry in d.load_lock().items():
        assert entry["effective_from"] >= entry["added_on"], key


def test_registry_hash_is_a_hash_of_the_whole_lock_file() -> None:
    assert d._registry_hash() == d.REGISTRY_HASH
    assert len(d.REGISTRY_HASH) == 64


def test_a_metrics_hash_excludes_its_dimensions() -> None:
    """Adding a grouping must never force a metric's version to change."""
    base = d.METRICS["application_to_hire@1"]
    with_other_dims = d.Metric(
        base.name, base.version, base.effective_from, base.kind, base.label,
        base.description, base.cohort_bases, ("requisition",),  # dimensions differ
        numerator=base.numerator, denominator=base.denominator, measure=base.measure,
        buckets=base.buckets, change_note=base.change_note,
    )
    assert d._sha256_of(d._canonical_spec(base)) == d._sha256_of(d._canonical_spec(with_other_dims))


# ===========================================================================
# Cross-module pins
# ===========================================================================
def test_hire_stands_sql_matches_the_checkin_module_byte_for_byte() -> None:
    assert d.HIRE_STANDS_SQL == checkins_module.HIRE_STANDS_SQL


def test_reversed_offer_outcomes_matches_the_checkin_module() -> None:
    assert d.REVERSED_OFFER_OUTCOMES == checkins_module.REVERSED_OFFER_OUTCOMES


def test_current_scorecard_sql_matches_the_scorecard_modules_own_rule() -> None:
    """``summary_for_enrolments`` also counts `assigned`/`late`, so this is
    not a substring match of the whole query — it asserts the two clauses
    THAT rule is built from (submitted; live-or-superseded-by-a-withdrawal)
    both appear, verbatim, in its SQL text."""
    src = inspect.getsource(scorecards_module.summary_for_enrolments)
    assert "s.status = 'submitted'" in src
    assert "(s.superseded_at IS NULL OR nxt.status = 'withdrawn')" in src
    assert d.CURRENT_SCORECARD_SQL == (
        "s.status = 'submitted' AND (s.superseded_at IS NULL OR nxt.status = 'withdrawn')"
    )


def test_source_labels_cover_every_source_exactly() -> None:
    assert set(SOURCE_LABELS) == SOURCES


def test_source_dimension_uses_the_shared_labels() -> None:
    import app.metrics.compute as compute_module

    assert compute_module.SOURCE_LABELS is SOURCE_LABELS


def test_watcher_funnel_metrics_are_checkin_safe() -> None:
    """The nightly watcher's population is `application`/`interviewed` — a
    check-in outcome must never reach a nightly finding body."""
    current = d.current_metrics()
    for name in watch_runner_module._WATCHER_FUNNEL_METRICS:
        assert not d.uses_checkin_data(current[name]), name


def test_the_watcher_guard_passes_for_the_real_tuple_and_raises_for_a_checkin_metric() -> None:
    """The real guard function (not a re-derivation of the rule it enforces):
    it already ran once, at import, over the real tuple — re-running it here
    is the offline proof it did not silently no-op — and it must RAISE
    (RuntimeError, not merely fail an assert) the moment a check-in metric
    name is added to the watched set."""
    guard = watch_runner_module._assert_watcher_metrics_are_checkin_safe
    guard(watch_runner_module._WATCHER_FUNNEL_METRICS)  # no raise

    with pytest.raises(RuntimeError, match="checkin_coverage"):
        guard(("applications", "checkin_coverage"))


# ===========================================================================
# uses_checkin_data / uses_checkin_outcome / drillable
# ===========================================================================
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("applications", False), ("application_to_hire", False),
        ("hire_interviewer_score", False), ("checkin_coverage", True),
        ("retention_90d", True), ("performance_90d", True),
    ],
)
def test_uses_checkin_data(name: str, expected: bool) -> None:
    assert d.uses_checkin_data(d.current_metrics()[name]) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("checkin_coverage", False), ("retention_90d", True), ("performance_90d", True),
    ],
)
def test_uses_checkin_outcome(name: str, expected: bool) -> None:
    assert d.uses_checkin_outcome(d.current_metrics()[name]) is expected


def test_checkin_outcome_drilldown_rules() -> None:
    metrics = d.current_metrics()
    assert d.checkin_outcome_drilldown_blocked(metrics["checkin_coverage"], "numerator") is False
    assert d.checkin_outcome_drilldown_blocked(metrics["checkin_coverage"], "denominator") is False
    assert d.checkin_outcome_drilldown_blocked(metrics["retention_90d"], "numerator") is True
    assert d.checkin_outcome_drilldown_blocked(metrics["retention_90d"], "denominator") is False
    assert d.checkin_outcome_drilldown_blocked(metrics["performance_90d"], "numerator") is True
    assert d.checkin_outcome_drilldown_blocked(metrics["performance_90d"], "denominator") is True


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("retention_90d", {"numerator": False, "denominator": True}),
        ("performance_90d", {"numerator": False, "denominator": False}),
        ("hires", {"numerator": True, "denominator": True}),
        ("application_to_hire", {"numerator": True, "denominator": True}),
        ("checkin_coverage", {"numerator": True, "denominator": True}),
        ("time_to_hire_days", {"numerator": True, "denominator": True}),
    ],
)
def test_drillable_matches_the_members_route_rule(name: str, expected: dict) -> None:
    """The exact examples the design gives, so ``drillable`` and the members
    route's 422 can never quietly disagree (both derive from
    ``checkin_outcome_drilldown_blocked``)."""
    assert d.drillable(d.current_metrics()[name]) == expected


def test_copilot_funnel_tool_excludes_checkin_metrics() -> None:
    current = d.current_metrics()
    every_metric = {name: {"metric": name, "version": 1, "kind": m.kind} for name, m in current.items()}
    safe = tools_module._checkin_safe_metrics(every_metric)
    assert "checkin_coverage" not in safe
    assert "retention_90d" not in safe
    assert "performance_90d" not in safe
    assert "application_to_hire" in safe
    assert "hire_interviewer_score" in safe


# ===========================================================================
# This module never writes
# ===========================================================================
def _module_ast_facts(path: pathlib.Path) -> tuple[ast.AST, set[str], list[str], list[ast.Call]]:
    """``(tree, identifiers, sql_string_literals, audit_log_constructions)``
    for one source file — the shared scan both no-write tests below build on.
    ``sql_string_literals`` excludes every module/class/function docstring, so
    a docstring that happens to mention "UPDATE" in prose is not a false
    positive. ``audit_log_constructions`` is every ``AuditLog(...)`` call site,
    by AST node, for the one-write allowlist.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    identifiers: set[str] = set()
    sql: list[str] = []
    audit_log_calls: list[ast.Call] = []
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
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "AuditLog"):
            audit_log_calls.append(node)
    return tree, identifiers, sql, audit_log_calls


_WRITER_IDENTIFIERS = (
    "record_transition", "record_result", "record_final_decision",
    "record_round_move", "release_hold", "_hold",
)
_WRITE_VERBS = ("INSERT INTO", "UPDATE ", "DELETE FROM", "MERGE ", "TRUNCATE")


@pytest.mark.parametrize("module", ["metrics/definitions.py", "metrics/compute.py"])
def test_metrics_package_has_no_write_statements(module: str) -> None:
    """C1's 'no AI-generated metric or recommendation can modify candidate
    status', restated for the metric layer's own SQL: no INSERT/UPDATE/DELETE
    text, and no import of a lifecycle/decision writer."""
    _tree, identifiers, sql, audit_log_calls = _module_ast_facts(APP / module)
    for writer in _WRITER_IDENTIFIERS:
        assert writer not in identifiers, f"{module} references {writer}"
    for keyword in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert not any(keyword in s for s in sql), f"{module} contains {keyword!r}"
    assert not audit_log_calls, f"{module} must not construct AuditLog at all"


def test_metric_layer_is_read_only() -> None:
    """C1-14 ('No AI-generated metric or recommendation can modify candidate
    status') over the WHOLE layer, including the router — not just the two
    files above. The single permitted exception, allowlisted BY NAME rather
    than by file: the members drill-down's own audit row
    (``analytics.members_viewed``) in ``routers/hr_metrics.py``, which is an
    ORM ``AuditLog`` row, not a raw-SQL write, and is the ONE construct this
    test lets through.
    """
    modules = ("metrics/definitions.py", "metrics/compute.py", "routers/hr_metrics.py")
    for module in modules:
        _tree, identifiers, sql, audit_log_calls = _module_ast_facts(APP / module)

        for writer in _WRITER_IDENTIFIERS:
            assert writer not in identifiers, f"{module} references {writer}"
        for verb in _WRITE_VERBS:
            assert not any(verb in s for s in sql), f"{module} contains {verb!r}"

        if module == "routers/hr_metrics.py":
            assert len(audit_log_calls) == 1, (
                f"{module} must construct exactly one AuditLog (analytics.members_viewed), "
                f"found {len(audit_log_calls)}"
            )
        else:
            assert not audit_log_calls, f"{module} must not construct AuditLog at all"

    # No import of the agent layer or an LLM client from app/metrics — the
    # router is not included here; the ask is specifically app/metrics.
    for module in ("metrics/definitions.py", "metrics/compute.py"):
        tree, _identifiers, _sql, _audit = _module_ast_facts(APP / module)
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
        for name in names:
            assert not name.startswith("shared.agents"), f"{module} imports {name}"
            assert "llm" not in name.lower(), f"{module} imports {name}"
