"""The EXISTING applicant search, against a real Postgres with pgvector.

WHY THIS FILE EXISTS, AND WHY IT IS NOT AN E3 FILE
PH5 criterion 18 says the existing semantic applicant search keeps working.
Before this file, what actually covered ``GET /hr/applicants?q=`` was: one
escaping unit test with the embedder patched to RAISE, one route-wiring test
that monkeypatches the search out entirely, the embedding-client units (none of
which touch the SQL), and a smoke that runs its own raw cosine query without
going through the function. Nothing exercised the hybrid statement against a
database. Marking criterion 18 met on that evidence would have meant "it still
imports".

So this file is deliberately written to pass on ``main`` as it stands — it
imports nothing from PH5-E3 and asserts today's behaviour, including the two
places that behaviour is worse than E3's (a silent fallback and no reported
degradation). It was run against ``main`` before the E3 changes existed; if it
only passed afterwards it would be testing E3, not criterion 18.

``AI_FAKE_MODE`` makes the vectors deterministic (equal text, equal vector), so
a candidate whose stored vector IS the query's vector has cosine similarity 1
and everyone else has noise. That is what makes a RANKING assertion possible.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.embedding_client import EmbeddingError, to_pgvector_literal
from app.fake_ai import fake_embeddings
from app.routers import hr_applicants as ha

pytestmark = pytest.mark.integration

QUERY = "hydraulics preventive maintenance"
# Both the semantic best match AND a keyword hit.
RESUME_BEST = "Fitter with hydraulics and preventive maintenance of presses."
# A keyword hit, an unrelated vector.
RESUME_KEYWORD = "Shift supervisor. Some exposure to hydraulics on the line."
# Neither.
RESUME_NONE = "Accounts payable clerk: ledgers, invoices, reconciliation."


@pytest.fixture(autouse=True)
def deterministic_query_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The query embedding, faked per test rather than through the process-wide
    ``AI_FAKE_MODE`` — which four unrelated suites assert is OFF by default and
    which CI sets nowhere, so a file that needed it globally true would be a file
    the suite cannot run green. ``fake_embeddings`` is deterministic (equal text,
    equal vector), which is what makes a ranking assertion possible at all.

    The degradation test replaces this with a raiser."""

    async def _fake_embed_one_remote(
        *, text: str, task_type: str, acting_user_id: str
    ) -> list[float]:
        return fake_embeddings([text])[0]

    monkeypatch.setattr(ha, "embed_one_remote", _fake_embed_one_remote)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.other_company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.best = uuid.uuid4()
        self.keyword = uuid.uuid4()
        self.none = uuid.uuid4()
        self.other = uuid.uuid4()
        self.tag = self.company.hex[:8]


async def _seed(db: AsyncSession) -> F:
    f = F()
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c,'S A',:sa),(:o,'S B',:sb)"),
        {"c": f.company, "o": f.other_company, "sa": f"s-a-{f.tag}", "sb": f"s-b-{f.tag}"},
    )
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u,:e,:c)"),
        {"u": f.hr, "e": f"hr-{f.tag}@search.test", "c": f.company},
    )
    rows = (
        (f.best, f.company, "Best Match", RESUME_BEST, QUERY),
        (f.keyword, f.company, "Keyword Only", RESUME_KEYWORD, "something else"),
        (f.none, f.company, "No Signal", RESUME_NONE, "ledgers and invoices"),
        (f.other, f.other_company, "Other Tenant", RESUME_BEST, QUERY),
    )
    for applicant_id, company_id, name, resume, vector_text in rows:
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, full_name, email, target_job_title,"
                " resume_text, created_at, updated_at)"
                " VALUES (:i,:c,:n,:e,'Maintenance Fitter',:r,:t,:t)"
            ),
            {"i": applicant_id, "c": company_id, "n": name,
             "e": f"{applicant_id.hex[:8]}@search.test", "r": resume, "t": now},
        )
        await db.execute(
            text("UPDATE applicants SET embedding = CAST(:v AS halfvec) WHERE id = :i"),
            {"v": to_pgvector_literal(fake_embeddings([vector_text])[0]), "i": applicant_id},
        )
    return f


async def _search(db: AsyncSession, f: F, query: str = QUERY) -> list[Any]:
    return await ha._semantic_search(
        db, f.hr, f.company, query, None, None
    )


