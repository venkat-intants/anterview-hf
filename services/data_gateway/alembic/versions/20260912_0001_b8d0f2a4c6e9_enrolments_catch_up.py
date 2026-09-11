"""Catch-up: every applicant filed under an opening, every score on its enrolment.

The Group B migration (d6f8a0c2e4b7) created one enrolment per applicant that
existed at the time. Two things kept happening afterwards that it could not see:

1. **HR uploads created applicants without an enrolment.** Only the public apply
   form enrolled anyone, so every candidate HR uploaded — single or bulk — since
   that migration is invisible to the requisition dashboard, the decision queue
   and the ledger-based analytics. The upload paths now file every applicant
   under an opening; this files the ones that arrived in between, by exactly the
   grouping rule the first backfill used (case and whitespace only, never fuzzy).
   Openings minted here are ``from_backfill`` and land in HR's review screen,
   because nobody has confirmed them.

2. **No score ever reached an enrolment.** Every scoring path wrote the applicant
   row, so each enrolment created since has no ATS result while the dashboard and
   decision queue sort on it. Scoring now writes the enrolment. For the ones in
   between, the applicant's score is copied across ONLY where it provably
   belongs to that application: the enrolment is the applicant's latest, and
   the applicant's target title normalises to the enrolment's. Anything else is
   left NULL for the reconciler to score against the right role — a copied
   score for the wrong job would be worse than none.

Nothing is deleted or moved; applicant rows are untouched. The downgrade is a
no-op: removing rows that HR may by now have reviewed, split or acted on is not
an undo, it is data loss.
"""

from __future__ import annotations

from alembic import op

revision: str = "b8d0f2a4c6e9"
down_revision: str | None = "a7c9e1b3d5f8"
branch_labels: str | None = None
depends_on: str | None = None

# Must match the partial unique index and app.requisitions.normalise_title.
_NORM_A = "lower(btrim(regexp_replace(a.target_job_title, '\\s+', ' ', 'g')))"
_NORM_R = "lower(btrim(regexp_replace(r.title, '\\s+', ' ', 'g')))"
_NORM_E = "lower(btrim(regexp_replace(e.target_job_title, '\\s+', ' ', 'g')))"

# Applicants with a title and no live enrolment.
_ORPHANS = """
      FROM applicants a
     WHERE a.deleted_at IS NULL
       AND btrim(COALESCE(a.target_job_title, '')) <> ''
       AND NOT EXISTS (SELECT 1 FROM enrolments x
                        WHERE x.applicant_id = a.id AND x.deleted_at IS NULL)
"""


def upgrade() -> None:
    # 1. An opening for each title those applicants carry that has none yet.
    op.execute(
        f"""
        INSERT INTO job_requisitions
              (id, company_id, title, level, status, from_backfill, created_at, updated_at)
        SELECT gen_random_uuid(), a.company_id,
               (array_agg(a.target_job_title ORDER BY a.created_at))[1],
               COALESCE((array_agg(a.target_level ORDER BY a.created_at))[1], 'mid'),
               'open', true, MIN(a.created_at), now()
        {_ORPHANS}
           AND NOT EXISTS (SELECT 1 FROM job_requisitions r
                            WHERE r.company_id = a.company_id AND r.deleted_at IS NULL
                              AND {_NORM_R} = {_NORM_A})
         GROUP BY a.company_id, {_NORM_A}
        """
    )

    # 2. Their enrolments, copying the per-application fields the same way the
    #    first backfill did.
    op.execute(
        f"""
        INSERT INTO enrolments
              (id, company_id, requisition_id, applicant_id, status,
               target_job_title, target_level, target_jd_text,
               ats_overall, ats_breakdown, ats_strengths, ats_concerns,
               ats_recommendation, ats_summary, scored_resume_s3_key, scored_at,
               created_at, updated_at)
        SELECT gen_random_uuid(), a.company_id, r.id, a.id, a.status,
               a.target_job_title, a.target_level, a.target_jd_text,
               a.ats_overall, a.ats_breakdown, a.ats_strengths, a.ats_concerns,
               a.ats_recommendation, a.ats_summary,
               CASE WHEN a.ats_overall IS NOT NULL THEN a.resume_s3_key END,
               CASE WHEN a.ats_overall IS NOT NULL THEN a.updated_at END,
               a.created_at, a.updated_at
          FROM applicants a
          JOIN job_requisitions r
            ON r.company_id = a.company_id AND r.deleted_at IS NULL
           AND {_NORM_R} = {_NORM_A}
         WHERE a.deleted_at IS NULL
           AND btrim(COALESCE(a.target_job_title, '')) <> ''
           AND NOT EXISTS (SELECT 1 FROM enrolments x
                            WHERE x.applicant_id = a.id AND x.deleted_at IS NULL)
        """
    )

    # 3. A ledger floor for every enrolment that has no history at all — the
    #    ones just created, and any other that slipped through.
    op.execute(
        """
        INSERT INTO stage_transitions
              (company_id, enrolment_id, from_status, to_status, actor_user_id,
               automated, reason, occurred_at)
        SELECT e.company_id, e.id, NULL, e.status, NULL, true,
               'backfill: status at migration (catch-up)', e.updated_at
          FROM enrolments e
         WHERE NOT EXISTS (SELECT 1 FROM stage_transitions t WHERE t.enrolment_id = e.id)
        """
    )

    # 4. Exam and interview history with no enrolment gets the applicant's
    #    oldest, the same deterministic rule as the first backfill.
    for table in ("exam_assignments", "interview_invites"):
        op.execute(
            f"""
            UPDATE {table} t
               SET enrolment_id = e.id
              FROM (SELECT DISTINCT ON (applicant_id) applicant_id, id
                      FROM enrolments WHERE deleted_at IS NULL
                     ORDER BY applicant_id, created_at) e
             WHERE t.applicant_id = e.applicant_id AND t.enrolment_id IS NULL
            """
        )

    # 5. Scores that belong to an enrolment but only ever reached the applicant.
    op.execute(
        f"""
        UPDATE enrolments e
           SET ats_overall = a.ats_overall, ats_breakdown = a.ats_breakdown,
               ats_strengths = a.ats_strengths, ats_concerns = a.ats_concerns,
               ats_recommendation = a.ats_recommendation, ats_summary = a.ats_summary,
               scored_resume_s3_key = a.resume_s3_key, scored_at = a.updated_at
          FROM applicants a
         WHERE a.id = e.applicant_id
           AND e.deleted_at IS NULL AND e.ats_overall IS NULL
           AND a.ats_overall IS NOT NULL
           AND {_NORM_A} = {_NORM_E}
           AND NOT EXISTS (SELECT 1 FROM enrolments newer
                            WHERE newer.applicant_id = e.applicant_id
                              AND newer.deleted_at IS NULL
                              AND newer.created_at > e.created_at)
        """
    )


def downgrade() -> None:
    # Deliberately nothing — see the module docstring.
    pass
