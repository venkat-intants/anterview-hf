"""Structured decision reason codes — PH4-O4.

Revision ID: f4b6d8e0a2c3
Revises: e3a5c7d9f1b2
Create Date: 2026-09-17

A final decision has always carried a free-text reason, written to the
append-only ``stage_transitions`` ledger and the audit log. Free text is right
for the person reading one decision and useless for anyone asking a question
across a thousand: "how often do we reject on compensation?" has no answer when
every recruiter phrases it differently. This adds a structured category ALONGSIDE
the free text, never instead of it.

THE TAXONOMY IS PER COMPANY
``decision_reasons`` holds each company's categories. Every company starts with
the same defaults (seeded by the application, idempotently, on first use), can
add its own, and can retire any — including a default. Nothing is ever deleted,
because a decision that referenced it must stay explainable.

THE DECISION SNAPSHOTS THE LABEL
``stage_transitions`` gains ``reason_code`` AND ``reason_label``. The code is
what analytics aggregates on; the label is a copy of what the person actually
chose, taken at the moment of the decision. Rename or retire a category next
year and every historical decision still reads exactly as it was recorded — the
ledger does not join to a table that can change underneath it. Both are NULL
for decisions recorded before this migration, and for non-terminal moves; the
CHECK makes them travel together.

Adding nullable columns to ``stage_transitions`` is compatible with its
append-only trigger: that trigger refuses UPDATE and DELETE of rows, not schema
change, and every existing row simply reads NULL.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f4b6d8e0a2c3"
down_revision: str | None = "e3a5c7d9f1b2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "decision_reasons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("applies_to", sa.Text(), nullable=False, server_default="both"),
        sa.Column(
            "requires_explanation", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="100"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decision_reasons"),
        sa.UniqueConstraint("company_id", "code", name="uq_decision_reasons_company_code"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_decision_reasons_company",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "applies_to IN ('hired', 'rejected', 'both')", name="ck_decision_reasons_applies_to"
        ),
        # A code is a stable machine key: lower-case, underscores, bounded.
        sa.CheckConstraint(
            "code ~ '^[a-z][a-z0-9_]{1,63}$'", name="ck_decision_reasons_code_format"
        ),
        sa.CheckConstraint(
            "char_length(btrim(label)) BETWEEN 2 AND 120", name="ck_decision_reasons_label_len"
        ),
    )

    op.add_column("stage_transitions", sa.Column("reason_code", sa.Text(), nullable=True))
    op.add_column("stage_transitions", sa.Column("reason_label", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_stage_transitions_reason_pair",
        "stage_transitions",
        "(reason_code IS NULL) = (reason_label IS NULL)",
    )
    # The access path PH5 analytics will use: a company's decisions by reason.
    op.create_index(
        "ix_stage_transitions_reason_code",
        "stage_transitions",
        ["company_id", "reason_code", "occurred_at"],
        postgresql_where=sa.text("reason_code IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_stage_transitions_reason_code", table_name="stage_transitions")
    op.drop_constraint("ck_stage_transitions_reason_pair", "stage_transitions", type_="check")
    op.drop_column("stage_transitions", "reason_label")
    op.drop_column("stage_transitions", "reason_code")
    op.drop_table("decision_reasons")
