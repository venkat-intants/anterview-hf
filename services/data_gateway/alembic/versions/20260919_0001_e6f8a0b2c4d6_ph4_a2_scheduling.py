"""Interview scheduling and loops — PH4-A2.

Revision ID: e6f8a0b2c4d6
Revises: d5e7f9a1b3c4
Create Date: 2026-09-19

WHAT EXISTED
An AI interview invite carries an optional ``scheduled_at`` and a human
interview only a scorecard ``due_at``. Nobody could say when a human interview
happens, who is free, or whether two of them collide. This adds that, beside
the AI invites (which are untouched).

THE TABLES
- ``interviewer_availability`` — windows an interviewer is free. Set by HR or by
  the interviewer. Windows of one person never overlap (the UI merges them).
- ``interview_loops`` — one coordinated set of interviews for one application,
  with the candidate's timezone and the gap to leave between sessions. A single
  interview is a loop of one: there is one way to schedule, not two.
- ``interview_sessions`` — one sitting: its round (so it has criteria and
  scorecards), duration, time, place. ``awaiting_slot`` while a self-scheduling
  candidate has not picked yet.
- ``interview_session_interviewers`` — who sits on it, and the A1 scorecard
  each of them fills in. Times are COPIED from the session by trigger, because
  an exclusion constraint can only see its own row.

WHERE "NO DOUBLE BOOKING" HOLDS
In exclusion constraints (btree_gist), not in application code, so two HR
managers — or two candidates racing for one slot — cannot both win:
- an interviewer's live sessions never overlap;
- a candidate's scheduled sessions, each extended by its loop's buffer
  (``blocked_until``), never overlap — which is also what enforces the gap;
- an interviewer's availability windows never overlap.
The application checks first and explains; the database is what makes it true.

TIME
Every instant is timestamptz. A timezone is an IANA name kept on the loop — the
candidate's — and is used to SHOW times, never to store them.

WHAT THIS CANNOT DO
Move a candidate. Scheduling records when people meet; the stage ledger and
the decision stay where they were.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e6f8a0b2c4d6"
down_revision: str | None = "d5e7f9a1b3c4"
branch_labels: str | None = None
depends_on: str | None = None

LOOP_STATES = ("draft", "scheduled", "completed", "cancelled")
SESSION_STATES = ("awaiting_slot", "scheduled", "completed", "cancelled", "no_show")

# The interviewer rows carry the session's time so the exclusion constraint can
# see it. They are never written directly: this fills them from the session on
# every insert/update, and the session trigger below re-touches them whenever
# its time or status moves.
SYNC_INTERVIEWER_ROW = """
CREATE OR REPLACE FUNCTION interview_session_interviewers_sync() RETURNS trigger AS $$
DECLARE
    s record;
BEGIN
    SELECT starts_at, ends_at, status, company_id INTO s
      FROM interview_sessions WHERE id = NEW.session_id;
    IF s.company_id IS DISTINCT FROM NEW.company_id THEN
        RAISE EXCEPTION 'interviewer row % belongs to another company', NEW.session_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM users u
                    WHERE u.id = NEW.interviewer_user_id AND u.company_id = NEW.company_id) THEN
        RAISE EXCEPTION 'interviewer % is not staff of this company', NEW.interviewer_user_id;
    END IF;
    NEW.starts_at := s.starts_at;
    NEW.ends_at := s.ends_at;
    NEW.live := (s.status = 'scheduled');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
SYNC_INTERVIEWER_HOOK = """
CREATE TRIGGER interview_session_interviewers_sync
    BEFORE INSERT OR UPDATE ON interview_session_interviewers
    FOR EACH ROW EXECUTE FUNCTION interview_session_interviewers_sync()
"""
PROPAGATE_SESSION = """
CREATE OR REPLACE FUNCTION interview_sessions_propagate() RETURNS trigger AS $$
BEGIN
    IF NEW.starts_at IS DISTINCT FROM OLD.starts_at
       OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
       OR NEW.status IS DISTINCT FROM OLD.status THEN
        UPDATE interview_session_interviewers SET updated_at = now()
         WHERE session_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
PROPAGATE_SESSION_HOOK = """
CREATE TRIGGER interview_sessions_propagate
    AFTER UPDATE ON interview_sessions
    FOR EACH ROW EXECUTE FUNCTION interview_sessions_propagate()
