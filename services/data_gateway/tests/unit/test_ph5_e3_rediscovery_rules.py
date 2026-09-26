"""PH5-E3 — the rules that need no database.

Freshness banding, the review flags, ``explained``, the evidence boost,
competency coverage, the frozen match reason, and the structural pins that keep
this feature honest: the consent literals agree with the migration, the search
weights agree with the applicant search they were copied from, the module
cannot write a hiring outcome, and the audit row cannot carry the query string.

The DB-level behaviour (eligibility, tenancy, expiry, withdrawal, the evidence
join and PH4-A1 independence) is in
``tests/integration/test_ph5_e3_rediscovery_db.py``; the forbidden-source scans
are EXTENSIONS of ``tests/unit/test_ph5_w2_calibration.py`` rather than a third
copy of the same idea.
"""

from __future__ import annotations

import ast
import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app import rediscovery as r
from app.config import settings

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
SOURCE = (APP / "rediscovery.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _docstrings(tree: ast.AST) -> set[int]:
    """The id() of every docstring node, so a scan can skip them.

    A structural scan that greps the whole file catches its own documentation:
    this module's docstring explains at length which tables it must never write
    and which function's shape it copies, and a naive grep for those names
    would fail on the explanation rather than on the code. That is a guard that
    cannot pass, which is as useless as one that cannot fail.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _code_strings(source: str) -> list[str]:
    """Every string literal in a module EXCEPT its docstrings — i.e. the SQL."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
    ]


