"""PH5 Wave 2 / E4 — interviewer calibration & outcome learning, offline.

Real Postgres coverage (workflow-version isolation, the archived-criteria
freeze, independence in the drill-down, tenant isolation, outcome linkage,
cohort windows) lives in ``tests/integration/test_ph5_w2_calibration_db.py``.
This file is everything that does not need a database: the paired-gap
arithmetic extensions (same-direction gate, per-criterion keying, the panel
baseline), the governed spec and band-dimension registry entries and their
lock/validation, and the structural guardrails (no writes, no LLM, no banned
words, the DPDP greps).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
from datetime import date

import pytest

from app.calibration_core import ScoreRow, calibrate, criterion_baselines
from app.metrics import definitions as d

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _rows(pattern: dict[str, int], n: int, comp: str = "c1", rnd: str = "r1") -> list[ScoreRow]:
    return [ScoreRow(f"{who}-{i}", who, f"e{i}", rnd, comp, score)
            for i in range(n) for who, score in pattern.items()]


# ===========================================================================
# same_direction_share — a meaningful mean gap must still point the same way
# most of the time, or it must not signal on its own (code review fix: the
# original fixture here failed meaningful_delta on its own, so it proved
# nothing about the same-direction gate specifically — deleting the gate
# would have left this test passing unchanged).
# ===========================================================================
def test_a_meaningful_mean_gap_with_inconsistent_direction_does_not_signal() -> None:
    """A panel of 2 (gen, peer): three judgements gap +2, two gap -1.
    mean_delta = (2+2+2-1-1)/5 = 0.8, which ALONE clears meaningful_delta
    (0.75) and pairs (5) — the OLDER, pre-E4 rule would have flagged this.
    same_direction_share = 3/5 = 0.6 < 0.7, so the new gate must refuse it."""
    rows = []
    for i in range(3):  # gap +2: gen 5, peer 1, panel mean 3
        rows.append(ScoreRow(f"g{i}", "gen", f"e{i}", "r1", "c1", 5))
        rows.append(ScoreRow(f"p{i}", "peer", f"e{i}", "r1", "c1", 1))
    for i in range(3, 5):  # gap -1: gen 1, peer 3, panel mean 2
        rows.append(ScoreRow(f"g{i}", "gen", f"e{i}", "r1", "c1", 1))
        rows.append(ScoreRow(f"p{i}", "peer", f"e{i}", "r1", "c1", 3))
    out = {c.interviewer_id: c for c in calibrate(rows)}
    gen = out["gen"]
    assert gen.candidates == 5 and gen.pairs == 5
    assert gen.mean_delta == pytest.approx(0.8)
    assert gen.same_direction_share == pytest.approx(0.6)
    assert gen.same_direction_count == 3
    assert gen.flag is None


def test_the_same_size_gap_held_consistently_does_signal() -> None:
    """The mirror of the test above: the SAME +2 gap, on all 5 judgements
    instead of 3 of 5 — same_direction_share is 1.0, and the flag fires."""
    rows = []
    for i in range(5):
        rows.append(ScoreRow(f"g{i}", "gen", f"e{i}", "r1", "c1", 5))
        rows.append(ScoreRow(f"p{i}", "peer", f"e{i}", "r1", "c1", 1))
    out = {c.interviewer_id: c for c in calibrate(rows)}
    gen = out["gen"]
    assert gen.mean_delta == pytest.approx(2.0)
    assert gen.same_direction_share == 1.0
    assert gen.same_direction_count == 5
    assert gen.flag == "higher"


def test_same_direction_count_is_the_raw_count_the_share_is_computed_from() -> None:
    """The web sentence ("in 6 of 7 the gap was the same way") must read the
    server's own count, never reconstruct it as round(share * pairs) — the
    Wave 1 anti-pattern (code review, PH5-E4 sign-off). 6 of 7 pairs agree."""
    rows = []
    for i in range(6):
        rows.append(ScoreRow(f"g{i}", "gen", f"e{i}", "r1", "c1", 5))
        rows.append(ScoreRow(f"p{i}", "peer", f"e{i}", "r1", "c1", 3))
    rows.append(ScoreRow("g6", "gen", "e6", "r1", "c1", 3))
    rows.append(ScoreRow("p6", "peer", "e6", "r1", "c1", 5))
    out = {c.interviewer_id: c for c in calibrate(rows)}
    gen = out["gen"]
    assert gen.pairs == 7
    assert gen.same_direction_count == 6
    assert gen.same_direction_share == pytest.approx(6 / 7, abs=1e-3)


def test_a_consistent_gap_still_signals() -> None:
    """The existing O5 fixture: every judgement points the same way, so
    same_direction_share is 1.0 and the flag still fires."""
    rows = _rows({"gen": 5, "p1": 3, "p2": 3}, 6)
    out = {c.interviewer_id: c for c in calibrate(rows)}
    assert out["gen"].flag == "higher"
    assert out["gen"].same_direction_share == 1.0


def test_calibrate_with_no_spec_uses_the_default_thresholds() -> None:
    """Backward compatibility: ``calibrate(rows)`` with no spec still applies
    the same thresholds as the governed spec's @1 values."""
    from app.calibration_core import _DEFAULT_THRESHOLDS
    from app.metrics.definitions import calibration_spec

    assert calibration_spec().thresholds == _DEFAULT_THRESHOLDS


