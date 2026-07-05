"""scorecards_table

Sprint 5 — S5-006: Add scorecards table for end-of-session AI scoring.

Tables created:
  - scorecards  (scorecard_id UUID PK, session_id UUID UNIQUE NOT NULL,
                 scores jsonb, composite_score numeric(4,2),
                 strengths jsonb, improvements jsonb,
                 summary text, lang varchar(8), report_pdf_key text,
                 transcript_key text, scorer_model varchar(64),
                 scorer_version varchar(16), created_at timestamptz)

Indexes:
  - idx_scorecards_session ON scorecards(session_id)

Revision ID: d8e9f0a1b2c3
Revises:     c7d8e9f0a1b2
Create Date: 2026-05-29 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # scorecards — one row per completed interview session
    # ------------------------------------------------------------------
    op.create_table(
        "scorecards",
        sa.Column(
            "scorecard_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # session_id has a UNIQUE constraint — one scorecard per session.
        sa.Column("session_id", sa.UUID(), nullable=False),
        # scores: JSONB blob with keys communication, technical,
        #         problem_solving, confidence (each int 0-10).
        sa.Column("scores", JSONB(), nullable=False),
        sa.Column("composite_score", sa.Numeric(4, 2), nullable=True),
        # strengths: JSONB array of strings (3 elements).
        sa.Column("strengths", JSONB(), nullable=True),
        # improvements: JSONB array of {area, suggestion} objects (3 elements).
        sa.Column("improvements", JSONB(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(8), nullable=False),
        # report_pdf_key: S3/R2 object key for the generated PDF scorecard.
        #                 NULL until PDF is generated (Sprint 6).
        sa.Column("report_pdf_key", sa.Text(), nullable=True),
        # transcript_key: S3/R2 object key for the stored transcript JSON.
        #                 NULL until transcript export is implemented.
        sa.Column("transcript_key", sa.Text(), nullable=True),
        # scorer_model: Gemini model ID used for scoring, e.g.
        #               "gemini-2.5-flash". NULL for legacy rows.
        sa.Column("scorer_model", sa.String(64), nullable=True),
        # scorer_version: scorer logic version tag, e.g. "1.0". Allows
        #                 comparison of scoring consistency across code changes.
        sa.Column("scorer_version", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("scorecard_id", name="pk_scorecards"),
        sa.UniqueConstraint("session_id", name="uq_scorecards_session_id"),
    )

    op.create_index(
        "idx_scorecards_session",
        "scorecards",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_scorecards_session", table_name="scorecards")
    op.drop_table("scorecards")