def _code_identifiers(source: str) -> set[str]:
    """Every name, attribute and imported symbol a module actually USES."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


# ===========================================================================
# Freshness bands — computed from the source row's own timestamp, never stored
# ===========================================================================
@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (0, "fresh"),
        (180, "fresh"),   # rediscovery_fresh_days, inclusive
        (181, "ageing"),
        (365, "ageing"),  # rediscovery_stale_days, inclusive
        (366, "stale"),
        (900, "stale"),
    ],
)
def test_freshness_bands_at_their_boundaries(age_days: int, expected: str) -> None:
    band, reason = r.freshness_band(NOW - timedelta(days=age_days), now=NOW)
    assert band == expected
    assert reason is None


@pytest.mark.parametrize("lifecycle", ["purged", "redacted", "superseded"])
def test_content_that_no_longer_exists_is_unverifiable_at_any_age(lifecycle: str) -> None:
    """A band is about whether evidence can be TRUSTED, not only how old it is.
    A day-old purged session and a two-year-old one are equally unverifiable."""
    for age in (1, 400):
        band, reason = r.freshness_band(
            NOW - timedelta(days=age), now=NOW, lifecycle=lifecycle
        )
        assert band == "unverifiable"
        assert reason and lifecycle in reason


def test_an_undated_item_gets_no_band_and_says_why() -> None:
    band, reason = r.freshness_band(None, now=NOW)
    assert band is None
    assert reason == "this item carries no date, so it cannot be aged"


def test_the_row_header_takes_the_worst_band_never_the_best_or_a_mean() -> None:
    assert r.worst_band(["fresh", "stale"]) == "stale"
    assert r.worst_band(["fresh", "ageing"]) == "ageing"
    assert r.worst_band(["stale", "unverifiable"]) == "unverifiable"
    assert r.worst_band(["fresh", None]) == "fresh"
    assert r.worst_band([]) == "none"
    assert r.worst_band([None, None]) == "none"


# ===========================================================================
# requires_review — computed, never claimed. Four causes.
# ===========================================================================
def test_review_is_required_for_each_of_its_four_causes() -> None:
    assert r.review_reasons(header_band="ageing", explained=True, evidence_items=2) == [
        "evidence_ageing"
    ]
    assert r.review_reasons(header_band="stale", explained=True, evidence_items=2) == [
        "evidence_stale"
    ]
    assert r.review_reasons(
        header_band="unverifiable", explained=True, evidence_items=1
    ) == ["evidence_unverifiable"]
    assert r.review_reasons(header_band="none", explained=True, evidence_items=0) == [
        "no_evidence"
    ]
    assert r.review_reasons(header_band="fresh", explained=False, evidence_items=3) == [
        "unexplained_match"
    ]


def test_review_is_not_required_only_when_fresh_and_explained_and_evidenced() -> None:
    assert r.review_reasons(header_band="fresh", explained=True, evidence_items=1) == []


# ===========================================================================
# The evidence boost — capped, and zero without a target opening
# ===========================================================================
def test_the_evidence_boost_is_zero_without_a_target_opening() -> None:
    assert r.evidence_boost(coverage=1.0, factor=1.0, has_target=False) == 0.0


def test_the_evidence_boost_is_capped_at_its_configured_weight() -> None:
    cap = float(settings.rediscovery_evidence_weight)
    assert r.evidence_boost(coverage=1.0, factor=1.0, has_target=True) == pytest.approx(cap)
    # Nonsense inputs cannot exceed the cap either.
    assert r.evidence_boost(coverage=9.0, factor=9.0, has_target=True) == pytest.approx(cap)
    assert r.evidence_boost(coverage=-1.0, factor=1.0, has_target=True) == 0.0


def test_the_boost_scales_with_coverage_and_freshness() -> None:
    cap = float(settings.rediscovery_evidence_weight)
    assert r.evidence_boost(coverage=0.5, factor=1.0, has_target=True) == pytest.approx(cap / 2)
    assert r.evidence_boost(coverage=1.0, factor=0.6, has_target=True) == pytest.approx(cap * 0.6)
    # unverifiable evidence cannot lift a ranking at all.
    assert r.evidence_boost(
        coverage=1.0, factor=r._FRESHNESS_FACTOR["unverifiable"], has_target=True
    ) == 0.0


def test_the_freshness_factors_are_the_documented_four() -> None:
    assert r._FRESHNESS_FACTOR == {
        "fresh": 1.0, "ageing": 0.6, "stale": 0.25, "unverifiable": 0.0
    }


# ===========================================================================
# Competency coverage — exact ids only, never a fuzzy mapping
# ===========================================================================
def test_coverage_counts_only_exact_competency_id_matches() -> None:
    coverage, matched = r.competency_coverage(
        target_ids=["problem_solving", "communication", "safety"],
        scored_ids={"problem_solving", "safety", "teamwork"},
    )
    assert coverage == pytest.approx(2 / 3)
    assert matched == ["problem_solving", "safety"]


def test_an_llm_slugified_id_reduces_coverage_rather_than_fuzzy_matching() -> None:
    """On the LLM-refined path a model can emit ids that are slugified NAMES
    (``fault-diagnosis``) instead of archetype ids (``problem_solving``). A
    fuzzy mapping would invent agreement between two different rubrics; a lower
    number is the honest answer, and the response reports which ids matched."""
    coverage, matched = r.competency_coverage(
        target_ids=["problem_solving"], scored_ids={"problem-solving", "problem solving"}
    )
    assert coverage == 0.0
    assert matched == []


def test_coverage_is_zero_with_no_target_competencies() -> None:
    assert r.competency_coverage(target_ids=[], scored_ids={"anything"}) == (0.0, [])


def test_unverifiable_evidence_never_counts_as_a_scored_competency() -> None:
    """A purged AI interview or a redacted-away score cannot prove a
    competency: the content is gone."""
    items = [
        {"competency_id": "gone", "freshness": "unverifiable"},
        {"competency_id": "kept", "freshness": "stale"},
        {"competency_ids": ["also_kept"], "freshness": "fresh"},
    ]
    assert r._scored_competency_ids(items) == {"kept", "also_kept"}


# ===========================================================================
# explained / unexplained — the honesty rule, through the real assembler
# ===========================================================================
def _base(**over: Any) -> dict[str, Any]:
    row = {
        "id": uuid.uuid4(), "full_name": "Asha K", "current_title": "Technician",
        "current_company": "Acme", "years_experience": 6, "updated_at": NOW,
        "opted_in_at": NOW, "opt_in_expires_at": NOW + timedelta(days=365),
        "terms_matched": [], "snippet": None,
    }
    row.update(over)
    return row


def test_a_similarity_only_match_is_labelled_unexplained() -> None:
    out = r._assemble_result(
        base=_base(), semantic=0.8, lexical=0.0, semantic_available=True,
        evidence_rows=[], target=None, now=NOW,
    )
    assert out["explained"] is False
    assert out["unexplained_note"] == r.UNEXPLAINED_NOTE
    assert "unexplained_match" in out["review_reasons"]
    assert out["requires_review"] is True
    similarity = next(w for w in out["why"] if w["signal"] == "resume_similarity")
    assert similarity["explainable"] is False
    assert similarity["note"] == r.SIMILARITY_NOTE
    # There is no span and no term to quote for a cosine number.
    assert "snippet" not in similarity
    assert "terms_matched" not in similarity


def test_a_matched_term_makes_the_match_explained() -> None:
    out = r._assemble_result(
        base=_base(terms_matched=["hydraulics"], snippet="[[hydraulics]] presses"),
        semantic=0.8, lexical=0.2, semantic_available=True, evidence_rows=[],
        target=None, now=NOW,
    )
    assert out["explained"] is True
    assert out["unexplained_note"] is None
    terms = next(w for w in out["why"] if w["signal"] == "resume_terms")
    assert terms["explainable"] is True
    assert terms["terms_matched"] == ["hydraulics"]
    assert terms["snippet"] == "[[hydraulics]] presses"


def test_displayed_evidence_that_did_not_contribute_does_not_explain_the_match() -> None:
    """With no target opening nothing about the evidence caused this match —
    the CV did. Calling the row 'explained' because a scorecard happens to
    exist would dress a cosine number in somebody else's evidence."""
    out = r._assemble_result(
        base=_base(), semantic=0.8, lexical=0.0, semantic_available=True,
        evidence_rows=[_scorecard_row()], target=None, now=NOW,
    )
    assert out["breakdown"]["evidence_boost"] == 0.0
    assert any(w["signal"] == "interviewer_scorecard" for w in out["why"])  # still shown
    assert out["explained"] is False


