"""scorecard_rationale

Add a per-axis rationale column to scorecards so the UI can explain WHY each
aspect (communication / technical / problem_solving / confidence) received its
score, citing evidence from the transcript.

Column added:
  - scorecards.rationale jsonb NULL
      {"communication": "<why this score>", "technical": "...",
       "problem_solving": "...", "confidence": "..."}
    NULL for legacy rows scored before this feature.

Revision ID: f1a2b3c4d5e6
Revises:     e1f2a3b4c5d6
Create Date: 2026-06-18 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scorecards",
        sa.Column("rationale", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scorecards", "rationale")
