"""Turning an applicant's placeholder row into an account they can sign in to.

Applying through the public form mints a ``guest_candidate`` user, because
``dpdp_consent_ledger.user_id`` is NOT NULL and consent has to hang off a user.
That row is a bookkeeping artefact: its address is
``guest+<uuid>@applicants.invalid`` and it has no password, so the person it
represents cannot sign in and has never been able to see their own application.

This module is the bridge. It mints a single-use token, emails it, and on
redemption gives the applicant a real account — after which
``/users/me/applications`` can show them where they stand.

WHY THE LINK PROVES SOMETHING
-----------------------------
Anyone can type anyone's address into an application form, so the address on an
applicant row is a claim, not a fact. Receiving the token is what turns it into
a fact, and that is why activation — not application — is the moment an
applicant row is bound to a real account. Linking at apply time would let
someone inject an application into a stranger's account by typing their email.

THE TWO BRANCHES, AND WHY BOTH ARE NEEDED
-----------------------------------------
Applicants are company-scoped, so one person applying to three companies has
three applicant rows and three guest users. Their real address can therefore
already belong to an account by the time a second link is redeemed — as it can
if they were practising on the platform first. ``users.email`` is UNIQUE with no
``deleted_at`` predicate, so there is no version of this that ignores the case:

  * **No account with that address** — promote the guest row in place. It keeps
    its id, so the consent ledger and the applicant row need no rewriting.
  * **An account already exists** — do not make a second one. Re-point the
    applicant at it, move that guest's consent-ledger rows across, and retire
    the guest. The person signs in with the account they already had, and their
    new application appears alongside the rest.

Both branches end in the same state: this applicant row is owned by a real
candidate account. That is the only postcondition the applications view needs.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_tokens import hash_token, mint_token
from app.config import settings
from app.mailer import enqueue_email

log = structlog.get_logger(__name__)

# Reuses the 'password_reset' token kind rather than inventing a fourth one.
# It is the same operation — prove you hold this address, then set a password —
# and the alternative is a parallel secret and expiry story to keep in step
# with this one. ``hr_credentials`` already does exactly this for staff.
#
# The TTL, though, is this module's own. A password reset lives one hour
# because the person requesting it is sitting at the screen waiting for it. An
# applicant is not: they applied, they got on with their day, and they may read
# the email that evening or at the weekend. An hour would expire before most
# people act, and every expiry here costs someone their only route into their
# own application. ``expires_at`` is per row, so the two can differ freely.
_TOKEN_KIND = "password_reset"

# The placeholder domain _ensure_guest_user writes. Reserved by RFC 2606, so it
# can never route mail, and the marker this module tests to decide whether an
# applicant's account is still unclaimed.
GUEST_EMAIL_DOMAIN = "@applicants.invalid"


class ActivationError(Exception):
    """The token is unusable, or the account was already claimed."""


async def stage_activation_email(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    applicant_email: str,
    applicant_name: str,
    job_title: str,
    company_id: uuid.UUID,
    company_name: str | None,
    now: datetime,
) -> bool:
    """Queue the "your application is in" email, with an activation link.

    Returns True when an activation link was included. Caller commits.

    Deliberately best-effort at the call site: an application that landed must
    not be rolled back because an email could not be staged. The candidate
    still has their application; what they lose is a convenience.
    """
    claimed = await _is_claimed(db, user_id)
    raw: str | None = None
    if not claimed:
        raw = mint_token()
        await db.execute(
            text(
                "INSERT INTO auth_tokens (id, user_id, kind, token_hash, expires_at, created_at)"
                " VALUES (:id, :uid, :kind, :th, :exp, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "kind": _TOKEN_KIND,
                "th": hash_token(raw, _TOKEN_KIND),
                "exp": now + timedelta(hours=settings.apply_activation_ttl_hours),
                "now": now,
            },
        )

    lang = await db.scalar(
        text("SELECT preferred_language FROM users WHERE id = :uid"), {"uid": user_id}
    )
    await enqueue_email(
        db,
        to=applicant_email,
        template="application_received",
        lang=(lang or "en"),
        ctx={
            "name": applicant_name,
            "job_title": job_title,
            "company": company_name,
            # The token rides in the URL FRAGMENT, which browsers do not send
            # to servers — the same discipline as the exam and interview links.
            "set_url": (
                f"{settings.app_base_url.rstrip('/')}/activate#{raw}" if raw else None
            ),
            "applications_url": f"{settings.app_base_url.rstrip('/')}/applications",
            "brand": company_name,
        },
        to_user_id=user_id,
        company_id=company_id,
        related_kind="application_received",
    )
    return raw is not None


async def _is_claimed(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Whether this applicant's user is already a real, signed-in-able account."""
    row = (
        await db.execute(
            text("SELECT email, password_hash FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
    ).first()
    if row is None:
        return False
    return not str(row.email).endswith(GUEST_EMAIL_DOMAIN) or row.password_hash is not None


async def activation_target(db: AsyncSession, raw_token: str) -> dict[str, str]:
    """Whose account a token activates, for the page to greet them by name.

    Reads the applicant row rather than the user row on purpose: the user row
    still holds the placeholder address at this point, and showing somebody
    ``guest+3f2a...@applicants.invalid`` as "your email" would look broken.
    """
    user_id = await _live_token_user(db, raw_token)
    row = (
        await db.execute(
            text(
                "SELECT a.full_name, a.email, a.target_job_title, c.name AS company"
                "  FROM applicants a"
                "  JOIN companies c ON c.id = a.company_id"
                " WHERE a.user_id = :uid AND a.deleted_at IS NULL"
                " ORDER BY a.created_at DESC LIMIT 1"
            ),
            {"uid": user_id},
        )
    ).first()
    if row is None or not row.email:
        raise ActivationError("This link is no longer valid.")
    return {
        "full_name": row.full_name or "",
        "email": row.email,
        "job_title": row.target_job_title or "",
        "company_name": row.company or "",
    }


async def activate(
    db: AsyncSession, *, raw_token: str, new_password: str
) -> dict[str, str]:
    """Consume the token and give the applicant an account. Caller commits.

    Returns ``{"email": ..., "linked": "existing" | "new"}`` so the caller can
    tell the person whether they are signing in to an account they already had.
    """
    user_id = await _live_token_user(db, raw_token)
    now = datetime.now(tz=UTC)

    applicant = (
        await db.execute(
            text(
                "SELECT id, email, full_name FROM applicants"
                " WHERE user_id = :uid AND deleted_at IS NULL"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"uid": user_id},
        )
    ).first()
    if applicant is None or not applicant.email:
        raise ActivationError("This link is no longer valid.")
    address = str(applicant.email).strip().lower()

    # Hash before either branch: bcrypt at cost 12 is deliberately slow, and
    # doing it inside the transaction would hold row locks for its duration.
    password_hash = await asyncio.to_thread(
        lambda: bcrypt.hashpw(
            new_password.encode(), bcrypt.gensalt(rounds=settings.password_hash_rounds)
        ).decode()
    )

    existing = await db.scalar(
        text(
            "SELECT id FROM users WHERE lower(email) = :em"
            " AND deleted_at IS NULL AND id <> :uid"
        ),
        {"em": address, "uid": user_id},
    )

    await _consume(db, raw_token, now)

    if existing is not None:
        await _link_to_existing(
            db, guest_user_id=user_id, target_user_id=uuid.UUID(str(existing)), now=now
        )
        log.info("apply.activation.linked", user_id=str(existing))
        return {"email": address, "linked": "existing"}

    await _promote_guest(
        db,
        user_id=user_id,
        email=address,
        full_name=applicant.full_name,
        password_hash=password_hash,
        now=now,
    )
    log.info("apply.activation.promoted", user_id=str(user_id))
    return {"email": address, "linked": "new"}


async def _live_token_user(db: AsyncSession, raw_token: str) -> uuid.UUID:
    """The user a usable token belongs to, or ActivationError.

    One message for expired, consumed and never-existed. A link that has been
    used should not be distinguishable from one that was never issued.
    """
    row = (
        await db.execute(
            text(
                "SELECT user_id, consumed_at, expires_at FROM auth_tokens"
                " WHERE token_hash = :th AND kind = :kind"
            ),
            {"th": hash_token(raw_token, _TOKEN_KIND), "kind": _TOKEN_KIND},
        )
    ).first()
    now = datetime.now(tz=UTC)
    if row is None or row.consumed_at is not None or row.expires_at <= now:
        raise ActivationError(
            "This link is invalid or has expired. Ask the hiring team for a new one."
        )
    return uuid.UUID(str(row.user_id))


async def _consume(db: AsyncSession, raw_token: str, now: datetime) -> None:
    await db.execute(
        text(
            "UPDATE auth_tokens SET consumed_at = :now"
            " WHERE token_hash = :th AND kind = :kind AND consumed_at IS NULL"
        ),
        {"now": now, "th": hash_token(raw_token, _TOKEN_KIND), "kind": _TOKEN_KIND},
    )


async def _promote_guest(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    email: str,
    full_name: str | None,
    password_hash: str,
    now: datetime,
) -> None:
    """Give the placeholder row the applicant's real identity.

    ``email_verified_at`` is set because holding the token proved control of
    the address — the same reasoning ``auth.reset_password`` uses.

    ``company_id`` is cleared. The guest row inherited the hiring company's id
    so the consent ledger had a tenant to file under, but a candidate is not
    staff at the company they applied to, and leaving it set would make them
    look like a member of that tenant to anything that reads company_id.
    """
    await db.execute(
        text(
            "UPDATE users SET email = :em, password_hash = :pw,"
            " full_name = COALESCE(:fn, full_name),"
            " email_verified_at = COALESCE(email_verified_at, :now),"
            " must_change_password = false, company_id = NULL, updated_at = :now"
            " WHERE id = :uid"
        ),
        {"em": email, "pw": password_hash, "fn": full_name, "now": now, "uid": user_id},
    )
    # Becomes a candidate and stops being a guest, in that order: every
    # candidate route rejects guest_candidate, and the two roles together would
    # leave the account describable as both.
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at)"
            " SELECT :uid, id, :now FROM roles WHERE name = 'candidate'"
            " ON CONFLICT DO NOTHING"
        ),
        {"uid": user_id, "now": now},
    )
    await db.execute(
        text(
            "DELETE FROM user_roles WHERE user_id = :uid"
            " AND role_id = (SELECT id FROM roles WHERE name = 'guest_candidate')"
        ),
        {"uid": user_id},
    )


