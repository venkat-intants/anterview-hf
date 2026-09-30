"""Camera proctoring for exams — event vocabulary, round setting, attempt flag.

Revision ID: a3c5e7f9b1d4
Revises: f2a4c6e8b0d3
Create Date: 2026-09-29

THE CONTRACT THIS IMPLEMENTS
The camera proctoring contract (``exam_proctoring_contract.md``) extends the
exam integrity pipeline from two browser signals (``fullscreen_exit``,
``tab_blur``) to five, three of them RANGED (``face_absent``,
``multiple_faces``, ``gaze_away``), each carried by the existing
``exam_integrity_events.started_at``/``ended_at`` pair — no new event table.

WHAT CHANGES
1. ``exam_integrity_events.event_type`` gets a CHECK constraint naming the five
   known types. The ingest endpoint already validates this in Pydantic; the
   constraint is defense-in-depth on the DB.NEVER-store-junk rule this
   project applies everywhere else (``ck_exams_kind``, ``ck_exams_status``, …).
2. ``exam_rounds.camera_proctoring_required`` (boolean, default false) is the
   COMPANY'S OWN setting for whether a round needs the candidate's camera —
   HR-authored content, on the same footing as ``pass_threshold`` and
   ``time_limit_seconds``, and NOT one of the columns
   ``exam_rounds_frozen()`` locks once the round is published or taken: unlike
   grading, turning a camera requirement on or off does not change how a past
   attempt was scored, so it stays editable and each new attempt reads the
   round's CURRENT value at ``/exam/start`` (the same freeze-at-start
   discipline PH4-D2 already established for accommodations).
3. ``exam_attempts.camera_in_use`` (boolean, default false) records whether
   THIS attempt's ROUND REQUIRED the camera — frozen at ``/exam/start`` from
   the round's setting, so turning the requirement on or off later cannot
   change how a past attempt reads. Corrected 2026-09-30: this paragraph said
   the column records whether the attempt "actually had the camera on", and
   nothing sets it from a browser signal or from a consent check, so it cannot.
   An attempt on a round that never asked for the camera is distinguishable
   from one that did; whether a required camera actually STARTED is not
   recorded anywhere, and the HR timeline says so in words instead of implying
   a clean record.
   Added to ``exam_attempts_allowance_fixed()`` (PH4-D2, migration
   ``e3b5d7f9a1c5``) alongside ``extra_time_seconds`` /
   ``auto_submit_relaxed`` / ``accommodation_id`` — the same "this attempt
   keeps what it started with" guarantee, extended to the new column rather
   than left as an application-only promise.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a3c5e7f9b1d4"
down_revision: str | None = "f2a4c6e8b0d3"
branch_labels: str | None = None
depends_on: str | None = None

_EVENT_TYPES = (
    "'fullscreen_exit','tab_blur','face_absent','multiple_faces','gaze_away'"
)

EXAM_ATTEMPTS_ALLOWANCE_FIXED = """
CREATE OR REPLACE FUNCTION exam_attempts_allowance_fixed() RETURNS trigger AS $$
BEGIN
    IF NEW.extra_time_seconds IS DISTINCT FROM OLD.extra_time_seconds THEN
        RAISE EXCEPTION 'exam attempt % keeps the extra time it started with', OLD.id;
    END IF;
    IF NEW.auto_submit_relaxed IS DISTINCT FROM OLD.auto_submit_relaxed THEN
        RAISE EXCEPTION 'exam attempt % keeps whether auto-submit was relaxed', OLD.id;
    END IF;
    IF NEW.accommodation_id IS DISTINCT FROM OLD.accommodation_id
       AND NEW.accommodation_id IS NOT NULL THEN
        RAISE EXCEPTION 'exam attempt % keeps which accommodation applied, or clears it', OLD.id;
    END IF;
    IF NEW.camera_in_use IS DISTINCT FROM OLD.camera_in_use THEN
        RAISE EXCEPTION 'exam attempt % keeps whether the camera was in use', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

EXAM_ATTEMPTS_ALLOWANCE_FIXED_DOWN = """
CREATE OR REPLACE FUNCTION exam_attempts_allowance_fixed() RETURNS trigger AS $$
BEGIN
    IF NEW.extra_time_seconds IS DISTINCT FROM OLD.extra_time_seconds THEN
        RAISE EXCEPTION 'exam attempt % keeps the extra time it started with', OLD.id;
    END IF;
    IF NEW.auto_submit_relaxed IS DISTINCT FROM OLD.auto_submit_relaxed THEN
        RAISE EXCEPTION 'exam attempt % keeps whether auto-submit was relaxed', OLD.id;
    END IF;
    IF NEW.accommodation_id IS DISTINCT FROM OLD.accommodation_id
       AND NEW.accommodation_id IS NOT NULL THEN
        RAISE EXCEPTION 'exam attempt % keeps which accommodation applied, or clears it', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "exam_rounds",
        sa.Column(
            "camera_proctoring_required", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "exam_attempts",
        sa.Column("camera_in_use", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # NOT VALID, and deliberately never validated. Security review HIGH-1:
    # before this branch, `exam_integrity_events.event_type` had NO constraint
    # and the ingest endpoint did not check the vocabulary at all — it stored
    # `body.event_type[:40]`. The DEPLOYED client has meanwhile been firing
    # `copy`/`paste` on every clipboard action for a long time, so any database
    # that has run one real exam already holds rows this five-name CHECK
    # rejects, and in principle any <=40-char string a magic-link holder ever
    # posted. A plain ADD CONSTRAINT validates every existing row, so it would
    # raise CheckViolation here, `alembic upgrade head` would abort, and
    # `space/entrypoint.sh` treats a migration failure on a reachable database
    # as fatal — the Space would not boot, every route 503. CI cannot catch it:
    # CI migrates a freshly created, empty database.
    #
    # NOT VALID is exactly the right tool and not a compromise: Postgres still
    # enforces the constraint on every INSERT and UPDATE, which is all this is
    # for (a backstop under the application-level vocabulary check). It simply
    # does not re-litigate history we cannot retroactively make conform. We do
    # NOT follow up with VALIDATE CONSTRAINT anywhere, and the widening
    # revision f6b8d0a2c4e6 keeps NOT VALID for the same reason.
    op.execute(
        "ALTER TABLE exam_integrity_events ADD CONSTRAINT"
        f" ck_exam_integrity_events_event_type CHECK (event_type IN ({_EVENT_TYPES}))"
        " NOT VALID"
    )
    op.execute(EXAM_ATTEMPTS_ALLOWANCE_FIXED)


def downgrade() -> None:
    op.execute(EXAM_ATTEMPTS_ALLOWANCE_FIXED_DOWN)
    op.execute(
        "ALTER TABLE exam_integrity_events DROP CONSTRAINT"
        " IF EXISTS ck_exam_integrity_events_event_type"
    )
    op.drop_column("exam_attempts", "camera_in_use")
    op.drop_column("exam_rounds", "camera_proctoring_required")
