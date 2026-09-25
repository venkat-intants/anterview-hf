"""PH5-E2 — the corpus's DB-driven service functions, offline.

``tests/unit/test_ph5_e2_corpus_rules.py`` covers the refusals that happen
before the first query and ``test_ph5_e2_corpus_ingest.py`` the pure parsing and
chunking. This file covers the half in between: the functions that issue SQL,
in the ``tests/unit/test_agent_tools.py`` idiom — a fake session returning
canned rows — extended (``tests/unit/_corpus_fakes.py``) so that object-store
calls land on the SAME timeline as the statements. That is what makes the
ORDERING claims in ``app/corpus.py``'s docstrings assertable here:

* an upload's rows exist before its object does, and a refusal writes nothing;
* a purge removes the OBJECT first and only then redacts the row that names it,
  and refuses to redact at all if the removal came up short — so neither a row
  pointing at a deleted file nor a file with no row survives a crash;
* a replace-upload stamps the old version superseded and repoints the document
  without an intervening commit.

WHAT IS DELIBERATELY NOT HERE. Nothing that depends on what Postgres does with
a statement: that ``d.audience = ANY(CAST(:aud AS text[]))`` really filters,
that ``corpus_versions_immutable`` refuses a superseded row's update, that a
failed purge really rolls back, that ``ts_rank_cd``/``halfvec`` rank anything.
A fake session would let any of those "pass" while broken, so they live in
``tests/integration/test_ph5_e2_corpus_db.py`` and the assertions here stop at
the statement and its bound parameters.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import corpus
from app.interviewer_scorecards import RequestMeta
from tests.unit._corpus_fakes import FakeSession, StoreSpy, install_store

COMPANY = uuid.uuid4()
ACTOR = uuid.uuid4()
DOC = uuid.uuid4()
META = RequestMeta(ip_address="203.0.113.9", user_agent="pytest")

# Two blank-line-separated blocks, each comfortably over the 1,200-char chunk
# size, so a real upload produces several chunks through the real chunker.
TXT = (
    "# Leave policy\n\n"
    + "Staff accrue twenty days of paid leave in each calendar year. " * 24
    + "\n\n## Notice period\n\n"
    + "A confirmed employee gives thirty days of written notice. " * 24
).encode()


def doc_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": DOC, "company_id": COMPANY, "title": "Employee handbook",
        "audience": "all_staff", "doc_kind": "handbook", "expires_on": None,
        "current_version_id": None, "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC), "deleted_at": None,
    }
    row.update(over)
    return row


def version_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid.uuid4(), "version": 1, "status": "parsed", "failure_code": None,
        "original_name": "handbook.txt", "content_type": "text/plain", "size_bytes": len(TXT),
        "page_count": None, "char_count": len(TXT), "chunk_count": 3, "injection_markers": 0,
        "uploaded_at": datetime.now(tz=UTC),
    }
    row.update(over)
    return row


def ingest_session(*, documents: int = 3, chunks: int = 100) -> FakeSession:
    """A session whose quota answers leave room, so ingest reaches its writes."""
    return FakeSession(
        routes=[
            ("SELECT * FROM corpus_documents", [doc_row()]),
            ("SELECT * FROM corpus_document_versions WHERE id = :i", [version_row()]),
        ],
        scalars=[
            ("SELECT count(*) FROM corpus_documents", documents),
            ("COALESCE(SUM(chunk_count), 0)", chunks),
        ],
    )


async def ingest(db: FakeSession, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "company_id": COMPANY, "actor": ACTOR, "actor_role": "hr_manager",
        "title": "Employee handbook", "audience": "all_staff", "doc_kind": "handbook",
        "expires_on": None, "attested": True, "data": TXT, "filename": "handbook.txt",
        "meta": META,
    }
    kwargs.update(over)
    return await corpus.ingest_document(db, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ingest_document — the happy path, and the order it happens in
# ---------------------------------------------------------------------------
async def test_ingest_stores_the_object_only_after_the_rows_that_name_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror image of the purge ordering below, and the reason it is safe:
    on the way IN the rows come first, so a crash between the two leaves a row
    whose object is missing (a 410 on download, recoverable by re-upload) and
    never an object nothing references (an unreachable, unpurgeable file, which
    under DPDP erasure is the worse of the two)."""
    db = ingest_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db)

    assert db.step("INSERT INTO corpus_documents") < db.step("INSERT INTO corpus_document_versions")
    assert db.step("INSERT INTO corpus_document_versions") < db.step("INSERT INTO corpus_chunks")
    assert db.step("INSERT INTO corpus_chunks") < db.step("store.put:")
    assert len(spy.put) == 1


async def test_ingest_stores_the_object_under_exactly_the_key_it_wrote_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row whose storage_key names a different object than the one written is
    a document that exists on screen and 410s on download, with an orphan file
    left behind — so the key is asserted as ONE value used twice, not two."""
    db = ingest_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db)

    row_key = db.params_for("INSERT INTO corpus_document_versions")["k"]
    assert spy.put[0][0] == row_key
    assert spy.put[0][1] == TXT
    assert spy.put[0][2] == "text/plain"
    # The key is derived from the ids the same statements wrote, not invented.
    document_id = db.params_for("INSERT INTO corpus_documents")["i"]
    version_id = db.params_for("INSERT INTO corpus_document_versions")["i"]
    assert row_key == f"corpus/{COMPANY}/{document_id}/{version_id}"


async def test_ingest_records_a_chunk_count_that_matches_the_chunk_rows_it_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chunk_count`` is not decoration: ``_check_chunk_quota`` sums it to
    decide whether the next upload fits, and the library screen shows it. If it
    disagreed with the rows actually inserted, the company quota would be
    computed against a document that does not exist."""
    db = ingest_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db)

    chunk_inserts = db.matching("INSERT INTO corpus_chunks")
    assert len(chunk_inserts) > 1, "the fixture must produce a multi-chunk document"
    version_params = db.params_for("INSERT INTO corpus_document_versions")
    assert version_params["chc"] == len(chunk_inserts)
    assert version_params["sz"] == len(TXT)
    # Ordinals are dense and zero-based — the citation locator and the
    # reconciler both address a chunk by (version_id, ordinal).
    assert [p["o"] for _, p in chunk_inserts] == list(range(len(chunk_inserts)))


