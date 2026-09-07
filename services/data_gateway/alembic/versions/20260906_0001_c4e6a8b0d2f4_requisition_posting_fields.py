"""Requisition posting fields — what a job advert actually needs to say.

An opening could carry a title, a level and a wall of ``jd_text``. That was
enough while the only route in was a UUID somebody emailed you: HR knew which
role they meant, and the candidate had already been told. It is not enough for a
job board, where the whole interaction is a stranger deciding whether a role is
worth opening — and every filter a board offers is a column that has to exist
before the filter can.

So these columns are not cosmetic detail on the Builder's first step. They are
the precondition for candidate discovery, and nothing downstream of it can be
built until they land.

Three groups, each with a different reason to exist.

FACTS ABOUT THE ROLE — ``department``, ``location``, ``employment_type``
    The three filters a candidate reaches for first. All nullable: several
    thousand openings already exist, some created by the Group B backfill from
    nothing but a job title, and there is no honest value to invent for them.
    A board must be able to say "not specified" rather than guess a department.

WHAT IT PAYS AND WHAT IT ASKS — the experience and salary ranges
    Ranges rather than single values because that is how roles are actually
    advertised. ``salary_visible`` is separate from "is a salary recorded":
    plenty of companies store a band internally and publish nothing, and
    conflating the two would leak compensation the moment a recruiter filled the
    field in for their own planning. Default false, and the public endpoint
    honours it.

STRUCTURED DESCRIPTION — ``responsibilities``, ``required_skills``, ``nice_to_have_skills``
    JSONB arrays of strings, alongside ``jd_text`` rather than replacing it.
    Keeping the prose matters twice over: every existing opening has only prose,
    and the role engine already derives its competency model from ``jd_text``,
    so dropping it would silently change how candidates are assessed on openings
    nobody edited.

    Arrays rather than a child table on purpose. These are ordered lists of
    short strings that are always read whole, never joined to, never queried
    across openings — jsonb is the shape that matches the access pattern, and a
    child table would add a join to every posting read for nothing.

WHY NO WORK MODE COLUMN
    The spec lists remote / hybrid / on-site as a later filter, so it is not
    here. Adding an unused column now costs a migration either way; adding it
    and leaving it null on every row costs a migration AND makes the board look
    like it lost data. It can arrive with the feature that uses it.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "c4e6a8b0d2f4"
down_revision: str | None = "b1d3f5a7c9e2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for column in (
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("employment_type", sa.Text(), nullable=True),
        sa.Column("experience_min_years", sa.SmallInteger(), nullable=True),
        sa.Column("experience_max_years", sa.SmallInteger(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.Text(), nullable=True),
        sa.Column(
            "salary_visible", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "responsibilities",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "required_skills",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "nice_to_have_skills",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    ):
        op.add_column("job_requisitions", column)

    # Constrained rather than left to the application. A range whose floor is
    # above its ceiling is not a validation failure to report politely — it is a
    # row no filter can answer correctly, and the database is the only place
    # that catches it whatever wrote the row (API, copilot commit, backfill,
    # or somebody at a psql prompt).
    op.create_check_constraint(
        "ck_job_requisitions_experience_range",
        "job_requisitions",
        "experience_min_years IS NULL OR experience_max_years IS NULL"
        " OR experience_min_years <= experience_max_years",
    )
    op.create_check_constraint(
        "ck_job_requisitions_salary_range",
        "job_requisitions",
        "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
    )
    op.create_check_constraint(
        "ck_job_requisitions_employment_type",
        "job_requisitions",
        "employment_type IS NULL OR employment_type IN"
        " ('full_time','part_time','contract','internship','temporary')",
    )

    # The board's own query: open, public, newest first, filtered by department
    # and location. Partial on exactly the rows a board can ever show, so it
    # stays the size of what is advertised rather than of every opening the
    # company has ever had.
    op.create_index(
        "ix_job_requisitions_board",
        "job_requisitions",
        ["company_id", "department", "location", "created_at"],
        unique=False,
        postgresql_where=sa.text(
            "public_apply_enabled AND status = 'open' AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_job_requisitions_board", table_name="job_requisitions")
    op.drop_constraint("ck_job_requisitions_employment_type", "job_requisitions")
    op.drop_constraint("ck_job_requisitions_salary_range", "job_requisitions")
    op.drop_constraint("ck_job_requisitions_experience_range", "job_requisitions")
    for column in (
        "nice_to_have_skills",
        "required_skills",
        "responsibilities",
        "salary_visible",
        "salary_currency",
        "salary_max",
        "salary_min",
        "experience_max_years",
        "experience_min_years",
        "employment_type",
        "location",
        "department",
    ):
        op.drop_column("job_requisitions", column)