def test_an_evidence_item_that_covered_a_target_competency_explains_the_match() -> None:
    out = r._assemble_result(
        base=_base(), semantic=0.5, lexical=0.0, semantic_available=True,
        evidence_rows=[_scorecard_row()],
        target={"competency_ids": ["problem_solving"], "competencies": 1},
        now=NOW,
    )
    assert out["explained"] is True
    assert out["breakdown"]["evidence_boost"] > 0
    assert out["breakdown"]["covered_competencies"] == ["problem_solving"]
    scored = next(w for w in out["why"] if w["signal"] == "interviewer_scorecard")
    assert scored["contribution"] > 0
    assert scored["score"] == 4
    assert scored["of"] == 5
    assert scored["citation"]["kind"] == "interviewer_scorecard"
    assert scored["citation"]["href"].startswith("/hr/enrolments/")


def test_the_score_is_capped_at_one_hundred() -> None:
    out = r._assemble_result(
        base=_base(terms_matched=["x"]), semantic=1.0, lexical=1.0,
        semantic_available=True, evidence_rows=[_scorecard_row()],
        target={"competency_ids": ["problem_solving"], "competencies": 1}, now=NOW,
    )
    assert out["score"] == 100


def test_the_similarity_leg_is_absent_when_the_embedder_was_unavailable() -> None:
    out = r._assemble_result(
        base=_base(terms_matched=["hydraulics"]), semantic=0.0, lexical=0.4,
        semantic_available=False, evidence_rows=[], target=None, now=NOW,
    )
    assert [w["signal"] for w in out["why"]] == ["resume_terms"]
    assert out["breakdown"]["semantic_available"] is False
    assert out["explained"] is True


