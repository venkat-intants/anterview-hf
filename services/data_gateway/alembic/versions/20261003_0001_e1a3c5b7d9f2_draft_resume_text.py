"""Drafts keep the text read out of the CV, so the draft door can be scored.

Revision ID: e1a3c5b7d9f2
Revises: 2577ba99b7fe
Create Date: 2026-10-03

`upload_draft_resume` extracts the CV's text, uses it to derive the fields the
candidate confirms (PH3-B5), and throws it away. `submit_draft` therefore built
its `Applicant` with no `resume_text` at all. Two consequences, found in the
PH3-B4b round-10 review:

**Draft-door applications were never scored.** `reconciliation._UNSCORED_WORK_SQL`
selects `WHERE a.resume_text IS NOT NULL AND length(trim(a.resume_text)) > 0`,
so an applicant row created by that door matches nothing the reconciler looks
for — not "scored late", never scored. `pending_enrichment=True` named the
intent and nothing acted on it, because the enrichment path reads the column
that was never filled.

**And it blocked the fix for the one-shot door's timing channel.** That door
writes `resume_text` twice inside the reply pad, on the branch that creates an
applicant and nowhere else, so the only state that writes it is "this address
has never applied here" — and the text was unbounded, so a caller chose how far
that one state overran the pad. The obvious repair was "store nothing, like the
sibling door"; the paragraph above is why that would have traded a privacy
channel for a silently unscored door. Bounding the text (`_MAX_RESUME_TEXT_CHARS`,
100k characters) fixes the channel, and this column lets BOTH doors store the
same bounded text instead of diverging further.

NULLABLE, with no backfill, and the cost of that is NOT nothing — an earlier
version of this docstring said those drafts "behave exactly as they do today",
which is true and is the defect rather than a reassurance. A draft-door
application created from a pre-migration draft has no `resume_text`, so
`reconciliation._UNSCORED_WORK_SQL` still cannot see it, and
`_SETTLE_UNSCORABLE_SQL` will not clear `pending_enrichment` while the
enrolment has `ats_overall IS NULL` on an auto-scoring workflow. The HR views
that read that flag ("scoring_pending" on the requisition dashboard, "still
being read" on the applicant list) therefore never settle for those rows.

Left open deliberately, and recorded rather than fixed here: the repair is to
relax the reconciler's predicate to also admit `resume_s3_key IS NOT NULL`,
which is nearly free because the scoring pass already downloads and re-extracts
from an object key. That changes which rows the reconciler picks up across the
whole product — HR-uploaded applicants with a key and no text included — so it
belongs in a change reviewed on its own terms, not inside a privacy branch. The
affected set is bounded and knowable: drafts open at the moment this migration
ran.

AND HERE IS THE QUERY THAT COUNTS THEM, because "bounded and knowable" is a
claim until someone can run it, and the row this describes is invisible on every
screen that would show it — the HR views read `pending_enrichment` and report
"still being read" for ever, which looks like slowness rather than a stuck row:

    SELECT count(*) AS stuck
      FROM applicants a
      JOIN enrolments e ON e.applicant_id = a.id
     WHERE a.pending_enrichment
       AND (a.resume_text IS NULL OR btrim(a.resume_text) = '')
       AND a.resume_s3_key IS NOT NULL
       AND e.ats_overall IS NULL;

A zero means this deployment had no draft open when the migration ran and the
residue is empty here — which is the likely answer on any deployment that
migrated promptly, and is worth confirming rather than assuming. A non-zero
count is the exact set the predicate relaxation above would release, and those
rows can also be settled by hand: the scoring pass re-extracts from
`resume_s3_key`, so clearing nothing and simply widening the reconciler's
predicate picks them up on the next pass.

CONSENT IS ALREADY COVERED. `start_draft` records the DPDP consent ledger entry
in the same transaction as the draft row, before any CV exists, which is why the
consent checkbox sits on the first save rather than at submission. This column
holds the same person's CV content as `resume_s3_key` already points at, under
that same consent act — not a new category of data about anyone.

ERASURE NEEDS NO CHANGE, which is the reason this column is safe to add here
rather than on `applicants`. Step 5e of the executor hard-DELETEs the
`application_drafts` rows it matches (`DELETE FROM application_drafts d`), so
every column on the row goes with it; there is no per-column redaction list to
keep in step with. Contrast `applicants.resume_text`, which needs explicit
redaction because that row survives erasure as a tombstone.
"""

from __future__ import annotations

from alembic import op

revision: str = "e1a3c5b7d9f2"
down_revision: str | None = "2577ba99b7fe"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE application_drafts ADD COLUMN resume_text TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE application_drafts DROP COLUMN IF EXISTS resume_text")
