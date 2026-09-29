"""Restore copy/paste to the exam integrity event vocabulary.

Revision ID: f6b8d0a2c4e6
Revises: a3c5e7f9b1d4
Create Date: 2026-09-29

THE BUG THIS FIXES (code review FIX 1)
``web/src/pages/exam/useExamProctor.ts`` has, for a long time, fired ``copy``
and ``paste`` browser events on every clipboard action. Before migration
``a3c5e7f9b1d4`` (the camera-proctoring branch's first pass), ``event_type``
was an unconstrained string, so those posts were always accepted and stored
(never scored, never a violation — see ``app/exam_camera.py``'s module
docstring). That migration tightened ``exam_integrity_events.event_type`` to
a CHECK naming only the five camera-proctoring-contract types, WITHOUT
checking what the client already sent, so every ``copy``/``paste`` post
started failing the constraint (and, above it, the Pydantic vocabulary check)
with a 422 — and the client's ``sendIntegrityEvent`` swallows a failed
request and returns null, so the regression was silent: HR lost a real
signal, platform-wide, with nothing to notice it by.

This migration is a FOLLOW-ON to ``a3c5e7f9b1d4``, not an edit to it — that
revision is treated as immutable once opened for review, so widening the CHECK
constraint it created is its own revision.

WHAT CHANGES
``exam_integrity_events.event_type``'s CHECK constraint is dropped and
recreated naming SEVEN types instead of five: the five the camera-proctoring
contract added, plus ``copy`` and ``paste``, restored with their original
instantaneous shape (no ``ended_at``). The Pydantic-level vocabulary
(``app/exam_camera.py::INSTANT_EVENT_TYPES``) is the primary enforcement, as
before; this is the DB-level backstop, unchanged in kind from what
``a3c5e7f9b1d4`` already established for the other five types.

No data migration is needed: no row in this table can have
``event_type IN ('copy', 'paste')`` today, because the constraint being
replaced has never allowed it (either the pre-a3c5e7f9b1d4 code path, which
had no CHECK at all, or the current one, which forbids it) — the whole reason
this migration exists is that those two names went from "briefly reachable,
pre-camera-branch" to "unreachable" without a gap in which they could have
been written under the current constraint.
"""

from __future__ import annotations

from alembic import op

revision: str = "f6b8d0a2c4e6"
down_revision: str | None = "a3c5e7f9b1d4"
branch_labels: str | None = None
depends_on: str | None = None

_OLD_EVENT_TYPES = (
    "'fullscreen_exit','tab_blur','face_absent','multiple_faces','gaze_away'"
)
_NEW_EVENT_TYPES = (
    "'fullscreen_exit','tab_blur','face_absent','multiple_faces','gaze_away',"
    "'copy','paste'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE exam_integrity_events DROP CONSTRAINT"
        " IF EXISTS ck_exam_integrity_events_event_type"
    )
    op.execute(
        "ALTER TABLE exam_integrity_events ADD CONSTRAINT"
        f" ck_exam_integrity_events_event_type CHECK (event_type IN ({_NEW_EVENT_TYPES}))"
    )


def downgrade() -> None:
    # A row written as 'copy'/'paste' while this migration was applied would
    # violate the narrower constraint being restored below, so the ADD
    # CONSTRAINT statement fails loudly on such a row rather than silently
    # leaving the table in a state the constraint no longer describes — the
    # correct outcome: a downgrade that removes support for two event types
    # while data using them still exists should stop, not quietly succeed.
    op.execute(
        "ALTER TABLE exam_integrity_events DROP CONSTRAINT"
        " IF EXISTS ck_exam_integrity_events_event_type"
    )
    op.execute(
        "ALTER TABLE exam_integrity_events ADD CONSTRAINT"
        f" ck_exam_integrity_events_event_type CHECK (event_type IN ({_OLD_EVENT_TYPES}))"
    )
