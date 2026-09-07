"""Applicant details, and where their name came from.

Two changes that arrive together because the second is only visible once the
first exists.

THE DETAILS A MULTI-STEP APPLICATION COLLECTS
    ``phone``, ``years_experience``, ``current_company``, ``current_title``,
    ``linkedin_url``, ``github_url``.

    On ``applicants`` rather than ``enrolments``: these describe the person, not
    their application to one opening, and they sit beside ``email`` and
    ``resume_text``, which are on the applicant for the same reason. Somebody
    applying to a second role at the same company should not have to retype
    where they work.

    All nullable. The single-field apply form that exists today supplies none of
    them, HR's bulk upload supplies none of them, and every row already in the
    table has none of them.

WHERE THE NAME CAME FROM — ``full_name_source`` and ``parsed_full_name``
    This is the interesting one, and it fixes a defect that was live.

    ``apply_extracted_identity`` fills in the name and email a scorer read out
    of a PDF. Its email branch is careful — ``if raw_email and a.email is None``
    — so it only ever fills a gap. Its name branch was not: it overwrote
    ``full_name`` outright for any row still flagged ``pending_enrichment``.

    That was right when the only way in was HR's bulk upload, where the name is
    a placeholder derived from the filename and the parsed name is strictly
    better. It stopped being right when candidates began applying for
    themselves, because the public form asks a person to type their name and
    then the reconciler quietly replaced it with whatever the parser read off
    the CV. Observed in testing: an application submitted as "Nadia Newbie"
    landed in the database as "Priya Sharma", the name inside the uploaded PDF.

    Nobody is told. The candidate is not shown it, and HR cannot tell which
    name the person actually gave. A wrong name on somebody's own application
    is a DPDP accuracy question, not a cosmetic one.

    So the source is recorded, and the reconciler learns to only FILL rather
    than REPLACE — exactly what the email branch beside it already does:

        candidate   the person typed it on the public form. Authoritative.
        hr          a recruiter typed it. Authoritative.
        filename    derived from an uploaded file's name. A placeholder.
        resume      read out of the PDF by the scorer.

    NULL means a row that predates this column. Those are treated as
    ``filename``, which preserves today's behaviour exactly for the rows
    currently in flight — every one of them came in through bulk upload, since
    that was the only path that set ``pending_enrichment``.

    ``parsed_full_name`` keeps what the CV said in every case, including when
    it is not used. That is what makes the confirmation step possible ("we read
    this from your CV — is it right?") and what lets HR see a discrepancy
    instead of only ever seeing one of the two names.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d5f7b9c1e3a6"
down_revision: str | None = "c4e6a8b0d2f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for column in (
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("years_experience", sa.SmallInteger(), nullable=True),
        sa.Column("current_company", sa.Text(), nullable=True),
        sa.Column("current_title", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("github_url", sa.Text(), nullable=True),
        sa.Column("full_name_source", sa.Text(), nullable=True),
        sa.Column("parsed_full_name", sa.Text(), nullable=True),
    ):
        op.add_column("applicants", column)

    op.create_check_constraint(
        "ck_applicants_full_name_source",
        "applicants",
        "full_name_source IS NULL OR full_name_source IN"
        " ('candidate','hr','filename','resume')",
    )
    op.create_check_constraint(
        "ck_applicants_years_experience",
        "applicants",
        "years_experience IS NULL OR (years_experience >= 0 AND years_experience <= 60)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_applicants_years_experience", "applicants")
    op.drop_constraint("ck_applicants_full_name_source", "applicants")
    for column in (
        "parsed_full_name",
        "full_name_source",
        "github_url",
        "linkedin_url",
        "current_title",
        "current_company",
        "years_experience",
        "phone",
    ):
        op.drop_column("applicants", column)
