"""PH5-E2 DB-level tests — document corpus RAG against a real, migrated
Postgres with pgvector.

Every service function here is called directly (never through HTTP), on the
``test_ph5_w1_checkins.py`` / ``test_ph4_d4_wave5_fixes.py`` precedent: one
transaction per test, always rolled back, and object storage replaced by an
in-memory ``_FakeStore`` so nothing here needs real S3 credentials.

The one exception is the reconciler pipeline test at the bottom, which needs
``app/reconciliation.py::_corpus_embed_pass`` to commit incrementally (the
same reason the applicant embed pass does) — it uses its own truncate-based
setup instead of the shared rollback fixture.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.reconciliation as rec
from app import corpus as svc
from app.config import settings
from app.embedding_client import EmbeddingError
from app.interviewer_scorecards import RequestMeta

pytestmark = pytest.mark.integration

_META = RequestMeta(ip_address="127.0.0.1", user_agent="pytest")


class _FakeStore:
    """In-memory stand-in for object storage — the test_ph4_d4_wave5_fixes.py
    precedent. No network, no real S3 credentials needed."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def store(self, _settings: object, key: str, data: bytes, _content_type: str) -> None:
        self.objects[key] = data

    async def remove(self, _settings: object, keys: list[str]) -> int:
        removed = 0
        for k in keys:
            if self.objects.pop(k, None) is not None:
                removed += 1
        return removed

    async def signed_download(self, _settings: object, key: str, _filename: str) -> str:
        return f"https://fake.example/{key}"


class F:
    def __init__(self) -> None:
        self.company_a = uuid.uuid4()
        self.company_b = uuid.uuid4()
        self.hr_a = uuid.uuid4()
        self.super_a = uuid.uuid4()
        self.hr_b = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag_a, tag_b = f.company_a.hex[:10], f.company_b.hex[:10]
    p: dict[str, Any] = {
        "ca": f.company_a, "cb": f.company_b, "ha": f.hr_a, "sa": f.super_a, "hb": f.hr_b,
        "slug_a": f"e2-a-{tag_a}", "slug_b": f"e2-b-{tag_b}",
        "ea": f"hr-a-{tag_a}@e2.test", "esa": f"sup-a-{tag_a}@e2.test", "eb": f"hr-b-{tag_b}@e2.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:ca, 'E2 co A', :slug_a), (:cb, 'E2 co B', :slug_b)",
        "INSERT INTO users (id, email, company_id) VALUES"
        " (:ha, :ea, :ca), (:sa, :esa, :ca), (:hb, :eb, :cb)",
    ):
        await db.execute(text(sql), p)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(database_url=settings.database_url, database_ssl=settings.database_ssl,
                          pool_size=2)
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


def _patch_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    fake = _FakeStore()
    monkeypatch.setattr(svc.store, "store", fake.store)
    monkeypatch.setattr(svc.store, "remove", fake.remove)
    monkeypatch.setattr(svc.store, "signed_download", fake.signed_download)
    return fake


_POLICY_PDF = b"%PDF-1.4\n" + (
    b"Notice period: employees must give thirty days written notice before "
    b"resigning from their role at this company. " * 3
)
_HANDBOOK_TXT = "Leave policy: employees accrue twenty days of paid annual leave. " * 3


async def _index(db: AsyncSession, version_id: uuid.UUID) -> None:
    """Fast-forward a version straight to 'indexed' without running the
    reconciler — used by tests that exercise search_corpus, not ingestion."""
    await db.execute(
        text("UPDATE corpus_document_versions SET status = 'indexed', indexed_at = now() WHERE id = :v"),
        {"v": version_id},
    )


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_creates_document_version_chunks_events_and_audit(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Leave Policy",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="leave.txt", meta=_META,
    )
    # The AuditLog row is added via the ORM (db.add) and, unlike the raw-SQL
    # writes around it, is only visible to a later query in THIS transaction
    # once flushed — exactly what the caller's db.commit() does in production.
    await db.flush()
    assert out["version"] == 1
    assert out["status"] == "parsed"

    chunk_count = await db.scalar(
        text("SELECT count(*) FROM corpus_chunks WHERE document_id = :d"), {"d": uuid.UUID(out["id"])}
    )
    assert chunk_count and chunk_count > 0

    events = (
        await db.execute(
            text("SELECT action FROM corpus_events WHERE document_id = :d ORDER BY created_at"),
            {"d": uuid.UUID(out["id"])},
        )
    ).scalars().all()
    assert events == ["uploaded", "parsed"]

    audit_row = (
        await db.execute(
            text(
                "SELECT details FROM audit_log WHERE action = 'corpus.document.uploaded'"
                " AND resource_id = :d"
            ),
            {"d": uuid.UUID(out["id"])},
        )
    ).mappings().first()
    assert audit_row is not None
    assert audit_row["details"]["attested"] is True
    # Never the title, never a file name (design: facts only).
    assert "title" not in audit_row["details"]
    assert "filename" not in audit_row["details"] and "original_name" not in audit_row["details"]


