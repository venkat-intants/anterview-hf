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
Two links for the same applicant can be redeemed at once. Callers use
``link_or_reuse_guest``, which runs ``provision_guest_user`` inside a
SAVEPOINT. The link only ever fills an EMPTY ``applicants.user_id``, so the
request that loses the race raises ``GuestIdentityRaceError`` inside the
savepoint. Rolling back the savepoint discards only this call's own users
and user_roles rows, and the winner's user id is returned. The caller's
transaction, its row locks and its loaded ORM objects are untouched. The
earlier recovery rolled back the WHOLE session, which expired every loaded
object, and the next attribute read in ``interview_take`` raised
``MissingGreenlet`` (NEW-8, security re-review).

This used to rely on ``uq_applicants_user_id`` raising instead. It never
did: that index is on ``user_id``, and two different guest ids for one
applicant do not collide on it — the second UPDATE waited for the first to
commit, then overwrote it, orphaning the first guest identity and anything
already booked against it, such as a task's consent row (NEW-5, security
re-review, PH4-D4 wave 5).
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class GuestIdentityRaceError(IntegrityError):
    """Another request linked a user onto this applicant first. An
    ``IntegrityError`` so the callers' existing race recovery handles it."""

    def __init__(self) -> None:
        super().__init__(
            "UPDATE applicants SET user_id = ... WHERE user_id IS NULL", None,
            Exception("applicants.user_id was already set by a concurrent request"),
        )


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

    Returns the new user's id. Raises ``GuestIdentityRaceError`` (an
    ``IntegrityError``) when another request already linked a user onto the
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
    linked = await db.execute(
        text(
            "UPDATE applicants SET user_id = :uid, updated_at = :now"
            " WHERE id = :aid AND user_id IS NULL RETURNING id"
        ),
        {"uid": guest_user_id, "aid": applicant_id, "now": now},
    )
    if linked.first() is None:
        raise GuestIdentityRaceError()
    await db.flush()
    return guest_user_id


async def link_or_reuse_guest(
    db: AsyncSession,
    *,
    applicant_id: uuid.UUID,
    full_name: str | None,
    company_id: uuid.UUID | None,
    language: str | None,
    resume_text: str = "",
    email_prefix: str = "guest",
    now: datetime,
) -> uuid.UUID | None:
    """The applicant's guest identity: provisioned now or, if a concurrent
    request linked one first, that one. Returns ``None`` only if the
    applicant turns out to have no user at all (e.g. it is gone). Runs the
    provisioning in a SAVEPOINT; see RACES above. Caller commits."""
    try:
        async with db.begin_nested():
            return await provision_guest_user(
                db, applicant_id=applicant_id, full_name=full_name, company_id=company_id,
                language=language, resume_text=resume_text, email_prefix=email_prefix, now=now,
            )
    except IntegrityError:
        winner = await db.scalar(
            text("SELECT user_id FROM applicants WHERE id = :a"), {"a": applicant_id},
        )
        return winner  # asyncpg returns a uuid.UUID (or None)
