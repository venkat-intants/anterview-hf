"""PH5-E2 — the indexing half of ingestion: the reconciler pass and the three
hooks ``app/corpus.py`` exposes to it.

Ingestion is split on purpose. Parsing happens in the request, because its
failures are the ones a person must see on the spot; embedding happens in
``app/reconciliation.py::_corpus_embed_pass``, because it is the network-flaky
part and the reconciler already owns retry, backoff and parking. That split is
only safe if the background half behaves under failure, and every behaviour
below is one a broken version depends on:

* a version whose chunks were all filled by CARRY-OVER still graduates to
  ``indexed`` — the bug that left an unchanged re-upload invisible to search
  forever, with nothing on screen to say so;
* a failing batch parks the VERSION once, not once per chunk, so the
  eight-attempt budget is not spent by a single document's fourth paragraph;
* a version parked at ``failed`` says so, with the code the library screen turns
  into a sentence, and the pass keeps going for every other company.

The same fake session as the rest of PH5-E2's unit tests, with ``commit()``
ALLOWED here: unlike ``app/corpus.py``, this pass commits incrementally on
purpose, so a failure part-way through a batch does not lose the work already
done. What is asserted is the statements, their bound parameters and their
order; whether Postgres's ``NOT EXISTS`` really finds the unembedded chunk is
``tests/integration/test_ph5_e2_corpus_db.py``'s business.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app import corpus
from app import reconciliation as rec
from tests.unit._corpus_fakes import FakeSession

COMPANY = uuid.uuid4()
DOC = uuid.uuid4()
VER = uuid.uuid4()
CHUNK_A = uuid.uuid4()
CHUNK_B = uuid.uuid4()

CARRY_OVER = "UPDATE corpus_chunks c SET embedding = p.embedding"
FULLY_EMBEDDED = "SELECT v.id, v.company_id, v.document_id FROM corpus_document_versions v"
DUE_CHUNKS = "SELECT c.id, c.company_id, c.content, c.version_id, c.document_id"
MARK_INDEXING = "SET status = 'indexing'"
MARK_INDEXED = "SET status = 'indexed'"
MARK_FAILED = "SET status = 'failed'"
WRITE_VECTOR = "UPDATE corpus_chunks SET embedding = CAST(:e AS halfvec)"
PARK = "INSERT INTO reconciliation_state"


def chunk_row(chunk_id: uuid.UUID, content: str = "Thirty days notice.") -> tuple[Any, ...]:
    """(id, company_id, content, version_id, document_id) — the pass's own tuple."""
    return (chunk_id, COMPANY, content, VER, DOC)


def pass_session(
    *,
    fully_embedded: list[tuple[Any, ...]] | None = None,
    due: list[tuple[Any, ...]] | None = None,
    attempts: int = 1,
    remaining: int = 0,
    indexed_rowcount: int = 1,
    extra: list[tuple[str, Any]] | None = None,
) -> FakeSession:
    return FakeSession(
        routes=[
            (FULLY_EMBEDDED, fully_embedded or []),
            (DUE_CHUNKS, due or []),
            (MARK_INDEXED, indexed_rowcount),
            (PARK, [(attempts,)]),
            *(extra or []),
        ],
        scalars=[("SELECT count(*) FROM corpus_chunks", remaining)],
        allow_commit=True,
    )


def embedder(vectors: list[list[float]] | None = None, *, fail: bool = False) -> Any:
    calls: list[dict[str, Any]] = []

    async def _embed(*, texts: list[str], task_type: str, acting_user_id: str) -> list[list[float]]:
        calls.append({"texts": texts, "task_type": task_type, "acting_user_id": acting_user_id})
        if fail:
            raise RuntimeError("embedder unreachable")
        return vectors if vectors is not None else [[0.5, 0.25] for _ in texts]

    _embed.calls = calls  # type: ignore[attr-defined]
    return _embed


# ---------------------------------------------------------------------------
# The three hooks app/corpus.py exposes to the reconciler
# ---------------------------------------------------------------------------
async def test_marking_nothing_as_indexing_issues_no_statement() -> None:
    """Called unconditionally with whatever the pass selected; an empty batch is
    the normal steady state, not an error."""
    db = FakeSession()

    await corpus.mark_versions_indexing(db, [])  # type: ignore[arg-type]

    assert db.statements == []