async def test_ingest_points_the_document_at_the_version_it_just_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Until ``current_version_id`` is set, the document is invisible to
    ``list_documents``' LEFT JOIN and to the download's default-version join."""
    db = ingest_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db)

    repoint = db.params_for("UPDATE corpus_documents SET current_version_id")
    assert repoint["v"] == db.params_for("INSERT INTO corpus_document_versions")["i"]
    assert repoint["i"] == db.params_for("INSERT INTO corpus_documents")["i"]


async def test_ingest_writes_the_uploaded_then_parsed_events_for_the_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = ingest_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db)

    events = [p for _, p in db.matching("INSERT INTO corpus_events")]
    assert [e["a"] for e in events] == ["uploaded", "parsed"]
    version_id = db.params_for("INSERT INTO corpus_document_versions")["i"]
    assert {e["v"] for e in events} == {version_id}
    assert {e["c"] for e in events} == {COMPANY}
    assert json.loads(events[1]["j"])["chunk_count"] == len(db.matching("INSERT INTO corpus_chunks"))


async def test_the_upload_audit_row_carries_facts_and_neither_the_title_nor_the_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_audit``'s stated rule ("facts only — never a title, a file name, or
    extracted text"), asserted rather than trusted: a title is HR's own free
    text and a file name regularly carries a person's name."""
    db = ingest_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db, title="Sensitive — Priya's grievance handbook", filename="priya-notes.txt")

    assert len(db.added) == 1
    entry = db.added[0]
    assert entry.action == "corpus.document.uploaded"
    assert entry.resource_type == "corpus_document"
    assert entry.ip_address == "203.0.113.9"
    blob = json.dumps(entry.details)
    assert "Priya" not in blob
    assert "priya-notes" not in blob
    assert "Staff accrue" not in blob
    assert entry.details["attested"] is True
    assert entry.details["audience"] == "all_staff"


# ---------------------------------------------------------------------------
# ingest_document — quotas, and that a refusal writes nothing
# ---------------------------------------------------------------------------
async def test_the_document_quota_is_checked_before_the_file_is_even_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proved by WHICH error comes back: the payload here is binary junk that
    ``check_corpus`` would refuse as ``unsupported_type``. Getting
    ``quota_exceeded`` instead is only possible if the quota query ran first —
    so a company at its limit never pays for a parse."""
    db = ingest_session(documents=corpus.settings.corpus_max_documents_per_company)
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await ingest(db, data=b"\x00\x01binary junk", filename="junk.txt")

    assert caught.value.code == "quota_exceeded"
    assert caught.value.message == corpus.FAILURE_SENTENCES["quota_exceeded"]
    assert db.writes() == []
    assert spy.put == []
    assert len(db.statements) == 1


async def test_the_chunk_quota_is_checked_after_parsing_but_before_the_first_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It cannot be checked earlier — the incoming chunk count is only known
    once the document is chunked — so this is the last refusal, and it still
    has to leave no row and no object behind."""
    db = ingest_session(chunks=corpus.settings.corpus_max_chunks_per_company)
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await ingest(db)

    assert caught.value.code == "quota_exceeded"
    assert db.writes() == []
    assert spy.put == []
    assert db.added == []


async def test_the_chunk_quota_counts_only_the_searchable_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``superseded_at IS NULL``/``redacted_at IS NULL`` in that sum is the
    documented decision (a company that fixes typos by re-uploading must not
    accumulate quota against history nobody can search), so the predicate is
    pinned here."""
    db = ingest_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await ingest(db)

    sql, params = db.one_statement("COALESCE(SUM(chunk_count), 0)")
    assert "redacted_at IS NULL" in sql
    assert "superseded_at IS NULL" in sql
    assert params == {"c": COMPANY}


# Built by a factory rather than passed as a parameter: one of these payloads is
# 10 MB, and pytest would put the whole thing in the test id.
@pytest.mark.parametrize(
    ("make", "filename", "code"),
    [
        (lambda: b"x" * (corpus.settings.corpus_document_max_bytes + 1), "big.txt", "too_large"),
        (lambda: b"a " * 250_000, "long.txt", "too_long"),
        (lambda: b"\x00\x01\x02 not a document", "thing.bin", "unsupported_type"),
    ],
    ids=["too_large", "too_long", "unsupported_type"],
)
async def test_a_file_the_parser_refuses_creates_no_row(
    monkeypatch: pytest.MonkeyPatch, make: Any, filename: str, code: str
) -> None:
    db = ingest_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await ingest(db, data=make(), filename=filename)

    assert caught.value.code == code
    assert caught.value.code in corpus.FAILURE_CODES
    assert db.writes() == []
    assert spy.put == []


async def test_a_scanned_pdf_with_no_text_layer_is_refused_as_no_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented reason the corpus does not accept images: a photographed
    policy would be indexed as nothing at all. A real PDF with real pages and
    no text is the closest thing to that scan, and it must not become a
    searchable document with zero chunks."""
    db = ingest_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await ingest(db, data=blank_pdf(2), filename="scan.pdf")

    assert caught.value.code == "no_text"
    assert "scan" in caught.value.message
    assert db.writes() == []
    assert spy.put == []


# ---------------------------------------------------------------------------
# add_version — a new row, never an overwrite
# ---------------------------------------------------------------------------
def replace_session(*, current: uuid.UUID | None, next_version: int = 2) -> FakeSession:
    return FakeSession(
        routes=[
            ("SELECT * FROM corpus_documents", [doc_row(current_version_id=current)]),
            (
                "SELECT * FROM corpus_document_versions WHERE id = :i",
                [version_row(version=next_version)],
            ),
        ],
        scalars=[
            ("COALESCE(max(version), 0) + 1", next_version),
            ("COALESCE(SUM(chunk_count), 0)", 50),
        ],
    )


async def replace(db: FakeSession, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "company_id": COMPANY, "actor": ACTOR, "actor_role": "hr_manager",
        "document_id": DOC, "data": TXT, "filename": "handbook-v2.txt", "meta": META,
    }
    kwargs.update(over)
    return await corpus.add_version(db, **kwargs)  # type: ignore[arg-type]


