"""One camera-notice acceptance row per (candidate, round, notice, grant).

Revision ID: d9f1b3c5e7a2
Revises: c5e7a9b1d3f5
Create Date: 2026-09-30

`exam.camera_notice.accepted` rows evidence that a candidate accepted the exam's
camera notice. They are written from `/exam/camera-consent`, which is
UNAUTHENTICATED (a magic link) and capped only by a rate limiter that fails
open — and `audit_log` is append-only by trigger and excluded from DPDP
erasure, so every row written there is permanent and undeletable.

The first version of that feature deduplicated with a SELECT probe before
inserting. Security review found two problems with it, and this index fixes
both:

* The probe filtered on `action`, `actor_id` and two `details->>` keys, and
  `audit_log`'s ONLY index is `ix_audit_log_event_ts`. So every FIRST acceptance
  for a round — the normal path, not the abuse path — sequentially scanned the
  whole audit table, which the platform retains for three years. One cheap
  unauthenticated request, one full scan, on a serverless database billed by
  compute that this project has already exhausted twice.

* SELECT-then-INSERT is TOCTOU. Ten parallel POSTs on one link all read "not
  present" and all insert, recovering most of the ~14,400 rows/day the dedupe
  was added to prevent. The rate limiter was the real bound, and it fails open
  on a Redis blip.

A partial UNIQUE index makes the property structural instead of advisory, and
lets the writer use `ON CONFLICT DO NOTHING` — which is also why the conflict
must be resolved at INSERT time rather than at commit: a conflict surfacing at
commit would roll back the consent ledger row with it and turn a double-click
into a failed exam start.

`DO NOTHING`, never `DO UPDATE`: `audit_log_no_mutation` fires BEFORE UPDATE OR
DELETE and would raise. `DO NOTHING` performs no UPDATE, so the trigger is not
reached. Creating the index is likewise safe — the triggers cover mutation, not
DDL, and no pre-existing row carries this action.

`consent_id` is in the key deliberately. Without it, a candidate who withdraws
consent and accepts again would be suppressed by the row written for the
revoked grant — leaving the grant actually in force with no evidence at all,
which is the exact question this feature exists to answer. A new `consent_id`
requires an authenticated withdrawal, so growth stays bounded.
"""

from __future__ import annotations

from alembic import op

revision: str = "d9f1b3c5e7a2"
down_revision: str | None = "c5e7a9b1d3f5"
branch_labels: str | None = None
depends_on: str | None = None

_INDEX = "ix_audit_log_camera_notice_round"
_ACTION = "exam.camera_notice.accepted"


def upgrade() -> None:
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON audit_log ("
        "  actor_id,"
        "  (details->>'exam_round_id'),"
        "  (details->>'notice_version'),"
        "  (details->>'consent_id')"
        f") WHERE action = '{_ACTION}'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
