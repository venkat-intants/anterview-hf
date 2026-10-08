"""Converge the PH3-B4b branch with main's question-bank import. No schema change.

Revision ID: f1b3d5a7c9e2
Revises: e1a3c5b7d9f2, e1c3f5a7b9d2
Create Date: 2026-10-04

WHY A MERGE REVISION AND NOT A REPOINT
--------------------------------------
Two branches each added migrations on top of ``d9f1b3c5e7a2``:

* main, PR #59 — ``e1c3f5a7b9d2`` (``bank_questions.origin`` gains ``imported``)
* this branch, PH3-B4b — ``2577ba99b7fe`` then ``e1a3c5b7d9f2``

Merging the branches therefore produced two alembic heads, and
``alembic upgrade head`` refuses to run against more than one.

The first attempt at fixing that repointed ``2577ba99b7fe.down_revision`` from
``d9f1b3c5e7a2`` to ``e1c3f5a7b9d2``, threading this branch's chain after
main's. That produces a correct, single-headed, linear history **from scratch**
— and it silently strands every database that had already migrated on the old
chain. ``alembic_version`` records only the head, so a developer or preview
deployment sitting at ``e1a3c5b7d9f2`` would find ``e1c3f5a7b9d2`` moved
*behind* a revision it had already recorded: ``alembic upgrade head`` reports
nothing to do, ``alembic current`` reports head, and the CHECK constraint on
``bank_questions.origin`` never gains ``'imported'``. The first spreadsheet
import on that database then fails with a CheckViolation from
``question_banks.py``'s ``origin="imported"`` write.

That was reproduced on a real database during the round-14 review, which is the
only reason it is not still in the branch: CI migrates an EMPTY database every
time, so CI cannot see it. That blind spot is this repo's own documented one —
``e1c3f5a7b9d2``'s docstring says the same thing about its own backfill.

So both parents keep their original ``down_revision`` and this revision is the
convergence point:

* a database at the old ``e1a3c5b7d9f2`` applies ``e1c3f5a7b9d2``, then this;
* a database at main's ``e1c3f5a7b9d2`` applies ``2577ba99b7fe``,
  ``e1a3c5b7d9f2``, then this;
* a fresh database walks either order and lands here.

The instinct behind the repoint — do not rewrite another branch's migration to
resolve your own collision — was right. A merge revision is how you honour it
without stranding the other branch's databases.

NOTHING TO DO IN upgrade()
--------------------------
A merge revision exists to join the graph. Both parents are independent: one
widens a CHECK on ``bank_questions``, the other adds ``application_drafts``
columns and the staged-reapplication tables. They touch no common object, so
there is no ordering hazard to resolve and no data to reconcile — which is
exactly the case a merge revision is for. An empty ``upgrade()`` here is the
intended shape, not an omission.
"""

from __future__ import annotations

revision: str = "f1b3d5a7c9e2"
down_revision: tuple[str, str] = ("e1a3c5b7d9f2", "e1c3f5a7b9d2")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Join the two branches. See the module docstring: deliberately empty."""


def downgrade() -> None:
    """Split them again. Also empty — the parents own their own downgrades."""