# ===========================================================================
# by_criterion — per-frozen-criterion keying never merges two rounds
# ===========================================================================
def test_by_criterion_never_merges_two_rounds_sharing_a_competency_id() -> None:
    """Two DIFFERENT rounds happen to share the competency id 'c1' (a
    role-engine string, not a frozen identity — see the design). One round's
    interviewer scores everyone +2 high; the other, exactly on the panel. The
    per-criterion breakdown must show a signal for the first round only."""
    rows = _rows({"gen": 5, "peer": 3}, 6, comp="c1", rnd="round-A")
    rows += _rows({"gen": 3, "peer": 3}, 6, comp="c1", rnd="round-B")
    out = {c.interviewer_id: c for c in calibrate(rows)}
    gen_cells = {g.criterion_key: g for g in out["gen"].by_criterion}
    # Panel mean for round-A is (5+3)/2 = 4; gen's gap is 5-4 = 1.
    assert gen_cells["round-A:c1"].signal == "higher"
    assert gen_cells["round-A:c1"].gap == pytest.approx(1.0)
    assert gen_cells["round-B:c1"].signal is None
    assert gen_cells["round-B:c1"].gap == pytest.approx(0.0)
    # And the OVERALL (merged) mean_delta sits between the two, diluted by
    # the round with no gap — precisely the O5 gap #1 the per-criterion
    # breakdown exists to surface.
    assert out["gen"].mean_delta == pytest.approx(0.5)


def test_by_criterion_omits_cells_below_min_candidates() -> None:
    """A cell resting on fewer than min_candidates distinct applications is
    omitted entirely from by_criterion — the same convention by_competency
    already uses, not a suppressed placeholder."""
    rows = _rows({"gen": 5, "peer": 3}, 6, comp="c1", rnd="round-A")
    rows += _rows({"gen": 5, "peer": 3}, 3, comp="c1", rnd="round-B")  # only 3 candidates
    out = {c.interviewer_id: c for c in calibrate(rows)}
    keys = {g.criterion_key for g in out["gen"].by_criterion}
    assert "round-A:c1" in keys
    assert "round-B:c1" not in keys


# ===========================================================================
# criterion_baselines — the PANEL's own figure, never an interviewer's
# ===========================================================================
def test_baseline_suppressed_with_only_one_interviewer() -> None:
    """min_interviewers_for_baseline=2: a single interviewer's scores are not
    a PANEL baseline, however many candidates they cover."""
    rows = [ScoreRow(f"s{i}", "solo", f"e{i}", "r1", "c1", 4) for i in range(10)]
    (b,) = criterion_baselines(rows)
    assert b.suppressed and b.mean is None and b.distribution is None
    assert b.candidates == 10 and b.interviewers == 1