async def test_marking_versions_indexing_skips_a_superseded_or_redacted_row() -> None:
    """HIGH-1. Not redundant with the caller's SELECT: a version can be
    superseded in the window between the two, and the immutability trigger
    refuses ANY status change on a superseded row — which would abort the whole
    batch UPDATE and take every other company's indexing down with it. Excluding
    the row here means Postgres simply does not touch it."""
    db = FakeSession()

    await corpus.mark_versions_indexing(db, [VER])  # type: ignore[arg-type]

    sql, params = db.one_statement(MARK_INDEXING)
    assert "status = 'parsed'" in sql, "only a parsed version is on its way to indexing"
    assert "superseded_at IS NULL" in sql
    assert "redacted_at IS NULL" in sql
    assert params == {"ids": [VER]}


async def test_promoting_a_version_writes_the_indexed_event() -> None:
    db = FakeSession(routes=[(MARK_INDEXED, 1)], allow_commit=True)

    promoted = await corpus.mark_version_indexed(  # type: ignore[arg-type]
        db, company_id=COMPANY, document_id=DOC, version_id=VER
    )

    assert promoted is True
    sql, _ = db.one_statement(MARK_INDEXED)
    assert "indexed_at = now()" in sql
    assert "status IN ('parsed', 'indexing')" in sql
    event = db.params_for("INSERT INTO corpus_events")
    assert (event["a"], event["v"], event["t"]) == ("indexed", VER, "system")


async def test_promoting_a_version_that_is_not_a_candidate_is_a_silent_no_op() -> None:
    """The pass calls this unconditionally, so "already indexed" and "superseded
    since" must both come back False — and must NOT write a second ``indexed``
    event, which the version-history screen would show as the document having
    been indexed twice."""
    db = FakeSession(routes=[(MARK_INDEXED, 0)], allow_commit=True)

    promoted = await corpus.mark_version_indexed(  # type: ignore[arg-type]
        db, company_id=COMPANY, document_id=DOC, version_id=VER
    )

    assert promoted is False
    assert not db.has("INSERT INTO corpus_events")


async def test_failing_a_version_records_the_code_the_screen_turns_into_a_sentence() -> None:
    db = FakeSession(allow_commit=True)

    await corpus.mark_version_failed(  # type: ignore[arg-type]
        db, company_id=COMPANY, document_id=DOC, version_id=VER,
        failure_code="embedding_unavailable",
    )

    sql, params = db.one_statement(MARK_FAILED)
    assert params == {"fc": "embedding_unavailable", "v": VER}
    assert "redacted_at IS NULL" in sql, (
        "a version purged under retention must not be resurrected as 'failed'"
    )
    assert params["fc"] in corpus.FAILURE_CODES
    import json

    assert json.loads(db.params_for("INSERT INTO corpus_events")["j"]) == {
        "failure_code": "embedding_unavailable"
    }


# ---------------------------------------------------------------------------
# Carry-over — the reason a re-upload costs one embedder call, not four hundred
# ---------------------------------------------------------------------------
async def test_the_pass_carries_over_identical_chunks_before_it_embeds_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = pass_session()
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    assert db.step(CARRY_OVER) < db.step(FULLY_EMBEDDED)
    assert db.step(CARRY_OVER) < db.step(DUE_CHUNKS)


async def test_carry_over_never_crosses_a_document_let_alone_a_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content hash alone would match a paragraph that appears in another
    company's handbook — and would then hand that company's vector to this one.
    Scoped to (company_id, document_id), asserted on the statement because it is
    the only place the scope exists."""
    db = pass_session()
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    sql, _ = db.one_statement(CARRY_OVER)
    assert "p.content_sha256 = c.content_sha256" in sql
    assert "p.company_id = c.company_id" in sql
    assert "p.document_id = c.document_id" in sql
    assert "c.embedding IS NULL" in sql
    assert "p.embedding IS NOT NULL" in sql


async def test_a_version_made_whole_by_carry_over_alone_still_graduates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The code-review MUST finding. A re-upload whose text is unchanged has
    every chunk filled by the carry-over, so the "chunks needing an embedding"
    select comes back empty — and the pass used to return before promoting the
    version. It sat at ``parsed`` forever while ``search_corpus`` filtered on
    ``status = 'indexed'``, so the document was silently unfindable."""
    db = pass_session(fully_embedded=[(VER, COMPANY, DOC)], due=[])
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert result.corpus_indexed == 1
    assert db.params_for(MARK_INDEXED)["v"] == VER
    assert db.params_for("DELETE FROM reconciliation_state") == {
        "kind": rec.KIND_CORPUS_CHUNK, "ref": VER
    }


