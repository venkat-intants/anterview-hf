"""Per-round auto-advance: HR decides, round by round, who moves people.

Revision ID: c5e7a9b1d3f5
Revises: b4d6f8a0c2e4
Create Date: 2026-09-30

``workflows.auto_advance_rounds`` already decides whether a passing result
moves a candidate on by itself or waits for a person — but it is one switch for
the WHOLE workflow, so a process cannot be automatic through screening and
deliberate at the final round, which is the shape most hiring actually has.

``workflow_rounds.auto_advance`` is a per-round override:

    NULL   inherit the workflow's auto_advance_rounds (the default, so every
           existing round keeps behaving exactly as it does today)
    TRUE   this round always advances a passing candidate automatically
    FALSE  this round always holds a passing candidate for HR, who then
           releases them onward or takes them out through the final-decision
           path that requires a reason

NULLABLE ON PURPOSE. A three-state column is the honest model here: "no opinion,
follow the workflow" is a genuinely different answer from "automatic", and
collapsing them into a boolean would silently freeze every existing round at
whatever the workflow said on the day of this migration — so later changing the
workflow default would stop reaching the rounds that never expressed a view.

Nothing here can reject anyone. Holding stops progression; ending a candidacy
still goes through app.final_decision, which requires a reason and a reason
code that this path does not collect (workflow_runner's RELEASE_TO_STATUSES).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c5e7a9b1d3f5"
down_revision: str | None = "b4d6f8a0c2e4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_rounds",
        sa.Column("auto_advance", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_rounds", "auto_advance")
