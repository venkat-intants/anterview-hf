"""Group C / C8 — freeze the rubric, not just the competency list

Why this migration exists
-------------------------
Migration ``e7a9c1d3f5b8`` froze *what* each round assesses: competency id, name,
kind and weight. That is half the guarantee. What it did not freeze is *how* the
thing is measured — the behavioural anchors that tell a scorer what a 3, a 6 and
a 9 actually look like for this role, and the probe stems the interviewer works
from.

Those live on the derived ``RoleProfile``, which is not persisted. So a
published workflow's rubric could still move underneath it: re-derive the
profile with a refined anchor set and yesterday's candidate was graded against a
description of "adequate" that no longer exists. The score would still cite the
right competency and mean something different.

Freezing the anchors closes that. After this migration a round carries
everything needed to reconstruct the exact rubric it was published with, and
``profile_from_round_criteria`` can rebuild a ``RoleProfile`` from the round
alone — no derivation, no drift, no network call at scoring time.

Both columns are nullable because rounds authored before this migration have no
anchors to backfill. ``profile_from_round_criteria`` substitutes a neutral
generic band for those, and says so in the profile's ``source``, rather than
inventing role-specific text nobody wrote.

Revision ID: f8b0d2e4a6c9
Revises:     e7a9c1d3f5b8
Create Date: 2026-09-04 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f8b0d2e4a6c9"
down_revision: str | None = "e7a9c1d3f5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # {"low": "...", "mid": "...", "high": "..."} — the three behavioural bands.
    op.add_column("round_criteria", sa.Column("anchors", postgresql.JSONB(), nullable=True))
    # ["question stem", ...] — shapes of question that would evidence this
    # competency. Not a script: the interviewer adapts them to the candidate.
    op.add_column("round_criteria", sa.Column("probes", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("round_criteria", "probes")
    op.drop_column("round_criteria", "anchors")
