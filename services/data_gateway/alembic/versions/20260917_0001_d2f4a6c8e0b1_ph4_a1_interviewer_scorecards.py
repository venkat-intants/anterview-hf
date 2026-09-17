"""The interviewer role and human interview scorecards — PH4-A1.

Revision ID: d2f4a6c8e0b1
Revises: c9e1b3d5f7a2
Create Date: 2026-09-17

WHAT A HUMAN ROUND COULD RECORD BEFORE THIS
A ``human_review`` round took one click from any HR manager — pass, or hold —
plus a free-text note, written into ``round_results`` with ``passed_override``.
No assigned reviewer, no per-criterion score, nothing to say which of three
interviewers thought what. The frozen ``round_criteria`` a round was published
with were shown as a checklist and then ignored.

THE ROLE (decision D4-1)
``interviewer`` is company staff who sees ONLY the interviews assigned to them.
It is a new role rather than "an HR manager with a flag" because every HR
screen is company-wide: an interviewer who could open /hr would see every
candidate the company has, and "an interviewer sees assigned candidates" would
be a UI convention rather than a rule. HR managers may also be assigned.

THE TABLES
- ``interviewer_scorecards`` — one per (round, enrolment, interviewer), and it
  IS the assignment: created ``assigned``, becomes ``in_progress`` on the first
  draft save, ``submitted`` on submit. Late is DERIVED from ``due_at`` at read
  time rather than stored, so it can never be stale.
- ``interviewer_scorecard_scores`` — one row per criterion. Its composite
  foreign key to ``round_criteria (round_id, competency_id)`` is what makes
  "scorecard criteria come from the frozen round criteria" true at the database:
  a score for a competency the round was not published with cannot be written.
- ``interviewer_notes`` — the interviewer's own working notes. Deliberately a
  separate table: notes are not the submission (PH4-A5), are never shown to HR,
  and are erased outright under DPDP.

IMMUTABILITY — THE SAME PLACE THE OTHER PROMISES HOLD
The published-workflow and stage-ledger guarantees live in triggers, because the
application is where a rule is explained and the database is where it holds. A
submitted scorecard is decision evidence, so it gets the same treatment. Exactly
two changes are permitted after submission, and both are explicit:

1. SUPERSESSION — the audited correction path. The old row is marked superseded
   (once, irreversibly) and a new draft is opened that points back at it. The
   original scores are never overwritten; both versions stay readable.
2. REDACTION — DPDP erasure. Free-text ``summary`` and per-criterion ``evidence``
   may be set to NULL, and only together with ``redacted_at``. Scores and
   competency ids are kept, on the ``round_results`` precedent: numbers against
   an anonymised applicant are the company's evaluation record, whereas prose an
   interviewer wrote can quote the candidate and re-identify them.

Anything else — a changed score, a revived withdrawal, a rewritten summary — is
refused with an exception that names the scorecard.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d2f4a6c8e0b1"
down_revision: str | None = "c9e1b3d5f7a2"
branch_labels: str | None = None
depends_on: str | None = None


SCORECARD_TRIGGER = """
CREATE OR REPLACE FUNCTION interviewer_scorecards_protect() RETURNS trigger AS $$
DECLARE
    locked_keys text[] := ARRAY['superseded_at', 'superseded_by_id',
                                'summary', 'redacted_at', 'updated_at'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- A submitted scorecard is only removed by the cascade that follows its
        -- application out of existence, never on its own.
        IF OLD.status = 'submitted'
           AND EXISTS (SELECT 1 FROM enrolments e WHERE e.id = OLD.enrolment_id) THEN
            RAISE EXCEPTION
                'scorecard % is submitted decision evidence and cannot be deleted', OLD.id;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status IN ('submitted', 'withdrawn') THEN
        IF (to_jsonb(NEW) - locked_keys) IS DISTINCT FROM (to_jsonb(OLD) - locked_keys) THEN
            RAISE EXCEPTION
                'scorecard % is % and cannot be edited; open a correction instead',
                OLD.id, OLD.status;
        END IF;
        -- Supersession happens once and is never undone.
        IF OLD.superseded_at IS NOT NULL
           AND (NEW.superseded_at IS DISTINCT FROM OLD.superseded_at
                OR NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id) THEN
            RAISE EXCEPTION 'scorecard % is already superseded', OLD.id;
        END IF;
        IF NEW.superseded_at IS NOT NULL AND OLD.status <> 'submitted' THEN
            RAISE EXCEPTION 'only a submitted scorecard can be superseded (% is %)',
                OLD.id, OLD.status;
        END IF;
        -- The summary may only ever be erased, and erasure is marked.
        IF NEW.summary IS DISTINCT FROM OLD.summary
           AND NOT (NEW.summary IS NULL AND NEW.redacted_at IS NOT NULL) THEN
            RAISE EXCEPTION
                'scorecard % summary can only be redacted, not rewritten', OLD.id;
        END IF;
        IF OLD.redacted_at IS NOT NULL AND NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
            RAISE EXCEPTION 'scorecard % redaction cannot be reversed', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

SCORECARD_TRIGGER_HOOK = """
CREATE TRIGGER interviewer_scorecards_protect
    BEFORE UPDATE OR DELETE ON interviewer_scorecards
    FOR EACH ROW EXECUTE FUNCTION interviewer_scorecards_protect()
"""

SCORES_TRIGGER = """
CREATE OR REPLACE FUNCTION interviewer_scorecard_scores_protect() RETURNS trigger AS $$
DECLARE
    parent_status text;
BEGIN
    SELECT s.status INTO parent_status FROM interviewer_scorecards s
     WHERE s.id = COALESCE(NEW.scorecard_id, OLD.scorecard_id);

    -- No parent: this is the cascade after the scorecard itself went.
    IF parent_status IS NULL OR parent_status NOT IN ('submitted', 'withdrawn') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    IF TG_OP IN ('INSERT', 'DELETE') THEN
        RAISE EXCEPTION
            'scorecard % is %; its scores cannot change',
            COALESCE(NEW.scorecard_id, OLD.scorecard_id), parent_status;
    END IF;

    -- UPDATE: only redaction of the evidence text.
    IF (to_jsonb(NEW) - 'evidence' - 'updated_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'evidence' - 'updated_at') THEN
        RAISE EXCEPTION
            'scorecard % is %; its scores cannot change', OLD.scorecard_id, parent_status;
    END IF;
    IF NEW.evidence IS DISTINCT FROM OLD.evidence AND NEW.evidence IS NOT NULL THEN
        RAISE EXCEPTION
            'scorecard % evidence can only be redacted, not rewritten', OLD.scorecard_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

SCORES_TRIGGER_HOOK = """
CREATE TRIGGER interviewer_scorecard_scores_protect
    BEFORE INSERT OR UPDATE OR DELETE ON interviewer_scorecard_scores
    FOR EACH ROW EXECUTE FUNCTION interviewer_scorecard_scores_protect()
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO roles (name, description) VALUES "
            "('interviewer', 'Company staff who conduct human interviews and see "
            "only the interviews assigned to them') "
            "ON CONFLICT (name) DO NOTHING"
        )
    )

    op.create_table(
        "interviewer_scorecards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="assigned"),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        # The correction chain: the NEW draft points at what it corrects, and
        # the old row records what superseded it.
        sa.Column("corrects_id", sa.Uuid(), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.Column("superseded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("redacted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interviewer_scorecards"),
        sa.UniqueConstraint("id", "company_id", name="uq_interviewer_scorecards_id_company"),
        # Lets the scores table prove a score belongs to its scorecard's round.
        sa.UniqueConstraint("id", "round_id", name="uq_interviewer_scorecards_id_round"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_interviewer_scorecards_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_interviewer_scorecards_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"],
            ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_interviewer_scorecards_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interviewer_user_id"], ["users.id"],
            name="fk_interviewer_scorecards_interviewer",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"],
            name="fk_interviewer_scorecards_assigned_by", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["corrects_id"], ["interviewer_scorecards.id"],
            name="fk_interviewer_scorecards_corrects",
        ),
        # DEFERRED, and it has to be. A correction marks the old row superseded
        # BY a new draft, and inserts that draft — but the live unique index
        # refuses the new row while the old one is still live, and an immediate
        # foreign key refuses pointing at a row that does not exist yet. Each
        # order breaks one of the two. Checked at commit, both hold.
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["interviewer_scorecards.id"],
            name="fk_interviewer_scorecards_superseded_by",
            deferrable=True, initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "status IN ('assigned', 'in_progress', 'submitted', 'withdrawn')",
            name="ck_interviewer_scorecards_status",
        ),
        sa.CheckConstraint(
            "(status = 'submitted') = (submitted_at IS NOT NULL)",
            name="ck_interviewer_scorecards_submitted_at",
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name="ck_interviewer_scorecards_withdrawn_at",
        ),
        sa.CheckConstraint(
            "superseded_at IS NULL OR status = 'submitted'",
            name="ck_interviewer_scorecards_superseded_submitted",
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by_id IS NULL)",
            name="ck_interviewer_scorecards_superseded_pair",
        ),
        sa.CheckConstraint(
            "(corrects_id IS NULL) = (correction_reason IS NULL)",
            name="ck_interviewer_scorecards_correction_pair",
        ),
        sa.CheckConstraint(
            "summary IS NULL OR char_length(summary) <= 4000",
            name="ck_interviewer_scorecards_summary_len",
        ),
        sa.CheckConstraint(
            "correction_reason IS NULL OR char_length(correction_reason) BETWEEN 10 AND 1000",
            name="ck_interviewer_scorecards_correction_reason_len",
        ),
        sa.CheckConstraint(
            "withdrawn_reason IS NULL OR char_length(withdrawn_reason) <= 1000",
            name="ck_interviewer_scorecards_withdrawn_reason_len",
        ),
    )
    # One LIVE scorecard per interviewer, candidate and round — the doc's key
    # relationship. Partial, so a withdrawn assignment can be re-made and a
    # superseded scorecard can have its correction.
    op.create_index(
        "uq_interviewer_scorecards_live",
        "interviewer_scorecards",
        ["round_id", "enrolment_id", "interviewer_user_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'withdrawn' AND superseded_at IS NULL"),
    )
    # "My interviews" — the interviewer console's only query.
    op.create_index(
        "ix_interviewer_scorecards_interviewer",
        "interviewer_scorecards",
        ["interviewer_user_id", "status"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "ix_interviewer_scorecards_enrolment",
        "interviewer_scorecards",
        ["company_id", "enrolment_id"],
    )

    op.create_table(
        "interviewer_scorecard_scores",
        sa.Column("scorecard_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("competency_id", sa.Text(), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("not_assessed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "scorecard_id", "competency_id", name="pk_interviewer_scorecard_scores"
        ),
        sa.ForeignKeyConstraint(
            ["scorecard_id", "company_id"],
            ["interviewer_scorecards.id", "interviewer_scorecards.company_id"],
            name="fk_scorecard_scores_scorecard", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scorecard_id", "round_id"],
            ["interviewer_scorecards.id", "interviewer_scorecards.round_id"],
            name="fk_scorecard_scores_same_round",
        ),
        # THE rule: a score can only be written against a criterion the round
        # was published with. No second rubric can exist.
        sa.ForeignKeyConstraint(
            ["round_id", "competency_id"],
            ["round_criteria.round_id", "round_criteria.competency_id"],
            name="fk_scorecard_scores_frozen_criterion",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 1 AND 5", name="ck_scorecard_scores_range"
        ),
        sa.CheckConstraint(
            "NOT (score IS NOT NULL AND not_assessed)",
            name="ck_scorecard_scores_scored_or_not_assessed",
        ),
        sa.CheckConstraint(
            "evidence IS NULL OR char_length(evidence) <= 4000",
            name="ck_scorecard_scores_evidence_len",
        ),
    )

    op.create_table(
        "interviewer_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interviewer_notes"),
        # Keyed on the interview, not the scorecard, so a correction — which
        # opens a new scorecard row — does not strand the notes.
        sa.UniqueConstraint(
            "round_id", "enrolment_id", "interviewer_user_id",
            name="uq_interviewer_notes_interview",
        ),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_interviewer_notes_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"],
            ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_interviewer_notes_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interviewer_user_id"], ["users.id"], name="fk_interviewer_notes_interviewer",
        ),
        sa.CheckConstraint(
            "char_length(notes) <= 20000", name="ck_interviewer_notes_len"
        ),
    )

    op.execute(sa.text(SCORECARD_TRIGGER))
    op.execute(sa.text(SCORECARD_TRIGGER_HOOK))
    op.execute(sa.text(SCORES_TRIGGER))
    op.execute(sa.text(SCORES_TRIGGER_HOOK))


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS interviewer_scorecard_scores_protect "
            "ON interviewer_scorecard_scores"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS interviewer_scorecard_scores_protect()"))
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS interviewer_scorecards_protect ON interviewer_scorecards")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS interviewer_scorecards_protect()"))
    op.drop_table("interviewer_notes")
    op.drop_table("interviewer_scorecard_scores")
    op.drop_index("ix_interviewer_scorecards_enrolment", table_name="interviewer_scorecards")
    op.drop_index("ix_interviewer_scorecards_interviewer", table_name="interviewer_scorecards")
    op.drop_index("uq_interviewer_scorecards_live", table_name="interviewer_scorecards")
    op.drop_table("interviewer_scorecards")
    # The role is left in place: user_roles rows may reference it, and a
    # downgrade that deleted a role people hold would orphan their accounts.
