"""Workflow dry-run results — PH4-O2.

Revision ID: c4d6e8f0a2b3
Revises: b3c5d7e9f1a2
Create Date: 2026-09-18

A simulation walks a workflow version with synthetic candidates (SIM-001, …)
through the same routing function the runner uses, and records what happened:
the trace of every path, and each error and warning found on the way. It
writes nothing else — no enrolment, no email, no invite, no stage change. This
table is the one thing a simulation leaves behind, so HR can see the last run
beside the version it ran against, and a reviewer approving that version can
read it (PH4-O6).

``fingerprint`` is a hash of the version's configuration at the moment it ran.
When the version changes afterwards the result is shown as stale rather than
silently vouching for something it never tested.

Append-only, like the other records that exist to be evidence: a result that
could be edited after the fact would prove nothing about the version it names.
The synthetic candidates are not people — no personal data is stored here.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4d6e8f0a2b3"
down_revision: str | None = "b3c5d7e9f1a2"
branch_labels: str | None = None
depends_on: str | None = None

APPEND_ONLY = """
CREATE OR REPLACE FUNCTION workflow_simulations_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND NOT EXISTS (SELECT 1 FROM workflows w WHERE w.id = OLD.workflow_id) THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE'
       AND NEW.run_by_user_id IS NULL AND OLD.run_by_user_id IS NOT NULL
       AND (to_jsonb(NEW) - 'run_by_user_id') = (to_jsonb(OLD) - 'run_by_user_id') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'workflow_simulations is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "workflow_simulations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("run_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.Integer(), nullable=False),
        sa.Column("scenarios", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_simulations"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "company_id"], ["workflows.id", "workflows.company_id"],
            name="fk_workflow_simulations_workflow", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_by_user_id"], ["users.id"],
            name="fk_workflow_simulations_run_by", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'warnings', 'failed')", name="ck_workflow_simulations_status"
        ),
        sa.CheckConstraint(
            "errors >= 0 AND warnings >= 0 AND scenarios >= 0",
            name="ck_workflow_simulations_counts",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (errors > 0)", name="ck_workflow_simulations_failed_iff_errors"
        ),
    )
    op.create_index(
        "ix_workflow_simulations_workflow", "workflow_simulations",
        ["workflow_id", "created_at"],
    )
    op.execute(APPEND_ONLY)
    op.execute(
        "CREATE TRIGGER workflow_simulations_append_only"
        " BEFORE UPDATE OR DELETE ON workflow_simulations"
        " FOR EACH ROW EXECUTE FUNCTION workflow_simulations_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS workflow_simulations_append_only ON workflow_simulations"
    )
    op.execute("DROP FUNCTION IF EXISTS workflow_simulations_append_only()")
    op.drop_index("ix_workflow_simulations_workflow", table_name="workflow_simulations")
    op.drop_table("workflow_simulations")
