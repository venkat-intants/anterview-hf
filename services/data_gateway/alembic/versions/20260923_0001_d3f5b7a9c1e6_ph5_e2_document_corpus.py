"""Document corpus RAG — PH5-E2.

Revision ID: d3f5b7a9c1e6
Revises: 7416694620ed
Create Date: 2026-09-23

WHAT THIS IS
A governed library of a COMPANY's own documents (policies, handbooks, process
notes) that the staff copilot can search — never a candidate's own file, and
never anything that reaches a hiring decision (the copilot tool built on top
of this is `read`-only, `shared/agents/schema.py`).

FOUR TABLES, on the ``candidate_documents`` precedent
(``20260920_0002_c1e3a5b7d9f4``): composite ``(id, company_id)`` foreign keys
throughout, so a child row cannot silently point at another tenant's parent
even if a caller ever passed the wrong ids.

  * ``corpus_documents``         — the identity of a document, stable across
                                   versions. ``audience`` (`all_staff` /
                                   `hr_only`) decides who may retrieve it —
                                   see ``app/corpus.py::CORPUS_AUDIENCE_ROLES``,
                                   deliberately NOT this migration and
                                   deliberately NOT reusing
                                   ``shared.agents.schema.DATA_CLASS_ROLES``'s
                                   ``candidate_pii`` set (a policy document is
                                   not a candidate's personal data).
  * ``corpus_document_versions`` — one row per upload. Nothing is overwritten:
                                   a replace supersedes the row it replaces
                                   (``superseded_at`` / ``superseded_by_id``),
                                   so a citation naming an old version keeps
                                   resolving. ``corpus_versions_immutable``
                                   (below) freezes the file identity after
                                   insert and the whole row once redacted.
  * ``corpus_chunks``            — the retrievable unit. ``embedding
                                   halfvec(3072)`` is added by raw SQL exactly
                                   as ``applicants.embedding`` was
                                   (``a9c1e2f3b4d5``) — pgvector's ``halfvec``
                                   is not ORM-mapped anywhere in this codebase
                                   (see ``alembic/env.py``), so this migration
                                   does not try to be the first.
  * ``corpus_events``            — append-only history (uploaded, parsed,
                                   indexed, failed, superseded,
                                   audience_changed, expired, deleted,
                                   reindex_requested), on the
                                   ``document_events`` precedent.

WHAT HOLDS WHERE
``corpus_versions_immutable``: a version's file identity (``storage_key``,
``sha256``, ``content_type``, ``size_bytes``, ``document_id``, ``version``)
never changes after insert except to NULL under redaction; a superseded row is
frozen except for redaction; status only ever moves
``parsed`` -> ``indexing`` -> ``indexed``/``failed``, OR ``failed`` back to
``parsed`` — a DELIBERATE addition (code review, 2026-09-23), not part of the
original design: without it, a version the reconciler parked after
``MAX_ATTEMPTS`` embedding failures had NO path back to being indexed, ever,
even after the transient outage that caused the failure was long over — the
version's own ``embedding_unavailable`` sentence ("will be indexed
automatically") was false for it. ``POST /hr/library/{id}/reindex``
(``app/corpus.py::reindex_document``) is the only caller of this transition:
it resets the CURRENT version's status to ``parsed`` and clears its
``failure_code``, and the reconciler's next pass then treats it exactly like
a fresh upload. A redacted row cannot change at all, whatever its status. On
the ``candidate_documents_review()`` precedent.

``corpus_events_append_only``: no UPDATE ever; DELETE only once the document
it names is already gone (defence-in-depth — companies are soft-deleted in
this platform, so this branch is not expected to fire in practice). On the
``preboarding_append_only()`` precedent.

WHAT DOES NOT LIVE HERE
The erasure-inventory declaration for these four tables
(``services/admin_ops/app/erasure_executor.py::EXCLUDED_TABLES`` — there is no
key from a candidate to a chunk of an HR-uploaded document, recorded there and
in ``docs/ACCEPTED-RISKS.md`` AR-8, not glossed over here).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3f5b7a9c1e6"
down_revision: str | None = "7416694620ed"
branch_labels: str | None = None
depends_on: str | None = None

AUDIENCES = ("all_staff", "hr_only")
DOC_KINDS = ("policy", "handbook", "process", "jd_library", "other")
VERSION_STATUSES = ("parsed", "indexing", "indexed", "failed")
CONTENT_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
)
# The closed failure_code vocabulary (app/corpus.py::FAILURE_CODES). Only
# `embedding_unavailable` is realistically ever written here — the other ten
# are 422s at upload with no row created (design §4.6) — but the column is
# constrained to the whole vocabulary so a later code path cannot invent a
# freeform reason string that the Documents screen has no sentence for.
FAILURE_CODES = (
    "unsupported_type", "too_large", "active_content", "encrypted", "no_text",
    "parse_timeout", "parse_error", "too_long", "too_many_chunks",
    "quota_exceeded", "embedding_unavailable",
)
EVENTS = (
    "uploaded", "parsed", "indexed", "failed", "superseded", "audience_changed",
    "expired", "deleted", "reindex_requested",
)

IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION corpus_versions_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF OLD.redacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'corpus document version % was redacted; it cannot change', OLD.id;
    END IF;
    -- The file identity is fixed once parsed; a new file is a new version.
    -- The one licensed change is REDACTION, which clears exactly these columns.
    IF (NEW.storage_key IS DISTINCT FROM OLD.storage_key
        OR NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.content_type IS DISTINCT FROM OLD.content_type
        OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
        OR NEW.document_id IS DISTINCT FROM OLD.document_id
        OR NEW.version IS DISTINCT FROM OLD.version)
       AND NEW.redacted_at IS NULL THEN
        RAISE EXCEPTION 'corpus document version % is fixed; upload a new version instead', OLD.id;
    END IF;
    IF OLD.superseded_at IS NOT NULL AND NEW.redacted_at IS NULL
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'corpus document version % was superseded and is kept as it was', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        -- failed -> parsed is the one deliberate exception (code review,
        -- 2026-09-23): a version the reconciler parked after MAX_ATTEMPTS
        -- had no path back without it. Only POST /hr/library/{id}/reindex
        -- (app/corpus.py::reindex_document) drives this transition.
        IF NOT ((OLD.status = 'parsed' AND NEW.status IN ('indexing', 'indexed', 'failed'))
             OR (OLD.status = 'indexing' AND NEW.status IN ('indexed', 'failed'))
             OR (OLD.status = 'failed' AND NEW.status = 'parsed')) THEN
            RAISE EXCEPTION 'corpus document version % cannot go from % to %',
                OLD.id, OLD.status, NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
IMMUTABLE_HOOK = """
CREATE TRIGGER corpus_versions_immutable
    BEFORE INSERT OR UPDATE ON corpus_document_versions
    FOR EACH ROW EXECUTE FUNCTION corpus_versions_immutable()
