"""session_presenter_id

Area 4 (UI redesign v2): Add ``presenter_id`` column to ``sessions``.

This enables the UI presenter picker — the user selects one of the 6 D-ID
stock presenters (3 male / 3 female) before starting an interview.  The
column is nullable with a DB-level default of the baseline presenter ID
("presenter_alice") so existing session rows require no backfill.

sessions table:
  - presenter_id  TEXT NULL DEFAULT 'presenter_alice'

Revision ID: e1f2a3b4c5d6
Revises:     d0e1f2a3b4c5
Create Date: 2026-05-30 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The baseline presenter id matches PRESENTERS catalog default in
# services/interview_core/app/presenters.py.
_BASELINE_PRESENTER_ID = "presenter_alice"


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "presenter_id",
            sa.Text(),
            # sa.text() wraps the value in single-quotes so Postgres treats it as
            # a string literal rather than a column reference.
            # Without this, Postgres interprets the bare identifier "presenter_alice"
            # as a column name and raises: column "presenter_alice" does not exist.
            server_default=sa.text(f"'{_BASELINE_PRESENTER_ID}'"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "presenter_id")
