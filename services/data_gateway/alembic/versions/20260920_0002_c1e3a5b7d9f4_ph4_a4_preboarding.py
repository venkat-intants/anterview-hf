"""Documents and preboarding — PH4-A4.

Revision ID: c1e3a5b7d9f4
Revises: b9d1f3a5c7e2
Create Date: 2026-09-20

WHAT HR ASKS FOR
``document_requirements`` — per opening: a name, a type, mandatory or optional,
and whether the document carries an expiry date (a passport, a visa).

WHAT THE CANDIDATE GIVES
``candidate_documents`` — one row per upload, after the offer is accepted.
A re-upload is a NEW row that supersedes the last; nothing is overwritten, so
the history of what was sent, verified and rejected stays readable. Files are
PDF, JPEG or PNG, recognised by their content (decision D4-3), and live in
object storage under a key that names no person; the row holds its hash, size
and detected type. Downloads are only ever through short-lived signed links.

WHAT HOLDS WHERE
- ``candidate_documents_review``: a document moves submitted → verified |
  rejected | replacement_requested, and verified → replacement_requested, only
  with a reviewer; the file behind a row never changes; a superseded row is
  frozen; a verified document is never expired.
- ``offers_preboarding_complete``: an offer is marked preboarding-complete only
  while every mandatory requirement of its opening has a current, verified,
  unexpired document — at the database, whatever the caller.
- ``document_events`` and ``hrms_exports`` are append-only: every upload,
  review and download, and every signed HRMS payload prepared, with who and
  when.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c1e3a5b7d9f4"
down_revision: str | None = "b9d1f3a5c7e2"
branch_labels: str | None = None
depends_on: str | None = None

DOC_TYPES = ("identity", "address", "education", "employment", "tax", "bank", "photo",
             "medical", "other")
DOC_STATES = ("submitted", "verified", "rejected", "replacement_requested")
CONTENT_TYPES = ("application/pdf", "image/jpeg", "image/png")
DOC_EVENTS = ("uploaded", "verified", "rejected", "replacement_requested", "downloaded",
              "superseded", "deleted")

REVIEW = """
CREATE OR REPLACE FUNCTION candidate_documents_review() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'submitted' OR NEW.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'a document arrives as submitted';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.redacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'document % was erased; it cannot change', OLD.id;
    END IF;
    -- The file behind a row is fixed; a new file is a new row.
    IF NEW.storage_key IS DISTINCT FROM OLD.storage_key
       OR NEW.sha256 IS DISTINCT FROM OLD.sha256
       OR NEW.content_type IS DISTINCT FROM OLD.content_type
       OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
       OR NEW.offer_id IS DISTINCT FROM OLD.offer_id
       OR NEW.requirement_id IS DISTINCT FROM OLD.requirement_id THEN
        IF NEW.redacted_at IS NULL THEN
            RAISE EXCEPTION 'document % is fixed; upload a new version instead', OLD.id;
        END IF;
    END IF;
    IF OLD.superseded_at IS NOT NULL AND NEW.redacted_at IS NULL THEN
        RAISE EXCEPTION 'document % was superseded and is kept as it was', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT ((OLD.status = 'submitted'
                 AND NEW.status IN ('verified', 'rejected', 'replacement_requested'))
             OR (OLD.status = 'verified' AND NEW.status = 'replacement_requested')) THEN
            RAISE EXCEPTION 'document % cannot go from % to %', OLD.id, OLD.status, NEW.status;
        END IF;
        IF NEW.reviewed_by_user_id IS NULL OR NEW.reviewed_at IS NULL THEN
            RAISE EXCEPTION 'document % is reviewed by a named person', OLD.id;
        END IF;
        IF NEW.status = 'verified' AND NEW.expires_on IS NOT NULL
           AND NEW.expires_on <= current_date THEN
            RAISE EXCEPTION 'document % has expired and cannot be verified', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
