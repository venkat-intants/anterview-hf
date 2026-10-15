"""Per-interview avatar mode — HR chooses avatar or audio-only, per candidate

Revision ID: f3a5c7e9b1d4
Revises: e1c3f5a7b9d2
Create Date: 2026-10-15

Whether a candidate meets a talking face or a voice was, until now, the
deployment-wide ``AVATAR_PROVIDER`` environment variable: one value for every
interview in every company on the instance. Changing it meant an ops change and
it changed it for everyone at once.

That is the wrong grain for the decision. The avatar is the single most
expensive thing in a session and the only one billed per minute, so "use it for
the shortlist, not the first screen" is a budget decision a hiring team makes
per role — and it is also an accessibility and bandwidth decision, because a
candidate on a weak rural connection has a better interview with audio only
than with a face that stalls. Both are judgements the person running the hiring
makes, not the person holding the deploy key.

So the mode moves onto the row that already carries the other per-interview
choices. ``interview_invites`` already holds ``language`` and ``avatar_id``;
``avatar_mode`` sits beside them and is copied onto the ``sessions`` row at
first redeem exactly as those two are, which is the path the worker already
reads. No new plumbing, and a session keeps the mode it started under even if
HR changes the invite afterwards.

WHAT THE TWO VALUES MEAN, because the asymmetry is deliberate:
  ``avatar`` — use the configured provider (``AVATAR_PROVIDER``: tavus/simli).
  ``audio``  — voice only, whatever the environment says.
The toggle can switch the avatar OFF for one interview; it cannot choose a
different vendor. Which vendor to pay is an instance-level decision with
contracts and residency behind it (docs/DATA-FLOW.md), and putting a vendor
picker on an HR form would make every tenant a procurement decision-maker.

DEFAULTS AND EXISTING ROWS. ``server_default='avatar'`` with NOT NULL, so every
row that exists today keeps exactly the behaviour it has now and no backfill is
needed. The CHECK is added plain rather than NOT VALID: the column is new and
defaulted, so no existing row can violate it. That distinction matters here —
see docs/ACCEPTED-RISKS.md and the "CI migrates an empty database" blind spot,
where a migration that only fails against real rows passes all sixteen CI jobs.
A narrowing change to this column later would need the NOT VALID treatment.

The downgrade drops both columns, which loses the per-interview choice and
returns every session to the environment default. That is lossy and it is the
honest behaviour: there is nowhere else to keep the value.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f3a5c7e9b1d4"
down_revision: str | None = "e1c3f5a7b9d2"
branch_labels: None = None
depends_on: None = None

#: Kept in one place so the two tables cannot drift apart. ``sessions`` is what
#: the worker reads; ``interview_invites`` is what HR sets. A value legal in one
#: and not the other would be a session the worker cannot interpret.
_MODES = ("avatar", "audio")
_CHECK_SQL = "avatar_mode IN ('avatar','audio')"


def upgrade() -> None:
    for table, ck in (
        ("interview_invites", "ck_interview_invites_avatar_mode"),
        ("sessions", "ck_sessions_avatar_mode"),
    ):
        op.add_column(
            table,
            sa.Column(
                "avatar_mode",
                sa.Text(),
                nullable=False,
                server_default="avatar",
            ),
        )
        op.create_check_constraint(ck, table, _CHECK_SQL)


def downgrade() -> None:
    for table, ck in (
        ("sessions", "ck_sessions_avatar_mode"),
        ("interview_invites", "ck_interview_invites_avatar_mode"),
    ):
        op.drop_constraint(ck, table, type_="check")
        op.drop_column(table, "avatar_mode")
