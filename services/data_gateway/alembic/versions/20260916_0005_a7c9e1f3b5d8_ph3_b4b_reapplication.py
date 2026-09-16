"""Reapplication cooldowns — PH3-B4b.

Revision ID: a7c9e1f3b5d8
Revises: f6b8d0e2a4c7
Create Date: 2026-09-16

WHAT A COOLDOWN ACTUALLY GUARDS
Not a duplicate application — one enrolment per (requisition, applicant) is
already a database invariant, and a repeat submission returns the first
application rather than creating a second. The case this addresses is the one
after that: somebody was turned down for this opening and applies again next
week, and the week after.

So the window is measured from the REJECTION, not from the application. A
candidate who applied six months ago and was rejected yesterday is one day into
their cooldown, not six months past it. The rejection time comes from
``stage_transitions``, which is append-only and records who moved them and when
— ``enrolments.updated_at`` moves on a rescore and would make the window
whatever the reconciler last touched.

NULL MEANS NO COOLDOWN, and that is the default, so nothing changes for any
opening until somebody sets one. The alternative — a platform-wide default —
would silently start refusing applications nobody had decided to refuse.

THE OVERRIDE LIVES ON THE OLD ENROLMENT
``reapply_override_at`` is set on the enrolment the cooldown is being measured
from. That is the row HR is actually looking at when they decide to let somebody
back in, it needs no new table, and it keeps the grant attached to the specific
rejection it forgives rather than being a blanket exemption on the person.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9e1f3b5d8"
down_revision: str | None = "f6b8d0e2a4c7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "job_requisitions",
        sa.Column("reapply_cooldown_days", sa.SmallInteger(), nullable=True),
    )
    # 0 is meaningful and distinct from NULL: "we considered this and decided
    # there is no waiting period" versus "nobody has set one".
    op.create_check_constraint(
        "ck_job_requisitions_reapply_cooldown",
        "job_requisitions",
        "reapply_cooldown_days IS NULL"
        " OR (reapply_cooldown_days >= 0 AND reapply_cooldown_days <= 1095)",
    )

    op.add_column(
        "enrolments",
        sa.Column("reapply_override_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "enrolments", sa.Column("reapply_override_by_user_id", sa.Uuid(), nullable=True)
    )
    op.add_column("enrolments", sa.Column("reapply_override_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_enrolments_reapply_override_by",
        "enrolments", "users", ["reapply_override_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_enrolments_reapply_override_by", "enrolments", type_="foreignkey")
    op.drop_column("enrolments", "reapply_override_reason")
    op.drop_column("enrolments", "reapply_override_by_user_id")
    op.drop_column("enrolments", "reapply_override_at")
    op.drop_constraint(
        "ck_job_requisitions_reapply_cooldown", "job_requisitions", type_="check"
    )
    op.drop_column("job_requisitions", "reapply_cooldown_days")
