"""PH5-E3 — the pools module's refusal rules and pure functions, offline.

Every refusal checked with ``_NoDb`` runs BEFORE the first query — the
``tests/unit/test_ph5_e2_corpus_rules.py`` discipline: a fake session that
RAISES if anything reaches the database, so a validation refusal that
silently started querying (or writing) first fails here rather than passing
quietly. Every other refusal in ``app/talent_pools.py`` (pool not found,
member not found, name taken, not eligible, requisition not open) genuinely
needs a row to exist or not, so those live in
``tests/integration/test_ph5_e3_pools_db.py``.

The tenancy/eligibility/erasure-ordering/idempotency behaviour, and the
migration's own literals, are in the integration file; this one is what does
not need Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app import talent_pools as pools
from app.config import settings
from app.interviewer_scorecards import RequestMeta

COMPANY = uuid.uuid4()
ACTOR = uuid.uuid4()
POOL = uuid.uuid4()
META = RequestMeta(ip_address="127.0.0.1", user_agent="pytest")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class _NoDb:
    """A session that refuses to be used. A refusal path must not touch it."""

    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("a refusal path reached the database")

    async def scalar(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("a refusal path reached the database")

    async def commit(self) -> None:  # pragma: no cover - must not run
        raise AssertionError("a refusal path committed")

    def add(self, *_a: Any, **_k: Any) -> None:  # pragma: no cover - must not run
        raise AssertionError("a refusal path wrote a row")


# ---------------------------------------------------------------------------
# create_pool — every check below runs before the first query
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_blank_pool_name_is_refused_before_any_query() -> None:
    with pytest.raises(pools.PoolError) as caught:
        await pools.create_pool(
            _NoDb(), company_id=COMPANY, actor=ACTOR, name="   ", description=None, meta=META,  # type: ignore[arg-type]
        )
    assert caught.value.code == "invalid_name"
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_an_over_long_pool_name_is_refused_before_any_query() -> None:
    with pytest.raises(pools.PoolError) as caught:
        await pools.create_pool(
            _NoDb(), company_id=COMPANY, actor=ACTOR, name="x" * 121, description=None, meta=META,  # type: ignore[arg-type]
        )
    assert caught.value.code == "invalid_name"


@pytest.mark.asyncio
async def test_an_over_long_pool_description_is_refused_before_any_query() -> None:
    with pytest.raises(pools.PoolError) as caught:
        await pools.create_pool(
            _NoDb(), company_id=COMPANY, actor=ACTOR, name="Fitters, Vizag",  # type: ignore[arg-type]
            description="x" * 1001, meta=META,
        )
    assert caught.value.code == "invalid_name"


# ---------------------------------------------------------------------------
# _sanitise_match_reason — defence in depth on the way INTO the database
# ---------------------------------------------------------------------------
def test_sanitise_match_reason_strips_prose_and_keeps_facts() -> None:
    raw = {
        "breakdown": {"semantic": 0.7, "lexical": 0.2},
        "searched_at": "2026-09-24T00:00:00+00:00",
        "query_terms": ["hydraulics", "maintenance"],
        "why": [
            {
                "signal": "resume_terms", "contribution": 4, "explainable": True,
                "terms_matched": ["hydraulics"],
                # PROSE that must never reach the database via this path.
                "snippet": "[[hydraulics]] presses at Acme",
                "citation": {"kind": "applicant", "id": "a1", "label": "CV", "href": "/x"},
            },
            {
                "signal": "resume_similarity", "contribution": 48, "explainable": False,
                "note": pools.rediscovery.SIMILARITY_NOTE,
            },
        ],
    }
    out = pools._sanitise_match_reason(raw)
    assert out is not None
    blob = str(out)
    assert "presses at Acme" not in blob
    assert "[[hydraulics]]" not in blob
    assert pools.rediscovery.SIMILARITY_NOTE not in blob
    assert "href" not in blob  # citation keeps kind/id/label only
    terms_item = next(w for w in out["why"] if w["signal"] == "resume_terms")
    assert terms_item["terms_matched"] == ["hydraulics"]
    assert terms_item["citation"] == {"kind": "applicant", "id": "a1", "label": "CV"}
    assert out["query_terms"] == ["hydraulics", "maintenance"]


def test_sanitise_match_reason_bounds_why_items_and_query_terms() -> None:
    raw = {
        "why": [{"signal": "resume_terms", "contribution": 1, "explainable": True}] * 50,
        "query_terms": [f"t{i}" for i in range(50)],
    }
    out = pools._sanitise_match_reason(raw)
    assert out is not None
    assert len(out["why"]) == pools._MAX_MATCH_REASON_WHY
    assert len(out["query_terms"]) == pools._MAX_QUERY_TERMS


def test_sanitise_match_reason_drops_malformed_input_rather_than_raising() -> None:
    assert pools._sanitise_match_reason(None) is None
    assert pools._sanitise_match_reason({}) is None
    assert pools._sanitise_match_reason({"why": "not a list"}) == {
        "breakdown": None, "why": [], "searched_at": None, "query_terms": [],
    }


# ---------------------------------------------------------------------------
# MemberAddIn.match_reason — the size bound AHEAD of _sanitise_match_reason
# (security review, LOW): the field was `dict[str, Any] | None` with no bound
# at all, so an arbitrarily large or deeply nested body was fully parsed and
# held in memory before anything ever walked it looking for fields to keep.
# ---------------------------------------------------------------------------
def test_a_realistic_frozen_snapshot_is_well_under_the_bound() -> None:
    """The bound must not bite a genuine search result — generous headroom,
    not a target size."""
    from app.schemas.pools import MemberAddIn

    snapshot = {
        "breakdown": {"semantic": 0.7, "lexical": 0.2},
        "searched_at": "2026-09-24T00:00:00+00:00",
        "query_terms": ["hydraulics", "maintenance"],
        "why": [
            {"signal": "resume_terms", "contribution": 4, "explainable": True,
             "terms_matched": ["hydraulics"],
             "citation": {"kind": "applicant", "id": "a1", "label": "CV"}},
        ],
    }
    parsed = MemberAddIn(
        applicant_ids=[str(uuid.uuid4())], source="rediscovery", match_reason=snapshot,
    )
    assert parsed.match_reason == snapshot


def test_an_oversized_match_reason_is_refused_before_it_is_walked() -> None:
    """A hand-crafted body large enough to matter is rejected at parse time —
    a 422 at the FastAPI boundary, never handed to `_sanitise_match_reason`."""
    from pydantic import ValidationError

    from app.schemas.pools import MemberAddIn

    huge = {"why": [{"signal": "resume_terms", "note": "x" * 1000}] * 100}
    with pytest.raises(ValidationError) as caught:
        MemberAddIn(applicant_ids=[str(uuid.uuid4())], source="manual", match_reason=huge)
    assert "match_reason is too large" in str(caught.value)


def test_a_deeply_nested_match_reason_is_refused_too() -> None:
    """Nesting depth alone, not just raw byte count, must not sail through —
    a body built to be expensive to walk rather than merely large."""
    from pydantic import ValidationError

    from app.schemas.pools import MemberAddIn

    nested: dict[str, Any] = {"v": "leaf"}
    for _ in range(2000):
        nested = {"child": nested, "padding": "x" * 20}
    with pytest.raises(ValidationError) as caught:
        MemberAddIn(applicant_ids=[str(uuid.uuid4())], source="manual", match_reason=nested)
    assert "match_reason is too large" in str(caught.value)


def test_match_reason_left_absent_is_unaffected() -> None:
    from app.schemas.pools import MemberAddIn

    parsed = MemberAddIn(applicant_ids=[str(uuid.uuid4())], source="manual")
    assert parsed.match_reason is None


# ---------------------------------------------------------------------------
# _evidence_review_is_current — FIX 1 (code review): a review EXPIRES.
# Boundary test in the test_freshness_bands_at_their_boundaries style
# (tests/unit/test_ph5_e3_rediscovery_rules.py): call the real function at the
# real boundary, never re-implement the day arithmetic here and assert it
# against itself.
# ---------------------------------------------------------------------------
def test_a_never_reviewed_member_is_never_current() -> None:
    assert pools._evidence_review_is_current(None, now=NOW) is False


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (0, True),
        (365, True),   # rediscovery_review_valid_days, inclusive
        (366, False),
    ],
)
def test_a_review_is_current_only_within_its_validity_window(
    age_days: int, expected: bool,
) -> None:
    assert settings.rediscovery_review_valid_days == 365  # the boundary this test pins
    reviewed_at = NOW - timedelta(days=age_days)
    assert pools._evidence_review_is_current(reviewed_at, now=NOW) is expected


def test_review_validity_defaults_equal_to_stale_days_today_deliberately() -> None:
    """Per the setting's own comment in app/config.py: a review lapses at the
    same point the evidence it accepted would newly cross into "stale". Equal
    today, but two separate settings -- this pins that they have NOT drifted,
    not that they may never be given different values."""
    assert settings.rediscovery_review_valid_days == settings.rediscovery_stale_days


def test_match_reason_evidence_freshness_is_the_worst_band() -> None:
    sanitised = {"why": [{"freshness": "fresh"}, {"freshness": "stale"}]}
    assert pools._match_reason_evidence_freshness(sanitised) == "stale"
    assert pools._match_reason_evidence_freshness({"why": []}) is None
    assert pools._match_reason_evidence_freshness(None) is None


# ---------------------------------------------------------------------------
# Vocabularies agree with the migration's CHECK constraints — a migration
# cannot import application code, so this pin is what keeps the two in step
# (the app/rediscovery.py::REDISCOVERY_CONSENT_TYPE precedent).
# ---------------------------------------------------------------------------
def test_member_sources_and_freshness_bands_match_the_migration() -> None:
    """A migration cannot import application code, so both files carry the
    same string literals — the ``rediscovery.py``/``REDISCOVERY_CONSENT_TYPE``
    precedent (``test_ph5_e3_rediscovery_rules.py``). Read as text, on that
    same file's pattern, rather than imported as a module (``alembic.versions``
    is not an importable package)."""
    import pathlib

    migration = (
        pathlib.Path(__file__).resolve().parents[2]
        / "alembic" / "versions"
        / "20260924_0001_e4a6c8b0d2f7_ph5_e3_talent_pools.py"
    ).read_text(encoding="utf-8")
    assert 'MEMBER_SOURCES = ("manual", "rediscovery")' in migration
    assert pools.MEMBER_SOURCES == ("manual", "rediscovery")
    assert (
        'FRESHNESS_BANDS = ("fresh", "ageing", "stale", "unverifiable", "none")' in migration
    )
    assert pools.FRESHNESS_BANDS == ("fresh", "ageing", "stale", "unverifiable", "none")


# ---------------------------------------------------------------------------
# A pool cannot produce a hiring outcome (design §6.6.1 / CLAUDE.md D-05) —
# structural, checked on the SQL text itself, not only on the "hiring words"
# forbidden-source scan in tests/unit/test_ph5_w2_calibration.py.
# ---------------------------------------------------------------------------
def test_talent_pools_module_writes_enrolments_only_through_enrol_applicant() -> None:
    """The only path from a pool to ``enrolments`` is the SAME function every
    other enrolment in this service goes through — never a raw INSERT/UPDATE
    against enrolments, round_results, stage_transitions or
    interviewer_scorecards written directly in this module."""
    import inspect
    import re

    src = inspect.getsource(pools)
    # `stage_transitions` was missing from this tuple while the docstring above
    # already claimed it was covered, so a change that advanced a candidate's
    # stage on invite — the exact hiring-outcome write this design forbids
    # structurally — would have passed here. Found in code review, 2026-09-26.
    for table in (
        "enrolments", "round_results", "stage_transitions", "interviewer_scorecards",
    ):
        assert re.search(rf"(?i)\b(insert into|update|delete from)\s+{table}\b", src) is None, (
            f"talent_pools.py writes {table} directly"
        )
    assert "enrol_applicant" in src, "the sentinel import is gone — has this scan gone blind?"