async def test_a_replace_supersedes_the_old_version_and_repoints_the_document_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two halves of a replace-upload: the previous version is stamped
    ``superseded_at``/``superseded_by_id`` and the document is repointed at the
    new one. Either alone is a broken library — an unsuperseded old version is
    still retrievable by ``search_corpus`` (its own filter is
    ``superseded_at IS NULL``), and an unrepointed document still shows and
    downloads the old file.

    The fake session refuses ``commit()``, so this also pins that neither
    happens in a transaction of its own: the router commits once, after both.
    """
    previous = uuid.uuid4()
    db = replace_session(current=previous)
    install_store(monkeypatch, corpus, StoreSpy(db))

    await replace(db)

    new_version_id = db.params_for("INSERT INTO corpus_document_versions")["i"]
    supersede = db.params_for("SET superseded_at = :now, superseded_by_id = :v")
    assert supersede["p"] == previous
    assert supersede["v"] == new_version_id
    repoint = db.params_for("UPDATE corpus_documents SET current_version_id")
    assert repoint["v"] == new_version_id
    assert db.commits == 0


async def test_the_superseded_event_names_the_old_version_not_the_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The version history screen reads these events; attaching "superseded" to
    the incoming version would read as the new upload having been replaced by
    itself."""
    previous = uuid.uuid4()
    db = replace_session(current=previous, next_version=4)
    install_store(monkeypatch, corpus, StoreSpy(db))

    await replace(db)

    events = [p for _, p in db.matching("INSERT INTO corpus_events")]
    superseded = [e for e in events if e["a"] == "superseded"]
    assert len(superseded) == 1
    assert superseded[0]["v"] == previous
    assert json.loads(superseded[0]["j"])["superseded_by_version"] == 4
    assert [e["a"] for e in events] == ["superseded", "uploaded", "parsed"]


async def test_the_first_upload_through_add_version_supersedes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document whose ingest stored its rows but died before the repoint has
    ``current_version_id IS NULL``; replacing it must not UPDATE a NULL id."""
    db = replace_session(current=None, next_version=2)
    install_store(monkeypatch, corpus, StoreSpy(db))

    await replace(db)

    assert not db.has("superseded_by_id")
    assert not [p for _, p in db.matching("INSERT INTO corpus_events") if p["a"] == "superseded"]


async def test_a_replace_numbers_the_new_version_from_the_documents_own_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoped to this document (``WHERE document_id = :d``), not the company —
    version numbers are per document and are what a citation locator prints."""
    db = replace_session(current=uuid.uuid4(), next_version=7)
    install_store(monkeypatch, corpus, StoreSpy(db))

    await replace(db)

    sql, params = db.one_statement("COALESCE(max(version), 0) + 1")
    assert "WHERE document_id = :d" in sql
    assert params == {"d": DOC}
    assert db.params_for("INSERT INTO corpus_document_versions")["ver"] == 7
    assert db.added[0].details["version"] == 7
    assert db.added[0].action == "corpus.document.replaced"


async def test_a_super_admin_cannot_replace_an_hr_only_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audience check is the FIRST thing after the row is read: the refusal
    must cost no parse, no quota query and no object."""
    db = FakeSession(
        routes=[("SELECT * FROM corpus_documents", [doc_row(audience="hr_only")])],
    )
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await replace(db, actor_role="super_admin")

    assert caught.value.code == corpus._HR_ONLY_REQUIRES_HR_MANAGER
    assert len(db.statements) == 1
    assert spy.put == []


async def test_replacing_a_document_that_is_not_this_companys_is_a_404() -> None:
    """``_document_row`` binds company_id, so a foreign or deleted document
    simply is not there — indistinguishable from one that never existed."""
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [])])

    with pytest.raises(corpus.CorpusError) as caught:
        await replace(db)

    assert caught.value.status_code == 404
    sql, params = db.one_statement("SELECT * FROM corpus_documents")
    assert "company_id = :c" in sql
    assert "deleted_at IS NULL" in sql
    assert params == {"i": DOC, "c": COMPANY}


# ---------------------------------------------------------------------------
# update_document — audience and expiry are the document's FULL new state
# ---------------------------------------------------------------------------
async def edit(db: FakeSession, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "company_id": COMPANY, "actor": ACTOR, "actor_role": "hr_manager",
        "document_id": DOC, "audience": "all_staff", "expires_on": None, "meta": META,
    }
    kwargs.update(over)
    return await corpus.update_document(db, **kwargs)  # type: ignore[arg-type]


async def test_a_caller_who_cannot_read_a_document_cannot_widen_its_audience() -> None:
    """HIGH-2. A super_admin may not read an hr_only document, so it may not
    relabel one ``all_staff`` and then read it — and the refusal is the same 404
    ``get_document`` gives, so the response does not confirm the document
    exists. Nothing is written."""
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [doc_row(audience="hr_only")])])

    with pytest.raises(corpus.CorpusError) as caught:
        await edit(db, actor_role="super_admin", audience="all_staff")

    assert caught.value.status_code == 404
    assert db.writes() == []
    assert db.added == []


async def test_a_super_admin_editing_a_document_it_can_read_is_still_refused_hr_only() -> None:
    """The order of the two checks matters and is asserted by the DIFFERENCE in
    the two codes: readable-but-forbidden-target is the 422 (here), while
    unreadable is the 404 above — a 404 here would tell a super_admin that a
    document it is looking at does not exist."""
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [doc_row(audience="all_staff")])])

    with pytest.raises(corpus.CorpusError) as caught:
        await edit(db, actor_role="super_admin", audience="hr_only")

    assert caught.value.status_code == 422
    assert caught.value.code == corpus._HR_ONLY_REQUIRES_HR_MANAGER
    assert db.writes() == []


async def test_an_unknown_audience_is_refused_before_anything_is_written() -> None:
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [doc_row()])])

    with pytest.raises(corpus.CorpusError) as caught:
        await edit(db, audience="everyone")

    assert caught.value.code == "invalid_audience"
    assert db.writes() == []


async def test_an_absent_expiry_clears_the_one_on_the_row_rather_than_leaving_it() -> None:
    """The documented PUT-shaped contract: both fields are the FULL new state,
    so ``expires_on=None`` means "no expiry". Binding the old value instead
    would make an expiry impossible to remove, and expiry is what takes a
    stale policy out of retrieval."""
    db = FakeSession(
        routes=[("SELECT * FROM corpus_documents", [doc_row(expires_on=date(2026, 1, 1))])]
    )

    await edit(db, expires_on=None)

    params = db.params_for("UPDATE corpus_documents SET audience = :a")
    assert params["e"] is None
    assert params["i"] == DOC
    assert params["c"] == COMPANY


async def test_a_new_expiry_is_bound_as_a_date() -> None:
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [doc_row()])])

    await edit(db, expires_on=date(2027, 3, 31))

    assert db.params_for("UPDATE corpus_documents SET audience = :a")["e"] == date(2027, 3, 31)


async def test_narrowing_the_audience_writes_an_event_and_an_audit_row() -> None:
    db = FakeSession(
        routes=[("SELECT * FROM corpus_documents", [doc_row(audience="all_staff")])]
    )

    await edit(db, audience="hr_only")

    event = db.params_for("INSERT INTO corpus_events")
    assert event["a"] == "audience_changed"
    assert json.loads(event["j"]) == {"from": "all_staff", "to": "hr_only"}
    assert db.added[0].action == "corpus.document.edited"
    assert db.added[0].details["audience"] == "hr_only"


async def test_an_edit_that_changes_only_the_expiry_writes_no_audience_event() -> None:
    """The event log is read as a history of who could see what; an
    ``audience_changed`` row for an unchanged audience would be a false entry
    in it."""
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [doc_row(audience="all_staff")])])

    await edit(db, audience="all_staff", expires_on=date(2028, 1, 1))

    assert not db.has("INSERT INTO corpus_events")
    assert db.added == []
    assert db.has("UPDATE corpus_documents SET audience = :a")


# ---------------------------------------------------------------------------
# delete_document — immediate and complete
# ---------------------------------------------------------------------------
V1 = uuid.uuid4()
V2 = uuid.uuid4()


def delete_session(**over: Any) -> FakeSession:
    return FakeSession(
        routes=[
            ("SELECT * FROM corpus_documents", [doc_row(**over)]),
            (
                "SELECT id, storage_key, version FROM corpus_document_versions",
                [
                    {"id": V1, "storage_key": f"corpus/{COMPANY}/{DOC}/{V1}", "version": 1},
                    {"id": V2, "storage_key": f"corpus/{COMPANY}/{DOC}/{V2}", "version": 2},
                ],
            ),
        ],
    )


async def delete(db: FakeSession, **over: Any) -> None:
    kwargs: dict[str, Any] = {
        "company_id": COMPANY, "actor": ACTOR, "actor_role": "hr_manager",
        "document_id": DOC, "meta": META,
    }
    kwargs.update(over)
    await corpus.delete_document(db, **kwargs)  # type: ignore[arg-type]


async def test_delete_removes_the_objects_before_it_redacts_the_rows_naming_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``app/retention.py`` ordering. Objects first means a crash in the
    middle leaves a row whose file is already gone — visible as a 410 and
    re-purgeable on the next run. Rows first would leave a file that no row
    names, which nothing can ever find again to delete: under DPDP erasure that
    is the failure that matters."""
    db = delete_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    await delete(db)

    assert db.step("store.remove:") < db.step("DELETE FROM corpus_chunks")
    assert db.step("store.remove:") < db.step("SET storage_key = NULL")
    assert spy.removed_keys == [[f"corpus/{COMPANY}/{DOC}/{V1}", f"corpus/{COMPANY}/{DOC}/{V2}"]]


