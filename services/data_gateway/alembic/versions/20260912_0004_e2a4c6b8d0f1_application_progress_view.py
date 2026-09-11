"""application_progress — one row per application, the single definition (B5).

Revision ID: e2a4c6b8d0f1
Revises: d1f3a5b7c9e2
Create Date: 2026-09-12

The pipeline board, the analytics funnel, the copilot's pipeline tools and the
watchers each carried their own copy of "where is this candidate", and every
copy was per PERSON: applicants.status, applicants.ats_overall, the latest exam
attempt and interview anywhere. Since D-06 a person can hold several
applications, so a Python shortlist and a nursing rejection collapsed into
whichever one wrote the applicant row last, and an exam taken for one opening
showed up as progress on all of them.

This view is the one place that answers the question per APPLICATION. Every
reader selects from it, so the board and the copilot cannot disagree about a
candidate — which was the reason tools.py already mirrored the board's SQL by
hand, and the thing a hand-kept mirror eventually gets wrong.

Rows:
  * one per live enrolment;
  * one per live applicant with NO live enrolment (enrolment_id NULL), so a
    person nobody filed under an opening is still on the board rather than
    silently missing from it.

Attributing exams and interviews to an application:
  * an exam attempt belongs to its assignment's enrolment; an invite to its own
    enrolment_id;
  * activity recorded with no enrolment (manual actions before this change)
    counts for the application only when the person has at most one — with
    several, crediting it to all of them would be exactly the bleed this view
    exists to stop, and picking one would be a guess.

Changing a column this view reads means DROP VIEW / CREATE VIEW in that
migration; Postgres refuses the ALTER otherwise, which is the reminder.
"""

from __future__ import annotations

from alembic import op

revision: str = "e2a4c6b8d0f1"
down_revision: str | None = "d1f3a5b7c9e2"
branch_labels = None
depends_on = None

VIEW_SQL = """
CREATE VIEW application_progress AS
WITH apps AS (
    SELECT a.id                                        AS applicant_id,
           a.company_id,
           a.full_name,
           e.id                                        AS enrolment_id,
           e.requisition_id,
           COALESCE(r.title, e.target_job_title, a.target_job_title) AS opening_title,
           COALESCE(e.target_job_title, a.target_job_title)          AS target_job_title,
           COALESCE(e.target_level, a.target_level)                  AS target_level,
           CASE WHEN e.id IS NULL THEN a.ats_overall ELSE e.ats_overall END
                                                       AS ats_overall,
           CASE WHEN e.id IS NULL THEN a.ats_recommendation ELSE e.ats_recommendation END
                                                       AS ats_recommendation,
           COALESCE(e.status, a.status)                AS stored_status,
           COALESCE(e.created_at, a.created_at)        AS applied_at,
           COALESCE(e.updated_at, a.updated_at)        AS updated_at,
           (SELECT count(*) FROM enrolments n
             WHERE n.applicant_id = a.id AND n.deleted_at IS NULL) AS live_applications
      FROM applicants a
      LEFT JOIN enrolments e
             ON e.applicant_id = a.id AND e.deleted_at IS NULL
      LEFT JOIN job_requisitions r ON r.id = e.requisition_id
     WHERE a.deleted_at IS NULL
)
SELECT p.applicant_id, p.company_id, p.full_name, p.enrolment_id, p.requisition_id,
       p.opening_title, p.target_job_title, p.target_level,
       p.ats_overall, p.ats_recommendation, p.stored_status,
       p.applied_at, p.updated_at, p.live_applications,
       ex.best_exam_percent,
       ex.exam_passed,
       COALESCE(ex.total_exam_attempts, 0)             AS total_exam_attempts,
       iv.interview_status,
       sc.composite_score                              AS interview_score,
       sc.scorecard_id,
       COALESCE(inv.ever_invited, false)               AS ever_invited,
       COALESCE(inv.has_active_invite, false)          AS has_active_invite,
       CASE
         WHEN p.stored_status IN ('hired', 'rejected') THEN p.stored_status
         WHEN sc.scorecard_id IS NOT NULL AND p.stored_status IN ('new', 'shortlisted')
              THEN 'interviewed'
         ELSE p.stored_status
       END                                             AS status
  FROM apps p
  LEFT JOIN LATERAL (
      SELECT max(t.score_percent)  AS best_exam_percent,
             bool_or(t.passed)     AS exam_passed,
             count(*)              AS total_exam_attempts
        FROM exam_attempts t
        LEFT JOIN exam_assignments g ON g.id = t.assignment_id
       WHERE t.applicant_id = p.applicant_id AND t.company_id = p.company_id
         AND t.status = 'submitted' AND t.deleted_at IS NULL
         AND (g.enrolment_id = p.enrolment_id
              OR (g.enrolment_id IS NULL AND p.live_applications <= 1))
  ) ex ON TRUE
  LEFT JOIN LATERAL (
      SELECT i.status AS interview_status, i.session_id
        FROM interview_invites i
       WHERE i.applicant_id = p.applicant_id AND i.company_id = p.company_id
         AND i.deleted_at IS NULL AND i.session_id IS NOT NULL
         AND (i.enrolment_id = p.enrolment_id
              OR (i.enrolment_id IS NULL AND p.live_applications <= 1))
       ORDER BY i.created_at DESC
       LIMIT 1
  ) iv ON TRUE
  LEFT JOIN scorecards sc ON sc.session_id = iv.session_id
  LEFT JOIN LATERAL (
      SELECT bool_or(i.status IN ('invited', 'consumed', 'completed')) AS ever_invited,
             bool_or(i.status IN ('invited', 'consumed'))              AS has_active_invite
        FROM interview_invites i
       WHERE i.applicant_id = p.applicant_id AND i.company_id = p.company_id
         AND i.deleted_at IS NULL AND i.status <> 'revoked'
         AND (i.enrolment_id = p.enrolment_id
              OR (i.enrolment_id IS NULL AND p.live_applications <= 1))
  ) inv ON TRUE
"""


def upgrade() -> None:
    op.execute(VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS application_progress")
