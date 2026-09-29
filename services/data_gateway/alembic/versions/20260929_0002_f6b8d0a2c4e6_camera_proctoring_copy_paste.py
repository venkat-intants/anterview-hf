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

No data migration is needed — but NOT for the reason an earlier version of this
docstring gave. It claimed no row can hold ``copy``/``paste`` "because the
constraint being replaced has never allowed it". That reasoning was about the
wrong predecessor state and security review HIGH-1 corrected it: before
``a3c5e7f9b1d4`` there was no CHECK at all AND no vocabulary validation in the
ingest endpoint, while the deployed client has been firing ``copy``/``paste`` on
every clipboard action all along. So rows with those names — and in principle
any <=40-char string a magic-link holder posted — very likely DO exist.

The two revisions do not commute, and ``a3c5e7f9b1d4`` runs FIRST in the same
``upgrade head``. That is why both revisions add the constraint ``NOT VALID``:
Postgres keeps enforcing it on every INSERT and UPDATE (the only thing this
backstop is for) without validating history that cannot be made to conform.
Nothing here or later runs ``VALIDATE CONSTRAINT``. See ``a3c5e7f9b1d4``'s own
comment for what a validating ADD CONSTRAINT would have done at boot.
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
        " NOT VALID"
    )


def downgrade() -> None:
    # Also NOT VALID, reversing an earlier decision here. This previously added
    # a validating constraint so that a downgrade would "fail loudly" if a
    # copy/paste row existed, on the argument that removing support for two
    # event types while data uses them should stop rather than quietly succeed.
    # The argument is wrong in one important way: after this migration has been
    # live for any length of time such rows ALWAYS exist, so that downgrade
    # could never run — an escape hatch that cannot be opened is not a safety
    # feature, it is a trap, and the moment you want a downgrade is the moment
    # you least want to be fighting a CheckViolation. NOT VALID restores the
    # narrower vocabulary for everything WRITTEN from here on, which is what a
    # downgrade actually means, and leaves the existing rows alone.
    op.execute(
        "ALTER TABLE exam_integrity_events DROP CONSTRAINT"
        " IF EXISTS ck_exam_integrity_events_event_type"
    )
    op.execute(
        "ALTER TABLE exam_integrity_events ADD CONSTRAINT"
        f" ck_exam_integrity_events_event_type CHECK (event_type IN ({_OLD_EVENT_TYPES}))"
        " NOT VALID"
    )