def test_baseline_suppressed_below_min_candidates() -> None:
    rows = _rows({"a": 4, "b": 2}, 4)  # only 4 candidates
    (b,) = criterion_baselines(rows)
    assert b.suppressed
    assert b.candidates == 4 and b.interviewers == 2


def test_baseline_disagreement_is_the_mean_max_minus_min_over_shared_judgements() -> None:
    rows = []
    # 5 candidates, two interviewers each; a consistent 2-point spread.
    for i in range(5):
        rows.append(ScoreRow(f"a{i}", "a", f"e{i}", "r1", "c1", 5))
        rows.append(ScoreRow(f"b{i}", "b", f"e{i}", "r1", "c1", 3))
    (b,) = criterion_baselines(rows)
    assert not b.suppressed
    assert b.shared_judgements == 5
    assert b.disagreement == pytest.approx(2.0)
    assert b.signal == "wide_disagreement"  # >= 1.5 over >= 5 shared judgements


def test_baseline_not_wide_when_the_panel_agrees() -> None:
    rows = []
    for i in range(5):
        rows.append(ScoreRow(f"a{i}", "a", f"e{i}", "r1", "c1", 4))
        rows.append(ScoreRow(f"b{i}", "b", f"e{i}", "r1", "c1", 4))
    (b,) = criterion_baselines(rows)
    assert not b.suppressed
    assert b.disagreement == pytest.approx(0.0)
    assert b.signal is None


def test_baseline_not_assessed_and_scores_are_counted_per_criterion() -> None:
    rows = _rows({"a": 4, "b": 3}, 5)
    rows.append(ScoreRow("na1", "a", "e_extra", "r1", "c1", None))
    (b,) = criterion_baselines(rows)
    assert b.scores == 10
    assert b.not_assessed == 1
    assert b.candidates == 6  # e_extra counts as a candidate even though not_assessed


# ===========================================================================
# The spec lock — changing a threshold without a new version is caught
# ===========================================================================
def test_calibration_spec_is_locked_in_the_registry() -> None:
    spec = d.calibration_spec()
    assert spec.name == "interviewer_calibration"
    assert spec.version == 1
    assert spec.thresholds == {
        "min_candidates": 5, "min_pairs": 5, "meaningful_delta": 0.75,
        "min_same_direction_share": 0.7, "min_interviewers_for_baseline": 2,
        "wide_disagreement_range": 1.5, "min_span_days": 7, "max_span_days": 366,
        "scale": "1-5",
    }


def test_editing_a_calibration_threshold_without_a_new_version_is_caught() -> None:
    real = d.CALIBRATION_SPECS["interviewer_calibration@1"]
    tampered = d.CalibrationSpec(
        real.name, real.version, real.effective_from, real.description, real.method,
        real.unit, real.cohort_bases,
        {**real.thresholds, "meaningful_delta": 0.5},  # a silent edit
        real.change_note,
    )
    real_hash = d._sha256_of(d._canonical_spec(real))
    tampered_hash = d._sha256_of(d._canonical_spec(tampered))
    assert real_hash != tampered_hash
    assert real_hash == d.load_lock()["calibration:interviewer_calibration@1"]["sha256"]


def test_calibration_and_dimension_entries_are_in_the_lock() -> None:
    lock = d.load_lock()
    assert "calibration:interviewer_calibration@1" in lock
    assert "dimension:interviewer_score_band@1" in lock
    entries = d.build_lock_entries()
    assert lock["calibration:interviewer_calibration@1"] == entries["calibration:interviewer_calibration@1"]
    assert lock["dimension:interviewer_score_band@1"] == entries["dimension:interviewer_score_band@1"]


# ===========================================================================
# The band dimension — fixed edges, and derives from nothing but the measure
# ===========================================================================
def test_band_edges_are_monotonic_and_total() -> None:
    sql = d.INTERVIEWER_SCORE_BAND_SQL
    # A crude but sufficient structural check: every edge literal appears in
    # ascending order, and every branch is covered (NULL, <3, <4, else).
    assert sql.index("IS NULL") < sql.index("< 3") < sql.index("< 4") < sql.index("ELSE")


