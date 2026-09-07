"""Tag each bulk-uploaded applicant with the batch it arrived in — A4, E5.

A4 asks for a notification when a bulk upload completes. Nothing could emit one,
because "the upload" stopped being a thing the moment E5 moved scoring out of
the request: the endpoint returns as soon as the PDFs are stored, and the work
that actually finishes minutes later is a set of independent reconciler passes
over unrelated rows. There was no handle on "these twenty-five belong together".

``upload_batch_id`` is that handle. It is deliberately NOT a foreign key to a
batches table: a batch has no state of its own worth storing beyond the rows
that carry it, and the one question anybody asks — "is this batch still being
read?" — is answered by counting its rows with ``pending_enrichment`` true.
Adding a parent table would mean keeping two representations of that answer in
step.

The index is partial, on the pending rows only. That is the whole access
pattern: the reconciler asks whether any of a batch's rows are still
outstanding, and once the batch is done its rows are never looked up by batch
again. A full index would be larger than the data it serves and would keep
growing after it stopped being read.

Nullable, with no backfill. Rows that predate this arrived before batches were
tracked and genuinely have no batch; inventing one per row would make every
historical applicant look like a completed single-file upload and could fire a
notification for each. NULL means "not part of a tracked batch", which is true.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e2a4c6b8d0f3"
down_revision: str | None = "a1c3e5b7d9f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "applicants",
        sa.Column("upload_batch_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_applicants_pending_batch",
        "applicants",
        ["upload_batch_id"],
        unique=False,
        postgresql_where=sa.text("pending_enrichment AND upload_batch_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_applicants_pending_batch", table_name="applicants")
    op.drop_column("applicants", "upload_batch_id")
