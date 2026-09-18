"""How much interviewing one person should carry — PH4-O5.

Revision ID: a8c0e2f4b6d9
Revises: e6f8a0b2c4d6
Create Date: 2026-09-19

Workload is COMPUTED (from interview sessions and open scorecards) and never
stored, so it cannot go stale. What is stored is the line HR draws: at most so
many sessions a day and a week for one interviewer. With no row, the company
default applies (4 a day, 15 a week — see ``panel_workload.DEFAULT_*``).

Over-allocation is a flag on HR's screen, not a refusal: the person who knows
that an interviewer volunteered for a busy week is HR, not a threshold.
Calibration stores nothing at all.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a8c0e2f4b6d9"
down_revision: str | None = "e6f8a0b2c4d6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "interviewer_capacity",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("max_sessions_per_day", sa.SmallInteger(), nullable=True),
        sa.Column("max_sessions_per_week", sa.SmallInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("user_id", name="pk_interviewer_capacity"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name="fk_interviewer_capacity_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_interviewer_capacity_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"],
                                name="fk_interviewer_capacity_updated_by", ondelete="SET NULL"),
        sa.CheckConstraint(
            "max_sessions_per_day IS NULL OR max_sessions_per_day BETWEEN 1 AND 24",
            name="ck_interviewer_capacity_day",
        ),
        sa.CheckConstraint(
            "max_sessions_per_week IS NULL OR max_sessions_per_week BETWEEN 1 AND 100",
            name="ck_interviewer_capacity_week",
        ),
    )


def downgrade() -> None:
    op.drop_table("interviewer_capacity")
