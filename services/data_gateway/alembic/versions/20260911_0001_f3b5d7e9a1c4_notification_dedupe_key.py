"""Give notifications the same one-per-event guard email already has.

``email_events.dedupe_key`` has made every lifecycle email safe to retry since
the email system landed. Notifications never got the equivalent, so each
producer invented its own guard — a status flip here, a count check there, the
email's key borrowed as a proxy for the notification's. Each of those holds
while exactly one sweep runs at a time and fails in its own way when two do:
two reconciler passes finishing a batch's last two rows both count zero
remaining, and two sweeps both read a consumed invite before either flips it.

A nullable key with a partial unique index closes all of them at once and lets
producers insert with ``ON CONFLICT DO NOTHING``. Partial because most
notifications are one-off by nature (a welcome, a review request) and have no
event identity to dedupe on; NULL there means exactly that.

No backfill. Existing rows predate the key and are all already delivered —
there is nothing to protect them from.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f3b5d7e9a1c4"
down_revision: str | None = "e2a4c6b8d0f3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("dedupe_key", sa.Text(), nullable=True))
    op.create_index(
        "uq_notifications_dedupe_key",
        "notifications",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_dedupe_key", table_name="notifications")
    op.drop_column("notifications", "dedupe_key")
