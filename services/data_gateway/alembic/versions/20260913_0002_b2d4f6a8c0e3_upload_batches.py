"""Bulk resume uploads processed in the background — Group E, E5.

Revision ID: b2d4f6a8c0e3
Revises: a1c3e5f7b9d2
Create Date: 2026-09-13

The bulk upload read every PDF and wrote every applicant inside the HTTP
request. Scoring had already moved to the reconciler, but a few hundred CVs was
still a few hundred extractions and inserts on one connection, and nothing
recorded which files failed once the response was gone.

Two tables make the upload a queue:

* ``upload_batches`` — one upload, for one OPENING (a real requisition id, never
  a typed title), by one person, with a status the console can poll.
* ``upload_items`` — one file in it: where its bytes are stored, and what
  became of it. ``stored`` is waiting for the reconciler; ``processing`` is
  claimed by a pass (row-locked, so two passes never take the same file);
  ``created`` names the applicant and enrolment it became; ``failed`` says why,
  in words HR can act on.

Both are company-scoped: the batch carries the composite foreign key to the
requisition, so a batch cannot point at another company's opening, and every
item repeats the company for the queries that read it directly.

The database is the queue on purpose. The deployment is one container; a
broker would be another process and another bill for work the reconciler
already schedules, retries and records.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b2d4f6a8c0e3"
down_revision: str | None = "a1c3e5f7b9d2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "upload_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'processing'"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_upload_batches"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_upload_batches_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requisition_id", "company_id"],
                                ["job_requisitions.id", "job_requisitions.company_id"],
                                name="fk_upload_batches_requisition", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"],
                                name="fk_upload_batches_user", ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('processing', 'finished')",
                           name="ck_upload_batches_status"),
        sa.CheckConstraint("total_files >= 0", name="ck_upload_batches_total"),
    )
    op.create_index("ix_upload_batches_company_created", "upload_batches",
                    ["company_id", "created_at"])

    op.create_table(
        "upload_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=True),
        sa.Column("enrolment_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_upload_items"),
        sa.ForeignKeyConstraint(["batch_id"], ["upload_batches.id"],
                                name="fk_upload_items_batch", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_upload_items_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["applicant_id"], ["applicants.id"],
                                name="fk_upload_items_applicant", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["enrolment_id"], ["enrolments.id"],
                                name="fk_upload_items_enrolment", ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('stored', 'processing', 'created', 'failed')",
                           name="ck_upload_items_status"),
    )
    op.create_index("ix_upload_items_batch_status", "upload_items", ["batch_id", "status"])
    # The reconciler's only access path: the oldest files still waiting.
    op.create_index("ix_upload_items_waiting", "upload_items", ["created_at"],
                    postgresql_where=sa.text("status = 'stored'"))


def downgrade() -> None:
    op.drop_index("ix_upload_items_waiting", table_name="upload_items")
    op.drop_index("ix_upload_items_batch_status", table_name="upload_items")
    op.drop_table("upload_items")
    op.drop_index("ix_upload_batches_company_created", table_name="upload_batches")
    op.drop_table("upload_batches")
