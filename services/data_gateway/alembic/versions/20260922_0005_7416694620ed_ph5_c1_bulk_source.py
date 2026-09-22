"""HR can set an acquisition channel on a bulk upload — PH5-C1.

Revision ID: 7416694620ed
Revises: 8f41cb18a299
Create Date: 2026-09-22

HR single add already writes ``enrolments.source`` (PH3-B1); bulk upload never
had anywhere to put one, so ``app.bulk_ingest._create_applicant`` always wrote
``internal``. A bulk upload is filed under one opening at request time and
turned into applicants later, in the reconciler's own pass (``ingest_pass``),
which reads each file back through ``upload_items``/``upload_batches`` — it
does not still hold the request. So the channel has to be a real column,
persisted at the same moment the batch is recorded, not a value threaded
through Python state that the reconciler will not see.

PER BATCH, NOT PER ROW. A bulk upload is already scoped to one opening; in
practice it is also one channel (a career fair's stack of CVs, one college's
placement list), and a column on ``upload_items`` for a value every row in a
batch will carry alike is a column for no reason. If a future upload genuinely
mixes channels, per-file attribution can be added the same way this HR add
handles it — through ``requisition_id``, filed per row already.

``source`` ONLY — NO ``source_detail`` (architecture review)
HR's own inputs (this one and the single-add form) accept a channel from the
governed vocabulary and nothing else. A free-text detail typed by HR would
invite a referrer's name or an agency contact — third-party personal data the
erasure executor has no inventory entry for and no way to find. The public
apply path keeps its own ``source_detail`` unchanged (a campaign code lifted
from ``?src=``, sanitised by ``app.application_source.normalise_detail``);
HR-added applications simply carry ``source_detail = NULL``, same as today.

Nullable, defaults to nothing: ``app.bulk_ingest.create_batch`` itself writes
whatever it is given (NULL included) — it is
``app.routers.hr_applicants.bulk_upload_applicants`` that validates the
form field first (``app.application_source.validate_hr_source``, defaulting
to ``'internal'`` when HR does not say), so the column is ``'internal'`` on
every request this endpoint accepts. ``app.bulk_ingest._create_applicant``
still falls back to ``'internal'`` itself (``it["source"] or INTERNAL``) for
the column reading NULL regardless — a legacy row from before this migration,
or a future caller of ``create_batch`` that skips validation — so no existing
or future caller's behaviour silently regresses to ``'unknown'``. No CHECK
constraint here — the write path already validates against
``app.application_source.SOURCES`` before this column is touched, on the same
reasoning ``enrolments.source``'s CHECK lives in that table's own migration
rather than being repeated a third place; a constraint would be repeating the
vocabulary a second time in DDL for a column that is never read directly by
SQL that needs the guarantee (only ``app.bulk_ingest`` writes it, and it feeds
straight into ``app.workflow_runner.enrol_applicant``, which re-normalises
again before the CHECK on ``enrolments.source`` that DOES enforce it).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "7416694620ed"
down_revision: str | None = "8f41cb18a299"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("upload_batches", sa.Column("source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("upload_batches", "source")