# The per-value edge case (None -> none, 2.99 -> below_3, 3.0 -> 3_to_4, ...)
# used to be re-implemented here in Python and asserted against ITSELF —
# touching no product code at all (code review fix). It is now
# tests/integration/test_ph5_w2_calibration_db.py::test_band_edges_match_the_real_registry_sql,
# which executes the REAL INTERVIEWER_SCORE_BAND_SQL string against Postgres
# for each value; test_band_edges_are_monotonic_and_total above stays here as
# the offline half (the CASE branches exist and are ordered correctly).


def test_band_dimension_derives_from_nothing_but_the_measure() -> None:
    d._validate_score_band_dimension(d.DIMENSIONS_REGISTRY)  # the real registry: no raise


def test_band_dimension_validation_raises_for_an_unregistered_dimension() -> None:
    with pytest.raises(d.MetricDefinitionError, match="not registered"):
        d._validate_score_band_dimension({})


def test_band_dimension_validation_raises_when_it_references_another_column() -> None:
    bad = d.MetricDimension(
        "interviewer_score_band", 1, date(2026, 9, 23), "Interviewer score band", "bad",
        "(CASE WHEN m_hire_interviewer_score IS NULL THEN 'none' "
        "WHEN f_hired THEN 'below_3' ELSE '4_plus' END)",
    )
    with pytest.raises(d.MetricDefinitionError, match="derive from"):
        d._validate_score_band_dimension({"interviewer_score_band@1": bad})


def test_band_dimension_validation_raises_when_the_measure_is_missing() -> None:
    bad = d.MetricDimension(
        "interviewer_score_band", 1, date(2026, 9, 23), "Interviewer score band", "bad",
        "(CASE WHEN TRUE THEN 'none' ELSE '4_plus' END)",
    )
    with pytest.raises(d.MetricDefinitionError, match="must reference"):
        d._validate_score_band_dimension({"interviewer_score_band@1": bad})


def test_checkin_safe_dimensions_include_the_band() -> None:
    assert "interviewer_score_band" in d.CHECKIN_SAFE_DIMENSIONS


def test_checkin_metrics_dimensions_stay_within_checkin_safe() -> None:
    """The structural half of PH5-C1 for the real registry: every metric that
    reads a check-in flag is grouped ONLY by checkin-safe dimensions —
    already enforced at import (validate_registry), re-checked here directly
    against the real METRICS so a future edit that reads this test sees the
    exact rule."""
    for m in d.METRICS.values():
        if d.uses_checkin_data(m):
            assert set(m.dimensions) <= d.CHECKIN_SAFE_DIMENSIONS, m.key


def test_outcome_metrics_are_groupable_by_the_score_band() -> None:
    """hires/checkin_coverage/retention_90d/performance_90d/hire_interviewer_score
    (the hire-cohort outcome metrics) and application_to_hire (the decision
    cohort's) all declare interviewer_score_band — the outcome-signals route
    groups by it for both cohorts."""
    for name in ("hires", "checkin_coverage", "retention_90d", "performance_90d",
                 "hire_interviewer_score"):
        assert "interviewer_score_band" in d.METRICS[f"{name}@1"].dimensions
    assert "interviewer_score_band" in d.METRICS["application_to_hire@1"].dimensions


def test_widening_dimensions_did_not_move_any_metric_to_a_new_version_or_hash() -> None:
    """PH5-E4 added interviewer_score_band to _HIRE_DIMS/_PIPELINE_DIMS
    in-place — a dimension is explicitly excluded from a Metric's hash
    (_canonical_spec), so every affected metric's lock entry is unchanged."""
    lock = d.load_lock()
    entries = d.build_lock_entries()
    for name in ("hires", "checkin_coverage", "retention_90d", "performance_90d",
                 "hire_interviewer_score", "application_to_hire"):
        key = f"metric:{name}@1"
        assert lock[key] == entries[key], key


