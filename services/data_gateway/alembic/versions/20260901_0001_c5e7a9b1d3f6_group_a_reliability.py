"""Group A reliability foundation — scheduled-job bookkeeping + reconciliation backoff

Two small tables, both operational rather than domain data.

``scheduled_job_runs`` records when each cron-triggered job last completed. The
Space this runs on sleeps after ~48h of inactivity, and APScheduler's CronTrigger
cannot fire while the container is suspended — a job pinned to 02:00 UTC simply
never runs if nobody visits overnight. Recording the last run lets the scheduler
ask "is this overdue?" on startup and on every tick, so a missed window is caught
on the next wake instead of silently skipped until someone notices the DPDP purge
has not run for a fortnight.

``reconciliation_state`` gives the reconciliation loop a per-row backoff. The loop
finds work by the ABSENCE of data (an applicant with no ATS score, a session with
no scorecard), which is the right query because it needs no bookkeeping to stay
correct — but it means a permanently broken row is re-selected every cycle and
consumes the batch forever. This table is the exception list: attempts, the last
error, and when to try again. It is deliberately NOT the source of truth for what
needs doing; deleting every row here only causes the next cycle to retry
everything immediately, which is safe.

Revision ID: c5e7a9b1d3f6
Revises:     b4d6f8a0c2e5
Create Date: 2026-09-01 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e7a9b1d3f6"
down_revision: str | None = "b4d6f8a0c2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── scheduled_job_runs ────────────────────────────────────────────────
    # One row per named job. job_id is the APScheduler id, so the two stay in
    # step by construction rather than by a mapping someone has to maintain.
    op.create_table(
        "scheduled_job_runs",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("last_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_status", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("run_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name="pk_scheduled_job_runs"),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN ('ok','error','running')",
            name="ck_scheduled_job_runs_status",
        ),
    )

    # ── reconciliation_state ──────────────────────────────────────────────
    # (kind, ref_id) is the natural key: one row per thing-that-failed, per
    # check. next_attempt_at drives the backoff; the partial index below is the
    # only access path the loop uses.
    op.create_table(
        "reconciliation_state",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.UUID(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("gave_up_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("kind", "ref_id", name="pk_reconciliation_state"),
    )
    # The loop asks "which of these are still backing off?" for one kind at a
    # time, so the index leads with kind. Rows that have given up are excluded:
    # they are retained as evidence for the operator, not as work.
    op.create_index(
        "ix_reconciliation_state_due",
        "reconciliation_state",
        ["kind", "next_attempt_at"],
        postgresql_where=sa.text("gave_up_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_state_due", table_name="reconciliation_state")
    op.drop_table("reconciliation_state")
    op.drop_table("scheduled_job_runs")
