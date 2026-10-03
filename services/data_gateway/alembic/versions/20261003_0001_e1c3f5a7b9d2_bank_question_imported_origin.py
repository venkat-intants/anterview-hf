"""bank_questions.origin gains 'imported' — a question that came from a spreadsheet

Revision ID: e1c3f5a7b9d2
Revises: d9f1b3c5e7a2
Create Date: 2026-10-03

A bank question's ``origin`` already distinguished how it came to exist —
``authored`` (someone typed it), ``ai_draft`` (a model drafted it, saved by a
person) and ``from_exam`` (lifted out of an exam that already had it). The
spreadsheet importer is a fourth way in, and it is worth telling apart from the
other three rather than being folded into ``authored``.

WHY PROVENANCE EARNS ITS OWN VALUE HERE. This library feeds assessments that
decide whether a person gets a job interview. "Where did this question come
from?" is therefore a question someone will eventually ask about a specific
question — after a candidate disputes it, or during a bid's due diligence — and
the honest answers differ: a question an HR manager typed was read by its
author as they wrote it, while one of 500 rows in a bought-in spreadsheet may
never have been read by anyone here at all. Recording ``imported`` costs one
CHECK constraint and keeps that distinction available; recording it as
``authored`` would assert something about the row that nobody can support.

WIDENING, SO EXISTING ROWS CANNOT FAIL. This drops and re-adds the CHECK with
one more allowed value. Every value the old constraint permitted the new one
permits too, so no existing row can violate it — which is what makes this safe
to run against a populated table, and why it does NOT need the NOT VALID
treatment a narrowing change would (see docs/ACCEPTED-RISKS.md and the
"CI migrates an empty database" blind spot: a migration that only fails against
real rows passes every CI job).

The reverse narrows, so ``downgrade`` rewrites any ``imported`` row back to
``authored`` before re-adding the old constraint. That loses the distinction
this migration exists to record, which is the honest cost of going back, and is
stated here rather than discovered by whoever runs it.
"""

from __future__ import annotations

from alembic import op

revision: str = "e1c3f5a7b9d2"
down_revision: str | None = "d9f1b3c5e7a2"
branch_labels: str | None = None
depends_on: str | None = None

_CK = "ck_bank_questions_origin"
_OLD = "origin IN ('authored','ai_draft','from_exam')"
_NEW = "origin IN ('authored','ai_draft','from_exam','imported')"


def upgrade() -> None:
    op.drop_constraint(_CK, "bank_questions", type_="check")
    op.create_check_constraint(_CK, "bank_questions", _NEW)


def downgrade() -> None:
    # Narrowing: anything the new constraint allowed and the old one does not
    # has to be rewritten first, or the ADD CONSTRAINT fails on real data.
    op.execute("UPDATE bank_questions SET origin = 'authored' WHERE origin = 'imported'")
    op.drop_constraint(_CK, "bank_questions", type_="check")
    op.create_check_constraint(_CK, "bank_questions", _OLD)
