"""Conditional routing between workflow rounds — PH4-O3.

Revision ID: b3c5d7e9f1a2
Revises: a2b4c6d8e0f1
Create Date: 2026-09-18

A round has always had one exit, ``on_pass_next_round_id``: pass and move on,
or fall below the threshold and be held. This adds the two branches the builder
now offers, and reuses that column as the pass branch rather than replacing it:

* ``on_fail_next_round_id`` — below the threshold, route the candidate to
  another round (say, a human review) instead of holding them. NULL keeps
  today's behaviour: held, for a person.
* ``fast_track_min_percent`` + ``on_fast_track_next_round_id`` — a score at or
  above a higher bar skips ahead. Both or neither.

WHAT A BRANCH CAN NEVER DO
There is no destination called "rejected". Every branch ends at another round,
at a hold, or at the final human decision — the same three places a candidate
could reach before. AI and scoring route or hold; only a person ends a
candidacy (D-05). That is enforced by the absence of a column that could say
otherwise.

A branch may only point inside its own workflow: the composite foreign keys use
``(target, workflow_id)`` against ``workflow_rounds (id, workflow_id)``. Cycles
and unreachable rounds are graph properties, so they are checked by validation
(and by the O2 simulation) rather than by a constraint. Published versions stay
immutable: the existing round trigger covers these columns like every other.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b3c5d7e9f1a2"
down_revision: str | None = "a2b4c6d8e0f1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflow_rounds_id_workflow", "workflow_rounds", ["id", "workflow_id"]
    )
    op.add_column("workflow_rounds", sa.Column("on_fail_next_round_id", sa.Uuid()))
    op.add_column(
        "workflow_rounds", sa.Column("fast_track_min_percent", sa.Numeric(5, 2))
    )
    op.add_column("workflow_rounds", sa.Column("on_fast_track_next_round_id", sa.Uuid()))
    # Same-workflow targets. Deleting a draft round is a soft delete, and the
    # builder clears branches pointing at it; a hard delete only ever comes from
    # the cascade that takes the whole workflow, and every referencing row goes
    # in the same statement. So NO ACTION: a lone hard delete of a round that is
    # still a branch target is refused rather than silently rerouting anyone.
    op.create_foreign_key(
        "fk_workflow_rounds_on_fail", "workflow_rounds", "workflow_rounds",
        ["on_fail_next_round_id", "workflow_id"], ["id", "workflow_id"],
    )
    op.create_foreign_key(
        "fk_workflow_rounds_on_fast_track", "workflow_rounds", "workflow_rounds",
        ["on_fast_track_next_round_id", "workflow_id"], ["id", "workflow_id"],
    )
    op.create_check_constraint(
        "ck_workflow_rounds_fail_not_self", "workflow_rounds",
        "on_fail_next_round_id IS NULL OR on_fail_next_round_id <> id",
    )
    op.create_check_constraint(
        "ck_workflow_rounds_fast_not_self", "workflow_rounds",
        "on_fast_track_next_round_id IS NULL OR on_fast_track_next_round_id <> id",
    )
    op.create_check_constraint(
        "ck_workflow_rounds_fast_track_pair", "workflow_rounds",
        "(fast_track_min_percent IS NULL) = (on_fast_track_next_round_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_workflow_rounds_fast_track_range", "workflow_rounds",
        "fast_track_min_percent IS NULL"
        " OR (fast_track_min_percent > 0 AND fast_track_min_percent <= 100)",
    )


def downgrade() -> None:
    for name in ("ck_workflow_rounds_fast_track_range", "ck_workflow_rounds_fast_track_pair",
                 "ck_workflow_rounds_fast_not_self", "ck_workflow_rounds_fail_not_self"):
        op.drop_constraint(name, "workflow_rounds", type_="check")
    op.drop_constraint("fk_workflow_rounds_on_fast_track", "workflow_rounds", type_="foreignkey")
    op.drop_constraint("fk_workflow_rounds_on_fail", "workflow_rounds", type_="foreignkey")
    op.drop_column("workflow_rounds", "on_fast_track_next_round_id")
    op.drop_column("workflow_rounds", "fast_track_min_percent")
    op.drop_column("workflow_rounds", "on_fail_next_round_id")
    op.drop_constraint("uq_workflow_rounds_id_workflow", "workflow_rounds", type_="unique")
