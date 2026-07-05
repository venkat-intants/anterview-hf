"""coding exams — exams.kind discriminator + coding_questions (HR workflow Phase 2)

Adds a coding-round exam type ALONGSIDE the MCQ exam, reusing the generic exam
shell + the type-agnostic magic-link (exam_assignments) and attempt (exam_attempts)
plumbing. Only two schema changes are needed:

  * exams.kind  TEXT NOT NULL DEFAULT 'mcq'  (CHECK in ('mcq','coding')) — existing
    exams become 'mcq' with no behaviour change; a coding exam sets kind='coding'.
  * coding_questions — mirrors exam_questions, but coding-shaped: a prompt, the
    allowed languages, optional starter code, a SECRET reference_solution (never
    served, like correct_index), and JSONB test_cases [{stdin, expected_output,
    is_sample, weight}] (hidden cases' expected_output is never served to the
    candidate, same discipline as correct_index).

Coding submissions reuse exam_attempts (answers/graded_snapshot are flexible JSONB,
score columns identical) — no new attempt or submission table.

Additive + safe to run live. Revision id b1d3f5a7c9e0.
Revises: a9c1e2f3b4d5
Create Date: 2026-06-27 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "b1d3f5a7c9e0"
down_revision: str | None = "a9c1e2f3b4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- exams.kind discriminator (existing rows default to 'mcq') ---
    op.add_column(
        "exams",
        sa.Column("kind", sa.Text(), server_default="mcq", nullable=False),
    )
    op.create_check_constraint("ck_exams_kind", "exams", "kind IN ('mcq','coding')")

    # --- coding_questions (mirror of exam_questions) ---
    op.create_table(
        "coding_questions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("exam_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        # Optional code pre-filled in the candidate's editor.
        sa.Column("starter_code", sa.Text(), nullable=True),
        # SECRET — HR-only, NEVER serialized to the candidate (like correct_index).
        sa.Column("reference_solution", sa.Text(), nullable=True),
        # JSONB array of language slugs, e.g. ["python","javascript","cpp"].
        sa.Column("allowed_languages", JSONB(), nullable=False),
        # JSONB array of {stdin, expected_output, is_sample(bool), weight(int>=1)}.
        # Hidden (is_sample=false) cases' expected_output is never served.
        sa.Column("test_cases", JSONB(), nullable=False),
        sa.Column("time_limit_ms", sa.Integer(), server_default="5000", nullable=False),
        sa.Column("points", sa.SmallInteger(), server_default="100", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_coding_questions"),
        # Composite FK pins company_id to the exam so it can't drift cross-tenant.
        sa.ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_coding_questions_exam", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_coding_questions_company", ondelete="CASCADE",
        ),
        sa.CheckConstraint("points >= 1", name="ck_coding_questions_points_positive"),
        sa.CheckConstraint(
            "jsonb_array_length(allowed_languages) >= 1",
            name="ck_coding_questions_languages_count",
        ),
        sa.CheckConstraint(
            "jsonb_array_length(test_cases) >= 1", name="ck_coding_questions_test_cases_count"
        ),
        sa.CheckConstraint("time_limit_ms >= 100", name="ck_coding_questions_time_limit"),
    )
    op.create_index("idx_coding_questions_exam", "coding_questions", ["exam_id", "company_id"])
    op.create_index(
        "uq_coding_questions_exam_position",
        "coding_questions",
        ["exam_id", "position"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_coding_questions_exam_position", table_name="coding_questions")
    op.drop_index("idx_coding_questions_exam", table_name="coding_questions")
    op.drop_table("coding_questions")
    op.drop_constraint("ck_exams_kind", "exams", type_="check")
    op.drop_column("exams", "kind")
