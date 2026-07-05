"""user profile fields (editable candidate / HR / admin profiles)

Adds self-service profile columns to ``users``:
  avatar_url         — small data-URI (client-downscaled) or external image URL
  headline           — short title / desired-role line
  bio                — free-text description / summary
  employment_status  — candidate: 'student' | 'employed'
  desired_roles      — candidate: comma-separated roles of interest
  official_email     — HR: work/official email shown on the company profile
  location           — city / region (optional)

All nullable — no backfill needed; existing rows simply have NULLs.

Revision ID: b2d4f6a8c0e2
Revises:     a1c2e3f4b5d6
Create Date: 2026-06-25 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2d4f6a8c0e2"
down_revision: str | None = "a1c2e3f4b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    "avatar_url",
    "headline",
    "bio",
    "employment_status",
    "desired_roles",
    "official_email",
    "location",
)


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column("users", sa.Column(col, sa.Text(), nullable=True))


def downgrade() -> None:
    for col in reversed(_COLUMNS):
        op.drop_column("users", col)