def _scorecard_row(**over: Any) -> dict[str, Any]:
    row = {
        "applicant_id": uuid.uuid4(), "signal": "interviewer_scorecard",
        "ref_id": str(uuid.uuid4()), "enrolment_id": str(uuid.uuid4()),
        "recorded_at": NOW - timedelta(days=10), "lifecycle": "live",
        "produced_by": "human", "competency_id": "problem_solving",
        "competency_name": "Fault Diagnosis", "score": 4, "out_of": 5,
        "percent": None, "passed": None, "round_title": "Round 2",
        "round_kind": "human_review", "criterion_ids": None,
    }
    row.update(over)
    return row


def test_a_redacted_scorecard_keeps_its_score_and_says_it_was_redacted() -> None:
    item = r._evidence_why_item(
        _scorecard_row(lifecycle="redacted"), applicant_id=str(uuid.uuid4()), now=NOW
    )
    assert item["score"] == 4
    assert item["lifecycle"] == "redacted"
    assert item["freshness"] == "unverifiable"
    assert "redacted" in item["content_hidden_reason"]


def test_an_ai_interview_item_carries_the_fact_and_never_a_score() -> None:
    row = {
        "applicant_id": uuid.uuid4(), "signal": "ai_interview", "ref_id": str(uuid.uuid4()),
        "enrolment_id": None, "recorded_at": NOW - timedelta(days=20), "lifecycle": "purged",
        "produced_by": "ai", "competency_id": None, "competency_name": None, "score": None,
        "out_of": None, "percent": None, "passed": None, "round_title": None,
        "round_kind": "ai_interview", "criterion_ids": None,
    }
    item = r._evidence_why_item(row, applicant_id=str(uuid.uuid4()), now=NOW)
    assert item["contribution"] == 0
    assert item["freshness"] == "unverifiable"
    assert item["lifecycle"] == "purged"
    assert item["content_hidden_reason"] == r._AI_INTERVIEW_HIDDEN_REASON
    assert item.get("score") is None
    assert item.get("percent") is None


def test_an_ai_graded_round_result_is_labelled_ai_and_unverifiable() -> None:
    row = _scorecard_row(
        signal="round_result", competency_id=None, competency_name=None, score=None,
        out_of=None, percent=72, passed=True, round_kind="ai_interview",
        produced_by="ai", criterion_ids=["problem_solving"],
    )
    item = r._evidence_why_item(row, applicant_id=str(uuid.uuid4()), now=NOW)
    assert item["produced_by"] == "ai"
    assert item["freshness"] == "unverifiable"
    assert "purged at 90 days" in item["freshness_reason"]


# ===========================================================================
# Snippets, query terms, and the frozen match reason
# ===========================================================================
def test_a_snippet_is_capped_and_stripped_of_invisible_characters() -> None:
    dirty = "a​b" + ("x" * 500)
    out = r._safe_snippet(dirty)
    assert out is not None
    assert len(out) <= r.SNIPPET_CHARS
    assert "​" not in out
    assert r._safe_snippet(None) is None
    assert r._safe_snippet("   ") is None


def test_query_terms_are_the_words_hr_typed_deduplicated_and_bounded() -> None:
    assert r.query_terms("hydraulics AND hydraulics maintenance") == [
        "hydraulics", "AND", "maintenance"
    ]
    assert r.query_terms("a bc") == ["bc"]  # single characters are not terms
    assert len(r.query_terms(" ".join(f"w{i}" for i in range(40)))) == r._MAX_QUERY_TERMS


