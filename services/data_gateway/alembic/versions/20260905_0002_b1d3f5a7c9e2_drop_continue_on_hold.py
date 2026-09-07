"""Drop workflows.continue_on_hold — a setting with no behaviour.

It shipped with Group C as part of the C9 settings block: stored, settable
through the API, copied on clone, and never read by anything. The workflow
runner has no branch on it.

It is removed rather than implemented because there is no meaning it could
carry that does not weaken the thing it sits next to. A hold already keeps the
candidate exactly where they are — ``_hold`` leaves ``current_round_id``
untouched, so "keeps their place" is the unconditional behaviour and the flag
would be describing something already true. The only reading that would change
anything is "send them the next round anyway while HR deliberates", and that is
precisely what the hold exists to prevent: it would have automation advance a
candidate who scored below a threshold, which is the judgement D-05 reserves
for a person.

A settable field that does nothing is worse than an absent one — someone will
eventually turn it on, believe they changed the process, and be wrong.
Inventing behaviour to justify it would be worse still.

Nothing is lost on downgrade: the column comes back with its original default
of false, which is the value every row holds today, because nothing has ever
acted on it being true.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b1d3f5a7c9e2"
down_revision: str | None = "a9c1e3f5b7d2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("workflows", "continue_on_hold")


def downgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "continue_on_hold", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
