"""mcq exam portal (HR workflow Phase 2: exams, questions, assignments, attempts)

HR managers author MCQ exams scoped to their company. Questions store
correct_index (NEVER served to applicants). An applicant is pre-assigned via an
opaque magic-link token (stored HASHED in token_hash — the raw token lives only
in the shared URL, mirroring how refresh tokens / consent evidence are hashed in
this repo). Taking the exam creates a server-graded attempt.

Tenant isolation is HARD: every table carries company_id (FK companies ON DELETE
CASCADE) AND composite FKs pin the denormalized company_id to the parent so it
can never drift across tenants.

Revision ID: e6f7a8b9c0d1
Revises:     d5e6f7a8b9c0
Create Date: 2026-06-22 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- exams ----------------
    op.create_table(
        "exams",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_job_title", sa.Text(), nullable=True),
        sa.Column("pass_threshold", sa.SmallInteger(), server_default="60", nullable=False),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),  # NULL = untimed
        sa.Column("allow_retake", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        # draft | published | closed  (only 'published' is takeable)
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_exams"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_exams_company", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_exams_created_by", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "pass_threshold BETWEEN 0 AND 100", name="ck_exams_pass_threshold_range"
        ),
        sa.CheckConstraint("status IN ('draft','published','closed')", name="ck_exams_status"),
        # Composite target so children can pin (id, company_id) and never drift.
        sa.UniqueConstraint("id", "company_id", name="uq_exams_id_company"),
    )
    op.create_index("idx_exams_company", "exams", ["company_id"])
    op.create_index("idx_exams_company_status", "exams", ["company_id", "status"])

    # ---------------- exam_questions ----------------
    op.create_table(
        "exam_questions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", JSONB(), nullable=False),  # array of 2-6 strings
        sa.Column("correct_index", sa.SmallInteger(), nullable=False),  # SECRET — never served
        sa.Column("points", sa.SmallInteger(), server_default="1", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_exam_questions"),
        # Composite FK pins company_id to the exam's — a cross-tenant mismatch
        # is rejected by the DB.
        sa.ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_questions_exam", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_exam_questions_company", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "jsonb_array_length(options) BETWEEN 2 AND 6",
            name="ck_exam_questions_options_count",
        ),
        sa.CheckConstraint("points >= 1", name="ck_exam_questions_points_positive"),
        sa.CheckConstraint(
            "correct_index >= 0 AND correct_index < jsonb_array_length(options)",
            name="ck_exam_questions_correct_index_range",
        ),
    )
    op.create_index("idx_exam_questions_company", "exam_questions", ["company_id"])
    op.create_index(
        "idx_exam_questions_exam_position", "exam_questions", ["exam_id", "position"]
    )
    # At most one LIVE question per (exam, position) — a soft-deleted question
    # frees its position. Lets reorder swap positions within one transaction.
    op.execute(
        "CREATE UNIQUE INDEX uq_exam_questions_exam_position "
        "ON exam_questions (exam_id, position) WHERE deleted_at IS NULL"
    )

    # ---------------- exam_assignments ----------------
    op.create_table(
        "exam_assignments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("applicant_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        # hmac_sha256(raw_token, exam_link_secret). The raw token is only in the URL.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # invited | started | completed | expired | revoked
        sa.Column("status", sa.Text(), server_default="invited", nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_exam_assignments"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_exam_assignments_company", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_assignments_exam", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"], ["applicants.id"],
            name="fk_exam_assignments_applicant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_exam_assignments_created_by", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_exam_assignments_token_hash"),
        sa.CheckConstraint(
            "status IN ('invited','started','completed','expired','revoked')",
            name="ck_exam_assignments_status",
        ),
        sa.UniqueConstraint("id", "company_id", name="uq_exam_assignments_id_company"),
    )
    op.create_index("idx_exam_assignments_company", "exam_assignments", ["company_id"])
    op.create_index("idx_exam_assignments_exam", "exam_assignments", ["exam_id"])
    op.create_index("idx_exam_assignments_applicant", "exam_assignments", ["applicant_id"])
    # At most one ACTIVE (invited) assignment per applicant+exam — re-assign must
    # revoke the prior one first (token rotation).
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_assignments_active "
        "ON exam_assignments (exam_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status = 'invited'"
    )

    # ---------------- exam_attempts ----------------
    op.create_table(
        "exam_attempts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("applicant_id", sa.UUID(), nullable=False),
        sa.Column("assignment_id", sa.UUID(), nullable=True),
        sa.Column("attempt_no", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("answers", JSONB(), nullable=True),  # {question_id: selected_index}
        # Frozen answer key + points snapshotted at submit (audit-stable grading).
        sa.Column("graded_snapshot", JSONB(), nullable=True),
        sa.Column("score_raw", sa.Integer(), nullable=True),
        sa.Column("score_max", sa.Integer(), nullable=True),
        sa.Column("score_percent", sa.SmallInteger(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        # in_progress | submitted | expired
        sa.Column("status", sa.Text(), server_default="in_progress", nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_exam_attempts"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_exam_attempts_company", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_attempts_exam", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"], ["applicants.id"],
            name="fk_exam_attempts_applicant", ondelete="CASCADE",
        ),
        # RESTRICT so the audit link to the originating assignment can't be severed.
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["exam_assignments.id"],
            name="fk_exam_attempts_assignment", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "score_percent IS NULL OR (score_percent BETWEEN 0 AND 100)",
            name="ck_exam_attempts_percent_range",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress','submitted','expired')", name="ck_exam_attempts_status"
        ),
        sa.UniqueConstraint(
            "exam_id", "applicant_id", "attempt_no", name="uq_exam_attempts_exam_applicant_no"
        ),
    )
    op.create_index("idx_exam_attempts_company", "exam_attempts", ["company_id"])
    op.create_index("idx_exam_attempts_exam", "exam_attempts", ["exam_id"])
    op.create_index("idx_exam_attempts_applicant", "exam_attempts", ["applicant_id"])
    op.create_index("idx_exam_attempts_exam_score", "exam_attempts", ["exam_id", "score_percent"])
    # At most one LIVE (non-expired) attempt per applicant+exam — collapses
    # concurrent /start races. A legitimate retake opens after the prior attempt
    # is 'submitted' (and only when exam.allow_retake), gated in the app layer.
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_attempts_one_live "
        "ON exam_attempts (exam_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status <> 'expired'"
    )


def downgrade() -> None:
    op.drop_index("uix_exam_attempts_one_live", table_name="exam_attempts")
    op.drop_index("idx_exam_attempts_exam_score", table_name="exam_attempts")
    op.drop_index("idx_exam_attempts_applicant", table_name="exam_attempts")
    op.drop_index("idx_exam_attempts_exam", table_name="exam_attempts")
    op.drop_index("idx_exam_attempts_company", table_name="exam_attempts")
    op.drop_table("exam_attempts")

    op.drop_index("uix_exam_assignments_active", table_name="exam_assignments")
    op.drop_index("idx_exam_assignments_applicant", table_name="exam_assignments")
    op.drop_index("idx_exam_assignments_exam", table_name="exam_assignments")
    op.drop_index("idx_exam_assignments_company", table_name="exam_assignments")
    op.drop_table("exam_assignments")

    op.drop_index("uq_exam_questions_exam_position", table_name="exam_questions")
    op.drop_index("idx_exam_questions_exam_position", table_name="exam_questions")
    op.drop_index("idx_exam_questions_company", table_name="exam_questions")
    op.drop_table("exam_questions")

    op.drop_index("idx_exams_company_status", table_name="exams")
    op.drop_index("idx_exams_company", table_name="exams")
    op.drop_table("exams")
