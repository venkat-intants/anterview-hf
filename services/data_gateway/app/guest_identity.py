"""Provisioning a guest ('guest_candidate') identity for a candidate who
reaches a magic link with no account of their own — extracted from
``routers/interview_take.py``'s redeem path (~lines 299-345 before PH4-D4) so
task submissions (``app/job_tasks.py``) provision identity the same way
rather than inventing a second shape.

WHO THIS IS FOR
A candidate reached by a link (an interview invite, a task link) who has
never signed in. One ``guest_candidate`` users row is minted and linked onto
``applicants.user_id`` (unique per applicant — ``uq_applicants_user_id``), so
a second link for the same applicant reuses it rather than minting again.

RACES
Two links for the same applicant can be redeemed at once. This function does
the INSERT and the link; it does NOT catch the ``IntegrityError`` a lost race
raises on ``uq_applicants_user_id`` — the caller does, exactly as
``interview_take.redeem`` and ``job_tasks.start`` each already need to: on a
lost race the caller re-reads ``applicants.user_id`` for the winner's row and
carries on with that, re-acquiring whatever lock it held before the insert.
(Provisioning moved from ``job_tasks.submit`` to ``job_tasks.start`` when
consent moved there too, PH4-D4 wave 5 — consent needs an identity to be
booked against, and that has to exist before anything is stored, not after.)

WHY THIS IS NOT ``offers._ensure_candidate_identity``
Offers deliberately mint a guest with ``company_id = NULL`` ("a candidate is
never tenant staff") and a different email domain, because an offer can be
answered by someone whose application had no company context yet. An
interview or task link is always already scoped to one company through the
invite/submission it came from, and this module keeps that company on the
guest row, unchanged from what ``interview_take`` did before this extraction.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def provision_guest_user(
    db: AsyncSession,
    *,
    applicant_id: uuid.UUID,
    full_name: str | None,
    company_id: uuid.UUID | None,
    language: str | None,
    resume_text: str = "",
    email_prefix: str = "guest",
    now: datetime,
) -> uuid.UUID:
    """Insert a new ``guest_candidate`` user and link it onto the applicant.

    Returns the new user's id. Raises ``sqlalchemy.exc.IntegrityError``
    (uncaught) on a lost race against another redemption for the same
    applicant — the caller recovers, as documented above. Caller commits.
    """
    guest_user_id = uuid.uuid4()
    guest_email = f"{email_prefix}+{guest_user_id}@guest.intants.local"
    await db.execute(
        text(
            "INSERT INTO users (id, email, password_hash, full_name, company_id,"
            " resume_text, preferred_language, is_active, must_change_password,"
            " created_at, updated_at) VALUES"
            " (:id, :email, NULL, :fn, :cid, :rt, :lang, true, false, :now, :now)"
        ),
        {
            "id": guest_user_id, "email": guest_email, "fn": full_name, "cid": company_id,
            "rt": resume_text, "lang": language, "now": now,
        },
    )
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES"
            " (:uid, (SELECT id FROM roles WHERE name = 'guest_candidate'), :now)"
        ),
        {"uid": guest_user_id, "now": now},
    )
    await db.execute(
        text("UPDATE applicants SET user_id = :uid, updated_at = :now WHERE id = :aid"),
        {"uid": guest_user_id, "aid": applicant_id, "now": now},
    )
    await db.flush()
    return guest_user_id