REVIEW_HOOK = """
CREATE TRIGGER candidate_documents_review
    BEFORE INSERT OR UPDATE ON candidate_documents
    FOR EACH ROW EXECUTE FUNCTION candidate_documents_review()
"""
COMPLETE = """
CREATE OR REPLACE FUNCTION offers_preboarding_complete() RETURNS trigger AS $$
DECLARE
    missing int;
BEGIN
    IF OLD.preboarding_completed_at IS NOT NULL
       AND NEW.preboarding_completed_at IS DISTINCT FROM OLD.preboarding_completed_at THEN
        RAISE EXCEPTION 'offer % preboarding completion is recorded once', OLD.id;
    END IF;
    IF OLD.preboarding_completed_at IS NULL AND NEW.preboarding_completed_at IS NOT NULL THEN
        IF NEW.preboarding_completed_by IS NULL THEN
            RAISE EXCEPTION 'offer % preboarding is completed by a named person', OLD.id;
        END IF;
        SELECT count(*) INTO missing
          FROM document_requirements r
         WHERE r.requisition_id = NEW.requisition_id AND r.company_id = NEW.company_id
           AND r.mandatory AND r.deleted_at IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM candidate_documents d
                WHERE d.offer_id = NEW.id AND d.requirement_id = r.id
                  AND d.superseded_at IS NULL AND d.status = 'verified'
                  AND (d.expires_on IS NULL OR d.expires_on > current_date));
        IF missing > 0 THEN
            RAISE EXCEPTION
                'offer % cannot be marked preboarding-complete: % mandatory document(s) not verified',
                OLD.id, missing;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
COMPLETE_HOOK = """
CREATE TRIGGER offers_preboarding_complete
    BEFORE UPDATE ON offers
    FOR EACH ROW EXECUTE FUNCTION offers_preboarding_complete()