async def test_delete_purges_every_version_not_only_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Leave active retrieval" is not enough for a delete — an old version's
    chunks and file are the same document, and its chunks are still rows with
    embeddings."""
    db = delete_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await delete(db)

    chunk_deletes = [p for _, p in db.matching("DELETE FROM corpus_chunks")]
    assert {p["v"] for p in chunk_deletes} == {V1, V2}
    assert {p["c"] for p in chunk_deletes} == {COMPANY}, "the chunk delete is tenant-scoped"
    redactions = [p for _, p in db.matching("SET storage_key = NULL")]
    assert {p["i"] for p in redactions} == {V1, V2}


async def test_delete_nulls_the_storage_key_rather_than_leaving_it_dangling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The version row survives (the 180-day citation-resolution window needs
    it), so what is left must not claim a file that is gone: ``storage_key`` and
    ``original_name`` are blanked and ``redacted_at`` stamped, which is exactly
    what ``download_url`` turns into its 410."""
    db = delete_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await delete(db)

    sql, _ = db.matching("SET storage_key = NULL")[0]
    assert "original_name = NULL" in sql
    assert "redacted_at = now()" in sql
    assert "DELETE FROM corpus_document_versions" not in " ".join(db.writes())


async def test_a_short_object_removal_aborts_the_delete_without_redacting_a_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the ordering guarantee, and the only part of it a
    fake session can prove outright: if the store reports fewer objects removed
    than asked for, the function raises INSTEAD of blanking the keys — so the
    one surviving pointer to the file that is still there is not thrown away.
    The router's rollback then leaves the document exactly as it was."""
    db = delete_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db, removed=1))

    with pytest.raises(RuntimeError, match="refusing to redact"):
        await delete(db)

    assert spy.removed_keys, "it did try"
    assert db.writes() == []
    assert db.added == []


async def test_a_caller_who_cannot_read_a_document_cannot_delete_it_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH-2 again, and the leak it closes: a 204 where a 404 belongs tells a
    super_admin that an hr_only document exists. No object is touched."""
    db = delete_session(audience="hr_only")
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await delete(db, actor_role="super_admin")

    assert caught.value.status_code == 404
    assert spy.removed_keys == []
    assert db.writes() == []


async def test_delete_soft_deletes_the_document_after_its_content_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = delete_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await delete(db)

    assert db.step("SET storage_key = NULL") < db.step("UPDATE corpus_documents SET deleted_at")
    assert [p["a"] for _, p in db.matching("INSERT INTO corpus_events")] == ["deleted", "deleted"]
    assert db.added[0].action == "corpus.document.deleted"


def test_the_only_place_a_corpus_embedding_lives_is_the_chunk_row() -> None:
    """Why deleting the chunk rows is enough to delete the embeddings — the
    claim ``delete_document``'s purge rests on. Checked structurally rather
    than asserted in prose, so a future pass that caches vectors in a table of
    its own fails here instead of quietly surviving a delete."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    hits = 0
    for name in ("corpus.py", "reconciliation.py"):
        body = (root / name).read_text(encoding="utf-8")
        for match in re.finditer(r"(?:UPDATE|INSERT INTO)\s+(\w+)[^;\"']*embedding", body):
            hits += 1
            assert match.group(1) == "corpus_chunks", f"{name} writes an embedding to {match.group(1)}"
    assert hits >= 2, "the embedding writes moved — re-verify what a delete has to purge"


