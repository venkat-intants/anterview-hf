"""Purge historical exam_integrity_events.event_metadata.

Revision ID: b4d6f8a0c2e4
Revises: f6b8d0a2c4e6
Create Date: 2026-09-29

Security review MEDIUM-3, a DPDP §12 gap that the camera-proctoring branch
closed going forward but left open behind it.

``exam_integrity_events`` is in the erasure executor's ``EXCLUDED_TABLES``,
justified on the grounds that the table holds only an event type and two
timestamps — nothing a right-to-erasure request needs to reach. Code review
FIX 3 made that true BY CONSTRUCTION for new rows: ``IntegrityEventIn`` no
longer has a ``metadata`` field and is ``extra="forbid"``, so
``event_metadata`` is NULL for every row the ingest endpoint can now write.

But the justification is forward-dated and the EXCLUSION is not. Before that
fix the schema carried ``metadata: dict[str, object] | None`` with **no
validation of any kind** and wrote it straight to ``event_metadata``,
reachable by anyone holding a valid exam magic link. So rows already in the
table may carry arbitrary candidate-supplied JSON — a name, a phone number,
in principle a base64-encoded frame — and erasure would never touch them.

There is no legitimate writer to preserve: neither ``useExamProctor.ts`` nor
the shared ``features/proctoring/useProctoring.ts`` has ever set the field,
and no server-side caller exists. So the honest fix is to empty the column
rather than to narrow the documentation, which would leave real candidate
data sitting in an erasure-exempt table.

Irreversible by design — see ``downgrade``.
"""

from __future__ import annotations

from alembic import op

revision: str = "b4d6f8a0c2e4"
down_revision: str | None = "f6b8d0a2c4e6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE exam_integrity_events SET event_metadata = NULL"
        " WHERE event_metadata IS NOT NULL"
    )


def downgrade() -> None:
    """Deliberately a no-op.

    The upgrade destroys data on purpose, because that data should not exist.
    A downgrade cannot restore it and must not pretend to — and nobody
    downgrading this revision wants the candidate PII back.
    """
