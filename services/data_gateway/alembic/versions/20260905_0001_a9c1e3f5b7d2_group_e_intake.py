"""Group E — public applications and deferred resume enrichment.

Two columns, both small, both load-bearing for how candidates get INTO the
system rather than what happens once they are here.

``job_requisitions.public_apply_enabled``
    An explicit opt-in before an opening accepts applications from the open
    web. Defaults to FALSE, and that default is the point: the apply link is
    addressed by requisition id, and a UUID that turns up in a forwarded email
    or a browser history must not by itself open an application channel HR
    never meant to open. Publishing a role is a decision, so it is a flag.

``applicants.pending_enrichment``
    "This resume has been stored but not yet read." Bulk upload used to score
    each resume inside the request — roughly ten seconds per file, sequentially,
    for up to twenty-five files — so a full batch held one HTTP connection open
    for minutes and any drop lost the tail of it. Now the bytes are stored and
    the reconciler (Group A) scores and embeds them in the background.

    The flag exists because the SCORER is what extracts a candidate's name and
    email from the PDF. Deferring the score defers those too, so a freshly
    uploaded row carries a filename-derived placeholder until the reconciler
    catches up.

    Reconciliation may therefore overwrite the name — but ONLY while this flag
    is set. Nothing else writes ``full_name`` today: there is no endpoint that
    edits an applicant's name, so the flag is set once at ingest and cleared
    once by the reconciler. If a name-edit endpoint is ever added it MUST clear
    this flag in the same statement, because quietly reverting a correction
    someone typed by hand looks like a typo rather than a bug and would go
    unreported.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a9c1e3f5b7d2"
down_revision: str | None = "f8b0d2e4a6c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "job_requisitions",
        sa.Column(
            "public_apply_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "applicants",
        sa.Column(
            "pending_enrichment",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # Partial: the reconciler asks "what is still waiting to be read?", which is
    # a handful of rows against a table that grows without bound. Indexing only
    # the true rows keeps the index the size of the backlog rather than the
    # size of the applicant table.
    op.create_index(
        "ix_applicants_pending_enrichment",
        "applicants",
        ["company_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("pending_enrichment AND deleted_at IS NULL"),
    )
    # Answers "which openings are live to the public?" without scanning every
    # requisition a company has ever had.
    op.create_index(
        "ix_job_requisitions_public_open",
        "job_requisitions",
        ["company_id"],
        unique=False,
        postgresql_where=sa.text(
            "public_apply_enabled AND status = 'open' AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_job_requisitions_public_open", table_name="job_requisitions")
    op.drop_index("ix_applicants_pending_enrichment", table_name="applicants")
    op.drop_column("applicants", "pending_enrichment")
    op.drop_column("job_requisitions", "public_apply_enabled")