# ---------------------------------------------------------------------------
# reindex_document — the retry for a version parked at 'failed'
# ---------------------------------------------------------------------------
VER = uuid.uuid4()


def reindex_session(*, status: str = "failed", current: uuid.UUID | None = VER) -> FakeSession:
    return FakeSession(
        routes=[
            ("SELECT * FROM corpus_documents", [doc_row(current_version_id=current)]),
            (
                "SELECT id, version, status FROM corpus_document_versions",
                [{"id": VER, "version": 3, "status": status}],
            ),
            (
                "SELECT * FROM corpus_document_versions WHERE id = :i",
                [version_row(status="parsed", version=3)],
            ),
        ],
    )


async def reindex(db: FakeSession, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "company_id": COMPANY, "actor": ACTOR, "actor_role": "hr_manager",
        "document_id": DOC, "meta": META,
    }
    kwargs.update(over)
    return await corpus.reindex_document(db, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["parsed", "indexing", "indexed"])
async def test_reindex_refuses_a_version_that_is_not_actually_stuck(status: str) -> None:
    """409, not a silent success. Resetting an ``indexed`` version to
    ``parsed`` would take a working document OUT of search
    (``search_corpus`` filters on ``status = 'indexed'``) for as long as the
    reconciler takes to come round — a button that breaks what it claims to
    fix."""
    db = reindex_session(status=status)

    with pytest.raises(corpus.CorpusError) as caught:
        await reindex(db)

    assert caught.value.status_code == 409
    assert caught.value.code == "not_failed"
    assert db.writes() == []


async def test_reindex_refuses_a_document_that_has_no_current_version() -> None:
    db = reindex_session(current=None)

    with pytest.raises(corpus.CorpusError) as caught:
        await reindex(db)

    assert caught.value.code == "not_failed"
    assert not db.has("SELECT id, version, status FROM corpus_document_versions")


async def test_reindex_checks_the_audience_before_it_looks_at_any_version() -> None:
    db = FakeSession(routes=[("SELECT * FROM corpus_documents", [doc_row(audience="hr_only")])])

    with pytest.raises(corpus.CorpusError) as caught:
        await reindex(db, actor_role="super_admin")

    assert caught.value.status_code == 404
    assert len(db.statements) == 1


async def test_reindex_resets_the_version_and_clears_its_failure_code() -> None:
    db = reindex_session()

    out = await reindex(db)

    sql, params = db.one_statement("SET status = 'parsed'")
    assert "failure_code = NULL" in sql, (
        "leaving the old failure_code would keep the 'indexing unavailable' sentence on screen"
    )
    assert params == {"v": VER}
    assert out["status"] == "parsed"


async def test_reindex_deletes_the_parking_record_that_would_skip_the_retry() -> None:
    """The reason the button exists at all. ``mark_version_failed`` leaves a
    ``reconciliation_state`` row with ``gave_up_at`` set, and the embed pass's
    own predicate excludes anything parked — so resetting the STATUS alone
    produces a document that says it is being retried and never is."""
    db = reindex_session()

    await reindex(db)

    sql, params = db.one_statement("DELETE FROM reconciliation_state")
    assert "kind = :kind AND ref_id = :ref" in sql
    assert params == {"kind": "corpus_chunk", "ref": VER}


def test_the_parked_kind_reindex_clears_is_the_one_the_reconciler_writes() -> None:
    """The two modules deliberately do not import each other, so the literal is
    duplicated; a mismatch would make the DELETE above a no-op against a row
    that is still there, which looks exactly like the bug it fixes."""
    from app.reconciliation import KIND_CORPUS_CHUNK

    assert corpus._RECONCILIATION_KIND_CORPUS_CHUNK == KIND_CORPUS_CHUNK


async def test_reindex_touches_only_the_documents_current_version() -> None:
    """A superseded version that failed years ago is not this button's
    business, and un-failing one would put an old generation back in front of
    the reconciler."""
    db = reindex_session()

    await reindex(db)

    sql, params = db.one_statement("SELECT id, version, status FROM corpus_document_versions")
    assert params == {"v": VER}, "the version comes from the document's current_version_id"
    assert "document_id" not in sql, (
        "selecting by document_id would sweep up every failed version it ever had"
    )
    assert db.params_for("SET status = 'parsed'")["v"] == VER


async def test_reindex_leaves_an_event_and_an_audit_row_naming_the_version() -> None:
    db = reindex_session()

    await reindex(db)

    assert db.params_for("INSERT INTO corpus_events")["a"] == "reindex_requested"
    assert db.added[0].action == "corpus.document.reindex_requested"
    assert db.added[0].details["version"] == 3


# ---------------------------------------------------------------------------
# purge_corpus — nightly retention
# ---------------------------------------------------------------------------
EXPIRED = uuid.uuid4()
OLD_VERSION = uuid.uuid4()


def purge_session(*, expired: bool = True) -> FakeSession:
    return FakeSession(
        routes=[
            ("SELECT id FROM corpus_documents", [(EXPIRED,)] if expired else []),
            (
                "SELECT id, company_id, document_id, version, storage_key",
                [
                    {
                        "id": OLD_VERSION, "company_id": COMPANY, "document_id": DOC,
                        "version": 1, "storage_key": f"corpus/{COMPANY}/{DOC}/{OLD_VERSION}",
                    }
                ],
            ),
            ("SELECT company_id FROM corpus_documents", [{"company_id": COMPANY}]),
            (
                "SELECT id, storage_key, version FROM corpus_document_versions",
                [{"id": V1, "storage_key": f"corpus/{COMPANY}/{EXPIRED}/{V1}", "version": 2}],
            ),
        ],
    )