# ===========================================================================
# Guardrails (design 4.8) — no writes, no LLM, no banned words, DPDP greps
# ===========================================================================
def _module_source(path: pathlib.Path) -> tuple[set[str], list[str]]:
    """(identifiers, non-docstring string literals) for one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    identifiers: set[str] = set()
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.asname or node.name)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings):
            strings.append(node.value)
    return identifiers, strings


_WRITER_IDENTIFIERS = (
    "record_transition", "record_result", "record_final_decision",
    "record_round_move", "release_hold", "_hold",
)
_WRITE_VERBS = ("INSERT INTO", "UPDATE ", "DELETE FROM", "MERGE ", "TRUNCATE ")


def test_calibration_core_never_writes() -> None:
    identifiers, strings = _module_source(APP / "calibration_core.py")
    for writer in _WRITER_IDENTIFIERS:
        assert writer not in identifiers
    for verb in _WRITE_VERBS:
        assert not any(verb in s.upper() for s in strings), f"contains {verb!r}"


def test_panel_workload_calibration_functions_write_nothing_but_the_audit_row() -> None:
    """Scoped to the CALIBRATION functions specifically, not the whole
    module: ``set_capacity`` has a real, legitimate
    ``INSERT ... ON CONFLICT DO UPDATE`` for interviewer capacity, unrelated
    to calibration, that must not make this test fail."""
    import app.panel_workload as pw

    funcs = (pw.calibration, pw.judgements, pw._score_rows, pw._criterion_labels)
    sql_constants = (
        pw._SCORES_SQL, pw._JUDGEMENTS_SQL, pw._JUDGEMENTS_COUNT_SQL, pw._CRITERION_LABELS_SQL,
    )
    src = "\n".join(inspect.getsource(f) for f in funcs) + "\n".join(sql_constants)
    for writer in _WRITER_IDENTIFIERS:
        assert writer not in src, writer
    for verb in _WRITE_VERBS:
        assert verb not in src.upper(), verb
    assert "AuditLog" in inspect.getsource(pw.calibration)
    assert "AuditLog" in inspect.getsource(pw.judgements)


@pytest.mark.parametrize("module", ["calibration_core.py", "panel_workload.py"])
def test_calibration_modules_import_no_agent_or_llm_client(module: str) -> None:
    tree = ast.parse((APP / module).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    for name in names:
        assert not name.startswith("shared.agents"), f"{module} imports {name}"
        assert "llm" not in name.lower(), f"{module} imports {name}"


_BANNED_WORDS = ("bias", "harsh", "lenient", "outlier", "poor", "recommend", "decision_authority")


@pytest.mark.parametrize("module", ["calibration_core.py", "panel_workload.py"])
def test_calibration_modules_use_no_banned_wording(module: str) -> None:
    """Calibration output must read as signals, never as judgements of a
    person — the design's banned word list, applied to code and docstrings."""
    src = (APP / module).read_text(encoding="utf-8").lower()
    for word in _BANNED_WORDS:
        assert word not in src, f"{module} contains banned word {word!r}"


def test_outcome_signals_route_uses_no_banned_wording() -> None:
    src = (APP / "routers" / "hr_metrics.py").read_text(encoding="utf-8").lower()
    for word in _BANNED_WORDS:
        assert word not in src, f"hr_metrics.py contains banned word {word!r}"


def test_panel_current_scorecard_sql_matches_the_derivation_rule() -> None:
    """Pinned against a literal transform of CURRENT_SCORECARD_SQL (s/nxt ->
    s2/nxt2), not re-typed independently, so it cannot silently drift from
    the one true "current scorecard" rule."""
    import app.panel_workload as pw

    derived = d.CURRENT_SCORECARD_SQL.replace("s.", "s2.").replace("nxt.", "nxt2.")
    assert derived == pw._PANEL_CURRENT_SCORECARD_SQL


