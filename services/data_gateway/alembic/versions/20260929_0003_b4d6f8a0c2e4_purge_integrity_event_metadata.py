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

Note for the Tier-2 Neon -> RDS Mumbai cutover (security re-audit LOW-3): this
revision clears ``event_metadata`` but does NOT remove rows whose
``event_type`` is outside the seven-name vocabulary, and ``COPY FROM`` DOES
enforce a NOT VALID CHECK. So a logical-replication initial sync, or any
restore into a schema that already carries the constraint, will fail on those
rows. Create the subscriber schema without the constraint and add it
``NOT VALID`` after the sync — the same order ``pg_dump`` itself uses.
"""

from __future__ import annotations

from alembic import op

revision: str = "b4d6f8a0c2e4"
down_revision: str | None = "f6b8d0a2c4e6"
branch_labels: str | None = None
depends_on: str | None = None


#: Must stay in step with f6b8d0a2c4e6's _NEW_EVENT_TYPES — this revision
#: re-adds the same constraint after the purge.
_EVENT_TYPES = (
    "'fullscreen_exit','tab_blur','face_absent','multiple_faces','gaze_away',"
    "'copy','paste'"
)


def upgrade() -> None:
    # Drop the CHECK, purge, re-add it NOT VALID — not a bare UPDATE.
    #
    # Security re-audit HIGH-3. A NOT VALID CHECK is still enforced on UPDATE,
    # and Postgres validates the WHOLE new row version, not just the columns
    # the statement changed. So a bare `UPDATE ... SET event_metadata = NULL`
    # raises CheckViolation on any row whose event_type is outside the seven
    # names — which is exactly the population this revision exists to clean,
    # because the pre-branch endpoint accepted arbitrary event_type AND
    # arbitrary metadata through the same unauthenticated request.
    #
    # Reproduced on PG16 before writing this: the statement aborts, `alembic
    # upgrade head` aborts with it, and space/entrypoint.sh treats that as
    # fatal — the same boot failure a3c5e7f9b1d4's NOT VALID was added to
    # prevent, one revision later. And because a single UPDATE rolls back
    # whole, the purge would not even happen for rows with valid event types,
    # leaving the candidate PII this revision promises to remove AND making
    # erasure_executor.py's EXCLUDED_TABLES justification false.
    #
    # DDL is transactional in Postgres and the DROP takes ACCESS EXCLUSIVE, so
    # no concurrent session ever observes the table unconstrained. The WHERE
    # clause is deliberately NOT narrowed to conforming event types: that would
    # leave PII on precisely the junk-typed rows this is meant to clear.
    op.execute(
        "ALTER TABLE exam_integrity_events DROP CONSTRAINT"
        " IF EXISTS ck_exam_integrity_events_event_type"
    )
    op.execute(
        "UPDATE exam_integrity_events SET event_metadata = NULL"
        " WHERE event_metadata IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE exam_integrity_events ADD CONSTRAINT"
        f" ck_exam_integrity_events_event_type CHECK (event_type IN ({_EVENT_TYPES}))"
        " NOT VALID"
    )


def downgrade() -> None:
    """Deliberately a no-op.

    The upgrade destroys data on purpose, because that data should not exist.
    A downgrade cannot restore it and must not pretend to — and nobody
    downgrading this revision wants the candidate PII back.
    """
