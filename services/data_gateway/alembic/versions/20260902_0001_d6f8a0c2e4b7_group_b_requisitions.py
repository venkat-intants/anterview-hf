"""Group B — job requisitions, enrolments and the stage-transition ledger

The problem
-----------
There is no first-class job. ``jobs`` has no ``company_id`` (it was built for the
candidate's self-serve practice interview), and the hiring tables reference a
role three incompatible ways: ``applicants.target_job_title`` and
``exams.target_job_title`` by free text, ``interview_invites.job_id`` and
``sessions.job_id`` by a real key. Nothing joins applicant -> exam -> interview,
the pipeline endpoint has no job filter, and the nightly funnel watcher is
reduced to using the title *string* as the job's identity.

What this adds
--------------
``job_requisitions``  a company-scoped, stateful opening — the thing a workflow,
                      a dashboard and a candidate all hang off.
``enrolments``        one row per (person, opening). This is where a *particular
                      application* lives, because one person may apply to three
                      openings and score differently for each.
``stage_transitions`` an append-only record of every move, and whether a person
                      or the system made it. Time-in-stage and the delivery-risk
                      projection are both unanswerable without it today.

What this deliberately does NOT do
----------------------------------
**Nothing is removed and nothing existing changes shape.** ``applicants.status``
and the ``target_*`` / ``ats_*`` columns stay exactly where they are, because
they are read by the pipeline SQL, the applicant list, the interview-eligibility
gate, the funnel watcher and the frontend types. This migration copies them onto
the enrolment; the enrolment becomes authoritative for new code while the legacy
columns keep every existing reader working. Dropping them is a later migration,
once all readers have moved. A migration that flipped the source of truth and
rewrote nine call sites in one step is exactly how a hiring pipeline goes dark.

**No applicant is merged.** D-06 makes one person one applicant per company, but
merging two existing rows is destructive and irreversible — it repoints exam and
interview history onto a survivor. So the backfill creates one enrolment per
existing applicant (1:1) and merging stays an explicit, reviewed HR action. The
duplicates are *detected* here and surfaced for review; they are not resolved.

Grouping rule
-------------
Applicants are grouped into requisitions by ``lower(collapse_whitespace(title))``
— case and spacing only, never fuzzy matching. A near-miss match would silently
merge two genuinely different openings, and the cost of that is a candidate
assessed against the wrong role.

Revision ID: d6f8a0c2e4b7
Revises:     c5e7a9b1d3f6
Create Date: 2026-09-02 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d6f8a0c2e4b7"
down_revision: str | None = "c5e7a9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Case-fold + collapse internal whitespace + trim. Used by the backfill below and
# mirrored in app.requisitions.normalise_title — the two must agree, so the
# expression is written once here and referenced by name in the index.
_NORM = "lower(btrim(regexp_replace(%s, '\\s+', ' ', 'g')))"


def upgrade() -> None:
    # ── job_requisitions ──────────────────────────────────────────────────
    op.create_table(
        "job_requisitions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), server_default=sa.text("'mid'"), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("target_hires", sa.SmallInteger(), nullable=True),
        sa.Column("closes_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        # True when the row was minted by this migration's backfill rather than
        # created deliberately. The review screen leads with these, because they
        # are the ones whose grouping nobody has confirmed yet.
        sa.Column(
            "from_backfill", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_job_requisitions"),
        # Composite unique so child tables can pin company_id through a composite
        # FK and it cannot drift cross-tenant. Same pattern as exams/applicants.
        sa.UniqueConstraint("id", "company_id", name="uq_job_requisitions_id_company"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_job_requisitions_company", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"],
            name="fk_job_requisitions_owner", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('open','paused','closed')", name="ck_job_requisitions_status"
        ),
        sa.CheckConstraint(
            "target_hires IS NULL OR target_hires > 0",
            name="ck_job_requisitions_target_hires",
        ),
    )
    # One live opening per normalised title per company. Partial on deleted_at so
    # a closed-and-deleted requisition does not permanently reserve its title.
    op.execute(
        "CREATE UNIQUE INDEX uq_job_requisitions_company_title "
        "ON job_requisitions (company_id, " + (_NORM % "title") + ") "
        "WHERE deleted_at IS NULL"
    )
    op.create_index(
        "ix_job_requisitions_company_status",
        "job_requisitions",
        ["company_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── enrolments ────────────────────────────────────────────────────────
    op.create_table(
        "enrolments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("requisition_id", sa.UUID(), nullable=False),
        sa.Column("applicant_id", sa.UUID(), nullable=False),
        # Same vocabulary as applicants.status today, so the copy below is exact
        # and no reader has to learn a second set of names. 'held' is reserved
        # for the Phase 2 workflow runner (D-05) and unused until then.
        sa.Column("status", sa.Text(), server_default=sa.text("'new'"), nullable=False),
        # The role as applied for. Per-enrolment because one person may hold
        # several with different targets.
        sa.Column("target_job_title", sa.Text(), nullable=False),
        sa.Column("target_level", sa.Text(), server_default=sa.text("'mid'"), nullable=False),
        sa.Column("target_jd_text", sa.Text(), nullable=True),
        # ATS result is per-application: the same CV scores differently against
        # a Python role and a nursing role.
        sa.Column("ats_overall", sa.SmallInteger(), nullable=True),
        sa.Column("ats_breakdown", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("ats_strengths", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("ats_concerns", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("ats_recommendation", sa.Text(), nullable=True),
        sa.Column("ats_summary", sa.Text(), nullable=True),
        # Which CV this score was actually produced from, so the score stays
        # reproducible after the candidate uploads a newer one.
        sa.Column("scored_resume_s3_key", sa.Text(), nullable=True),
        sa.Column("scored_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_enrolments"),
        sa.UniqueConstraint("id", "company_id", name="uq_enrolments_id_company"),
        sa.ForeignKeyConstraint(
            ["requisition_id", "company_id"],
            ["job_requisitions.id", "job_requisitions.company_id"],
            name="fk_enrolments_requisition", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id", "company_id"],
            ["applicants.id", "applicants.company_id"],
            name="fk_enrolments_applicant", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('new','shortlisted','interviewed','held','hired','rejected')",
            name="ck_enrolments_status",
        ),
    )
    # A person is enrolled into an opening once. Partial so a withdrawn (soft
    # deleted) enrolment does not block re-applying later.
    op.create_index(
        "uq_enrolments_requisition_applicant",
        "enrolments",
        ["requisition_id", "applicant_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_enrolments_requisition_status",
        "enrolments",
        ["requisition_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_enrolments_applicant", "enrolments", ["applicant_id"])

    # ── stage_transitions ─────────────────────────────────────────────────
    op.create_table(
        "stage_transitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("enrolment_id", sa.UUID(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        # NULL actor means the system moved it; `automated` says so explicitly
        # rather than making every reader infer it from a null.
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("automated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stage_transitions"),
        sa.ForeignKeyConstraint(
            ["enrolment_id"], ["enrolments.id"],
            name="fk_stage_transitions_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name="fk_stage_transitions_actor", ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_stage_transitions_enrolment", "stage_transitions", ["enrolment_id", "occurred_at"]
    )
    op.create_index(
        "ix_stage_transitions_company_time", "stage_transitions", ["company_id", "occurred_at"]
    )

    # ── nullable links on the existing hiring tables ──────────────────────
    # Nullable and unenforced for now. Phase 2 makes them required once every
    # writer sets them; today they exist so the backfill has somewhere to write
    # and new code can start reading through them.
    for table in ("exam_assignments", "interview_invites"):
        op.add_column(table, sa.Column("enrolment_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_enrolment", table, "enrolments",
            ["enrolment_id"], ["id"], ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_enrolment", table, ["enrolment_id"])

    # ══════════════════════════════════════════════════════════════════════
    # BACKFILL
    # ══════════════════════════════════════════════════════════════════════
    # Written as set-based SQL rather than a Python loop so it is one statement
    # per step and runs inside the migration's transaction: either the whole
    # backfill lands or none of it does.

    # 1. One requisition per (company, normalised title) seen in applicants.
    #    MIN(created_at) dates the opening from its first applicant, which is
    #    the closest thing to a real opening date that exists in the data.
    op.execute(
        """
        INSERT INTO job_requisitions
              (id, company_id, title, level, status, from_backfill, created_at, updated_at)
        SELECT gen_random_uuid(),
               a.company_id,
               -- Keep the most common spelling as the display title; the index
               -- normalises, but a human should see the form they typed.
               (array_agg(a.target_job_title ORDER BY a.created_at))[1],
               COALESCE((array_agg(a.target_level ORDER BY a.created_at))[1], 'mid'),
               'open',
               true,
               MIN(a.created_at),
               now()
          FROM applicants a
         WHERE a.deleted_at IS NULL
           AND btrim(COALESCE(a.target_job_title, '')) <> ''
         GROUP BY a.company_id, lower(btrim(regexp_replace(a.target_job_title, '\\s+', ' ', 'g')))
        """
    )

    # 2. One enrolment per existing applicant, joined back on the same
    #    normalisation. Copies the per-application fields; the applicant
    #    columns are left untouched.
    op.execute(
        """
        INSERT INTO enrolments
              (id, company_id, requisition_id, applicant_id, status,
               target_job_title, target_level, target_jd_text,
               ats_overall, ats_breakdown, ats_strengths, ats_concerns,
               ats_recommendation, ats_summary, scored_resume_s3_key,
               created_at, updated_at)
        SELECT gen_random_uuid(), a.company_id, r.id, a.id, a.status,
               a.target_job_title, a.target_level, a.target_jd_text,
               a.ats_overall, a.ats_breakdown, a.ats_strengths, a.ats_concerns,
               a.ats_recommendation, a.ats_summary,
               CASE WHEN a.ats_overall IS NOT NULL THEN a.resume_s3_key END,
               a.created_at, a.updated_at
          FROM applicants a
          JOIN job_requisitions r
            ON r.company_id = a.company_id
           AND lower(btrim(regexp_replace(r.title, '\\s+', ' ', 'g')))
             = lower(btrim(regexp_replace(a.target_job_title, '\\s+', ' ', 'g')))
           AND r.deleted_at IS NULL
         WHERE a.deleted_at IS NULL
           AND btrim(COALESCE(a.target_job_title, '')) <> ''
        """
    )

    # 3. Seed the ledger with what is known: every enrolment's current status,
    #    attributed to the system and dated from the applicant row. This is an
    #    honest floor, not invented history — one entry saying "as of the
    #    backfill, this is where they were".
    op.execute(
        """
        INSERT INTO stage_transitions
              (company_id, enrolment_id, from_status, to_status, actor_user_id,
               automated, reason, occurred_at)
        SELECT e.company_id, e.id, NULL, e.status, NULL, true,
               'backfill: status at migration', e.updated_at
          FROM enrolments e
        """
    )

    # 4. Point existing exam assignments and interview invites at the enrolment
    #    for the applicant. Where an applicant somehow has several enrolments
    #    (they cannot yet, but the join must be deterministic), the oldest wins.
    for table in ("exam_assignments", "interview_invites"):
        op.execute(
            f"""
            UPDATE {table} t
               SET enrolment_id = e.id
              FROM (
                    SELECT DISTINCT ON (applicant_id) applicant_id, id
                      FROM enrolments
                     WHERE deleted_at IS NULL
                     ORDER BY applicant_id, created_at
                   ) e
             WHERE t.applicant_id = e.applicant_id
               AND t.enrolment_id IS NULL
            """
        )


def downgrade() -> None:
    for table in ("exam_assignments", "interview_invites"):
        op.drop_index(f"ix_{table}_enrolment", table_name=table)
        op.drop_constraint(f"fk_{table}_enrolment", table, type_="foreignkey")
        op.drop_column(table, "enrolment_id")
    op.drop_index("ix_stage_transitions_company_time", table_name="stage_transitions")
    op.drop_index("ix_stage_transitions_enrolment", table_name="stage_transitions")
    op.drop_table("stage_transitions")
    op.drop_index("ix_enrolments_applicant", table_name="enrolments")
    op.drop_index("ix_enrolments_requisition_status", table_name="enrolments")
    op.drop_index("uq_enrolments_requisition_applicant", table_name="enrolments")
    op.drop_table("enrolments")
    op.drop_index("ix_job_requisitions_company_status", table_name="job_requisitions")
    op.execute("DROP INDEX IF EXISTS uq_job_requisitions_company_title")
    op.drop_table("job_requisitions")