# ---------------------------------------------------------------------------
# DPDP greps (design 4.8): hire_checkins is never referenced where it must
# not be; the outcome module and the band SQL never reference AI-scored or
# purged-at-90-days tables.
# ---------------------------------------------------------------------------
def _code_without_prose(path: pathlib.Path) -> str:
    """A module's code and SQL, with comments and docstrings removed.

    A plain grep is enough for ``calibration_core``/``panel_workload``, which
    never discuss the tables they must not read. It is NOT enough for
    ``app/rediscovery.py``, whose docstring deliberately LISTS every excluded
    source with the reason for excluding it (the project's documentation rule):
    a raw grep there fails on the explanation instead of on the code, which is a
    guard that can never pass and therefore proves nothing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body[0] = ast.Pass()
    return ast.unparse(ast.fix_missing_locations(tree)).lower()
def test_hire_checkins_is_never_referenced_outside_its_approved_readers() -> None:
    forbidden_dirs = [
        pathlib.Path(__file__).resolve().parents[3] / "feedback_billing" / "app",
        pathlib.Path(__file__).resolve().parents[4] / "shared" / "intelligence",
        pathlib.Path(__file__).resolve().parents[4] / "shared" / "agents",
        APP / "agents",
    ]
    for d_ in forbidden_dirs:
        if not d_.exists():
            continue
        for path in d_.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "hire_checkins" not in text, f"{path} references hire_checkins"
    admin_analytics = (
        pathlib.Path(__file__).resolve().parents[3] / "admin_ops" / "app" / "routers" / "analytics"
    )
    if admin_analytics.exists():
        for path in admin_analytics.rglob("*.py"):
            assert "hire_checkins" not in path.read_text(encoding="utf-8"), path
    # PH5-E3 joins the list, as a FILE rather than a directory: a rediscovery
    # match must not know that somebody left their last job. `hire_checkins` is
    # aggregate-only and OMITTED_ALWAYS in the evidence graph, and a
    # forward-looking "should we contact this person again?" surface is exactly
    # where that boundary would be crossed by accident.
    forbidden_files = [APP / "rediscovery.py", APP / "routers" / "hr_rediscovery.py"]
    for path in forbidden_files:
        assert path.exists(), f"{path} is gone; this guard would otherwise scan nothing"
        assert "hire_checkins" not in _code_without_prose(path), (
            f"{path} references hire_checkins"
        )


def test_calibration_and_outcome_modules_never_reference_ai_scored_tables() -> None:
    """E4 must structurally exclude round_results, scorecards and sessions —
    only HUMAN interviewer_scorecards feed calibration or the outcome
    measure. Checked by table name, on calibration_core, panel_workload and
    the band SQL/measure themselves."""
    # PH5-E3 added the last five. None of them appeared in these two modules
    # before either — the point is that "an unreviewed automated suspicion, a
    # screening answer, or a rejection reason written for another opening must
    # not become an input" is now stated for E4 as well as for rediscovery,
    # instead of being true only by luck.
    forbidden = (
        "round_results", "ats_", "exam_attempts",
        "code_similarity_signals", "code_quality_reports", "code_fingerprints",
        "application_answers", "stage_transitions",
    )
    for module in ("calibration_core.py", "panel_workload.py"):
        src = (APP / module).read_text(encoding="utf-8").lower()
        for name in forbidden:
            assert name not in src, f"{module} references {name}"
        # "sessions" and "scorecards" alone are too broad a grep
        # (interviewer_scorecards legitimately appears everywhere here) — the
        # precise table names are what must never appear.
        assert re.search(r"\bfrom sessions\b", src) is None, module
        assert re.search(r"\bjoin sessions\b", src) is None, module
        assert re.search(r"\bfrom scorecards\b", src) is None, module
        assert re.search(r"\bjoin scorecards\b", src) is None, module

    band_sql = d.INTERVIEWER_SCORE_BAND_SQL.lower()
    measure_sql = d.MEASURES["hire_interviewer_score@1"].sql.lower()
    for name in ("round_results", "sessions", "ats_", "exam_attempts", " scorecards "):
        assert name not in band_sql
        assert name not in measure_sql
    # The measure is built from interviewer_scorecard_scores/hsc, never
    # sessions or round_results.
    assert "hsc" in measure_sql or "mean_score" in measure_sql


def test_rediscovery_never_reads_ai_scored_or_unreviewed_evidence() -> None:
    """PH5-E3's structural exclusions, EXTENDING this file's scan rather than
    repeating it somewhere else.

    Rediscovery's forbidden set is not E4's: it legitimately reads
    ``round_results`` and ``exam_attempts`` (a number against an anonymised
    applicant is the company's own assessment record), but it must never reach

      * the AI interview's SCORE (``scorecards`` via ``sessions``) — purged at
        90 days, so it exists for recent candidates and is absent for older
        ones, and using it would rank recent candidates higher for a reason
        that is not about them;
      * coding similarity / quality / fingerprint / integrity findings —
        automated and unreviewed by design, which PH4-D3 refused to let near a
        decision;
      * ``application_answers`` — prose written for one specific opening;
      * ``stage_transitions`` — the decision rationale (AR-5, closed: redacted
        on erasure, but still a negative human judgement about a DIFFERENT
        job, and not this module's to read either way);
      * ``interviewer_notes`` and ``candidate_accommodations`` —
        ``OMITTED_ALWAYS`` in the evidence graph;
      * ``applicants.ats_*`` — scored against one specific JD, and it survives
        erasure.

    PH5 Wave 4 EXTENDS this same scan, rather than copying it, to the pools
    half of E3 (``app/talent_pools.py`` and ``app/routers/hr_pools.py``): a
    pool member's row is never an occasion to go read an AI interview's score,
    a rejection reason written for a different opening, or any of the other
    excluded sources — the module reads and writes ``talent_pool_members``/
    ``talent_pool_events`` and asks ``rediscovery.eligibility_for_applicants``
    for eligibility, and nothing else.
    """
    forbidden = (
        "code_similarity_signals", "code_quality_reports", "code_fingerprints",
        "code_integrity_findings", "application_answers", "stage_transitions",
        "interviewer_notes", "candidate_accommodations", "hire_checkins",
        "ats_overall", "ats_breakdown", "ats_summary", "graded_snapshot",
    )
    # The sentinel per module is a string the module certainly DOES contain, so
    # a scan that silently read nothing (a moved file, a broken parse) fails
    # here rather than passing vacuously — the Wave 3 lesson, where a structural
    # guard inspected zero functions and stayed green.
    for module, sentinel in (
        ("rediscovery.py", "applicants"),          # its SQL
        ("routers/hr_rediscovery.py", "search"),   # its route
        ("talent_pools.py", "talent_pool_members"),        # its SQL
        ("routers/hr_pools.py", "applicant_id"),           # its routes
    ):
        path = APP / module
        assert path.exists(), f"{module} is gone; this guard would scan nothing"
        src = _code_without_prose(path)
        assert sentinel in src, f"{module} has no {sentinel!r} — has the scan gone blind?"
        for name in forbidden:
            assert name not in src, f"{module} references {name}"
        assert re.search(r"\bfrom sessions\b", src) is None, module
        assert re.search(r"\bjoin sessions\b", src) is None, module
        assert re.search(r"\bfrom scorecards\b", src) is None, module
        assert re.search(r"\bjoin scorecards\b", src) is None, module


def test_hire_interviewer_score_measure_reads_only_human_scorecards() -> None:
    """Belt-and-braces on the ONE measure the whole outcome view is built
    from: its docstring says it explicitly, and its sql is the bare lateral
    alias hsc.mean_score computed (in compute.py) from
    interviewer_scorecards/interviewer_scorecard_scores only."""
    measure = d.MEASURES["hire_interviewer_score@1"]
    assert "AI" in measure.description
    assert "never" in measure.description.lower()
    assert measure.sql == "hsc.mean_score"


def test_evidence_graph_module_is_not_referenced_by_calibration() -> None:
    """E4 and E5 are independent waves; calibration must not import the
    evidence graph (owned by the other wave)."""
    for module in ("calibration_core.py", "panel_workload.py"):
        src = (APP / module).read_text(encoding="utf-8")
        assert "evidence_graph" not in src, module
