"""Where an application came from — PH3-B1.

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c0e3
Create Date: 2026-09-16

Two columns on ``enrolments``, because an enrolment IS the application: one
person against one opening, already company-scoped, already the row every later
stage hangs off. Putting the channel on ``applicants`` instead would attribute a
person rather than an application, and the same person applying to a second
opening through a different campaign would overwrite the first attribution.

``source`` is NOT NULL with a default rather than nullable, so PH5-C1's
``GROUP BY source`` is correct without a COALESCE in every query — a NULL that
means "untracked" is a NULL somebody eventually forgets to handle. Historical
rows take ``unknown``: they genuinely were not tracked, and inferring a channel
for them from what we can see today would put invented numbers in a funnel chart
somebody makes a decision from.

``source_detail`` carries the sub-channel — which job board, which campaign. It
is the escape hatch that lets the channel vocabulary stay closed: an
unrecognised ``?src=`` is recorded as ``other`` with the raw value here rather
than being dropped or admitted as a channel of its own.

The CHECK constraint is the same list as ``app.application_source.SOURCES`` and
``tests/unit/test_ph3_source_tracking.py`` asserts the two agree, so the
database cannot come to allow a value the application does not know about.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c3e5a7b9d1f4"
down_revision: str | None = "b2d4f6a8c0e3"
branch_labels: str | None = None
depends_on: str | None = None

# Kept in sync with app.application_source.SOURCES by an executable test.
_SOURCES = (
    "unknown",
    "direct",
    "careers_site",
    "job_board",
    "referral",
    "social",
    "email_campaign",
    "agency",
    "campus",
    "qr_code",
    "internal",
    "other",
)


def upgrade() -> None:
    op.add_column(
        "enrolments",
        sa.Column(
            "source",
            sa.Text(),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
    )
    op.add_column("enrolments", sa.Column("source_detail", sa.Text(), nullable=True))

    values = ", ".join(f"'{s}'" for s in _SOURCES)
    op.create_check_constraint(
        "ck_enrolments_source",
        "enrolments",
        f"source IN ({values})",
    )
    # Bounded and charset-restricted at the database, not only in Python. The
    # value originates on a public URL and is grouped on in analytics SQL; a
    # second writer added later must not be able to widen what lands here.
    op.create_check_constraint(
        "ck_enrolments_source_detail",
        "enrolments",
        "source_detail IS NULL OR source_detail ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
    )

    # PH5-C1 asks "how many applications per channel, for this company, over
    # this window". Company first because every query is tenant-scoped; source
    # second because that is the grouping; created_at last so a cohort filter
    # rides the same index.
    op.create_index(
        "ix_enrolments_source",
        "enrolments",
        ["company_id", "source", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_enrolments_source", table_name="enrolments")
    op.drop_constraint("ck_enrolments_source_detail", "enrolments", type_="check")
    op.drop_constraint("ck_enrolments_source", "enrolments", type_="check")
    op.drop_column("enrolments", "source_detail")
    op.drop_column("enrolments", "source")