async def test_a_dry_run_counts_the_candidates_and_writes_absolutely_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RETENTION_DRY_RUN`` is how this purge is verified against real data
    before it is trusted with it, so "dry" has to mean no statement and no
    object call — not "writes less"."""
    db = purge_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    total = await corpus.purge_corpus(db, superseded_days=180, dry_run=True)

    assert total == 2
    assert db.writes() == []
    assert spy.removed_keys == []
    assert len(db.statements) == 2


async def test_the_purge_removes_a_superseded_versions_object_before_redacting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = purge_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await corpus.purge_corpus(db, superseded_days=180, dry_run=False)

    redaction = [i for i, e in enumerate(db.timeline) if "SET storage_key = NULL" in e]
    assert db.step(f"store.remove:corpus/{COMPANY}/{DOC}/{OLD_VERSION}") < redaction[-1]
    # The superseded branch deletes chunks by version id alone; the
    # document-scoped purge above also binds the company. Both appear here, so
    # the assertion names the exact parameter set rather than the phrase.
    assert {"v": OLD_VERSION} in [p for _, p in db.matching("DELETE FROM corpus_chunks")]


async def test_a_superseded_version_whose_object_will_not_delete_is_left_for_next_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same refusal as the document purge, one row at a time: raise rather than
    blank the key, because the key is the only way back to the file. No expired
    document in this fixture, so the superseded branch is what raises."""
    db = purge_session(expired=False)
    install_store(monkeypatch, corpus, StoreSpy(db, removed=0))

    with pytest.raises(RuntimeError, match="leaving it for the next run"):
        await corpus.purge_corpus(db, superseded_days=180, dry_run=False)

    assert not db.has("SET storage_key = NULL"), "the row still names the file that is still there"


async def test_the_purge_soft_deletes_an_expired_document_after_purging_its_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expiry is a retrieval rule until the nightly pass runs; when it does,
    the content goes the same way a delete sends it, under a ``system`` actor
    and an ``expired`` event rather than ``deleted``."""
    db = purge_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    total = await corpus.purge_corpus(db, superseded_days=180, dry_run=False)

    assert total == 2
    assert db.step("SET storage_key = NULL") < db.step("UPDATE corpus_documents SET deleted_at")
    actions = [p["a"] for _, p in db.matching("INSERT INTO corpus_events")]
    assert actions == ["expired", "deleted"]
    assert {p["t"] for _, p in db.matching("INSERT INTO corpus_events")} == {"system"}
    assert db.added == [], "a nightly system pass writes no actor audit row"


async def test_the_purge_asks_for_expiry_by_date_and_supersession_by_the_configured_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window is the whole control: a purge that ignored
    ``superseded_days`` would destroy the version a 6-month-old citation
    resolves against."""
    db = purge_session()
    install_store(monkeypatch, corpus, StoreSpy(db))
    before = datetime.now(tz=UTC)

    await corpus.purge_corpus(db, superseded_days=180, dry_run=False)

    expiry_sql, _ = db.one_statement("SELECT id FROM corpus_documents")
    assert "expires_on < CURRENT_DATE" in expiry_sql
    assert "deleted_at IS NULL" in expiry_sql
    sup_sql, sup_params = db.one_statement("SELECT id, company_id, document_id, version, storage_key")
    assert "redacted_at IS NULL" in sup_sql
    cutoff = sup_params["cutoff"]
    assert abs((before - cutoff) - timedelta(days=180)) < timedelta(seconds=30)


async def test_the_purge_skips_an_expired_document_that_vanished_under_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two selects and the work are not one statement, so a document
    deleted by HR in between is normal, not an error — it must not take the
    whole nightly pass down with it."""
    db = purge_session()
    db.routes = [r for r in db.routes if not r[0].startswith("SELECT company_id")]
    db.routes.append(("SELECT company_id FROM corpus_documents", []))
    install_store(monkeypatch, corpus, StoreSpy(db))

    total = await corpus.purge_corpus(db, superseded_days=180, dry_run=False)

    assert total == 2
    assert not db.has("UPDATE corpus_documents SET deleted_at")


# ---------------------------------------------------------------------------
# list_documents / get_document — the audience predicate as a bound parameter
# ---------------------------------------------------------------------------
def list_session(rows: list[dict[str, Any]] | None = None) -> FakeSession:
    listed = rows if rows is not None else [{**doc_row(), **version_row(), "uploaded_by_name": "Asha"}]
    return FakeSession(routes=[("FROM corpus_documents d", listed)])


@pytest.mark.parametrize(
    ("role", "audiences"),
    [("hr_manager", ["all_staff", "hr_only"]), ("super_admin", ["all_staff"])],
)
async def test_the_list_query_binds_only_the_audiences_the_caller_may_read(
    role: str, audiences: list[str]
) -> None:
    """The control is IN the statement, as a parameter — so a row this caller
    may not see is never fetched and cannot be counted, logged or cited. The
    assertion is on the bound value, not on the rows the fake handed back."""
    db = list_session()

    await corpus.list_documents(db, company_id=COMPANY, role=role)  # type: ignore[arg-type]

    sql, params = db.one_statement("FROM corpus_documents d")
    assert "d.audience = ANY(CAST(:aud AS text[]))" in sql
    assert params["aud"] == audiences
    assert params["c"] == COMPANY
    assert str(COMPANY) not in sql


async def test_a_role_with_no_corpus_audience_lists_nothing_without_a_query() -> None:
    """Defence in depth in front of the WHERE clause, and the reason it is
    cheap: a platform_owner or a candidate never reaches the database at all."""
    for role in ("platform_owner", "admin", "candidate", "interviewer"):
        db = list_session()
        assert await corpus.list_documents(db, company_id=COMPANY, role=role) == []  # type: ignore[arg-type]
        assert db.statements == []


async def test_the_list_shows_who_uploaded_each_document() -> None:
    db = list_session()

    out = await corpus.list_documents(db, company_id=COMPANY, role="hr_manager")  # type: ignore[arg-type]

    sql, _ = db.one_statement("FROM corpus_documents d")
    assert "LEFT JOIN users u" in sql
    assert "u.company_id = d.company_id" in sql, "the uploader join must not cross a tenant"
    assert out[0]["uploaded_by_name"] == "Asha"
    assert out[0]["version"] == 1


async def test_get_document_hides_a_document_the_caller_may_not_read() -> None:
    """Fetched then filtered here (one row, by primary key, already scoped to
    the company) — and the None is what the router turns into the same 404 a
    missing document gets."""
    db = FakeSession(
        routes=[("FROM corpus_documents d", [{**doc_row(audience="hr_only"), **version_row()}])]
    )

    assert await corpus.get_document(  # type: ignore[arg-type]
        db, company_id=COMPANY, role="super_admin", document_id=DOC
    ) is None
    assert await corpus.get_document(  # type: ignore[arg-type]
        db, company_id=COMPANY, role="hr_manager", document_id=DOC
    ) is not None


async def test_get_document_scopes_by_company_and_excludes_a_deleted_row() -> None:
    db = FakeSession(routes=[("FROM corpus_documents d", [])])

    assert await corpus.get_document(  # type: ignore[arg-type]
        db, company_id=COMPANY, role="hr_manager", document_id=DOC
    ) is None
    sql, params = db.one_statement("FROM corpus_documents d")
    assert "d.company_id = :c" in sql
    assert "d.deleted_at IS NULL" in sql
    assert params == {"i": DOC, "c": COMPANY}


# ---------------------------------------------------------------------------
# download_url — the only path to a document's CONTENT
# ---------------------------------------------------------------------------
DOWNLOAD_PHRASE = "SELECT d.audience, v.storage_key, v.original_name, v.version"


def download_session(**over: Any) -> FakeSession:
    row = {
        "audience": "all_staff", "storage_key": f"corpus/{COMPANY}/{DOC}/{V1}",
        "original_name": "handbook.txt", "version": 2,
    }
    row.update(over)
    return FakeSession(routes=[(DOWNLOAD_PHRASE, [row])])


async def download(db: FakeSession, **over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "company_id": COMPANY, "actor": ACTOR, "role": "hr_manager", "document_id": DOC,
        "meta": META,
    }
    kwargs.update(over)
    return await corpus.download_url(db, **kwargs)  # type: ignore[arg-type]


async def test_a_download_writes_the_audit_row_that_records_who_saw_the_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MEDIUM-4. Reading the metadata rows is not auditable and does not need to
    be; this is the call that hands somebody the document itself, so it leaves
    the same trail every other content download in this service leaves."""
    db = download_session()
    install_store(monkeypatch, corpus, StoreSpy(db))

    await download(db)

    assert len(db.added) == 1
    assert db.added[0].action == "corpus.document.downloaded"
    assert db.added[0].resource_id == DOC
    assert db.added[0].actor_id == ACTOR
    assert db.added[0].details == {"company_id": str(COMPANY), "version": 2}
    assert "handbook.txt" not in json.dumps(db.added[0].details)