@pytest.mark.asyncio
async def test_list_and_get_carry_the_uploaders_display_name(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Documents screen's 'Uploaded by' column — COALESCE(full_name,
    email), the house pattern (hire_checkins.py, accommodations.py, offers.py,
    preboarding.py), company-scoped like every other join, null once the
    uploader's account is gone."""
    _patch_store(monkeypatch)
    f = await _build(db)
    named_uploader = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, company_id) VALUES (:u, :e, 'Asha Verma', :c)"),
        {"u": named_uploader, "e": f"named-{named_uploader.hex[:10]}@e2.test", "c": f.company_a},
    )

    named_out = await svc.ingest_document(
        db, company_id=f.company_a, actor=named_uploader, actor_role="hr_manager", title="Named",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="named.txt", meta=_META,
    )
    # hr_a (from _build) has no full_name set — COALESCE falls back to email.
    email_only_out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="EmailOnly",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="email.txt", meta=_META,
    )

    listed = {d["id"]: d for d in await svc.list_documents(db, company_id=f.company_a, role="hr_manager")}
    assert listed[named_out["id"]]["uploaded_by_name"] == "Asha Verma"
    hr_a_row = (
        await db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": f.hr_a})
    ).mappings().one()
    assert listed[email_only_out["id"]]["uploaded_by_name"] == hr_a_row["email"]

    fetched = await svc.get_document(db, company_id=f.company_a, role="hr_manager",
                                     document_id=uuid.UUID(named_out["id"]))
    assert fetched is not None
    assert fetched["uploaded_by_name"] == "Asha Verma"

    # Null once the uploader is gone -- the FK is ON DELETE SET NULL, so this
    # simulates the state that leaves in, without needing a real user delete.
    await db.execute(
        text("UPDATE corpus_document_versions SET uploaded_by_user_id = NULL WHERE document_id = :d"),
        {"d": uuid.UUID(named_out["id"])},
    )
    orphaned = await svc.get_document(db, company_id=f.company_a, role="hr_manager",
                                      document_id=uuid.UUID(named_out["id"]))
    assert orphaned is not None
    assert orphaned["uploaded_by_name"] is None


