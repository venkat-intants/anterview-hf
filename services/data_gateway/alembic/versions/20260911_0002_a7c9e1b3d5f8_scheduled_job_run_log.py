"""Keep a history of scheduled and background job runs — A6.

``scheduled_job_runs`` holds one row per job: the latest run and nothing else.
That answers "did the purge run last night?" and nothing more. It cannot answer
"has it been failing all week?", "did the catch-up after Tuesday's outage
actually run it?", or "when did the reminder sweep last fail, and why?" — and
the last of those is exactly what an operator asks when a candidate says they
never got a reminder.

This is the history. One row per run of a calendar job (ok or error), and one
row per FAILED pass of an interval loop — a loop that succeeds every five
minutes is recorded in the summary row, not here, or this table would be a
few hundred rows a day of "fine". ``trigger`` says what started the run:
``cron`` (the scheduler at its hour), ``catchup`` (the overdue check found a
missed window), ``manual``, or ``loop`` (an interval worker).

Pruned after 90 days by the catch-up loop. Operational telemetry: no user
column, no personal data.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9e1b3d5f8"
down_revision: str | None = "f3b5d7e9a1c4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_job_run_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_scheduled_job_run_log"),
        sa.CheckConstraint("status IN ('ok','error')", name="ck_scheduled_job_run_log_status"),
        sa.CheckConstraint(
            "trigger IN ('cron','catchup','manual','loop')",
            name="ck_scheduled_job_run_log_trigger",
        ),
    )
    # The only read is "this job's recent runs, newest first".
    op.create_index(
        "ix_scheduled_job_run_log_job_time",
        "scheduled_job_run_log",
        ["job_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_job_run_log_job_time", table_name="scheduled_job_run_log")
    op.drop_table("scheduled_job_run_log")
