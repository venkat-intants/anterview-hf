"""Exam-round content locks — PH4-D1.

A published exam round's content, and its grading fields, are frozen once it
is published or once anyone has taken it (migration ``d2a4c6e8f0b3``, triggers
``exam_round_content_frozen`` and ``exam_rounds_frozen``). Those triggers are
the backstop that holds even against a raw UPDATE; this module is the single
place every HR writer calls to turn that refusal into a sentence a person can
act on BEFORE the write reaches the database — so the app-level message and
the trigger's own message never drift apart in meaning, and every writer
checks the same rule the same way.

"Duplicate round" (``POST .../rounds/{id}/duplicate`` in
``app/routers/hr_rounds.py``) is the way forward once a round is locked: HR
gets an editable draft copy rather than a dead end.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExamAttempt, ExamRound

PUBLISHED_REASON = (
    "This round is published — its content is fixed. Unpublish it while nobody has "
    "taken it, or duplicate it to make changes."
)
TAKEN_REASON = "This round has been taken — its content is fixed."
RACE_REASON = "This changed while you were working. Reload and try again."


async def _attempt_count(db: AsyncSession, company_id: uuid.UUID, round_id: uuid.UUID) -> int:
    n = await db.scalar(
        select(func.count()).select_from(ExamAttempt).where(
            ExamAttempt.round_id == round_id,
            ExamAttempt.company_id == company_id,
            ExamAttempt.deleted_at.is_(None),
        )
    )
    return int(n or 0)


async def round_lock_reason(db: AsyncSession, company_id: uuid.UUID, rnd: ExamRound) -> str | None:
    """The sentence for a locked round, or ``None`` when it may still be edited.

    Takes the round already in hand — every caller here has just loaded it
    (``_get_owned_round`` or equivalent) to check ownership, and re-fetching it
    would be a second, redundant query for the same row.
    """
    if rnd.status == "published":
        return PUBLISHED_REASON
    if await _attempt_count(db, company_id, rnd.id) > 0:
        return TAKEN_REASON
    return None


async def assert_round_editable(db: AsyncSession, company_id: uuid.UUID, rnd: ExamRound) -> None:
    """Raise 409 with a reason a person can act on when the round is locked."""
    reason = await round_lock_reason(db, company_id, rnd)
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)


async def assert_round_editable_by_id(
    db: AsyncSession, company_id: uuid.UUID, round_id: uuid.UUID
) -> None:
    """For a caller that has only the round's id (a section's ``round_id``,
    say) rather than the row itself. Fetches, then applies the same rule."""
    rnd = await db.scalar(
        select(ExamRound).where(
            ExamRound.id == round_id,
            ExamRound.company_id == company_id,
            ExamRound.deleted_at.is_(None),
        )
    )
    if rnd is None:
        return  # gone, or a cascade cleaning up — not this module's call
    await assert_round_editable(db, company_id, rnd)


async def assert_editable_by_exam(
    db: AsyncSession, company_id: uuid.UUID, exam_id: uuid.UUID
) -> None:
    """The legacy flat-exam writers (``hr_exams.py`` / ``hr_coding.py``) only
    know ``exam_id``; check the exam's first live round — a fresh exam always
    has exactly one, from the default-round backfill and every exam creation
    since."""
    rnd = await db.scalar(
        select(ExamRound)
        .where(
            ExamRound.exam_id == exam_id,
            ExamRound.company_id == company_id,
            ExamRound.deleted_at.is_(None),
        )
        .order_by(ExamRound.position.asc())
        .limit(1)
    )
    if rnd is None:
        return
    await assert_round_editable(db, company_id, rnd)


async def commit_or_conflict(db: AsyncSession) -> None:
    """Commit a guarded write, turning a trigger's own refusal (the backstop —
    a race lost between the app-level check above and this commit) into the
    same 409 shape every other refusal here uses."""
    try:
        await db.commit()
    except DBAPIError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=RACE_REASON) from None