"""

EXCLUSIONS = (
    "ALTER TABLE interviewer_availability ADD CONSTRAINT ex_interviewer_availability_overlap"
    " EXCLUDE USING gist (user_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)"
    " WHERE (deleted_at IS NULL)",
    "ALTER TABLE interview_sessions ADD CONSTRAINT ex_interview_sessions_candidate_overlap"
    " EXCLUDE USING gist (applicant_id WITH =, tstzrange(starts_at, blocked_until, '[)') WITH &&)"
    " WHERE (status = 'scheduled')",
    "ALTER TABLE interview_session_interviewers ADD CONSTRAINT ex_session_interviewers_overlap"
    " EXCLUDE USING gist (interviewer_user_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)"
    " WHERE (live)",
)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "interviewer_availability",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        _ts("starts_at", nullable=False),
        _ts("ends_at", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_stamps(),
        _ts("deleted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_interviewer_availability"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_interviewer_availability_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name="fk_interviewer_availability_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_interviewer_availability_created_by",
                                ondelete="SET NULL"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_interviewer_availability_order"),
        sa.CheckConstraint("ends_at - starts_at <= interval '14 days'",
                           name="ck_interviewer_availability_span"),
    )
    op.create_index("ix_interviewer_availability_user", "interviewer_availability",
                    ["user_id", "starts_at"], postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "interview_loops",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("candidate_timezone", sa.Text(), nullable=False,
                  server_default="Asia/Kolkata"),
        sa.Column("buffer_minutes", sa.SmallInteger(), nullable=False, server_default="15"),
        sa.Column("self_schedule", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("sent_at"),
        sa.Column("itinerary_version", sa.Integer(), nullable=False, server_default="0"),
        _ts("cancelled_at"),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        _ts("redacted_at"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_interview_loops"),
        sa.UniqueConstraint("id", "company_id", name="uq_interview_loops_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_interview_loops_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrolment_id", "company_id"],
                                ["enrolments.id", "enrolments.company_id"],
                                name="fk_interview_loops_enrolment", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["applicant_id", "company_id"],
                                ["applicants.id", "applicants.company_id"],
                                name="fk_interview_loops_applicant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cancelled_by_user_id"], ["users.id"],
                                name="fk_interview_loops_cancelled_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_interview_loops_created_by", ondelete="SET NULL"),
        sa.CheckConstraint(f"status IN {LOOP_STATES}", name="ck_interview_loops_status"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200",
                           name="ck_interview_loops_title_len"),
        sa.CheckConstraint("char_length(candidate_timezone) BETWEEN 1 AND 64",
                           name="ck_interview_loops_tz_len"),
        sa.CheckConstraint("buffer_minutes BETWEEN 0 AND 240", name="ck_interview_loops_buffer"),
        sa.CheckConstraint("(status = 'cancelled') = (cancelled_at IS NOT NULL)",
                           name="ck_interview_loops_cancelled_at"),
        sa.CheckConstraint("cancel_reason IS NULL OR char_length(cancel_reason) <= 500",
                           name="ck_interview_loops_reason_len"),
    )
    op.create_index("ix_interview_loops_enrolment", "interview_loops", ["enrolment_id"])

    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("loop_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        _ts("starts_at"),
        _ts("ends_at"),
        _ts("blocked_until"),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="awaiting_slot"),
        sa.Column("booked_by", sa.Text(), nullable=True),
        _ts("booked_at"),
        _ts("cancelled_at"),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_interview_sessions"),
        sa.UniqueConstraint("id", "company_id", name="uq_interview_sessions_id_company"),
        sa.ForeignKeyConstraint(["loop_id", "company_id"],
                                ["interview_loops.id", "interview_loops.company_id"],
                                name="fk_interview_sessions_loop", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrolment_id", "company_id"],
                                ["enrolments.id", "enrolments.company_id"],
                                name="fk_interview_sessions_enrolment", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["applicant_id", "company_id"],
                                ["applicants.id", "applicants.company_id"],
                                name="fk_interview_sessions_applicant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["round_id", "company_id"],
                                ["workflow_rounds.id", "workflow_rounds.company_id"],
                                name="fk_interview_sessions_round", ondelete="CASCADE"),
        sa.CheckConstraint(f"status IN {SESSION_STATES}", name="ck_interview_sessions_status"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200",
                           name="ck_interview_sessions_title_len"),
        sa.CheckConstraint("duration_minutes BETWEEN 15 AND 480",
                           name="ck_interview_sessions_duration"),
        # The three instants agree; awaiting a slot means no time yet, and every
        # other state but cancelled has one (a session cancelled while still
        # awaiting its slot never got a time).
        sa.CheckConstraint(
            "(starts_at IS NULL) = (ends_at IS NULL)"
            " AND (starts_at IS NULL) = (blocked_until IS NULL)"
            " AND (status <> 'awaiting_slot' OR starts_at IS NULL)"
            " AND (status IN ('awaiting_slot', 'cancelled') OR starts_at IS NOT NULL)",
            name="ck_interview_sessions_time_present",
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR ("
            " extract(epoch FROM ends_at - starts_at) = duration_minutes * 60"
            " AND extract(epoch FROM blocked_until - ends_at) BETWEEN 0 AND 14400)",
            name="ck_interview_sessions_time_shape",
        ),
        sa.CheckConstraint("location IS NULL OR char_length(location) <= 500",
                           name="ck_interview_sessions_location_len"),
        sa.CheckConstraint("booked_by IS NULL OR booked_by IN ('hr', 'candidate')",
                           name="ck_interview_sessions_booked_by"),
        sa.CheckConstraint("(status = 'cancelled') = (cancelled_at IS NOT NULL)",
                           name="ck_interview_sessions_cancelled_at"),
        sa.CheckConstraint("cancel_reason IS NULL OR char_length(cancel_reason) <= 500",
                           name="ck_interview_sessions_reason_len"),
    )
    op.create_index("ix_interview_sessions_loop", "interview_sessions", ["loop_id", "position"])
    op.create_index("ix_interview_sessions_upcoming", "interview_sessions",
                    ["company_id", "starts_at"],
                    postgresql_where=sa.text("status = 'scheduled'"))

    op.create_table(
        "interview_session_interviewers",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("scorecard_id", sa.Uuid(), nullable=True),
        _ts("starts_at"),
        _ts("ends_at"),
        sa.Column("live", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_stamps(),
        sa.PrimaryKeyConstraint("session_id", "interviewer_user_id",
                                name="pk_interview_session_interviewers"),
        sa.ForeignKeyConstraint(["session_id", "company_id"],
                                ["interview_sessions.id", "interview_sessions.company_id"],
                                name="fk_session_interviewers_session", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interviewer_user_id"], ["users.id"],
                                name="fk_session_interviewers_user"),
        sa.ForeignKeyConstraint(["scorecard_id", "company_id"],
                                ["interviewer_scorecards.id", "interviewer_scorecards.company_id"],
                                name="fk_session_interviewers_scorecard"),
    )
    op.create_index("ix_session_interviewers_user", "interview_session_interviewers",
                    ["interviewer_user_id", "starts_at"],
                    postgresql_where=sa.text("live"))

    for sql in (SYNC_INTERVIEWER_ROW, SYNC_INTERVIEWER_HOOK, PROPAGATE_SESSION,
                PROPAGATE_SESSION_HOOK, *EXCLUSIONS):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS interview_sessions_propagate ON interview_sessions")
    op.execute("DROP FUNCTION IF EXISTS interview_sessions_propagate()")
    op.execute("DROP TRIGGER IF EXISTS interview_session_interviewers_sync"
               " ON interview_session_interviewers")
    op.execute("DROP FUNCTION IF EXISTS interview_session_interviewers_sync()")
    op.drop_index("ix_session_interviewers_user", table_name="interview_session_interviewers")
    op.drop_table("interview_session_interviewers")
    op.drop_index("ix_interview_sessions_upcoming", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_loop", table_name="interview_sessions")
    op.drop_table("interview_sessions")
    op.drop_index("ix_interview_loops_enrolment", table_name="interview_loops")
    op.drop_table("interview_loops")
    op.drop_index("ix_interviewer_availability_user", table_name="interviewer_availability")
    op.drop_table("interviewer_availability")
    # btree_gist stays: extensions are shared, and dropping one another object
    # may have come to depend on is not this migration's call.
