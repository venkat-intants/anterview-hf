"""Recruiter-defined application questions, and the answers to them.

The last thing standing between the Builder and the apply form. HR writes the
questions once against an opening; the public form renders whatever that
opening asks; the answers arrive with the application and sit beside the CV.

TWO TABLES

``application_questions``
    Per opening, ordered, with a kind that decides how the form renders it and
    how the answer is validated. ``options`` is meaningful only for the choice
    kinds and is a jsonb array of strings — the same shape decision as the
    requisition's skill lists, and for the same reason: short ordered lists,
    always read whole, never joined to.

``application_answers``
    One row per (enrolment, question). Keyed on the ENROLMENT rather than the
    applicant because a question belongs to an opening: the same person
    applying to two roles answers two different sets, and hanging answers off
    the applicant would collapse them into one.

    ``answer`` is jsonb so one column can hold every kind — a string, a number,
    a boolean, or an array for a multi-choice. A column per kind would be five
    mostly-null columns and a CASE at every read.

THE INVARIANT: AN ANSWERED QUESTION CANNOT BE REWORDED
    ``prompt`` and ``kind`` are frozen once an answer exists, and the check is
    in the service layer beside the workflow's identical rule.

    Without it, editing "Do you have a work visa?" into "Do you need visa
    sponsorship?" silently inverts the meaning of every stored "yes" — and
    nothing in the data would show it happened. The stored answer would still
    read as an answer to the new question, which is worse than losing it.

    So an answered question can be REORDERED, made optional, given help text,
    or retired — none of which changes what an existing answer means — but its
    text and its type are fixed. Retiring soft-deletes it: the answers stay
    readable on the applications that gave them, and the question stops being
    asked. HR who genuinely want to ask something different add a new question,
    which is the honest representation of what happened.

WHY NO FILE QUESTION YET
    The spec's example list includes "Upload portfolio [File]". That is not a
    question kind, it is a second upload path — S3 keys, size limits, virus
    posture, an erasure story of its own, and a DPDP consent line that does not
    currently mention it. It belongs with the portfolio-round work §10.3
    already defers, not smuggled in behind a dropdown.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "a1c3e5b7d9f2"
down_revision: str | None = "d5f7b9c1e3a6"
branch_labels: str | None = None
depends_on: str | None = None

QUESTION_KINDS = "('short_text','long_text','number','single_choice','multi_choice','yes_no')"


def upgrade() -> None:
    op.create_table(
        "application_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("options", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_application_questions"),
        # Composite, matching every other tenant-scoped table here: it is what
        # makes a cross-tenant answer impossible at the database level rather
        # than only in a handler.
        sa.UniqueConstraint("id", "company_id", name="uq_application_questions_id_company"),
        sa.ForeignKeyConstraint(
            ["requisition_id", "company_id"],
            ["job_requisitions.id", "job_requisitions.company_id"],
            name="fk_application_questions_requisition",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"kind IN {QUESTION_KINDS}", name="ck_application_questions_kind"),
        sa.CheckConstraint("position >= 0", name="ck_application_questions_position"),
        # A choice question with nothing to choose renders as an empty dropdown
        # the candidate cannot satisfy — and if it is required, an application
        # nobody can submit.
        sa.CheckConstraint(
            "kind NOT IN ('single_choice','multi_choice')"
            " OR jsonb_array_length(options) >= 2",
            name="ck_application_questions_options",
        ),
    )
    # Partial: the ordering only has to be unique among questions still being
    # asked. A retired question keeps its position for the answers that
    # reference it, and must not block the live one that took its place.
    op.create_index(
        "uq_application_questions_position",
        "application_questions",
        ["requisition_id", "position"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "application_answers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        # One column for every kind: string, number, boolean, or array.
        sa.Column("answer", JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_application_answers"),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"],
            ["enrolments.id", "enrolments.company_id"],
            name="fk_application_answers_enrolment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "company_id"],
            ["application_questions.id", "application_questions.company_id"],
            name="fk_application_answers_question",
            ondelete="CASCADE",
        ),
        # One answer per question per application. A repeat submission updates
        # rather than accumulating, and a duplicate would make "what did they
        # say?" ambiguous.
        sa.UniqueConstraint(
            "enrolment_id", "question_id", name="uq_application_answers_one_per_question"
        ),
    )
    op.create_index(
        "ix_application_answers_enrolment",
        "application_answers",
        ["enrolment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_application_answers_enrolment", table_name="application_answers")
    op.drop_table("application_answers")
    op.drop_index("uq_application_questions_position", table_name="application_questions")
    op.drop_table("application_questions")