async def test_a_download_returns_a_signed_link_for_the_key_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = download_session()
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    out = await download(db)

    assert spy.signed == [(f"corpus/{COMPANY}/{DOC}/{V1}", "handbook.txt")]
    assert out == {"url": spy.url, "expires_in": corpus.store.PRESIGN_SECONDS}


async def test_a_download_of_a_version_with_no_original_name_still_names_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = download_session(original_name=None)
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    await download(db)

    assert spy.signed[0][1] == "document"


async def test_asking_for_a_specific_version_binds_it_instead_of_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two hardcoded join predicates, chosen by whether a version was asked
    for — never composed from request input (the reason the ``nosec B608`` on
    that line is defensible). Both shapes are pinned here."""
    db = download_session()
    install_store(monkeypatch, corpus, StoreSpy(db))
    await download(db, version=2)
    sql, params = db.one_statement(DOWNLOAD_PHRASE)
    assert "v.version = :ver" in sql
    assert params["ver"] == 2

    db2 = download_session()
    install_store(monkeypatch, corpus, StoreSpy(db2))
    await download(db2)
    sql2, params2 = db2.one_statement(DOWNLOAD_PHRASE)
    assert "v.id = d.current_version_id" in sql2
    assert params2["ver"] is None
    assert "2" not in sql2.replace("B608", "")


async def test_a_caller_who_may_not_read_the_document_gets_no_link_and_no_audit_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = download_session(audience="hr_only")
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await download(db, role="super_admin")

    assert caught.value.status_code == 404
    assert spy.signed == []
    assert db.added == []


async def test_a_version_purged_under_retention_is_a_410_not_a_broken_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The visible consequence of the purge NULLing ``storage_key``: the row is
    still there to resolve an old citation, and the download says plainly that
    the file is gone instead of signing a URL for nothing."""
    db = download_session(storage_key=None)
    spy = install_store(monkeypatch, corpus, StoreSpy(db))

    with pytest.raises(corpus.CorpusError) as caught:
        await download(db)

    assert caught.value.status_code == 410
    assert caught.value.code == "gone"
    assert "retention" in caught.value.message
    assert spy.signed == []
    assert db.added == []


async def test_a_download_of_a_document_in_another_company_is_a_404() -> None:
    db = FakeSession(routes=[(DOWNLOAD_PHRASE, [])])

    with pytest.raises(corpus.CorpusError) as caught:
        await download(db)

    assert caught.value.status_code == 404
    sql, params = db.one_statement(DOWNLOAD_PHRASE)
    assert "d.company_id = :c" in sql
    assert "d.deleted_at IS NULL" in sql
    assert params["c"] == COMPANY


# ---------------------------------------------------------------------------
# PDF extraction — the format the design's `no_text` and `encrypted` refusals
# are about, and the one that carries page numbers into a citation
# ---------------------------------------------------------------------------
def blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def text_pdf(*pages: str) -> bytes:
    """A real PDF with a real text layer, one short line per page."""
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for line in pages:
        page = writer.add_blank_page(width=400, height=400)
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf 20 200 Td (" + line.encode("ascii") + b") Tj ET")
        # pypdf types this parameter as ContentStream|EncodedStreamObject, but a
        # DecodedStreamObject is what it writes out; no encoding filter is wanted here.
        page.replace_contents(stream)  # type: ignore[arg-type]
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


async def test_a_password_protected_pdf_is_refused_as_encrypted_not_as_unreadable() -> None:
    """A distinct code because the fix is distinct and the person can do it:
    ``encrypted`` tells them to remove the password, ``parse_error`` tells them
    nothing."""
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.encrypt("hunter2")
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(corpus.CorpusError) as caught:
        await corpus.extract_text(buf.getvalue(), "application/pdf")

    assert caught.value.code == "encrypted"
    assert "password" in caught.value.message


async def test_a_pdf_that_is_not_really_a_pdf_is_a_parse_error() -> None:
    with pytest.raises(corpus.CorpusError) as caught:
        await corpus.extract_text(b"%PDF-1.4\nnot really a pdf at all\n", "application/pdf")

    assert caught.value.code == "parse_error"


