"""One applicant per person per company, keyed on (company_id, lower(email)) — B4.

D-06 makes an applicant a PERSON within a company, and a person applying to
several openings is one applicant with several enrolments. Nothing enforced it:
HR's uploads created a new applicant every time, so the same person could exist
twice, with their history split between the rows.

The rule itself is a partial unique index on (company_id, lower(btrim(email)))
over live rows with an email. Scoped by company, so identity can never be shared
across tenants; partial, so rows with no email (a bulk-uploaded CV not yet read,
an erased applicant) are not all "the same person".

It cannot always be created here. Existing data may already hold duplicates —
the Group B migration detected them and deliberately left merging to a person,
because a merge moves someone's assessment history and cannot be undone. A
CREATE UNIQUE INDEX over duplicates fails, and on the Space a failed migration
is a Space that does not boot. So the index is created only when there are no
duplicates; otherwise this records a NOTICE and the application creates it
itself (requisitions.ensure_applicant_identity_index) the moment the review
screen's last merge removes the last duplicate. The ingest paths stop creating
new ones either way.

``parsed_email`` is where an address read out of a CV goes when it already
belongs to someone else in the company. It cannot become ``email`` — that would
be a second applicant for the same person — and dropping it would hide the
match. Kept, it lets the review screen propose the merge. Personal data: the
erasure executor nulls it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d1f3a5b7c9e2"
down_revision: str | None = "c9e1a3b5d7f0"
branch_labels: str | None = None
depends_on: str | None = None

# Shared with requisitions.ensure_applicant_identity_index — the two must build
# the same index, or the application would create a different one later.
IDENTITY_INDEX_DDL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM applicants
         WHERE deleted_at IS NULL AND email IS NOT NULL AND btrim(email) <> ''
         GROUP BY company_id, lower(btrim(email))
        HAVING count(*) > 1
    ) THEN
        RAISE NOTICE 'uq_applicants_company_email not created: duplicate applicants exist '
                     '(merge them on the review screen; the index is created then)';
    ELSIF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_applicants_company_email') THEN
        CREATE UNIQUE INDEX uq_applicants_company_email
            ON applicants (company_id, lower(btrim(email)))
         WHERE deleted_at IS NULL AND email IS NOT NULL AND btrim(email) <> '';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.add_column("applicants", sa.Column("parsed_email", sa.Text(), nullable=True))
    op.execute(IDENTITY_INDEX_DDL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_applicants_company_email")
    op.drop_column("applicants", "parsed_email")