"""
APPEND_ONLY = """
CREATE OR REPLACE FUNCTION preboarding_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
        RETURN NEW;
    END IF;
    -- Deleted with its offer (the application went), or — for a signed HRMS
    -- payload, which carries a name, an email and pay — by erasure, once the
    -- offer it belongs to has been redacted.
    IF TG_OP = 'DELETE' AND (
           NOT EXISTS (SELECT 1 FROM offers o WHERE o.id = OLD.offer_id)
        OR (TG_TABLE_NAME = 'hrms_exports' AND EXISTS (
               SELECT 1 FROM offers o WHERE o.id = OLD.offer_id AND o.redacted_at IS NOT NULL))) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "document_requirements",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_expiry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False), _ts("deleted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_document_requirements"),
        sa.UniqueConstraint("id", "company_id", name="uq_document_requirements_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_document_requirements_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requisition_id", "company_id"],
                                ["job_requisitions.id", "job_requisitions.company_id"],
                                name="fk_document_requirements_requisition", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_document_requirements_created_by", ondelete="SET NULL"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120",
                           name="ck_document_requirements_name"),
        sa.CheckConstraint(f"doc_type IN {DOC_TYPES}", name="ck_document_requirements_type"),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 1000",
                           name="ck_document_requirements_description"),
    )
    op.alter_column("document_requirements", "created_at", server_default=sa.text("now()"))
    op.alter_column("document_requirements", "updated_at", server_default=sa.text("now()"))
    op.create_index("ix_document_requirements_requisition", "document_requirements",
                    ["requisition_id", "position"], postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "candidate_documents",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="submitted"),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        _ts("uploaded_at", nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        _ts("reviewed_at"),
        sa.Column("review_note", sa.Text(), nullable=True),
        _ts("superseded_at"),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        _ts("redacted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_documents"),
        sa.UniqueConstraint("storage_key", name="uq_candidate_documents_storage_key"),
        sa.ForeignKeyConstraint(["offer_id", "company_id"], ["offers.id", "offers.company_id"],
                                name="fk_candidate_documents_offer", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requirement_id", "company_id"],
                                ["document_requirements.id", "document_requirements.company_id"],
                                name="fk_candidate_documents_requirement", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrolment_id", "company_id"],
                                ["enrolments.id", "enrolments.company_id"],
                                name="fk_candidate_documents_enrolment", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"],
                                name="fk_candidate_documents_reviewed_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["candidate_documents.id"],
                                name="fk_candidate_documents_superseded_by",
                                deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint(f"status IN {DOC_STATES}", name="ck_candidate_documents_status"),
        sa.CheckConstraint(f"content_type IN {CONTENT_TYPES}",
                           name="ck_candidate_documents_content_type"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 10485760", name="ck_candidate_documents_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_candidate_documents_sha256"),
        sa.CheckConstraint("original_name IS NULL OR char_length(original_name) <= 200",
                           name="ck_candidate_documents_name"),
        sa.CheckConstraint("review_note IS NULL OR char_length(review_note) <= 1000",
                           name="ck_candidate_documents_note"),
        sa.CheckConstraint("(storage_key IS NULL) = (redacted_at IS NOT NULL)",
                           name="ck_candidate_documents_key_until_erased"),
    )
    op.create_index("uq_candidate_documents_current", "candidate_documents",
                    ["offer_id", "requirement_id"], unique=True,
                    postgresql_where=sa.text("superseded_at IS NULL"))
    op.create_index("ix_candidate_documents_offer", "candidate_documents", ["offer_id"])

    op.create_table(
        "document_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.dialects.postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_events"),
        sa.ForeignKeyConstraint(["offer_id", "company_id"], ["offers.id", "offers.company_id"],
                                name="fk_document_events_offer", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_document_events_actor", ondelete="SET NULL"),
        sa.CheckConstraint(f"action IN {DOC_EVENTS}", name="ck_document_events_action"),
        sa.CheckConstraint("actor_type IN ('user', 'candidate', 'system')",
                           name="ck_document_events_actor"),
    )
    op.alter_column("document_events", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_document_events_offer", "document_events", ["offer_id", "created_at"])

    op.create_table(
        "hrms_exports",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hrms_exports"),
        sa.ForeignKeyConstraint(["offer_id", "company_id"], ["offers.id", "offers.company_id"],
                                name="fk_hrms_exports_offer", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_hrms_exports_actor", ondelete="SET NULL"),
        sa.CheckConstraint("signature ~ '^[0-9a-f]{64}$'", name="ck_hrms_exports_signature"),
    )
    op.alter_column("hrms_exports", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_hrms_exports_offer", "hrms_exports", ["offer_id", "created_at"])

    for sql in (REVIEW, REVIEW_HOOK, COMPLETE, COMPLETE_HOOK, APPEND_ONLY):
        op.execute(sql)
    for table in ("document_events", "hrms_exports"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION preboarding_append_only()"
        )


def downgrade() -> None:
    for table in ("document_events", "hrms_exports"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS preboarding_append_only()")
    op.execute("DROP TRIGGER IF EXISTS offers_preboarding_complete ON offers")
    op.execute("DROP FUNCTION IF EXISTS offers_preboarding_complete()")
    op.execute("DROP TRIGGER IF EXISTS candidate_documents_review ON candidate_documents")
    op.execute("DROP FUNCTION IF EXISTS candidate_documents_review()")
    op.drop_index("ix_hrms_exports_offer", table_name="hrms_exports")
    op.drop_table("hrms_exports")
    op.drop_index("ix_document_events_offer", table_name="document_events")
    op.drop_table("document_events")
    op.drop_index("ix_candidate_documents_offer", table_name="candidate_documents")
    op.drop_index("uq_candidate_documents_current", table_name="candidate_documents")
    op.drop_table("candidate_documents")
    op.drop_index("ix_document_requirements_requisition", table_name="document_requirements")
    op.drop_table("document_requirements")
