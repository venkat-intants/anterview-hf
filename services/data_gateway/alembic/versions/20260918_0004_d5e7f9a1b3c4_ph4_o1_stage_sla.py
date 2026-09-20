"""Stage owners, SLAs and exception records — PH4-O1.

Revision ID: d5e7f9a1b3c4
Revises: c4d6e8f0a2b3
Create Date: 2026-09-18

WHAT A STAGE IS
Each round of a workflow version, plus the final human decision that follows
the last one. ``workflow_stage_settings`` gives a stage an owner (the HR
manager answerable for it) and an SLA in hours. ``round_id`` NULL is the
decision stage.

WHY NOT COLUMNS ON workflow_rounds
Rounds are frozen once published, and they should be: thresholds and criteria
decide who advances. Who is answerable for a stage and how long it should take
are operational — people go on leave, targets get tightened — so they live
beside the rubric rather than in it, like interview kits (PH4-A5). Changing one
is audited and moves nobody. Cloning a version carries them forward.

THE CLOCK
``enrolment_stage_entered_at`` is when an application reached the stage it is
in now: the latest ledger entry that changed its status or its round. The
existing ``enrolment_state_since`` counts every ledger row, including notes
that move nothing, and a note must not restart an SLA. Nothing is stored about
lateness: overdue is computed when it is read, so it is never stale.

EXCEPTIONS
``stage_exceptions`` records that normal processing cannot proceed for an
application — the candidate asked to reschedule, the interviewer is off sick —
with a reason, an owner who has to act, who raised it and when. It changes no
status and no round: an exception is information for a person, never a
decision (D-05). Resolved, never deleted; the reason is prose about a candidate,
so DPDP erasure redacts it (erasure step 5f).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d5e7f9a1b3c4"
down_revision: str | None = "c4d6e8f0a2b3"
branch_labels: str | None = None
depends_on: str | None = None

STAGE_ENTERED_AT = """
CREATE OR REPLACE FUNCTION enrolment_stage_entered_at(
    p_enrolment_id uuid, p_fallback timestamptz
)
RETURNS timestamptz
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT max(st.occurred_at) FROM stage_transitions st
          WHERE st.enrolment_id = p_enrolment_id
            AND (st.from_status IS DISTINCT FROM st.to_status
                 OR st.from_round_id IS DISTINCT FROM st.to_round_id)),
        p_fallback)
$$
"""


# Erasure (step 5f) redacts an exception's prose. The CHECK above keeps a
# redacted row consistent; this keeps it redacted — ``redacted_at`` cannot be
# cleared to let a reason or note be written back about an erased person.
STAY_REDACTED = """
CREATE OR REPLACE FUNCTION stage_exceptions_stay_redacted() RETURNS trigger AS $$
BEGIN
    IF OLD.redacted_at IS NOT NULL AND NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
        RAISE EXCEPTION
            'stage exception % was redacted on erasure and stays redacted', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
STAY_REDACTED_HOOK = """
CREATE TRIGGER stage_exceptions_redaction_final
    BEFORE UPDATE ON stage_exceptions
    FOR EACH ROW EXECUTE FUNCTION stage_exceptions_stay_redacted()
"""


def upgrade() -> None:
    op.create_table(
        "workflow_stage_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("sla_hours", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_stage_settings"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "company_id"], ["workflows.id", "workflows.company_id"],
            name="fk_stage_settings_workflow", ondelete="CASCADE",
        ),
        # The round must belong to the same workflow.
        sa.ForeignKeyConstraint(
            ["round_id", "workflow_id"], ["workflow_rounds.id", "workflow_rounds.workflow_id"],
            name="fk_stage_settings_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_stage_settings_owner", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_stage_settings_updated_by", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "sla_hours IS NULL OR (sla_hours >= 1 AND sla_hours <= 8760)",
            name="ck_stage_settings_sla_range",
        ),
    )
    # One row per round, and one decision-stage row, per version.
    op.create_index(
        "uq_stage_settings_round", "workflow_stage_settings", ["workflow_id", "round_id"],
        unique=True, postgresql_where=sa.text("round_id IS NOT NULL"),
    )
    op.create_index(
        "uq_stage_settings_decision", "workflow_stage_settings", ["workflow_id"],
        unique=True, postgresql_where=sa.text("round_id IS NULL"),
    )

    op.create_table(
        "stage_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=True),
        sa.Column("stage_label", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("raised_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "raised_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("redacted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stage_exceptions"),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_stage_exceptions_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["workflow_rounds.id"],
            name="fk_stage_exceptions_round", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_stage_exceptions_owner", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["raised_by_user_id"], ["users.id"],
            name="fk_stage_exceptions_raised_by", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"],
            name="fk_stage_exceptions_resolved_by", ondelete="SET NULL",
        ),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_stage_exceptions_status"),
        sa.CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_stage_exceptions_resolved_at",
        ),
        sa.CheckConstraint(
            "char_length(reason) BETWEEN 10 AND 1000", name="ck_stage_exceptions_reason_len"
        ),
        sa.CheckConstraint(
            "resolution_note IS NULL OR char_length(resolution_note) <= 1000",
            name="ck_stage_exceptions_note_len",
        ),
        sa.CheckConstraint(
            "char_length(stage_label) BETWEEN 1 AND 200", name="ck_stage_exceptions_label_len"
        ),
        sa.CheckConstraint(
            "redacted_at IS NULL OR (reason = '[redacted]' AND resolution_note IS NULL)",
            name="ck_stage_exceptions_redacted",
        ),
    )
    op.create_index(
        "ix_stage_exceptions_open", "stage_exceptions", ["company_id", "raised_at"],
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_stage_exceptions_enrolment", "stage_exceptions", ["enrolment_id"])
    op.execute(STAGE_ENTERED_AT)
    op.execute(STAY_REDACTED)
    op.execute(STAY_REDACTED_HOOK)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS stage_exceptions_redaction_final ON stage_exceptions")
    op.execute("DROP FUNCTION IF EXISTS stage_exceptions_stay_redacted()")
    op.execute("DROP FUNCTION IF EXISTS enrolment_stage_entered_at(uuid, timestamptz)")
    op.drop_index("ix_stage_exceptions_enrolment", table_name="stage_exceptions")
    op.drop_index("ix_stage_exceptions_open", table_name="stage_exceptions")
    op.drop_table("stage_exceptions")
    op.drop_index("uq_stage_settings_decision", table_name="workflow_stage_settings")
    op.drop_index("uq_stage_settings_round", table_name="workflow_stage_settings")
    op.drop_table("workflow_stage_settings")
