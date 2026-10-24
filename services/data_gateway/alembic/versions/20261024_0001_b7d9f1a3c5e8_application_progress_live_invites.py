"""application_progress: an invite counts as active only while it can be used

Revision ID: b7d9f1a3c5e8
Revises: a2c4e6f8b0d3
Create Date: 2026-10-24

Re-audit finding (interviews, has_active_invite). The view computed
``has_active_invite`` as ``status IN ('invited', 'consumed')``. Nothing sweeps
an 'invited' row to 'expired' once its link lapses, and a consumed invite stays
'consumed' after the interview is scored, so the flag stayed true for:

  * a link past its ``expires_at`` (the candidate cannot use it);
  * an interview the candidate has already finished.

The HR invite form then labelled those candidates "(already invited)" and told
HR the old link would stop working, which was false; and the copilot refused to
draft an invite for them.

The flag now matches what POST /hr/interviews itself treats as live:

  * ``has_active_invite``: an unexpired 'invited' link, or an unexpired
    'consumed' one whose interview is not over (no scorecard, and the session
    not marked completed). 'abandoned' is not over: a dropped call stays
    reconnectable, as in ``_interview_finished``.
  * ``has_started_invite`` (new): the second half of that, alone. The server
    refuses to replace a started interview (409), so the form shows "end the
    started interview first" for it instead of offering a replacement.

Both use ``now()``, which a view evaluates per query, so an invite drops out
the moment it expires with no sweep needed.

A new column cannot be inserted mid-list with CREATE OR REPLACE VIEW, and the
definition is the single source the readers share, so this drops and recreates
the view in one transaction. Nothing else is defined on top of the view. No
rows are written; the downgrade restores the previous definition verbatim.
"""

from __future__ import annotations

from alembic import op

revision: str = "b7d9f1a3c5e8"
down_revision: str | None = "a2c4e6f8b0d3"
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
       COALESCE(inv.has_started_invite, false)         AS has_started_invite,
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
             bool_or(i.expires_at > now()
                     AND (i.status = 'invited'
                          OR (i.status = 'consumed' AND NOT (
                                i.session_id IS NOT NULL
                                AND (EXISTS (SELECT 1 FROM scorecards f
                                              WHERE f.session_id = i.session_id)
                                     OR EXISTS (SELECT 1 FROM sessions s
                                                 WHERE s.id = i.session_id
                                                   AND s.status = 'completed'))))))
                                                                       AS has_active_invite,
             bool_or(i.expires_at > now()
                     AND i.status = 'consumed' AND NOT (
                           i.session_id IS NOT NULL
                           AND (EXISTS (SELECT 1 FROM scorecards f
                                         WHERE f.session_id = i.session_id)
                                OR EXISTS (SELECT 1 FROM sessions s
                                            WHERE s.id = i.session_id
                                              AND s.status = 'completed'))))
                                                                       AS has_started_invite
        FROM interview_invites i
       WHERE i.applicant_id = p.applicant_id AND i.company_id = p.company_id
         AND i.deleted_at IS NULL AND i.status <> 'revoked'
         AND (i.enrolment_id = p.enrolment_id
              OR (i.enrolment_id IS NULL AND p.live_applications <= 1))
  ) inv ON TRUE
"""

# The definition this migration replaces (e2a4c6b8d0f1), kept verbatim for the
# downgrade. Migrations are immutable once merged, so it is copied, not imported.
PREVIOUS_VIEW_SQL = """
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
    op.execute("DROP VIEW IF EXISTS application_progress")
    op.execute(VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS application_progress")
    op.execute(PREVIOUS_VIEW_SQL)
