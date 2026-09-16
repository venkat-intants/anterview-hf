"""Openings that publish themselves at a chosen time — PH3-B4a.

Revision ID: f6b8d0e2a4c7
Revises: e5a7c9d1f3b6
Create Date: 2026-09-16

``publish_at`` is a REQUEST, not a state. It says "turn public_apply_enabled on
at this moment"; the publisher loop does the turning, and the moment it does it
clears ``publish_at`` and stamps ``published_at``. So the column is never a
second source of truth for "is this live" — the publish gate still reads
``public_apply_enabled`` and the four other conditions, exactly as before, and
PH3-B0's single predicate is untouched by this story.

WHY A SEPARATE ``publish_at_set_by``/``published_at`` PAIR
The acceptance criteria ask for scheduling to be audited: who scheduled, who
changed it, who cancelled, and when execution actually happened. The audit_log
carries the first three as events. ``published_at`` is on the row because it
answers a question the console asks about the CURRENT state ("did this go live
automatically, and when?") without a log query.

THE HONEST TOLERANCE
The publisher is an interval loop, not a cron trigger, for the reason
``app/scheduling.py`` documents at length: a CronTrigger cannot fire while the
container is suspended, and the demo Space sleeps after ~48h. An interval loop
resumes and catches up. What that buys is "within one interval of the scheduled
time on a running instance" — NOT "at 09:00 exactly", and on a cold Space not
until it wakes. That tolerance is stated in the API docstring and in the console
copy rather than left for somebody to discover during a demo.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f6b8d0e2a4c7"
down_revision: str | None = "e5a7c9d1f3b6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "job_requisitions",
        sa.Column("publish_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "job_requisitions", sa.Column("publish_at_set_by_user_id", sa.Uuid(), nullable=True)
    )
    # When the scheduler actually flipped the switch. Distinct from publish_at,
    # which is when somebody asked for it — and the gap between the two is the
    # tolerance, which an operator should be able to see rather than infer.
    op.add_column(
        "job_requisitions",
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_requisitions_publish_scheduler",
        "job_requisitions", "users", ["publish_at_set_by_user_id"], ["id"],
        ondelete="SET NULL",
    )

    # The publisher's own query: "anything due?". Partial on exactly the rows
    # that can ever be due, so the loop costs an index probe rather than a scan
    # of every requisition the platform has, every interval, forever.
    op.create_index(
        "ix_job_requisitions_publish_due",
        "job_requisitions",
        ["publish_at"],
        unique=False,
        postgresql_where=sa.text("publish_at IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_requisitions_publish_due", table_name="job_requisitions")
    op.drop_constraint(
        "fk_job_requisitions_publish_scheduler", "job_requisitions", type_="foreignkey"
    )
    op.drop_column("job_requisitions", "published_at")
    op.drop_column("job_requisitions", "publish_at_set_by_user_id")
    op.drop_column("job_requisitions", "publish_at")
