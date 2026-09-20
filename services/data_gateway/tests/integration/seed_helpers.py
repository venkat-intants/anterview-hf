"""Seeding helpers for the database smokes and integration tests.

``approve_for_publish`` walks a seeded workflow draft through review the way the
product does — submitted by one person, approved by another — at the SQL level.
PH4-O6 made the database refuse any other route to 'published' (migration
a2b4c6d8e0f1): a draft cannot be approved without a distinct submitter and
reviewer, and cannot be published unless it was already approved. A smoke that
seeds a live workflow must therefore take the same steps; this is them.

Call it once the draft's rounds and criteria are seeded: submitting locks them,
and the fingerprint recorded is the real one, so ``workflows.publish`` accepts
the version afterwards exactly as it would one approved through the API.

The two users it creates are synthetic, belong to the workflow's company, and
exist only so the separation-of-duties check has two different people to see.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.workflows import workflow_fingerprint


async def approve_for_publish(
    db: AsyncSession, *, workflow_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    """draft → in_review → approved. The caller then sets status = 'published'."""
    submitter, reviewer = uuid.uuid4(), uuid.uuid4()
    for uid, who in ((submitter, "submitter"), (reviewer, "reviewer")):
        await db.execute(
            text("INSERT INTO users (id, email, company_id) VALUES (:i, :e, :c)"),
            {"i": uid, "e": f"seed-{who}-{uid.hex[:12]}@seed.test", "c": company_id},
        )
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'in_review', submitted_by_user_id = :s,"
            " submitted_for_review_at = now(), review_fingerprint = :f WHERE id = :w"
        ),
        {"s": submitter, "w": workflow_id, "f": await workflow_fingerprint(db, workflow_id)},
    )
    await db.execute(
        text(
            "UPDATE workflows SET review_status = 'approved', reviewed_by_user_id = :r,"
            " reviewed_at = now() WHERE id = :w"
        ),
        {"r": reviewer, "w": workflow_id},
    )
