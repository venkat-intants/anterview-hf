"""integrity_events (Phase B — proctoring)

Adds the proctoring / malpractice-detection data model:

  - integrity_events: one row per flagged event during an interview
      (gaze_away, face_absent, multiple_faces, tab_blur, fullscreen_exit,
       copy, paste, second_voice, ...). Ranged events carry ended_at.
  - sessions.integrity_score   smallint NULL (0-100, computed at/after session)
  - sessions.proctoring_summary jsonb NULL (per-type counts + flagged seconds)

Detection runs client-side (MediaPipe in the browser) and emits lightweight
events; raw candidate video never leaves the device. NULL columns mean
"no proctoring data" (proctoring off / legacy session).

Revision ID: a2b3c4d5e6f7
Revises:     f1a2b3c4d5e6
Create Date: 2026-06-18 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrity_events",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", sa.UUID(), nullable=False),
        # event_type: gaze_away | face_absent | multiple_faces | tab_blur |
        #             fullscreen_exit | copy | paste | second_voice | ...
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # ended_at: NULL for instantaneous events (tab_blur, copy); set for
        #           ranged events (gaze_away, face_absent) to measure duration.
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("event_metadata", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_integrity_events"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_integrity_events_session",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_integrity_events_session",
        "integrity_events",
        ["session_id"],
    )

    # Rolling integrity score + summary on the session itself (for list/analytics
    # without scanning the events table).
    op.add_column(
        "sessions",
        sa.Column("integrity_score", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("proctoring_summary", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "proctoring_summary")
    op.drop_column("sessions", "integrity_score")
    op.drop_index("idx_integrity_events_session", table_name="integrity_events")
    op.drop_table("integrity_events")