def test_the_frozen_match_reason_keeps_facts_and_strips_every_piece_of_prose() -> None:
    out = r._assemble_result(
        base=_base(terms_matched=["hydraulics"], snippet="[[hydraulics]] presses at Acme"),
        semantic=0.8, lexical=0.2, semantic_available=True,
        evidence_rows=[_scorecard_row()],
        target={"competency_ids": ["problem_solving"], "competencies": 1}, now=NOW,
    )
    frozen = r.freeze_match_reason(out)
    blob = str(frozen)
    assert "presses at Acme" not in blob          # no CV prose
    assert "[[hydraulics]]" not in blob           # no lexical snippet
    assert r.SIMILARITY_NOTE not in blob          # no fixed sentences either
    assert r.UNEXPLAINED_NOTE not in blob
    assert frozen["score"] == out["score"]
    assert frozen["explained"] is True
    assert frozen["evidence_freshness"] == out["evidence_freshness"]
    scored = next(w for w in frozen["why"] if w["signal"] == "interviewer_scorecard")
    assert scored["competency_id"] == "problem_solving"
    assert scored["score"] == 4
    # The citation keeps (kind, id, label) so the pool can still link to the
    # record six weeks later. `label` names the round, the competency and the
    # score — the company's own rubric, not the candidate's words.
    assert scored["citation"]["kind"] == "interviewer_scorecard"
    assert "href" not in scored["citation"]
    assert "snippet" not in str(frozen["why"])


def test_add_months_is_calendar_arithmetic_and_clamps_the_day() -> None:
    """Not 30-day months: the eligibility query's bound is Postgres'
    make_interval(months => …), and a 30-day approximation showed candidates an
    expiry five days earlier than the one actually enforced."""
    assert r.add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert r.add_months(datetime(2026, 9, 25, tzinfo=UTC), 12) == datetime(2027, 9, 25, tzinfo=UTC)
    assert r.add_months(datetime(2024, 2, 29, tzinfo=UTC), 12) == datetime(2025, 2, 28, tzinfo=UTC)
    assert r.add_months(datetime(2023, 3, 15, tzinfo=UTC), 12) == datetime(2024, 3, 15, tzinfo=UTC)


# ===========================================================================
# Structural pins
# ===========================================================================
def test_the_consent_literals_agree_with_the_migration() -> None:
    """A migration cannot import application code, so both files carry the same
    string literals. This is what keeps them in step."""
    migration = (
        pathlib.Path(__file__).resolve().parents[2]
        / "alembic" / "versions"
        / "20260924_0001_e4a6c8b0d2f7_ph5_e3_talent_pools.py"
    ).read_text(encoding="utf-8")
    assert f'REDISCOVERY_CONSENT_TYPE = "{r.REDISCOVERY_CONSENT_TYPE}"' in migration
    assert r.REDISCOVERY_CONSENT_TYPE == "talent_pool_rediscovery"
    assert r.REDISCOVERY_PURPOSE == "rediscovery"
    # The index the eligibility join reads through, keyed the way the CTE joins.
    assert "ix_dpdp_consent_rediscovery_unique" in migration
    assert "(user_id, (evidence ->> 'company_id'))" in migration


def test_the_search_weights_are_the_applicant_search_weights_unchanged() -> None:
    """Criterion 18's pin. Rediscovery deliberately reuses the existing hybrid
    arithmetic so the tuning question stays ONE question — and retuning the
    0.7/0.3 split to flatter rediscovery would put the applicant search at
    risk for no measured gain."""
    from app.routers import hr_applicants as ha

    assert r._SEMANTIC_WEIGHT == ha._SEMANTIC_WEIGHT == 0.7
    assert r._LEXICAL_WEIGHT == ha._LEXICAL_WEIGHT == 0.3
    assert ha._SEARCH_LIMIT == 200
    assert settings.rediscovery_search_limit == ha._SEARCH_LIMIT


def test_rediscovery_does_not_import_or_call_the_applicant_search() -> None:
    """The existing search is not touched, and this module does not reach into
    it: a change here cannot change what ``GET /hr/applicants?q=`` returns.

    Checked on identifiers rather than raw text, because this module's docstring
    legitimately names ``_semantic_search`` as the shape it copies."""
    identifiers = _code_identifiers(SOURCE)
    assert "_semantic_search" not in identifiers
    assert not any("hr_applicants" in name for name in identifiers)


