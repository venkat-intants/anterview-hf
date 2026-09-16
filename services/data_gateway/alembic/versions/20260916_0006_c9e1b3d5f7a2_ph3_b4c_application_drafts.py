"""Save and resume an application, with consent taken first — PH3-B4c / PH3-B5.

Revision ID: c9e1b3d5f7a2
Revises: a7c9e1f3b5d8
Create Date: 2026-09-16

THE CONSENT PROBLEM THIS SOLVES
The apply endpoint has always committed the CV and its ``dpdp_consent_ledger``
entry in one transaction, deliberately: "there is no moment at which the CV
exists without the record of permission to hold it" (routers/public_apply.py).
Hard constraint 3 in CLAUDE.md says the same.

A saved draft holds a name, an email, a phone number and a CV *before* the
person reaches the submit button, so the naive version of Save & Resume breaks
that invariant outright. It is not broken here. A draft cannot be written
without consent — the endpoint refuses, and the ledger row is written in the
same transaction as the first save — so the invariant reads exactly as before,
one step earlier.

The consent is the same ledger entry the submitted application uses
(``application_data`` / ``recruitment``), and that entry is idempotent per user,
so submitting after drafting does not ask twice or record twice.

WHY THE DRAFT NEEDS ITS OWN USER ROW
For the same reason the applicant does: ``dpdp_consent_ledger.user_id`` is NOT
NULL. A draft therefore mints the ``guest_candidate`` user up front rather than
at submission, which also means a returning candidate is recognised.

EXPIRY IS A RETENTION RULE, NOT A CLEANUP JOB
An expired draft holds personal data. It is purged by the existing DPDP
retention cron rather than by a bespoke sweep, and ``expires_at`` is stored
rather than computed so a policy change does not retroactively delete drafts
somebody is still working on.

PH3-B5 RIDES HERE TOO. ``confirmed_at`` / ``confirmed_fields`` record that the
candidate reviewed what the CV parser read and said it was right. It lives on
the draft because that is where the review happens, and it is copied to nothing:
the applicant row gets the CORRECTED VALUES, which is the point.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c9e1b3d5f7a2"
down_revision: str | None = "a7c9e1f3b5d8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "application_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=False),
        # The guest_candidate row that owns the consent. NOT NULL: a draft
        # cannot exist without one, which is the whole mechanism.
        sa.Column("user_id", sa.Uuid(), nullable=False),
        # Opaque, single-purpose, stored HASHED. The resume link the candidate
        # keeps is the only credential on this row, and a leaked database must
        # not hand somebody every half-finished application.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("years_experience", sa.SmallInteger(), nullable=True),
        sa.Column("current_company", sa.Text(), nullable=True),
        sa.Column("current_title", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("github_url", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), server_default=sa.text("'en'"), nullable=False),
        # PH3-B1. Carried on the draft so a tracked link that was opened three
        # weeks ago still attributes the application it eventually becomes.
        sa.Column("source", sa.Text(), server_default=sa.text("'direct'"), nullable=False),
        sa.Column("source_detail", sa.Text(), nullable=True),
        # Answers to the opening's own questions, keyed by question id. Stored
        # as given and validated again at submission — a draft is allowed to be
        # incomplete, which is what a draft is.
        sa.Column("answers", sa.dialects.postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        # The CV, if one was uploaded before saving. Stored in the same bucket
        # as a submitted CV and deleted with the draft.
        sa.Column("resume_s3_key", sa.Text(), nullable=True),
        sa.Column("resume_filename", sa.Text(), nullable=True),
        # ── PH3-B5: the candidate confirmed what we read ─────────────────
        sa.Column("parsed", sa.dialects.postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # ── Lifecycle ────────────────────────────────────────────────────
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        # Stored, not computed: a later policy change must not retroactively
        # delete a draft somebody is still working on.
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_application_drafts"),
        sa.ForeignKeyConstraint(
            ["requisition_id", "company_id"],
            ["job_requisitions.id", "job_requisitions.company_id"],
            name="fk_application_drafts_requisition", ondelete="CASCADE",
        ),
        # CASCADE, and it is inert by design rather than load-bearing.
        #
        # Erasure never hard-deletes a users row — step 7 of the erasure
        # executor anonymises it in place, because erasure_requests.user_id is
        # ON DELETE RESTRICT and that row is the §12 proof the erasure
        # happened. So this cascade does not fire on the erasure path; the
        # draft is deleted explicitly there (step 5e), after its CV object has
        # been collected for deletion in step 8.
        #
        # It is CASCADE rather than RESTRICT for the case erasure does not
        # cover: if a users row is ever hard-deleted by some future path, a
        # draft full of that person's name, email, phone and CV must go with
        # it, not survive as an orphan RESTRICT would have blocked the delete
        # over. Erasure is the strict path; this is the backstop.
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_application_drafts_user",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('draft','submitted','expired')",
            name="ck_application_drafts_status",
        ),
    )

    op.create_index(
        "uq_application_drafts_token", "application_drafts", ["token_hash"], unique=True
    )
    # One live draft per person per opening. Partial so a submitted or expired
    # draft does not block starting a new one.
    op.create_index(
        "uq_application_drafts_live",
        "application_drafts",
        ["requisition_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    # What the retention cron asks: what has expired?
    op.create_index(
        "ix_application_drafts_expiry",
        "application_drafts",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'draft'"),
    )

    # ── PH3-B5 on the applicant ──────────────────────────────────────────
    # Recorded on the person, not only on the draft: the draft is deleted once
    # the application is submitted, and "did this candidate confirm what we
    # read off their CV?" is a question about the application that survives it.
    op.add_column(
        "applicants",
        sa.Column("details_confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applicants", "details_confirmed_at")
    op.drop_index("ix_application_drafts_expiry", table_name="application_drafts")
    op.drop_index("uq_application_drafts_live", table_name="application_drafts")
    op.drop_index("uq_application_drafts_token", table_name="application_drafts")
    op.drop_table("application_drafts")