async def _link_to_existing(
    db: AsyncSession,
    *,
    guest_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    now: datetime,
) -> None:
    """Hand this guest's applicant rows and consent history to a real account.

    The consent rows move rather than being re-created: their ``granted_at``
    and hashed request evidence are the record of a decision the person made at
    a particular moment, and re-stamping them today would quietly destroy the
    only proof of when consent was actually given.

    The guest row is soft-deleted and its address tombstoned, so the row stays
    for audit while releasing the UNIQUE constraint on ``email``.
    """
    await db.execute(
        text(
            "UPDATE applicants SET user_id = :new, updated_at = :now"
            " WHERE user_id = :old AND deleted_at IS NULL"
        ),
        {"new": target_user_id, "old": guest_user_id, "now": now},
    )
    # Skip any (consent_type, purpose) the target already holds: the ledger has
    # a uniqueness rule per active consent, and the older grant is the one that
    # matters.
    await db.execute(
        text(
            "UPDATE dpdp_consent_ledger SET user_id = :new"
            " WHERE user_id = :old"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM dpdp_consent_ledger t"
            "      WHERE t.user_id = :new"
            "        AND t.consent_type = dpdp_consent_ledger.consent_type"
            "        AND t.purpose = dpdp_consent_ledger.purpose"
            "        AND t.granted = true AND t.revoked_at IS NULL)"
        ),
        {"new": target_user_id, "old": guest_user_id},
    )
    await db.execute(
        text(
            "UPDATE users SET deleted_at = :now, is_active = false,"
            " email = 'retired+' || id::text || :domain, updated_at = :now"
            " WHERE id = :old"
        ),
        {"now": now, "old": guest_user_id, "domain": GUEST_EMAIL_DOMAIN},
    )
