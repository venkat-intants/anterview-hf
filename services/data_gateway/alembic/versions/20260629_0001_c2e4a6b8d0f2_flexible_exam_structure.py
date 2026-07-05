"""flexible exam structure — rounds, sections, exam proctoring, auto-advance

Turns the FLAT exam model (one exam = MCQ xor coding, questions hung directly off
the exam) into a 3-level hierarchy that HR can flex per company:

    Exam ── ExamRound* ── ExamSection* (kind: mcq|coding) ── questions

  * exam_rounds   — the unit HR SCHEDULES/ASSIGNS independently (own magic link +
                    deadline). pass_threshold + time limit are per-round.
  * exam_sections — typed (mcq|coding); mixing sections of different kinds in one
                    round is how an exam mixes MCQ + coding.
  * exam_questions / coding_questions gain section_id (their new parent).
  * exam_assignments gain round_id (+ scheduled_at) — token now grants ONE round.
  * exam_attempts  gain round_id (attempt is per-round) + integrity_score +
                   proctoring_summary (exam proctoring).
  * exams gain auto_advance_on_pass (terminal-round pass → interview, per-exam).
  * exam_integrity_events — exam analogue of integrity_events, keyed to the attempt
                   (exams have no session_id).

BACKWARD-COMPAT BACKFILL (mechanical, runs in this migration): every existing exam
gets ONE default round ("Round 1", advances_to_interview=true, kind/thresholds
inherited) + ONE default section (kind = exam.kind); existing questions /
assignments / attempts are reparented to it. Flat exams keep working as
single-round exams. Position-uniqueness and the "one active assignment / one live
attempt" partial indexes move from exam_id scope to section_id / round_id scope so
a candidate can hold a live attempt in more than one round at once.

Additive + reversible. Revision id c2e4a6b8d0f2.
Revises: b1d3f5a7c9e0
Create Date: 2026-06-29 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "c2e4a6b8d0f2"
down_revision: str | None = "b1d3f5a7c9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── exams.auto_advance_on_pass (existing rows default to manual gate) ──
    op.add_column(
        "exams",
        sa.Column(
            "auto_advance_on_pass", sa.Boolean(),
            server_default=sa.text("false"), nullable=False,
        ),
    )

    # ── exam_rounds ──
    op.create_table(
        "exam_rounds",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("pass_threshold", sa.SmallInteger(), server_default="60", nullable=False),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "advances_to_interview", sa.Boolean(),
            server_default=sa.text("false"), nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_exam_rounds"),
        sa.ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_rounds_exam", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_exam_rounds_company", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "pass_threshold BETWEEN 0 AND 100", name="ck_exam_rounds_pass_threshold_range"
        ),
        sa.CheckConstraint("status IN ('draft','published')", name="ck_exam_rounds_status"),
        # Composite target so sections can pin (id, company_id) and never drift.
        sa.UniqueConstraint("id", "company_id", name="uq_exam_rounds_id_company"),
    )
    op.create_index("idx_exam_rounds_exam", "exam_rounds", ["exam_id", "company_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_exam_rounds_exam_number "
        "ON exam_rounds (exam_id, round_number) WHERE deleted_at IS NULL"
    )

    # ── exam_sections ──
    op.create_table(
        "exam_sections",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("round_id", sa.UUID(), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),  # denormalized for fast lookups
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default="mcq", nullable=False),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_exam_sections"),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"], ["exam_rounds.id", "exam_rounds.company_id"],
            name="fk_exam_sections_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_exam_sections_company", ondelete="CASCADE",
        ),
        sa.CheckConstraint("kind IN ('mcq','coding')", name="ck_exam_sections_kind"),
        sa.UniqueConstraint("id", "company_id", name="uq_exam_sections_id_company"),
    )
    op.create_index("idx_exam_sections_round", "exam_sections", ["round_id", "company_id"])
    op.create_index("idx_exam_sections_exam", "exam_sections", ["exam_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_exam_sections_round_position "
        "ON exam_sections (round_id, position) WHERE deleted_at IS NULL"
    )

    # ── new parent columns (nullable until backfilled) ──
    op.add_column("exam_questions", sa.Column("section_id", sa.UUID(), nullable=True))
    op.add_column("coding_questions", sa.Column("section_id", sa.UUID(), nullable=True))
    op.add_column("exam_assignments", sa.Column("round_id", sa.UUID(), nullable=True))
    op.add_column(
        "exam_assignments",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("exam_attempts", sa.Column("round_id", sa.UUID(), nullable=True))
    op.add_column("exam_attempts", sa.Column("integrity_score", sa.SmallInteger(), nullable=True))
    op.add_column("exam_attempts", sa.Column("proctoring_summary", JSONB(), nullable=True))

    # ── backfill: one default round + section per exam, then reparent children ──
    # 1:1 exam→round and round→section, so the UPDATE...FROM joins are unambiguous.
    op.execute(
        """
        INSERT INTO exam_rounds
            (id, exam_id, company_id, round_number, title, pass_threshold,
             time_limit_seconds, advances_to_interview, status, position,
             created_at, updated_at)
        SELECT gen_random_uuid(), e.id, e.company_id, 1, 'Round 1',
               e.pass_threshold, e.time_limit_seconds, true,
               CASE WHEN e.status = 'published' THEN 'published' ELSE 'draft' END,
               1, now(), now()
        FROM exams e
        """
    )
    op.execute(
        """
        INSERT INTO exam_sections
            (id, round_id, exam_id, company_id, title, kind, time_limit_seconds,
             position, created_at, updated_at)
        SELECT gen_random_uuid(), r.id, r.exam_id, r.company_id, 'Section 1',
               e.kind, NULL, 1, now(), now()
        FROM exam_rounds r
        JOIN exams e ON e.id = r.exam_id
        """
    )
    op.execute(
        "UPDATE exam_questions q SET section_id = s.id "
        "FROM exam_sections s WHERE s.exam_id = q.exam_id"
    )
    op.execute(
        "UPDATE coding_questions q SET section_id = s.id "
        "FROM exam_sections s WHERE s.exam_id = q.exam_id"
    )
    op.execute(
        "UPDATE exam_assignments a SET round_id = r.id "
        "FROM exam_rounds r WHERE r.exam_id = a.exam_id"
    )
    op.execute(
        "UPDATE exam_attempts t SET round_id = r.id "
        "FROM exam_rounds r WHERE r.exam_id = t.exam_id"
    )

    # ── composite FKs to the new parents (validate now that backfill is done) ──
    op.create_foreign_key(
        "fk_exam_questions_section", "exam_questions", "exam_sections",
        ["section_id", "company_id"], ["id", "company_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_coding_questions_section", "coding_questions", "exam_sections",
        ["section_id", "company_id"], ["id", "company_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_exam_assignments_round", "exam_assignments", "exam_rounds",
        ["round_id", "company_id"], ["id", "company_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_exam_attempts_round", "exam_attempts", "exam_rounds",
        ["round_id", "company_id"], ["id", "company_id"], ondelete="CASCADE",
    )

    # ── lock the new parents NOT NULL ──
    op.alter_column("exam_questions", "section_id", nullable=False)
    op.alter_column("coding_questions", "section_id", nullable=False)
    op.alter_column("exam_assignments", "round_id", nullable=False)
    op.alter_column("exam_attempts", "round_id", nullable=False)

    # ── re-scope position uniqueness from exam → section ──
    op.drop_index("uq_exam_questions_exam_position", table_name="exam_questions")
    op.execute(
        "CREATE UNIQUE INDEX uq_exam_questions_section_position "
        "ON exam_questions (section_id, position) WHERE deleted_at IS NULL"
    )
    op.drop_index("uq_coding_questions_exam_position", table_name="coding_questions")
    op.execute(
        "CREATE UNIQUE INDEX uq_coding_questions_section_position "
        "ON coding_questions (section_id, position) WHERE deleted_at IS NULL"
    )

    # ── re-scope "one active assignment / one live attempt" from exam → round ──
    op.create_index("idx_exam_assignments_round", "exam_assignments", ["round_id"])
    op.drop_index("uix_exam_assignments_active", table_name="exam_assignments")
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_assignments_active "
        "ON exam_assignments (round_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status = 'invited'"
    )

    op.create_index("idx_exam_attempts_round", "exam_attempts", ["round_id"])
    op.drop_constraint("uq_exam_attempts_exam_applicant_no", "exam_attempts", type_="unique")
    op.create_unique_constraint(
        "uq_exam_attempts_round_applicant_no", "exam_attempts",
        ["round_id", "applicant_id", "attempt_no"],
    )
    op.drop_index("uix_exam_attempts_one_live", table_name="exam_attempts")
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_attempts_one_live "
        "ON exam_attempts (round_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status <> 'expired'"
    )

    # ── exam_integrity_events (exam analogue of integrity_events, keyed to attempt) ──
    op.create_table(
        "exam_integrity_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("attempt_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        # fullscreen_exit | tab_blur | copy | paste | ...
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("event_metadata", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exam_integrity_events"),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["exam_attempts.id"],
            name="fk_exam_integrity_events_attempt", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_exam_integrity_events_company", ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_exam_integrity_events_attempt", "exam_integrity_events", ["attempt_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_exam_integrity_events_attempt", table_name="exam_integrity_events")
    op.drop_table("exam_integrity_events")

    # exam_attempts: round → exam scoping
    op.drop_index("uix_exam_attempts_one_live", table_name="exam_attempts")
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_attempts_one_live "
        "ON exam_attempts (exam_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status <> 'expired'"
    )
    op.drop_constraint("uq_exam_attempts_round_applicant_no", "exam_attempts", type_="unique")
    op.create_unique_constraint(
        "uq_exam_attempts_exam_applicant_no", "exam_attempts",
        ["exam_id", "applicant_id", "attempt_no"],
    )
    op.drop_constraint("fk_exam_attempts_round", "exam_attempts", type_="foreignkey")
    op.drop_index("idx_exam_attempts_round", table_name="exam_attempts")
    op.drop_column("exam_attempts", "proctoring_summary")
    op.drop_column("exam_attempts", "integrity_score")
    op.drop_column("exam_attempts", "round_id")

    # exam_assignments: round → exam scoping
    op.drop_index("uix_exam_assignments_active", table_name="exam_assignments")
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_assignments_active "
        "ON exam_assignments (exam_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status = 'invited'"
    )
    op.drop_constraint("fk_exam_assignments_round", "exam_assignments", type_="foreignkey")
    op.drop_index("idx_exam_assignments_round", table_name="exam_assignments")
    op.drop_column("exam_assignments", "scheduled_at")
    op.drop_column("exam_assignments", "round_id")

    # coding_questions: section → exam position scoping
    op.drop_index("uq_coding_questions_section_position", table_name="coding_questions")
    op.execute(
        "CREATE UNIQUE INDEX uq_coding_questions_exam_position "
        "ON coding_questions (exam_id, position) WHERE deleted_at IS NULL"
    )
    op.drop_constraint("fk_coding_questions_section", "coding_questions", type_="foreignkey")
    op.drop_column("coding_questions", "section_id")

    # exam_questions: section → exam position scoping
    op.drop_index("uq_exam_questions_section_position", table_name="exam_questions")
    op.execute(
        "CREATE UNIQUE INDEX uq_exam_questions_exam_position "
        "ON exam_questions (exam_id, position) WHERE deleted_at IS NULL"
    )
    op.drop_constraint("fk_exam_questions_section", "exam_questions", type_="foreignkey")
    op.drop_column("exam_questions", "section_id")

    # exam_sections / exam_rounds
    op.drop_index("uq_exam_sections_round_position", table_name="exam_sections")
    op.drop_index("idx_exam_sections_exam", table_name="exam_sections")
    op.drop_index("idx_exam_sections_round", table_name="exam_sections")
    op.drop_table("exam_sections")

    op.drop_index("uq_exam_rounds_exam_number", table_name="exam_rounds")
    op.drop_index("idx_exam_rounds_exam", table_name="exam_rounds")
    op.drop_table("exam_rounds")

    op.drop_column("exams", "auto_advance_on_pass")