async def test_the_graduation_query_wants_chunks_that_exist_and_none_unembedded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves matter. Without the EXISTS, a version whose chunks were all
    deleted (a purge racing the pass) would "graduate" with nothing in it."""
    db = pass_session()
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    sql, _ = db.one_statement(FULLY_EMBEDDED)
    assert "EXISTS (SELECT 1 FROM corpus_chunks c WHERE c.version_id = v.id)" in sql
    assert "AND c.embedding IS NULL)" in sql
    assert "v.status IN ('parsed', 'indexing')" in sql


# ---------------------------------------------------------------------------
# The embedding batch
# ---------------------------------------------------------------------------
async def test_the_pass_only_selects_work_that_is_not_parked_or_backing_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this, a version that has already given up is re-attempted on
    every pass forever — which is what the reindex button exists to override
    deliberately rather than by accident."""
    db = pass_session()
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    sql, params = db.one_statement(DUE_CHUNKS)
    assert "rs.gave_up_at IS NOT NULL" in sql
    assert "rs.next_attempt_at IS NOT NULL AND rs.next_attempt_at > :now" in sql
    assert params["kind"] == rec.KIND_CORPUS_CHUNK
    assert params["lim"] == rec.CORPUS_EMBED_BATCH


async def test_an_embedded_chunk_is_written_with_its_vector_and_its_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = pass_session(due=[chunk_row(CHUNK_A), chunk_row(CHUNK_B, "Leave accrues monthly.")])
    embed = embedder([[0.5, 0.25], [0.125, -0.5]])
    monkeypatch.setattr(rec, "embed_texts_remote", embed)
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    writes = [p for _, p in db.matching(WRITE_VECTOR)]
    assert [w["i"] for w in writes] == [CHUNK_A, CHUNK_B]
    assert [w["e"] for w in writes] == ["[0.5,0.25]", "[0.125,-0.5]"]
    assert {w["c"] for w in writes} == {COMPANY}
    assert result.corpus_embedded == 2
    assert embed.calls[0]["texts"] == ["Thirty days notice.", "Leave accrues monthly."]
    assert embed.calls[0]["task_type"] == "document"
    assert embed.calls[0]["acting_user_id"] == f"system:corpus:{COMPANY}"


async def test_a_chunk_the_embedder_returned_nothing_for_is_left_for_the_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing an empty vector would satisfy the ``embedding IS NULL`` predicate
    and promote the version to ``indexed`` with a chunk that can never match —
    a document that is searchable and unfindable at the same time."""
    db = pass_session(due=[chunk_row(CHUNK_A), chunk_row(CHUNK_B)], remaining=1)
    monkeypatch.setattr(rec, "embed_texts_remote", embedder([[0.5, 0.25], []]))
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    writes = [p for _, p in db.matching(WRITE_VECTOR)]
    assert [w["i"] for w in writes] == [CHUNK_A]
    assert result.corpus_embedded == 1
    assert not db.has(MARK_INDEXED), "a version with an unembedded chunk must not graduate"


async def test_a_version_is_promoted_once_its_last_chunk_is_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = pass_session(due=[chunk_row(CHUNK_A)], remaining=0)
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert result.corpus_indexed == 1
    assert db.step(WRITE_VECTOR) < db.step(MARK_INDEXED)
    assert db.has("DELETE FROM reconciliation_state")


async def test_the_pass_marks_what_it_is_about_to_touch_as_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``indexing`` is what the library screen shows while a document is not yet
    searchable, so it has to be set before the slow call, not after."""
    db = pass_session(due=[chunk_row(CHUNK_A)])
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    assert db.step(MARK_INDEXING) < db.step(WRITE_VECTOR)
    assert db.params_for(MARK_INDEXING)["ids"] == [VER]