def test_both_searches_still_degrade_to_keyword_only_when_the_embedder_is_down() -> None:
    from app.routers import hr_applicants as ha

    applicant_src = (APP / "routers" / "hr_applicants.py").read_text(encoding="utf-8")
    for src in (applicant_src, SOURCE):
        assert "except EmbeddingError" in src
        # The degradation branch: the semantic leg becomes the literal "0" and
        # the signal predicate narrows to pure full text.
        assert "0" in _code_strings(src)
        assert "plainto_tsquery" in " ".join(_code_strings(src))
    assert ha._SEMANTIC_WEIGHT  # the module really imported


def test_rediscovery_never_writes_a_hiring_outcome() -> None:
    """Structural, not a promise. ``CLAUDE.md`` hard constraint 9 / D-05: a
    rediscovery result's only actions are add-to-pool and invite, so there must
    be no statement here that writes a status, an enrolment stage, a round
    result or a scorecard.

    Scanned over the module's SQL — every string literal that is not a
    docstring — so the guard cannot be satisfied or defeated by prose."""
    sql = " ".join(_code_strings(SOURCE)).lower()
    assert "insert into" in sql or "update " in sql, "the scan found no SQL at all"
    outcome_tables = (
        "enrolments", "round_results", "stage_transitions", "interviewer_scorecards",
        "interviewer_scorecard_scores", "exam_attempts", "applicants", "talent_pool_members",
        "talent_pools", "sessions", "scorecards",
    )
    for table in outcome_tables:
        for verb in ("insert into", "update", "delete from"):
            assert not re.search(rf"\b{verb}\s+{table}\b", sql), (
                f"rediscovery.py appears to {verb} {table}"
            )
    # The only table this module writes at all is the consent ledger, of which
    # it is the declared only writer. (audit_log is written through the ORM.)
    writes = set(re.findall(r"\b(?:insert into|update|delete from)\s+(\w+)", sql))
    assert writes == {"dpdp_consent_ledger"}, writes


def test_the_search_audit_cannot_carry_the_query_string() -> None:
    """HR will type candidate names into that box. The audit row records the
    query's LENGTH, the counts and the weights — never the text, and no log
    line in this module takes it either."""
    class _Recorder:
        def __init__(self) -> None:
            self.rows: list[Any] = []

        def add(self, row: Any) -> None:
            self.rows.append(row)

    db = _Recorder()
    payload = {
        "returned": 2, "matched": 5, "semantic": True,
        "universe": {"eligible": 7, "total": 900},
        "weights": {"semantic": 0.7, "lexical": 0.3, "evidence_max": 0.15},
        "target": {"requisition_id": "req-1", "competencies": 4},
    }
    r.record_search_audit(
        db,  # type: ignore[arg-type]
        actor_id=uuid.uuid4(), company_id=uuid.uuid4(), payload=payload, query_length=17,
    )
    details = db.rows[0].details
    assert details["query_length"] == 17
    assert details["results"] == 2
    assert set(details) == {
        "query_length", "results", "matched", "eligible", "semantic", "requisition_id",
        "target_competencies", "weights",
    }
    assert not any("quer" in str(k).lower() and k != "query_length" for k in details)
    # No log call in this module is handed the query itself.
    assert "query=query" not in SOURCE
    assert re.search(r"log\.\w+\([^)]*\bquery=(?!_length)", SOURCE) is None


def test_no_model_writes_any_part_of_the_explanation() -> None:
    """A second model call would manufacture an explanation for a cosine
    number, which is exactly what this feature refuses to do. The only prose on
    a result is the two fixed sentences in this module.

    Only ``embed_one_remote`` may reach an AI service from here — it turns the
    QUERY into a vector and never sees a candidate."""
    identifiers = _code_identifiers(SOURCE)
    forbidden = {
        "why_match_remote", "build_llm", "get_llm", "LLMProvider", "complete",
        "generate_content", "chat", "ask_llm", "exam_ai_client", "fake_why_match",
    }
    assert not (identifiers & forbidden), sorted(identifiers & forbidden)
    ai_calls = {name for name in identifiers if "remote" in name}
    assert ai_calls == {"embed_one_remote"}, sorted(ai_calls)