"""
APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION corpus_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND NOT EXISTS (
        SELECT 1 FROM corpus_documents d WHERE d.id = OLD.document_id
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'corpus_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""
APPEND_ONLY_HOOK = """
CREATE TRIGGER corpus_events_append_only
    BEFORE UPDATE OR DELETE ON corpus_events
    FOR EACH ROW EXECUTE FUNCTION corpus_events_append_only()
"""


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "corpus_documents",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("doc_kind", sa.Text(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        # FK added below via op.create_foreign_key, once corpus_document_versions
        # exists — the two tables point at each other.
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False), _ts("deleted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_documents"),
        sa.UniqueConstraint("id", "company_id", name="uq_corpus_documents_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_corpus_documents_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_corpus_documents_created_by", ondelete="SET NULL"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="ck_corpus_documents_title"),
        sa.CheckConstraint(f"audience IN {AUDIENCES}", name="ck_corpus_documents_audience"),
        sa.CheckConstraint(f"doc_kind IN {DOC_KINDS}", name="ck_corpus_documents_kind"),
    )
    op.alter_column("corpus_documents", "created_at", server_default=sa.text("now()"))
    op.alter_column("corpus_documents", "updated_at", server_default=sa.text("now()"))
    op.create_index(
        "ix_corpus_documents_company_audience", "corpus_documents", ["company_id", "audience"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "corpus_document_versions",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="parsed"),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("injection_markers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        _ts("uploaded_at", nullable=False),
        _ts("indexed_at"),
        _ts("superseded_at"),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        _ts("redacted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_document_versions"),
        sa.UniqueConstraint("id", "company_id", name="uq_corpus_document_versions_id_company"),
        sa.UniqueConstraint("document_id", "version", name="uq_corpus_document_versions_doc_version"),
        sa.UniqueConstraint("storage_key", name="uq_corpus_document_versions_storage_key"),
        sa.ForeignKeyConstraint(
            ["document_id", "company_id"], ["corpus_documents.id", "corpus_documents.company_id"],
            name="fk_corpus_document_versions_document", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"],
                                name="fk_corpus_document_versions_uploaded_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["corpus_document_versions.id"],
                                name="fk_corpus_document_versions_superseded_by",
                                deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint(f"status IN {VERSION_STATUSES}", name="ck_corpus_document_versions_status"),
        sa.CheckConstraint(f"failure_code IS NULL OR failure_code IN {FAILURE_CODES}",
                           name="ck_corpus_document_versions_failure_code"),
        sa.CheckConstraint(f"content_type IN {CONTENT_TYPES}",
                           name="ck_corpus_document_versions_content_type"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 10485760", name="ck_corpus_document_versions_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_corpus_document_versions_sha256"),
        sa.CheckConstraint("original_name IS NULL OR char_length(original_name) <= 200",
                           name="ck_corpus_document_versions_name"),
        sa.CheckConstraint("version >= 1", name="ck_corpus_document_versions_version_positive"),
        sa.CheckConstraint("(storage_key IS NULL) = (redacted_at IS NOT NULL)",
                           name="ck_corpus_document_versions_key_until_redacted"),
    )
    op.alter_column("corpus_document_versions", "uploaded_at", server_default=sa.text("now()"))
    op.create_index("ix_corpus_document_versions_document", "corpus_document_versions",
                    ["document_id", "version"])

    # The two tables point at each other: corpus_documents.current_version_id
    # cannot be declared inline above, because corpus_document_versions did not
    # exist yet when that table was created.
    op.create_foreign_key(
        "fk_corpus_documents_current_version", "corpus_documents", "corpus_document_versions",
        ["current_version_id"], ["id"], deferrable=True, initially="DEFERRED",
    )

    op.create_table(
        "corpus_chunks",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_chunks"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_corpus_chunks_version_ordinal"),
        sa.ForeignKeyConstraint(
            ["document_id", "company_id"], ["corpus_documents.id", "corpus_documents.company_id"],
            name="fk_corpus_chunks_document", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "company_id"],
            ["corpus_document_versions.id", "corpus_document_versions.company_id"],
            name="fk_corpus_chunks_version", ondelete="CASCADE",
        ),
        sa.CheckConstraint("char_length(content) > 0", name="ck_corpus_chunks_content"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_corpus_chunks_sha256"),
        sa.CheckConstraint("ordinal >= 0", name="ck_corpus_chunks_ordinal"),
    )
    op.alter_column("corpus_chunks", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_corpus_chunks_company_version", "corpus_chunks", ["company_id", "version_id"])
    op.create_index("ix_corpus_chunks_content_sha256", "corpus_chunks", ["content_sha256"])

    op.create_table(
        "corpus_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_corpus_events"),
        sa.ForeignKeyConstraint(
            ["document_id", "company_id"], ["corpus_documents.id", "corpus_documents.company_id"],
            name="fk_corpus_events_document", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_corpus_events_actor", ondelete="SET NULL"),
        sa.CheckConstraint(f"action IN {EVENTS}", name="ck_corpus_events_action"),
        sa.CheckConstraint("actor_type IN ('user', 'system')", name="ck_corpus_events_actor_type"),
    )
    op.alter_column("corpus_events", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_corpus_events_document", "corpus_events", ["document_id", "created_at"])

    # ------------------------------------------------------------------
    # Embeddings — raw SQL, exactly the applicants precedent (a9c1e2f3b4d5).
    # Not ORM-mapped: the halfvec cosine query is raw text() SQL throughout
    # this codebase (see alembic/env.py's note on why).
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE corpus_chunks ADD COLUMN IF NOT EXISTS embedding halfvec(3072) NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_corpus_chunks_embedding_hnsw "
        "ON corpus_chunks USING hnsw (embedding halfvec_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_corpus_chunks_fts "
        "ON corpus_chunks USING gin (to_tsvector('english', content))"
    )

    for sql in (IMMUTABLE_FN, IMMUTABLE_HOOK, APPEND_ONLY_FN, APPEND_ONLY_HOOK):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS corpus_events_append_only ON corpus_events")
    op.execute("DROP FUNCTION IF EXISTS corpus_events_append_only()")
    op.execute("DROP TRIGGER IF EXISTS corpus_versions_immutable ON corpus_document_versions")
    op.execute("DROP FUNCTION IF EXISTS corpus_versions_immutable()")

    op.execute("DROP INDEX IF EXISTS idx_corpus_chunks_fts")
    op.execute("DROP INDEX IF EXISTS idx_corpus_chunks_embedding_hnsw")
    op.execute("ALTER TABLE corpus_chunks DROP COLUMN IF EXISTS embedding")

    op.drop_index("ix_corpus_events_document", table_name="corpus_events")
    op.drop_table("corpus_events")

    op.drop_index("ix_corpus_chunks_content_sha256", table_name="corpus_chunks")
    op.drop_index("ix_corpus_chunks_company_version", table_name="corpus_chunks")
    op.drop_table("corpus_chunks")

    op.drop_constraint("fk_corpus_documents_current_version", "corpus_documents", type_="foreignkey")

    op.drop_index("ix_corpus_document_versions_document", table_name="corpus_document_versions")
    op.drop_table("corpus_document_versions")

    op.drop_index("ix_corpus_documents_company_audience", table_name="corpus_documents")
    op.drop_table("corpus_documents")