# ---------------------------------------------------------------------------
# Failure: parking, backoff, giving up
# ---------------------------------------------------------------------------
async def test_a_failing_batch_parks_the_version_once_not_once_per_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The eight-attempt budget is counted per VERSION. Several chunks of one
    document land in the same failing batch, so recording an attempt per chunk
    would burn the whole budget on one embedder blip and park a perfectly good
    document."""
    db = pass_session(due=[chunk_row(CHUNK_A), chunk_row(CHUNK_B)], attempts=2)
    monkeypatch.setattr(rec, "embed_texts_remote", embedder(fail=True))
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert len(db.matching(PARK)) == 1
    assert db.params_for(PARK)["ref"] == VER
    assert result.failed == 1
    assert result.gave_up == 0
    assert not db.has(MARK_FAILED), "two attempts in, it is still retrying"


async def test_a_failing_batch_schedules_a_retry_rather_than_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = pass_session(due=[chunk_row(CHUNK_A)], attempts=3)
    monkeypatch.setattr(rec, "embed_texts_remote", embedder(fail=True))

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    sql, params = db.one_statement("SET next_attempt_at = :nxt")
    assert params["ref"] == VER
    assert params["nxt"] is not None
    assert "gave_up_at" not in sql


async def test_the_last_attempt_parks_the_version_with_a_code_a_person_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After MAX_ATTEMPTS the version stops being retried, and the document has
    to SAY so — ``embedding_unavailable`` is the one failure code written onto
    an already-created version, and the sentence it maps to is what the reindex
    button sits next to."""
    db = pass_session(due=[chunk_row(CHUNK_A)], attempts=rec.MAX_ATTEMPTS)
    monkeypatch.setattr(rec, "embed_texts_remote", embedder(fail=True))
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert result.gave_up == 1
    assert db.params_for(MARK_FAILED)["fc"] == "embedding_unavailable"
    assert db.has("SET gave_up_at = :now")
    assert "will be indexed automatically" in corpus.FAILURE_SENTENCES["embedding_unavailable"]


async def test_a_refused_indexing_update_parks_the_batch_instead_of_wedging_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH-1's other half. If the status UPDATE is refused — a trigger, a
    version superseded in the narrow window — the pass must roll back, park the
    affected versions and RETURN. Raising past this function would stop every
    other company's indexing behind one row."""
    db = pass_session(
        due=[chunk_row(CHUNK_A)],
        attempts=1,
        extra=[(MARK_INDEXING, RuntimeError("corpus_versions_immutable"))],
    )
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert db.rollbacks == 1
    assert result.failed == 1
    assert db.params_for(PARK)["ref"] == VER
    assert not db.has(WRITE_VECTOR), "it returned instead of embedding into a refused state"


async def test_a_refused_indexing_update_on_its_last_attempt_fails_the_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = pass_session(
        due=[chunk_row(CHUNK_A)],
        attempts=rec.MAX_ATTEMPTS,
        extra=[(MARK_INDEXING, RuntimeError("corpus_versions_immutable"))],
    )
    monkeypatch.setattr(rec, "embed_texts_remote", embedder())
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert result.gave_up == 1
    assert db.params_for(MARK_FAILED)["fc"] == "embedding_unavailable"


async def test_the_parked_error_text_is_truncated_before_it_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An embedder that returns an HTML error page would otherwise write the
    whole page into ``last_error`` on every attempt."""
    db = pass_session(due=[chunk_row(CHUNK_A)], attempts=1)

    async def _huge(**_kw: Any) -> list[list[float]]:
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr(rec, "embed_texts_remote", _huge)

    await rec._corpus_embed_pass(db, rec.PassResult())  # type: ignore[arg-type]

    assert len(db.params_for(PARK)["err"]) <= 1000


async def test_nothing_to_do_is_the_quiet_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The steady state, run once a minute: the carry-over, the graduation check
    and the work select, and then out — no embedder call, no status churn."""
    db = pass_session()
    embed = embedder()
    monkeypatch.setattr(rec, "embed_texts_remote", embed)
    result = rec.PassResult()

    await rec._corpus_embed_pass(db, result)  # type: ignore[arg-type]

    assert embed.calls == []
    assert not db.has(MARK_INDEXING)
    assert (result.corpus_embedded, result.corpus_indexed, result.failed) == (0, 0, 0)