async def test_pdf_page_offsets_line_up_with_the_joined_text() -> None:
    """These offsets are the only thing behind a citation's page number: each
    one is where that page's text starts in the joined string, and the join
    inserts a newline. An off-by-one here cites the wrong page of a policy,
    which is worse than citing none."""
    data = text_pdf("Leave is accrued monthly", "Notice is thirty days")

    doc = await corpus.extract_text(data, "application/pdf")

    assert doc.page_count == 2
    assert doc.page_offsets[0] == 0
    first, second = doc.text.split("\n")
    assert doc.page_offsets[1] == len(first) + 1
    assert corpus._page_for_offset(doc.page_offsets, 0) == 1
    assert corpus._page_for_offset(doc.page_offsets, doc.page_offsets[1]) == 2
    assert doc.text[doc.page_offsets[1]:] == second


async def test_an_unsupported_content_type_never_reaches_a_parser() -> None:
    with pytest.raises(corpus.CorpusError) as caught:
        await corpus.extract_text(b"\x89PNG\r\n\x1a\n", "image/png")

    assert caught.value.code == "unsupported_type"


async def test_a_document_that_will_not_parse_in_time_is_refused_not_waited_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parse runs in a worker thread with a wall-clock ceiling, so one
    pathological file cannot hold a thread (and the uploader's request) open
    indefinitely. ``parse_timeout`` is in the closed failure vocabulary for
    exactly this."""
    import time

    def slow(_data: bytes) -> corpus.ExtractedDocument:
        # Never returns: the timeout below fires first, which is the point.
        time.sleep(5)
        return corpus.ExtractedDocument(text="never", page_count=None)

    monkeypatch.setattr(corpus, "_extract_text_sync", slow)
    monkeypatch.setattr(corpus, "_PARSE_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(corpus.CorpusError) as caught:
        await corpus.extract_text(b"anything", "text/plain")

    assert caught.value.code == "parse_timeout"


# ---------------------------------------------------------------------------
# Chunker edges the DB-facing paths depend on
# ---------------------------------------------------------------------------
def test_the_chunk_cap_cannot_be_evaded_by_the_final_partial_chunk() -> None:
    """The cap is enforced on the tail flush as well as mid-stream. Without
    that, a document could always land one chunk over the limit it was refused
    for, and ``too_many_chunks`` is a quota control, not a hint."""
    doc = corpus.ExtractedDocument(text="\n\n".join(["word " * 300] * 4), page_count=None)
    natural = len(corpus.chunk_document(doc, max_chunks=99))
    assert natural >= 3

    # One under what this document actually needs: every mid-stream flush fits,
    # so the only chunk left to refuse is the final partial one.
    with pytest.raises(corpus.CorpusError) as caught:
        corpus.chunk_document(doc, max_chunks=natural - 1)
    assert caught.value.code == "too_many_chunks"


def test_an_overlap_request_larger_than_the_chunk_returns_the_whole_chunk() -> None:
    """``_tail_overlap_parts``' two early exits, which the offset bookkeeping
    depends on: no fragment is invented and no offset is shifted, so the next
    chunk's page attribution still comes from the overlap's true origin."""
    parts = [(40, "first"), (80, "second")]

    assert corpus._tail_overlap_parts(parts, 0) == []
    assert corpus._tail_overlap_parts([], 150) == []
    assert corpus._tail_overlap_parts(parts, 150) == parts


# ---------------------------------------------------------------------------
# DOCX refusals the extractor tests next door do not reach
# ---------------------------------------------------------------------------
# ``_make_docx`` is imported below rather than copied: a second builder would
# drift from the one test_ph5_e2_corpus_ingest.py's DOCX cases are written
# against, and then "a valid DOCX" would mean two different things in one suite.
async def test_a_file_that_claims_to_be_a_docx_but_is_not_a_zip_is_a_parse_error() -> None:
    """Reachable in practice: ``sniff_corpus`` keys DOCX on the ZIP magic bytes,
    which a truncated upload still has."""
    with pytest.raises(corpus.CorpusError) as caught:
        await corpus.extract_text(b"PK\x03\x04 truncated upload", corpus.CORPUS_CONTENT_TYPES[1])

    assert caught.value.code == "parse_error"


async def test_a_zip_with_no_word_document_is_a_parse_error_not_an_empty_document() -> None:
    """An empty document would be ingested as zero chunks and then sit in the
    library as a file that matches nothing and cannot be told from one that
    simply has not been indexed yet."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/settings.xml", "<settings/>")

    with pytest.raises(corpus.CorpusError) as caught:
        await corpus.extract_text(buf.getvalue(), corpus.CORPUS_CONTENT_TYPES[1])

    assert caught.value.code == "parse_error"


async def test_an_empty_word_paragraph_is_skipped_rather_than_kept_as_a_blank_line() -> None:
    """Word documents are full of empty paragraphs used as spacing, and a blank
    line is exactly what the chunker treats as a block boundary — so carrying
    them through would chunk a spaced-out policy into sentence-long fragments."""
    from tests.unit.test_ph5_e2_corpus_ingest import _make_docx

    data = _make_docx([("Leave policy", "Heading1"), ("", None), ("Twenty days a year.", None)])

    doc = await corpus.extract_text(data, corpus.CORPUS_CONTENT_TYPES[1])

    assert doc.text == "# Leave policy\n\nTwenty days a year."


# ---------------------------------------------------------------------------
# A document that is not there
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda db: replace(db),
        lambda db: edit(db),
        lambda db: delete(db),
        lambda db: reindex(db),
        lambda db: download(db),
    ],
    ids=["add_version", "update_document", "delete_document", "reindex_document", "download_url"],
)
async def test_every_function_that_takes_an_existing_document_404s_when_it_is_gone(
    call: Any,
) -> None:
    """One contract across all five, because all five are reachable from a
    library screen a colleague deleted the document from a second earlier: the
    answer is a 404 a person can read, never a 500 from subscripting None. The
    lookup is scoped by company, so "another company's document" takes the same
    path — which is what stops the 404/200 split being a cross-tenant probe."""
    db = FakeSession(
        routes=[
            ("SELECT * FROM corpus_documents", []),
            ("SELECT d.audience, v.storage_key", []),
        ]
    )

    with pytest.raises(corpus.CorpusError) as caught:
        await call(db)

    assert caught.value.status_code == 404
    assert caught.value.code == "not_found"
    assert db.writes() == []
    assert db.added == []