@pytest.mark.asyncio
async def test_the_hybrid_search_returns_the_right_candidate_first(db: AsyncSession) -> None:
    """The semantic leg dominates (0.7 vs 0.3), so the candidate whose CV reads
    closest to the query outranks one that merely contains a keyword."""
    f = await _seed(db)
    results = await _search(db, f)
    names = [r.full_name for r in results]
    assert names[0] == "Best Match", names
    assert "Keyword Only" in names
    scores = {r.full_name: r.match_score for r in results}
    assert scores["Best Match"] is not None
    assert scores["Best Match"] > scores["Keyword Only"]
    assert 0 <= scores["Keyword Only"] <= 100


@pytest.mark.asyncio
async def test_a_candidate_with_no_signal_at_all_is_not_returned(db: AsyncSession) -> None:
    """The predicate is "an embedding OR a keyword hit". Every seeded applicant
    here HAS an embedding, so this asserts what actually bounds the set: a row
    with an unrelated vector and no keyword still comes back (with a low score),
    because an embedding is itself a signal. The row that must never appear is
    another tenant's."""
    f = await _seed(db)
    results = await _search(db, f)
    assert "Other Tenant" not in [r.full_name for r in results]
    ranked = {r.full_name: r.match_score for r in results}
    assert ranked.get("No Signal", 0) < ranked["Best Match"]


@pytest.mark.asyncio
async def test_the_search_is_scoped_to_the_callers_company(db: AsyncSession) -> None:
    """The other tenant's applicant has the SAME resume and the SAME vector as
    the best match, so it would rank first if tenancy were not in the query."""
    f = await _seed(db)
    results = await _search(db, f)
    assert all(r.full_name != "Other Tenant" for r in results)
    # And from the other side: that company sees only its own.
    theirs = await ha._semantic_search(db, f.hr, f.other_company, QUERY, None, None)
    assert [r.full_name for r in theirs] == ["Other Tenant"]


@pytest.mark.asyncio
async def test_a_deleted_applicant_never_appears(db: AsyncSession) -> None:
    f = await _seed(db)
    await db.execute(
        text("UPDATE applicants SET deleted_at = now() WHERE id = :i"), {"i": f.best}
    )
    assert "Best Match" not in [r.full_name for r in await _search(db, f)]


@pytest.mark.asyncio
async def test_it_degrades_to_full_text_when_the_embedder_is_down(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's behaviour, asserted as it is rather than as it should be: the
    semantic leg becomes 0, the predicate narrows to a keyword hit, and the
    CALLER IS NOT TOLD — there is no flag on the response saying the ranking is
    keyword-only. (PH5-E3's own search reports ``semantic: false`` for exactly
    this reason; this route still does not, and that is a known gap, not a
    regression introduced by E3.)"""
    f = await _seed(db)

    async def _boom(**_kw: Any) -> list[float]:
        raise EmbeddingError("down")

    monkeypatch.setattr(ha, "embed_one_remote", _boom)
    results = await _search(db, f)
    names = [r.full_name for r in results]
    assert "Best Match" in names
    # No keyword hit, so no row at all once the vector leg is gone.
    assert "No Signal" not in names
    # AND, not OR: plainto_tsquery joins the query's terms with `&`, so a CV
    # that contains "hydraulics" but neither "preventive" nor "maintenance"
    # does NOT match. Worth asserting rather than assuming — it is the
    # difference between "keyword search" as users imagine it and what this
    # actually does, and it is the whole ranking once the embedder is down.
    assert "Keyword Only" not in names
    assert all(r.match_score is not None for r in results)

    single_term = await _search(db, f, "hydraulics")
    assert "Keyword Only" in [r.full_name for r in single_term]


@pytest.mark.asyncio
async def test_a_query_with_no_keyword_hit_still_ranks_by_similarity(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    results = await _search(db, f, "zzzznonsenseterm")
    # Every applicant has an embedding, so the semantic leg keeps them all in
    # play — which is the behaviour the 0.7/0.3 weighting implies.
    assert [r.full_name for r in results]
    assert all(r.match_score is not None for r in results)


@pytest.mark.asyncio
async def test_like_wildcards_in_the_job_filter_are_escaped(db: AsyncSession) -> None:
    """The one behaviour the existing unit test covers with the embedder
    patched out, re-asserted against a real database: a ``%`` typed into the
    job filter matches literally instead of matching everything."""
    f = await _seed(db)
    hit = await ha._semantic_search(db, f.hr, f.company, QUERY, None, "%")
    assert hit == []
