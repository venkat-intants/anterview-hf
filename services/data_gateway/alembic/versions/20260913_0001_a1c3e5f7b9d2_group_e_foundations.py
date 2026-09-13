"""Group E foundations: who is awaiting a person, since when, and on which CV.

Revision ID: a1c3e5f7b9d2
Revises: f3b5d7a9c1e4
Create Date: 2026-09-13

Three things every Group E screen reads, each of which had more than one
answer before this.

1. ``enrolment_awaits_human(status, current_round_id)``. "Awaiting a decision"
   was defined three ways — the requisition counts took every non-terminal
   enrolment, the watcher took held-or-no-round, the decision queue added
   human-review rounds — and the watcher's comment claimed it matched the queue.
   All three also counted applicants nobody had shortlisted yet as "finished the
   workflow". It is now one function, and the queue, the watcher and the counts
   all call it:

     * held — below a threshold, waiting for a person (D-05);
     * finished — 'interviewed' with no current round, which is what the runner
       writes when the last round completes;
     * on a human_review round — the round IS a person's decision.

   Deliberately NOT included: 'new' and 'shortlisted' candidates with no round.
   They are waiting for a shortlist or for a workflow, not for a final decision.

2. ``enrolment_state_since(enrolment_id, fallback)``. Wait times were measured
   from ``updated_at``, which any rescore or edit resets, so a candidate
   rescored yesterday after three weeks in a round read as one day. The stage
   ledger is append-only and records every status and round move, so the last
   entry is when the candidate actually got where they are.

3. ``enrolments.applied_resume_s3_key`` — the CV an application was submitted
   with. Only ``scored_resume_s3_key`` existed, written once a score exists, so
   an application still waiting for the reconciler had no record of its CV. A
   person who re-applied elsewhere before the first was scored had that first
   application scored against the newer CV, and the older file could be deleted
   as unreferenced. Backfilled from the scored key where there is one, otherwise
   the applicant's current CV — the best record available for old rows.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: str | None = "f3b5d7a9c1e4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "enrolments", sa.Column("applied_resume_s3_key", sa.Text(), nullable=True)
    )
    op.execute(
        """
        UPDATE enrolments e
           SET applied_resume_s3_key = COALESCE(e.scored_resume_s3_key, a.resume_s3_key)
          FROM applicants a
         WHERE a.id = e.applicant_id
           AND e.applied_resume_s3_key IS NULL
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enrolment_awaits_human(p_status text, p_round_id uuid)
        RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT p_status NOT IN ('hired', 'rejected')
               AND (   p_status = 'held'
                    OR (p_round_id IS NULL AND p_status = 'interviewed')
                    OR EXISTS (SELECT 1 FROM workflow_rounds wr
                                WHERE wr.id = p_round_id
                                  AND wr.kind = 'human_review'
                                  AND wr.deleted_at IS NULL))
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enrolment_state_since(
            p_enrolment_id uuid, p_fallback timestamptz
        )
        RETURNS timestamptz
        LANGUAGE sql STABLE AS $$
            SELECT COALESCE(
                (SELECT max(st.occurred_at) FROM stage_transitions st
                  WHERE st.enrolment_id = p_enrolment_id),
                p_fallback)
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS enrolment_state_since(uuid, timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS enrolment_awaits_human(text, uuid)")
    op.drop_column("enrolments", "applied_resume_s3_key")
