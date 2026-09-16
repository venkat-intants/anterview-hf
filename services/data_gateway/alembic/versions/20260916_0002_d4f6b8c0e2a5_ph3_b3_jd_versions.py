"""Job descriptions are versioned, not overwritten — PH3-B3.

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
Create Date: 2026-09-16

Editing a JD used to be an UPDATE: the previous wording ceased to exist, and
"which JD was this candidate shown when they applied?" had no answer. That is a
question an enterprise buyer asks and a tribunal asks.

WHAT A VERSION HOLDS
The whole advert, not only ``jd_text``: the three list columns
(responsibilities, required_skills, nice_to_have_skills) are equally the job
description, and a version that captured the prose while the skills list moved
underneath it would preserve the wrong half.

WHY THE REQUISITION KEEPS ITS OWN COPY
``job_requisitions.jd_text`` and the three lists stay exactly where they are and
keep meaning "the published JD". Every existing reader — the careers board, the
apply endpoint, the role engine that derives competencies, the ATS scorer —
continues to work untouched, and publishing a version is a copy onto the
requisition inside the same transaction. The alternative, repointing every
reader at a join, would have made this story a rewrite of the read path in
exchange for no capability.

So ``jd_versions`` is the history and the drafting surface;
``job_requisitions`` is the live document. ``published_jd_version_id`` ties them
together, which is what makes provenance answerable.

WHAT THIS DELIBERATELY DOES NOT TOUCH
``round_criteria``. A published workflow froze its competencies at authoring
time, on purpose (Group C), and editing the advert must not re-grade anyone.
Nothing here writes to that table and
``tests/unit/test_ph3_jd_versions.py`` asserts it stays that way.

THE BACKFILL
Every requisition that has any JD content becomes version 1, published,
attributed to whoever created the requisition. Requisitions with no JD at all
get no version: inventing an empty v1 would put a row in a history that never
happened, and the first real edit mints v1 for them instead.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d4f6b8c0e2a5"
down_revision: str | None = "c3e5a7b9d1f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "jd_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=False),
        # Monotonic per requisition, never reused. A gap means a draft was
        # discarded, which is itself worth being able to see.
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=True),
        sa.Column("responsibilities", sa.dialects.postgresql.JSONB(),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("required_skills", sa.dialects.postgresql.JSONB(),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("nice_to_have_skills", sa.dialects.postgresql.JSONB(),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        # Free-text note from whoever made the change. Not required: forcing a
        # justification on every typo fix produces "update" a hundred times,
        # which is worse than an empty field because it looks like information.
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # When this version stopped being the published one. Together with
        # published_at it answers "which JD was live on this date?" without
        # having to reconstruct the order from version numbers.
        sa.Column("superseded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jd_versions"),
        sa.UniqueConstraint("id", "company_id", name="uq_jd_versions_id_company"),
        # Composite, like every other child of a requisition: a version cannot
        # point at another company's opening.
        sa.ForeignKeyConstraint(
            ["requisition_id", "company_id"],
            ["job_requisitions.id", "job_requisitions.company_id"],
            name="fk_jd_versions_requisition", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_jd_versions_author", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("requisition_id", "version", name="uq_jd_versions_number"),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')", name="ck_jd_versions_status"
        ),
        sa.CheckConstraint("version > 0", name="ck_jd_versions_version"),
        # A published version has a publication time and a draft does not.
        # Without this the two could disagree, and "when did this go live?"
        # would have two answers.
        sa.CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL AND superseded_at IS NULL)",
            name="ck_jd_versions_published_at",
        ),
    )

    # At most one published version per requisition. This is the invariant that
    # makes `published_jd_version_id` trustworthy — enforced by the database
    # rather than by every writer remembering to demote the old one.
    op.create_index(
        "uq_jd_versions_one_published",
        "jd_versions",
        ["requisition_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    # At most one draft, for the same reason: "the draft" is a thing the UI
    # refers to in the singular, so two of them is a bug with no correct
    # rendering.
    op.create_index(
        "uq_jd_versions_one_draft",
        "jd_versions",
        ["requisition_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    # The history view: newest first, for one requisition.
    op.create_index(
        "ix_jd_versions_history", "jd_versions", ["requisition_id", "version"], unique=False
    )

    # Which version the live document came from. Nullable for every requisition
    # that has never had a JD.
    op.add_column(
        "job_requisitions",
        sa.Column("published_jd_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_requisitions_published_jd",
        "job_requisitions", "jd_versions",
        ["published_jd_version_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── Backfill ─────────────────────────────────────────────────────────
    # One published v1 for every opening that actually has an advert. The
    # timestamps are the requisition's own created_at, not now(): claiming the
    # JD was written today would be a false provenance record in the exact
    # table that exists to provide provenance.
    op.execute(
        """
        INSERT INTO jd_versions (
            id, company_id, requisition_id, version, status,
            jd_text, responsibilities, required_skills, nice_to_have_skills,
            change_note, created_by_user_id, created_at, updated_at, published_at
        )
        SELECT gen_random_uuid(), r.company_id, r.id, 1, 'published',
               r.jd_text, r.responsibilities, r.required_skills, r.nice_to_have_skills,
               'Initial version, recorded when JD versioning was introduced.',
               r.created_by_user_id, r.created_at, r.updated_at, r.created_at
          FROM job_requisitions r
         WHERE r.deleted_at IS NULL
           AND (
               (r.jd_text IS NOT NULL AND btrim(r.jd_text) <> '')
               OR jsonb_array_length(COALESCE(r.responsibilities, '[]'::jsonb)) > 0
               OR jsonb_array_length(COALESCE(r.required_skills, '[]'::jsonb)) > 0
               OR jsonb_array_length(COALESCE(r.nice_to_have_skills, '[]'::jsonb)) > 0
           )
        """
    )
    op.execute(
        """
        UPDATE job_requisitions r
           SET published_jd_version_id = v.id
          FROM jd_versions v
         WHERE v.requisition_id = r.id AND v.status = 'published'
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_job_requisitions_published_jd", "job_requisitions", type_="foreignkey"
    )
    op.drop_column("job_requisitions", "published_jd_version_id")
    op.drop_index("ix_jd_versions_history", table_name="jd_versions")
    op.drop_index("uq_jd_versions_one_draft", table_name="jd_versions")
    op.drop_index("uq_jd_versions_one_published", table_name="jd_versions")
    op.drop_table("jd_versions")