@pytest.mark.asyncio
async def test_super_admin_cannot_create_hr_only_document(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    with pytest.raises(svc.CorpusError) as exc:
        await svc.ingest_document(
            db, company_id=f.company_a, actor=f.super_a, actor_role="super_admin", title="Secret",
            audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
            data=_HANDBOOK_TXT.encode(), filename="s.txt", meta=_META,
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "hr_only_requires_hr_manager"
    count = await db.scalar(text("SELECT count(*) FROM corpus_documents WHERE company_id = :c"),
                            {"c": f.company_a})
    assert count == 0  # no row created


@pytest.mark.asyncio
async def test_hr_manager_can_create_hr_only_document(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Comp bands",
        audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="comp.txt", meta=_META,
    )
    assert out["audience"] == "hr_only"


@pytest.mark.asyncio
async def test_attestation_required(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    with pytest.raises(svc.CorpusError) as exc:
        await svc.ingest_document(
            db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="X",
            audience="all_staff", doc_kind="policy", expires_on=None, attested=False,
            data=_HANDBOOK_TXT.encode(), filename="x.txt", meta=_META,
        )
    assert exc.value.code == "attestation_required"


@pytest.mark.asyncio
async def test_super_admin_never_reads_an_hr_only_document(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Comp bands",
        audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="comp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])

    listed = await svc.list_documents(db, company_id=f.company_a, role="super_admin")
    assert document_id not in {uuid.UUID(d["id"]) for d in listed}
    assert document_id in {
        uuid.UUID(d["id"])
        for d in await svc.list_documents(db, company_id=f.company_a, role="hr_manager")
    }

    assert await svc.get_document(db, company_id=f.company_a, role="super_admin",
                                  document_id=document_id) is None
    assert await svc.get_document(db, company_id=f.company_a, role="hr_manager",
                                  document_id=document_id) is not None

    with pytest.raises(svc.CorpusError) as exc:
        await svc.download_url(db, company_id=f.company_a, actor=f.super_a, role="super_admin",
                               document_id=document_id, meta=_META)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_super_admin_cannot_widen_an_hr_only_document_into_all_staff(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH-2 (security review): update_document validated only the
    REQUESTED audience, never whether the actor could read the CURRENT one —
    a super_admin PATCHing {"audience": "all_staff"} onto an hr_only document
    succeeded and could then read it, making AR-8's "a super_admin can never
    create or read back an hr_only document" false. 404, not 403, matching
    get_document's refusal shape (does not disclose that the document
    exists)."""
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Comp bands",
        audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="comp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])

    with pytest.raises(svc.CorpusError) as exc:
        await svc.update_document(
            db, company_id=f.company_a, actor=f.super_a, actor_role="super_admin",
            document_id=document_id, audience="all_staff", expires_on=None, meta=_META,
        )
    assert exc.value.status_code == 404

    # The document is untouched -- still hr_only, unreadable by super_admin.
    row = (
        await db.execute(text("SELECT audience FROM corpus_documents WHERE id = :i"), {"i": document_id})
    ).mappings().one()
    assert row["audience"] == "hr_only"
    assert await svc.get_document(db, company_id=f.company_a, role="super_admin",
                                  document_id=document_id) is None

    # A super_admin CAN still edit a document it can already read.
    visible = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Handbook",
        audience="all_staff", doc_kind="handbook", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="hb.txt", meta=_META,
    )
    edited = await svc.update_document(
        db, company_id=f.company_a, actor=f.super_a, actor_role="super_admin",
        document_id=uuid.UUID(visible["id"]), audience="all_staff",
        expires_on=None, meta=_META,
    )
    assert edited["audience"] == "all_staff"


@pytest.mark.asyncio
async def test_super_admin_cannot_delete_an_hr_only_document(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH-2 (security review): delete_document had NO read check at all —
    a super_admin could delete a document it could not see, which both
    destroys HR's document without their say and confirms (via the 204) that
    something was there to delete."""
    fake = _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Comp bands",
        audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="comp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])

    with pytest.raises(svc.CorpusError) as exc:
        await svc.delete_document(db, company_id=f.company_a, actor=f.super_a,
                                  actor_role="super_admin", document_id=document_id, meta=_META)
    assert exc.value.status_code == 404

    # Nothing was purged -- the object and the row both survive, undeleted.
    assert fake.objects
    row = (
        await db.execute(text("SELECT deleted_at FROM corpus_documents WHERE id = :i"), {"i": document_id})
    ).mappings().one()
    assert row["deleted_at"] is None

    # hr_manager, who CAN read it, can still delete it.
    await svc.delete_document(db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager",
                              document_id=document_id, meta=_META)
    row_after = (
        await db.execute(text("SELECT deleted_at FROM corpus_documents WHERE id = :i"), {"i": document_id})
    ).mappings().one()
    assert row_after["deleted_at"] is not None


@pytest.mark.asyncio
async def test_download_writes_an_audit_row(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """MEDIUM-4 (security review): a signed download link is what actually
    lets someone read the document's CONTENT — an hr_only document pulled
    repeatedly must leave a record, like every comparable download in this
    service."""
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Comp bands",
        audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="comp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])

    await svc.download_url(db, company_id=f.company_a, actor=f.hr_a, role="hr_manager",
                           document_id=document_id, meta=_META)
    await db.flush()  # AuditLog is ORM-added; a caller commits in production

    audit_row = (
        await db.execute(
            text(
                "SELECT actor_id, details FROM audit_log"
                " WHERE action = 'corpus.document.downloaded' AND resource_id = :d"
            ),
            {"d": document_id},
        )
    ).mappings().first()
    assert audit_row is not None
    assert audit_row["actor_id"] == f.hr_a
    assert audit_row["details"]["version"] == 1
    assert audit_row["details"]["company_id"] == str(f.company_a)


# ---------------------------------------------------------------------------
# Failure states
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_encrypted_pdf_and_scanned_pdf_are_distinguished(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)

    # A genuinely scanned PDF (no text layer) yields `no_text`, not `encrypted`.
    scanned = b"%PDF-1.4\n%no text layer, valid pdf structure enough for pypdf to open\n"
    with pytest.raises(svc.CorpusError) as exc:
        await svc.ingest_document(
            db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Scan",
            audience="all_staff", doc_kind="other", expires_on=None, attested=True,
            data=scanned, filename="scan.pdf", meta=_META,
        )
    assert exc.value.code in ("no_text", "parse_error")


@pytest.mark.asyncio
async def test_quota_exceeded_when_company_is_at_the_document_cap(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    monkeypatch.setattr(settings, "corpus_max_documents_per_company", 1)
    f = await _build(db)
    await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="One",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="one.txt", meta=_META,
    )
    with pytest.raises(svc.CorpusError) as exc:
        await svc.ingest_document(
            db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Two",
            audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
            data=_HANDBOOK_TXT.encode(), filename="two.txt", meta=_META,
        )
    assert exc.value.code == "quota_exceeded"


@pytest.mark.asyncio
async def test_chunk_quota_excludes_superseded_history(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code review CONSIDER: a company that fixes typos by re-uploading must
    not accumulate quota against superseded versions nobody can search — the
    chunk quota counts only the current, searchable generation of each
    document. Set the cap so ONE version's worth of chunks fits but THREE
    generations of the same document would not, then replace it twice and
    confirm a brand-new document still fits."""
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Handbook",
        audience="all_staff", doc_kind="handbook", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="v1.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])
    chunk_count = await db.scalar(
        text("SELECT chunk_count FROM corpus_document_versions WHERE document_id = :d AND version = 1"),
        {"d": document_id},
    )
    # Room for a little over one version's chunks, nowhere near three.
    monkeypatch.setattr(settings, "corpus_max_chunks_per_company", chunk_count + 2)

    # Two replaces — three versions on disk in total (v1, v2 superseded; v3
    # current) — would blow the cap if superseded versions counted.
    await svc.add_version(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", document_id=document_id,
        data=(_HANDBOOK_TXT + " v2").encode(), filename="v2.txt", meta=_META,
    )
    await svc.add_version(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", document_id=document_id,
        data=(_HANDBOOK_TXT + " v3").encode(), filename="v3.txt", meta=_META,
    )

    # And a brand-new, unrelated document still fits under the same cap.
    other = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Other",
        audience="all_staff", doc_kind="other", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="other.txt", meta=_META,
    )
    assert other["id"]


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replace_supersedes_v1_keeps_its_chunks_and_citation_still_resolves(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    v1 = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Handbook",
        audience="all_staff", doc_kind="handbook", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="v1.txt", meta=_META,
    )
    document_id = uuid.UUID(v1["id"])
    v2 = await svc.add_version(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", document_id=document_id,
        data=(_HANDBOOK_TXT + " Updated once.").encode(), filename="v2.txt", meta=_META,
    )
    assert v2["version"] == 2

    v1_chunks = await db.scalar(
        text(
            "SELECT count(*) FROM corpus_chunks c JOIN corpus_document_versions v ON v.id = c.version_id"
            " WHERE v.document_id = :d AND v.version = 1"
        ),
        {"d": document_id},
    )
    assert v1_chunks and v1_chunks > 0  # v1's chunks are KEPT, not deleted

    v1_superseded = (
        await db.execute(
            text(
                "SELECT superseded_at, redacted_at FROM corpus_document_versions"
                " WHERE document_id = :d AND version = 1"
            ),
            {"d": document_id},
        )
    ).mappings().one()
    assert v1_superseded["superseded_at"] is not None
    assert v1_superseded["redacted_at"] is None  # not yet redacted — only excluded from retrieval

    # A citation naming v1 still resolves to a signed link (the file exists).
    dl = await svc.download_url(db, company_id=f.company_a, actor=f.hr_a, role="hr_manager",
                                document_id=document_id, version=1, meta=_META)
    assert dl["url"]


@pytest.mark.asyncio
async def test_delete_purges_chunks_and_objects_immediately_row_survives(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Temp",
        audience="all_staff", doc_kind="other", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="temp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])
    assert fake.objects  # the object was actually stored

    await svc.delete_document(db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager",
                              document_id=document_id, meta=_META)

    assert not fake.objects  # purged
    remaining_chunks = await db.scalar(
        text("SELECT count(*) FROM corpus_chunks WHERE document_id = :d"), {"d": document_id}
    )
    assert remaining_chunks == 0
    row = (
        await db.execute(
            text("SELECT deleted_at FROM corpus_documents WHERE id = :d"), {"d": document_id}
        )
    ).mappings().one()
    assert row["deleted_at"] is not None  # row survives as the record it existed

    # Deleted documents disappear from retrieval and from the list.
    assert await svc.get_document(db, company_id=f.company_a, role="hr_manager",
                                  document_id=document_id) is None


# ---------------------------------------------------------------------------
# Retrieval — tenant isolation and the audience rule
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_isolation_company_b_gets_zero_rows_for_company_a_unique_phrase(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    unique_phrase = "zzqxphraseonlyincompanya unique marker text"
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="A-only",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=(unique_phrase + " " + _HANDBOOK_TXT).encode(), filename="a.txt", meta=_META,
    )
    version_id = (
        await db.execute(
            text("SELECT current_version_id FROM corpus_documents WHERE id = :d"),
            {"d": uuid.UUID(out["id"])},
        )
    ).scalar_one()
    await _index(db, version_id)

    same_company = await svc.search_corpus(db, company_id=f.company_a, role="hr_manager",
                                           query=unique_phrase)
    assert same_company["passages"]

    other_company = await svc.search_corpus(db, company_id=f.company_b, role="hr_manager",
                                            query=unique_phrase)
    assert other_company["passages"] == []
    assert all(p["document_id"] != out["id"] for p in other_company["passages"])


@pytest.mark.asyncio
async def test_audience_rule_super_admin_never_retrieves_hr_only_passage(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    phrase = "confidential compensation band structure zz9"
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Comp",
        audience="hr_only", doc_kind="policy", expires_on=None, attested=True,
        data=(phrase + " " + _HANDBOOK_TXT).encode(), filename="comp.txt", meta=_META,
    )
    version_id = (
        await db.execute(
            text("SELECT current_version_id FROM corpus_documents WHERE id = :d"),
            {"d": uuid.UUID(out["id"])},
        )
    ).scalar_one()
    await _index(db, version_id)

    as_hr = await svc.search_corpus(db, company_id=f.company_a, role="hr_manager", query=phrase)
    assert as_hr["passages"]

    as_super = await svc.search_corpus(db, company_id=f.company_a, role="super_admin", query=phrase)
    assert as_super["passages"] == []


@pytest.mark.asyncio
async def test_audience_predicate_is_a_bound_sql_parameter_not_a_python_filter(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert on the SQL PARAMETER, not only the result — so the audience
    filter cannot be moved to a post-filter in Python without this going red."""
    _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="X",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="x.txt", meta=_META,
    )
    version_id = (
        await db.execute(
            text("SELECT current_version_id FROM corpus_documents WHERE id = :d"),
            {"d": uuid.UUID(out["id"])},
        )
    ).scalar_one()
    await _index(db, version_id)

    captured: list[dict[str, Any]] = []
    real_execute = AsyncSession.execute

    async def spy_execute(self: AsyncSession, statement: Any, params: Any = None, *a: Any,
                          **kw: Any) -> Any:
        sql_text = str(getattr(statement, "text", statement))
        if "FROM corpus_chunks" in sql_text and isinstance(params, dict) and "is_hr" in params:
            captured.append(params)
        return await real_execute(self, statement, params, *a, **kw)

    monkeypatch.setattr(AsyncSession, "execute", spy_execute)
    try:
        await svc.search_corpus(db, company_id=f.company_a, role="hr_manager", query="leave")
        await svc.search_corpus(db, company_id=f.company_a, role="super_admin", query="leave")
    finally:
        monkeypatch.setattr(AsyncSession, "execute", real_execute)

    assert len(captured) == 2
    assert captured[0]["is_hr"] is True
    assert captured[1]["is_hr"] is False
    assert captured[0]["cid"] == f.company_a
    assert captured[1]["cid"] == f.company_a


@pytest.mark.asyncio
async def test_no_key_degradation_falls_back_to_full_text(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    phrase = "unique degrade mode phrase zzk1"
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="D",
        audience="all_staff", doc_kind="policy", expires_on=None, attested=True,
        data=(phrase + " " + _HANDBOOK_TXT).encode(), filename="d.txt", meta=_META,
    )
    version_id = (
        await db.execute(
            text("SELECT current_version_id FROM corpus_documents WHERE id = :d"),
            {"d": uuid.UUID(out["id"])},
        )
    ).scalar_one()
    await _index(db, version_id)

    async def _broken_embed(**_kw: Any) -> list[float]:
        raise EmbeddingError("embedder is down")

    monkeypatch.setattr(svc, "embed_one_remote", _broken_embed)
    result = await svc.search_corpus(db, company_id=f.company_a, role="hr_manager", query=phrase)
    assert result["semantic"] is False
    assert result["passages"]  # still found via full-text


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_purge_corpus_dry_run_counts_without_touching_anything(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Expiring",
        audience="all_staff", doc_kind="policy", expires_on=datetime.now(tz=UTC).date() - timedelta(days=1),
        attested=True, data=_HANDBOOK_TXT.encode(), filename="exp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])
    count = await svc.purge_corpus(db, superseded_days=180, dry_run=True)
    assert count >= 1
    assert fake.objects  # dry-run touches nothing
    row = (
        await db.execute(text("SELECT deleted_at FROM corpus_documents WHERE id = :d"), {"d": document_id})
    ).mappings().one()
    assert row["deleted_at"] is None


@pytest.mark.asyncio
async def test_purge_corpus_live_expires_a_document_past_its_date(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_store(monkeypatch)
    f = await _build(db)
    out = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Expiring",
        audience="all_staff", doc_kind="policy", expires_on=datetime.now(tz=UTC).date() - timedelta(days=1),
        attested=True, data=_HANDBOOK_TXT.encode(), filename="exp.txt", meta=_META,
    )
    document_id = uuid.UUID(out["id"])
    await svc.purge_corpus(db, superseded_days=180, dry_run=False)

    assert not fake.objects
    row = (
        await db.execute(text("SELECT deleted_at FROM corpus_documents WHERE id = :d"), {"d": document_id})
    ).mappings().one()
    assert row["deleted_at"] is not None
    remaining_chunks = await db.scalar(
        text("SELECT count(*) FROM corpus_chunks WHERE document_id = :d"), {"d": document_id}
    )
    assert remaining_chunks == 0


@pytest.mark.asyncio
async def test_purge_corpus_redacts_a_superseded_version_after_the_window(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    f = await _build(db)
    v1 = await svc.ingest_document(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", title="Handbook",
        audience="all_staff", doc_kind="handbook", expires_on=None, attested=True,
        data=_HANDBOOK_TXT.encode(), filename="v1.txt", meta=_META,
    )
    document_id = uuid.UUID(v1["id"])
    await svc.add_version(
        db, company_id=f.company_a, actor=f.hr_a, actor_role="hr_manager", document_id=document_id,
        data=(_HANDBOOK_TXT + " v2").encode(), filename="v2.txt", meta=_META,
    )
    # Back-date v1's supersession past the retention window.
    await db.execute(
        text(
            "UPDATE corpus_document_versions SET superseded_at = :old"
            " WHERE document_id = :d AND version = 1"
        ),
        {"old": datetime.now(tz=UTC) - timedelta(days=200), "d": document_id},
    )
    await svc.purge_corpus(db, superseded_days=180, dry_run=False)

    v1_row = (
        await db.execute(
            text(
                "SELECT redacted_at, storage_key FROM corpus_document_versions"
                " WHERE document_id = :d AND version = 1"
            ),
            {"d": document_id},
        )
    ).mappings().one()
    assert v1_row["redacted_at"] is not None
    assert v1_row["storage_key"] is None
    # The version ROW itself survives.
    still_there = await db.scalar(
        text("SELECT count(*) FROM corpus_document_versions WHERE document_id = :d AND version = 1"),
        {"d": document_id},
    )
    assert still_there == 1


# ---------------------------------------------------------------------------
# Reconciler pipeline — needs real commits, so it runs on its own connection
# and cleans up by truncation rather than rollback (the
# smoke_group_a_reconciliation.py precedent).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reconciler_indexes_a_document_and_reembeds_only_changed_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolated from AI_FAKE_MODE deliberately: that setting is process-wide
    and several unrelated production-env tests (test_config.py,
    test_security_fixes.py, test_e2e_test_switches.py, test_resume_search.py)
    assert it is OFF by default, so a suite run that needs it globally true
    for this one test is a suite nothing can actually run green. Patch the
    one call this pass makes instead — the ``embed_one_remote``/
    ``embed_texts_remote`` isolation shape ``test_resume_search.py`` already
    uses for the same client."""
    from app.fake_ai import fake_embeddings

    async def _fake_embed_texts_remote(*, texts: list[str], task_type: str,
                                       acting_user_id: str) -> list[list[float]]:
        return fake_embeddings(texts)

    monkeypatch.setattr(rec, "embed_texts_remote", _fake_embed_texts_remote)

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    company_id, hr_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Recon co', :s)"),
                {"c": company_id, "s": f"recon-{company_id.hex[:10]}"},
            )
            await db.execute(
                text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
                {"u": hr_id, "e": f"hr-{hr_id.hex[:10]}@recon.test", "c": company_id},
            )
            await db.commit()

        class _NullStore:
            async def store(self, *_a: Any, **_kw: Any) -> None:
                return None

        import app.corpus as corpus_module

        real_store = corpus_module.store.store
        corpus_module.store.store = _NullStore().store  # type: ignore[method-assign]
        try:
            async with factory() as db:
                out = await corpus_module.ingest_document(
                    db, company_id=company_id, actor=hr_id, actor_role="hr_manager",
                    title="Reconciled handbook", audience="all_staff", doc_kind="handbook",
                    expires_on=None, attested=True, data=_HANDBOOK_TXT.encode(),
                    filename="h.txt", meta=_META,
                )
                await db.commit()
            document_id = uuid.UUID(out["id"])

            # Pass 1: nothing has an embedding yet — the pass should index it.
            result1 = rec.PassResult()
            async with factory() as db:
                await rec._corpus_embed_pass(db, result1)
            assert result1.corpus_indexed == 1
            assert result1.corpus_embedded > 0

            async with factory() as db:
                status_after = await db.scalar(
                    text(
                        "SELECT status FROM corpus_document_versions"
                        " WHERE document_id = :d ORDER BY version DESC LIMIT 1"
                    ),
                    {"d": document_id},
                )
            assert status_after == "indexed"

            # Replace with one changed paragraph appended — most chunks are
            # byte-identical, so only the genuinely new content should embed.
            async with factory() as db:
                await corpus_module.add_version(
                    db, company_id=company_id, actor=hr_id, actor_role="hr_manager",
                    document_id=document_id,
                    data=(_HANDBOOK_TXT + "\n\nOne brand-new paragraph just added.").encode(),
                    filename="h2.txt", meta=_META,
                )
                await db.commit()

            async with factory() as db:
                unembedded_before = await db.scalar(
                    text(
                        "SELECT count(*) FROM corpus_chunks c"
                        " JOIN corpus_document_versions v ON v.id = c.version_id"
                        " WHERE v.document_id = :d AND v.version = 2 AND c.embedding IS NULL"
                    ),
                    {"d": document_id},
                )
            assert unembedded_before and unembedded_before > 0

            result2 = rec.PassResult()
            async with factory() as db:
                await rec._corpus_embed_pass(db, result2)

            # The carry-over UPDATE fills identical chunks for free; only the
            # new paragraph's chunk(s) should have gone through the embedder.
            assert 0 < result2.corpus_embedded <= 2
        finally:
            corpus_module.store.store = real_store  # type: ignore[method-assign]
    finally:
        async with factory() as db:
            await db.execute(text("DELETE FROM reconciliation_state WHERE kind = 'corpus_chunk'"))
            # corpus_chunks/corpus_document_versions/corpus_events all cascade
            # from corpus_documents (ON DELETE CASCADE) — deleting the child
            # tables directly hits corpus_events_append_only, which refuses a
            # DELETE while its parent document still exists.
            await db.execute(text("DELETE FROM corpus_documents WHERE company_id = :c"), {"c": company_id})
            await db.execute(text("DELETE FROM users WHERE company_id = :c"), {"c": company_id})
            await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": company_id})
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_replacing_a_document_before_it_indexes_does_not_wedge_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH-1 (security review): ingest v1, then replace it with v2 BEFORE the
    reconciler ever embeds v1 — v1 is superseded while its own status is
    still 'parsed'. Before the fix, the embed pass's SELECT picked v1's
    chunks up anyway (no ``v.superseded_at IS NULL``), ``mark_versions_indexing``
    then tried to move a superseded row from 'parsed' to 'indexing',
    ``corpus_versions_immutable`` refused it, and the exception propagated OUT
    of ``_corpus_embed_pass`` entirely — before any ``_record_failure``/parking
    ran — with those same superseded chunks at the head of the
    ``ORDER BY created_at LIMIT 32`` queue on every subsequent pass. No tenant
    scoping on that query means this wedges corpus indexing for every company,
    not only the one that triggered it.
    """
    from app.fake_ai import fake_embeddings

    async def _fake_embed_texts_remote(*, texts: list[str], task_type: str,
                                       acting_user_id: str) -> list[list[float]]:
        return fake_embeddings(texts)

    monkeypatch.setattr(rec, "embed_texts_remote", _fake_embed_texts_remote)

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    company_id, hr_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Wedge co', :s)"),
                {"c": company_id, "s": f"wedge-{company_id.hex[:10]}"},
            )
            await db.execute(
                text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
                {"u": hr_id, "e": f"hr-{hr_id.hex[:10]}@wedge.test", "c": company_id},
            )
            await db.commit()

        class _NullStore:
            async def store(self, *_a: Any, **_kw: Any) -> None:
                return None

        import app.corpus as corpus_module

        real_store = corpus_module.store.store
        corpus_module.store.store = _NullStore().store  # type: ignore[method-assign]
        try:
            async with factory() as db:
                out = await corpus_module.ingest_document(
                    db, company_id=company_id, actor=hr_id, actor_role="hr_manager",
                    title="Handbook v1", audience="all_staff", doc_kind="handbook",
                    expires_on=None, attested=True, data=_HANDBOOK_TXT.encode(),
                    filename="v1.txt", meta=_META,
                )
                await db.commit()
            document_id = uuid.UUID(out["id"])

            # Replace BEFORE the reconciler ever runs — v1 is superseded while
            # v1.status is still 'parsed', reproducing HIGH-1 exactly.
            async with factory() as db:
                await corpus_module.add_version(
                    db, company_id=company_id, actor=hr_id, actor_role="hr_manager",
                    document_id=document_id,
                    data=(_HANDBOOK_TXT + "\n\nA genuinely new paragraph.").encode(),
                    filename="v2.txt", meta=_META,
                )
                await db.commit()

            async with factory() as db:
                v1_status_before = await db.scalar(
                    text(
                        "SELECT status FROM corpus_document_versions"
                        " WHERE document_id = :d AND version = 1"
                    ),
                    {"d": document_id},
                )
            assert v1_status_before == "parsed"  # superseded, but its own status never moved

            result = rec.PassResult()
            async with factory() as db:
                await rec._corpus_embed_pass(db, result)  # must not raise

            assert result.failed == 0
            assert result.gave_up == 0
            assert result.corpus_indexed == 1

            async with factory() as db:
                v2_status = await db.scalar(
                    text(
                        "SELECT status FROM corpus_document_versions"
                        " WHERE document_id = :d AND version = 2"
                    ),
                    {"d": document_id},
                )
                v1_status_after = await db.scalar(
                    text(
                        "SELECT status FROM corpus_document_versions"
                        " WHERE document_id = :d AND version = 1"
                    ),
                    {"d": document_id},
                )
            assert v2_status == "indexed"
            # v1 is superseded and will never be retrieved — correctly left
            # alone at 'parsed' rather than being forced through indexing.
            assert v1_status_after == "parsed"

            # A second pass finds nothing outstanding and still does not raise
            # — nothing is parked, nothing is wedged.
            result2 = rec.PassResult()
            async with factory() as db:
                await rec._corpus_embed_pass(db, result2)
            assert result2.failed == 0
            assert result2.gave_up == 0
        finally:
            corpus_module.store.store = real_store  # type: ignore[method-assign]
    finally:
        async with factory() as db:
            await db.execute(text("DELETE FROM reconciliation_state WHERE kind = 'corpus_chunk'"))
            await db.execute(text("DELETE FROM corpus_documents WHERE company_id = :c"), {"c": company_id})
            await db.execute(text("DELETE FROM users WHERE company_id = :c"), {"c": company_id})
            await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": company_id})
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciler_indexes_a_100_percent_duplicate_reupload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code review MUST: a re-upload whose content is byte-identical to the
    version it replaces (HR fixes only the title, or genuinely re-uploads the
    same file) has EVERY chunk filled by the carry-over UPDATE alone — the
    ``rows`` SELECT that drives the rest of ``_corpus_embed_pass`` then comes
    back empty, and before the fix the function returned at
    ``if not rows: return`` before the version was ever promoted to
    'indexed'. It sat at 'parsed' forever and ``search_corpus``'s
    ``v.status = 'indexed'`` filter excluded it silently. The prior test
    (``..._reembeds_only_changed_chunks``) always appended new content, so at
    least one chunk differed and never exercised this path.
    """
    from app.fake_ai import fake_embeddings

    embed_calls: list[list[str]] = []

    async def _fake_embed_texts_remote(*, texts: list[str], task_type: str,
                                       acting_user_id: str) -> list[list[float]]:
        embed_calls.append(texts)
        return fake_embeddings(texts)

    monkeypatch.setattr(rec, "embed_texts_remote", _fake_embed_texts_remote)

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    company_id, hr_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Dup co', :s)"),
                {"c": company_id, "s": f"dup-{company_id.hex[:10]}"},
            )
            await db.execute(
                text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
                {"u": hr_id, "e": f"hr-{hr_id.hex[:10]}@dup.test", "c": company_id},
            )
            await db.commit()

        class _NullStore:
            async def store(self, *_a: Any, **_kw: Any) -> None:
                return None

        import app.corpus as corpus_module

        real_store = corpus_module.store.store
        corpus_module.store.store = _NullStore().store  # type: ignore[method-assign]
        try:
            async with factory() as db:
                out = await corpus_module.ingest_document(
                    db, company_id=company_id, actor=hr_id, actor_role="hr_manager",
                    title="Handbook", audience="all_staff", doc_kind="handbook",
                    expires_on=None, attested=True, data=_HANDBOOK_TXT.encode(),
                    filename="v1.txt", meta=_META,
                )
                await db.commit()
            document_id = uuid.UUID(out["id"])

            # Fully embed and index v1 first, exactly like a normal upload.
            result1 = rec.PassResult()
            async with factory() as db:
                await rec._corpus_embed_pass(db, result1)
            assert result1.corpus_indexed == 1

            # Re-upload with BYTE-IDENTICAL content — every chunk's
            # content_sha256 matches v2's own chunks against v1's, so the
            # carry-over should fill 100% of them and the embedder should
            # never be called again.
            embed_calls.clear()
            async with factory() as db:
                await corpus_module.add_version(
                    db, company_id=company_id, actor=hr_id, actor_role="hr_manager",
                    document_id=document_id, data=_HANDBOOK_TXT.encode(),
                    filename="v1-again.txt", meta=_META,
                )
                await db.commit()

            result2 = rec.PassResult()
            async with factory() as db:
                await rec._corpus_embed_pass(db, result2)

            assert embed_calls == []  # nothing needed the embedder at all
            assert result2.corpus_embedded == 0
            assert result2.corpus_indexed == 1  # v2 promoted by carry-over alone
            assert result2.failed == 0

            async with factory() as db:
                v2_status = await db.scalar(
                    text(
                        "SELECT status FROM corpus_document_versions"
                        " WHERE document_id = :d AND version = 2"
                    ),
                    {"d": document_id},
                )
            assert v2_status == "indexed"
        finally:
            corpus_module.store.store = real_store  # type: ignore[method-assign]
    finally:
        async with factory() as db:
            await db.execute(text("DELETE FROM reconciliation_state WHERE kind = 'corpus_chunk'"))
            await db.execute(text("DELETE FROM corpus_documents WHERE company_id = :c"), {"c": company_id})
            await db.execute(text("DELETE FROM users WHERE company_id = :c"), {"c": company_id})
            await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": company_id})
            await db.commit()
        await engine.dispose()
