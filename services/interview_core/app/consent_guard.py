"""DPDP consent gate — server-side enforcement of S3-011.

WHY this lives here (not in data_gateway):
    The gate is enforced at the boundary where PII processing actually
    begins — when a candidate creates a session (``POST /api/sessions``)
    and when they open the interview WebSocket. Both endpoints live in
    interview_core, so the check has to live here too. data_gateway owns
    the ledger; we read from it.

WHY raw SQL instead of importing the ORM model:
    Importing ``DpdpConsent`` from data_gateway would create a hard
    dependency on data_gateway's Python package, which we don't have
    today (services are deployed independently). The two services
    already share the same Postgres database, so a direct query is the
    legitimately cheap path. If we ever split databases per service,
    swap this helper for an internal ``GET /consent/status`` HTTP call
    against data_gateway — the call sites won't change.

WHY this MUST be enforced server-side:
    The React modal in S3-011 gates the UX, but any authenticated user
    with curl could ``POST /api/sessions`` and open the WS without ever
    triggering the modal. Without this server-side gate, the consent
    ledger is theatre. security-auditor flagged this as the CRITICAL
    finding on S3-011 before merge.

CONTRACT (must stay in lockstep with data_gateway/app/routers/consent.py):
    Active consent =
        consent_type = 'interview_voice_recording'
        purpose      = 'interview'
        granted      = TRUE
        revoked_at   IS NULL
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Constants mirror data_gateway/app/routers/consent.py — keep in sync.
_CONSENT_TYPE: str = "interview_voice_recording"
_PURPOSE: str = "interview"


async def has_active_consent(db: AsyncSession, user_id: str) -> bool:
    """Return True iff ``user_id`` has an active interview-recording consent.

    Args:
        db: open async DB session (caller-managed).
        user_id: the JWT ``sub`` claim — expected to be a UUID string. A
            malformed value returns ``False`` rather than raising; the
            gate fails closed and the caller will reject the request.

    A single ``SELECT 1 ... LIMIT 1`` against the indexed
    ``(user_id, granted_at)`` index — cheap enough to call on every
    session-create and every WS connect without batching.

    Revocation note (S4-010):
        The ``revoked_at IS NULL`` predicate ensures that a user who has
        exercised their DPDP §11 right-to-withdraw (via
        ``DELETE /consent`` in data_gateway) is immediately blocked from
        starting new sessions. No cache invalidation is needed — the gate
        re-checks the DB on every session-create and every WS connect.
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return False

    result = await db.execute(
        text(
            "SELECT 1 FROM dpdp_consent_ledger "
            "WHERE user_id = :user_id "
            "  AND consent_type = :consent_type "
            "  AND purpose = :purpose "
            "  AND granted = TRUE "
            "  AND revoked_at IS NULL "  # S4-010: revoked rows are excluded
            "LIMIT 1"
        ),
        {
            "user_id": user_uuid,
            "consent_type": _CONSENT_TYPE,
            "purpose": _PURPOSE,
        },
    )
    return result.scalar_one_or_none() is not None
