"""Candidate accommodations — PH4-D2.

Revision ID: e3b5d7f9a1c5
Revises: d2a4c6e8f0b3
Create Date: 2026-09-21

WHAT THIS ADDS
``candidate_accommodations`` records a recorded adjustment for one applicant —
extra time, a deadline extension, relaxed auto-submit on proctoring flags, or a
free-text "other" adjustment — scoped to the whole applicant, one application
(``enrolment_id``), one workflow round, or one hand-assigned exam round. Two
notes exist beside the parameters: ``interviewer_note`` (the only accommodation
text an assigned interviewer ever sees) and ``internal_note`` (HR only, never
shown anywhere else). ``accommodation_events`` is the append-only history of
what happened to a row — facts only, never the note text or the parameter
values.

The guard trigger (``candidate_accommodations_guard``) makes the promises the
app relies on hold even against a raw UPDATE: a row arrives active, not
superseded, not redacted, with a named recorder; its scope, parameters, basis
and dates never change after insert; its two notes change only as part of a
single redaction (to NULL or the fixed marker ``[redacted]``, together with
``redacted_at``), after which the whole row is frozen; ``active`` only ever
becomes ``revoked``, and only with who and when; a revision is a NEW row
(``supersedes_id`` / ``superseded_by_id``), never an edit of the one it
replaces, and those two pointers are set exactly once.

WHY THE ALLOWANCE IS FROZEN ON THE ATTEMPT
``exam_attempts`` gains ``accommodation_id``, ``extra_time_seconds`` and
``auto_submit_relaxed``, written once at ``/exam/start`` from whatever
accommodation is effective at that moment. ``exam_attempts_allowance_fixed``
then freezes ``extra_time_seconds`` and ``auto_submit_relaxed`` for the life of
the attempt, and lets ``accommodation_id`` only ever become NULL (the ON DELETE
SET NULL path if the accommodation itself is ever removed) — never point at a
different row. A candidate's time allowance is fixed the moment they start,
whatever HR does to their accommodation afterwards; ``exam_assignments`` and
``interview_invites`` also gain ``accommodation_id`` so it is on record which
adjustment, if any, extended a link's expiry.

WHAT THIS DOES NOT TOUCH
No column here is read by grading (``app/exam_grading.py``, ``app/coding_grader.py``):
the same answers score the same with or without an adjustment. Nothing here is
reachable by an agent or an LLM client.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3b5d7f9a1c5"
down_revision: str | None = "d2a4c6e8f0b3"
branch_labels: str | None = None
depends_on: str | None = None

ACCOMMODATION_BASIS = ("candidate_request", "hr_initiated")
ACCOMMODATION_STATUSES = ("active", "revoked")
ACCOMMODATION_EVENTS = ("recorded", "revised", "revoked", "applied", "redacted")


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


# ---------------------------------------------------------------------------
# The accommodation lifecycle guard
# ---------------------------------------------------------------------------
CANDIDATE_ACCOMMODATIONS_GUARD = """
CREATE OR REPLACE FUNCTION candidate_accommodations_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- Cascade only: the applicant (or the company) is going.
        IF EXISTS (SELECT 1 FROM applicants a WHERE a.id = OLD.applicant_id) THEN
            RAISE EXCEPTION 'candidate accommodation % is kept, not deleted', OLD.id;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' THEN
            RAISE EXCEPTION 'a candidate accommodation arrives active';
        END IF;
        IF NEW.superseded_at IS NOT NULL OR NEW.superseded_by_id IS NOT NULL THEN
            RAISE EXCEPTION 'a candidate accommodation arrives not superseded';
        END IF;
        IF NEW.redacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'a candidate accommodation arrives not redacted';
        END IF;
        IF NEW.recorded_by_user_id IS NULL THEN
            RAISE EXCEPTION 'a candidate accommodation needs a recorder';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF OLD.redacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'candidate accommodation % is redacted and is fixed', OLD.id;
    END IF;

    -- Scope, parameters, basis, dates and what this row supersedes never change
    -- after insert — the one exception (the two notes, via a redaction) is
    -- checked separately below.
    IF NEW.company_id IS DISTINCT FROM OLD.company_id
       OR NEW.applicant_id IS DISTINCT FROM OLD.applicant_id
       OR NEW.enrolment_id IS DISTINCT FROM OLD.enrolment_id
       OR NEW.round_id IS DISTINCT FROM OLD.round_id
       OR NEW.exam_round_id IS DISTINCT FROM OLD.exam_round_id
       OR NEW.extra_time_percent IS DISTINCT FROM OLD.extra_time_percent
       OR NEW.deadline_extension_days IS DISTINCT FROM OLD.deadline_extension_days
       OR NEW.relax_auto_submit IS DISTINCT FROM OLD.relax_auto_submit
       OR NEW.basis IS DISTINCT FROM OLD.basis
       OR NEW.requested_on IS DISTINCT FROM OLD.requested_on
       OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
       OR NEW.effective_until IS DISTINCT FROM OLD.effective_until
       OR NEW.recorded_by_user_id IS DISTINCT FROM OLD.recorded_by_user_id
       OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id THEN
        RAISE EXCEPTION
            'candidate accommodation % keeps its scope, parameters, basis and dates', OLD.id;
    END IF;

    IF NEW.redacted_at IS NOT NULL THEN
        -- A redaction: the two notes go to NULL or '[redacted]', together with
        -- redacted_at, and nothing else in this same statement.
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.revoked_by_user_id IS DISTINCT FROM OLD.revoked_by_user_id
           OR NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
           OR NEW.revoke_reason IS DISTINCT FROM OLD.revoke_reason
           OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
           OR NEW.superseded_at IS DISTINCT FROM OLD.superseded_at
           OR NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id THEN
            RAISE EXCEPTION 'a redaction changes only the notes and redacted_at';
        END IF;
        IF NEW.other_adjustment IS NOT NULL AND NEW.other_adjustment <> '[redacted]' THEN
            RAISE EXCEPTION 'a redacted note becomes NULL or [redacted]';
        END IF;
        IF NEW.interviewer_note IS NOT NULL AND NEW.interviewer_note <> '[redacted]' THEN
            RAISE EXCEPTION 'a redacted note becomes NULL or [redacted]';
        END IF;
        IF NEW.internal_note IS NOT NULL AND NEW.internal_note <> '[redacted]' THEN
            RAISE EXCEPTION 'a redacted note becomes NULL or [redacted]';
        END IF;
        RETURN NEW;
    END IF;

    -- Not a redaction: the two notes never change outside one.
    IF NEW.other_adjustment IS DISTINCT FROM OLD.other_adjustment
       OR NEW.interviewer_note IS DISTINCT FROM OLD.interviewer_note
       OR NEW.internal_note IS DISTINCT FROM OLD.internal_note THEN
        RAISE EXCEPTION 'candidate accommodation % keeps its notes outside a redaction', OLD.id;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (OLD.status = 'active' AND NEW.status = 'revoked') THEN
            RAISE EXCEPTION 'candidate accommodation % cannot go from % to %',
                OLD.id, OLD.status, NEW.status;
        END IF;
        IF NEW.revoked_by_user_id IS NULL OR NEW.revoked_at IS NULL THEN
            RAISE EXCEPTION 'a revoked candidate accommodation needs who and when';
        END IF;
    ELSIF NEW.revoked_by_user_id IS DISTINCT FROM OLD.revoked_by_user_id
       OR NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
       OR NEW.revoke_reason IS DISTINCT FROM OLD.revoke_reason THEN
        RAISE EXCEPTION 'candidate accommodation % keeps how it was revoked', OLD.id;
    END IF;

    -- superseded_at / superseded_by_id move from NULL to a value EXACTLY once,
    -- together, and never change again once set.
    IF OLD.superseded_by_id IS NOT NULL THEN
        IF NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id
           OR NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
            RAISE EXCEPTION 'candidate accommodation % keeps what superseded it', OLD.id;
        END IF;
    ELSIF (NEW.superseded_by_id IS NULL) <> (NEW.superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'candidate accommodation % must record what and when it was superseded together', OLD.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

ACCOMMODATION_EVENTS_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION accommodation_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (
            SELECT 1 FROM candidate_accommodations a WHERE a.id = OLD.accommodation_id
        ) THEN
            RETURN OLD;  -- cascade: the accommodation (or its applicant) is gone
        END IF;
    END IF;
    RAISE EXCEPTION 'accommodation_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""

# ---------------------------------------------------------------------------
# A started attempt's allowance is fixed
# ---------------------------------------------------------------------------
EXAM_ATTEMPTS_ALLOWANCE_FIXED = """
CREATE OR REPLACE FUNCTION exam_attempts_allowance_fixed() RETURNS trigger AS $$
BEGIN
    IF NEW.extra_time_seconds IS DISTINCT FROM OLD.extra_time_seconds THEN
        RAISE EXCEPTION 'exam attempt % keeps the extra time it started with', OLD.id;
    END IF;
    IF NEW.auto_submit_relaxed IS DISTINCT FROM OLD.auto_submit_relaxed THEN
        RAISE EXCEPTION 'exam attempt % keeps whether auto-submit was relaxed', OLD.id;
    END IF;
    IF NEW.accommodation_id IS DISTINCT FROM OLD.accommodation_id
       AND NEW.accommodation_id IS NOT NULL THEN
        RAISE EXCEPTION 'exam attempt % keeps which accommodation applied, or clears it', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "candidate_accommodations",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=True),
        sa.Column("round_id", sa.Uuid(), nullable=True),
        sa.Column("exam_round_id", sa.Uuid(), nullable=True),
        sa.Column("extra_time_percent", sa.SmallInteger(), nullable=True),
        sa.Column("deadline_extension_days", sa.SmallInteger(), nullable=True),
        sa.Column("relax_auto_submit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("other_adjustment", sa.Text(), nullable=True),
        sa.Column("interviewer_note", sa.Text(), nullable=True),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("requested_on", sa.Date(), nullable=True),
        _ts("effective_from", nullable=False),
        _ts("effective_until"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        _ts("revoked_at"),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        _ts("superseded_at"),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        _ts("redacted_at"),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_accommodations"),
        sa.UniqueConstraint("id", "company_id", name="uq_candidate_accommodations_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_candidate_accommodations_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["applicant_id", "company_id"], ["applicants.id", "applicants.company_id"],
            name="fk_candidate_accommodations_applicant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_candidate_accommodations_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"], ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_candidate_accommodations_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exam_round_id", "company_id"], ["exam_rounds.id", "exam_rounds.company_id"],
            name="fk_candidate_accommodations_exam_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"],
                                name="fk_candidate_accommodations_recorded_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"],
                                name="fk_candidate_accommodations_revoked_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "company_id"],
            ["candidate_accommodations.id", "candidate_accommodations.company_id"],
            name="fk_candidate_accommodations_supersedes", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id", "company_id"],
            ["candidate_accommodations.id", "candidate_accommodations.company_id"],
            name="fk_candidate_accommodations_superseded_by", ondelete="SET NULL",
            deferrable=True, initially="DEFERRED",
        ),
        sa.CheckConstraint(f"basis IN {ACCOMMODATION_BASIS}", name="ck_candidate_accommodations_basis"),
        sa.CheckConstraint(f"status IN {ACCOMMODATION_STATUSES}",
                           name="ck_candidate_accommodations_status"),
        sa.CheckConstraint(
            "extra_time_percent IS NULL OR extra_time_percent BETWEEN 10 AND 200",
            name="ck_candidate_accommodations_extra_time_range",
        ),
        sa.CheckConstraint(
            "deadline_extension_days IS NULL OR deadline_extension_days BETWEEN 1 AND 30",
            name="ck_candidate_accommodations_deadline_range",
        ),
        sa.CheckConstraint(
            "other_adjustment IS NULL OR char_length(other_adjustment) <= 500",
            name="ck_candidate_accommodations_other_len",
        ),
        sa.CheckConstraint(
            "interviewer_note IS NULL OR char_length(interviewer_note) <= 500",
            name="ck_candidate_accommodations_interviewer_note_len",
        ),
        sa.CheckConstraint(
            "internal_note IS NULL OR char_length(internal_note) <= 1000",
            name="ck_candidate_accommodations_internal_note_len",
        ),
        sa.CheckConstraint(
            "revoke_reason IS NULL OR char_length(revoke_reason) <= 500",
            name="ck_candidate_accommodations_revoke_reason_len",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_candidate_accommodations_window",
        ),
        sa.CheckConstraint(
            "round_id IS NULL OR enrolment_id IS NOT NULL",
            name="ck_candidate_accommodations_round_needs_enrolment",
        ),
        sa.CheckConstraint(
            "NOT (round_id IS NOT NULL AND exam_round_id IS NOT NULL)",
            name="ck_candidate_accommodations_one_round_scope",
        ),
        sa.CheckConstraint(
            "redacted_at IS NOT NULL OR extra_time_percent IS NOT NULL"
            " OR deadline_extension_days IS NOT NULL OR relax_auto_submit"
            " OR other_adjustment IS NOT NULL",
            name="ck_candidate_accommodations_at_least_one",
        ),
    )
    op.alter_column("candidate_accommodations", "effective_from", server_default=sa.text("now()"))
    op.alter_column("candidate_accommodations", "created_at", server_default=sa.text("now()"))
    op.alter_column("candidate_accommodations", "updated_at", server_default=sa.text("now()"))
    op.create_index(
        "ix_candidate_accommodations_applicant_active", "candidate_accommodations",
        ["company_id", "applicant_id"],
        postgresql_where=sa.text("status = 'active' AND superseded_at IS NULL AND redacted_at IS NULL"),
    )

    op.create_table(
        "accommodation_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("accommodation_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_accommodation_events"),
        sa.ForeignKeyConstraint(
            ["accommodation_id", "company_id"],
            ["candidate_accommodations.id", "candidate_accommodations.company_id"],
            name="fk_accommodation_events_accommodation", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_accommodation_events_actor", ondelete="SET NULL"),
        sa.CheckConstraint(f"action IN {ACCOMMODATION_EVENTS}", name="ck_accommodation_events_action"),
    )
    op.alter_column("accommodation_events", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_accommodation_events_accommodation", "accommodation_events",
                    ["accommodation_id", "created_at"])

    # exam_attempts: the allowance a started attempt is frozen with.
    op.add_column("exam_attempts", sa.Column("accommodation_id", sa.Uuid(), nullable=True))
    op.add_column(
        "exam_attempts",
        sa.Column("extra_time_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "exam_attempts",
        sa.Column("auto_submit_relaxed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        "fk_exam_attempts_accommodation", "exam_attempts", "candidate_accommodations",
        ["accommodation_id", "company_id"], ["id", "company_id"], ondelete="SET NULL",
    )

    # exam_assignments / interview_invites: which adjustment, if any, extended
    # this link's expiry.
    op.add_column("exam_assignments", sa.Column("accommodation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_exam_assignments_accommodation", "exam_assignments", "candidate_accommodations",
        ["accommodation_id", "company_id"], ["id", "company_id"], ondelete="SET NULL",
    )
    op.add_column("interview_invites", sa.Column("accommodation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_interview_invites_accommodation", "interview_invites", "candidate_accommodations",
        ["accommodation_id", "company_id"], ["id", "company_id"], ondelete="SET NULL",
    )

    for sql in (
        CANDIDATE_ACCOMMODATIONS_GUARD, ACCOMMODATION_EVENTS_APPEND_ONLY,
        EXAM_ATTEMPTS_ALLOWANCE_FIXED,
    ):
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER candidate_accommodations_guard"
        " BEFORE INSERT OR UPDATE OR DELETE ON candidate_accommodations"
        " FOR EACH ROW EXECUTE FUNCTION candidate_accommodations_guard()"
    )
    op.execute(
        "CREATE TRIGGER accommodation_events_append_only"
        " BEFORE UPDATE OR DELETE ON accommodation_events"
        " FOR EACH ROW EXECUTE FUNCTION accommodation_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER exam_attempts_allowance_fixed"
        " BEFORE UPDATE ON exam_attempts"
        " FOR EACH ROW EXECUTE FUNCTION exam_attempts_allowance_fixed()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS exam_attempts_allowance_fixed ON exam_attempts")
    op.execute("DROP FUNCTION IF EXISTS exam_attempts_allowance_fixed()")
    op.execute("DROP TRIGGER IF EXISTS accommodation_events_append_only ON accommodation_events")
    op.execute("DROP FUNCTION IF EXISTS accommodation_events_append_only()")
    op.execute("DROP TRIGGER IF EXISTS candidate_accommodations_guard ON candidate_accommodations")
    op.execute("DROP FUNCTION IF EXISTS candidate_accommodations_guard()")

    op.drop_constraint("fk_interview_invites_accommodation", "interview_invites", type_="foreignkey")
    op.drop_column("interview_invites", "accommodation_id")
    op.drop_constraint("fk_exam_assignments_accommodation", "exam_assignments", type_="foreignkey")
    op.drop_column("exam_assignments", "accommodation_id")

    op.drop_constraint("fk_exam_attempts_accommodation", "exam_attempts", type_="foreignkey")
    op.drop_column("exam_attempts", "auto_submit_relaxed")
    op.drop_column("exam_attempts", "extra_time_seconds")
    op.drop_column("exam_attempts", "accommodation_id")

    op.drop_index("ix_accommodation_events_accommodation", table_name="accommodation_events")
    op.drop_table("accommodation_events")

    op.drop_index("ix_candidate_accommodations_applicant_active", table_name="candidate_accommodations")
    op.drop_table("candidate_accommodations")
