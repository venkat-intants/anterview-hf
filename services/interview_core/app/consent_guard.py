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
        consent_type IN ('interview_voice_recording', 'video_capture')
        purpose      = 'interview'
        granted      = TRUE
        revoked_at   IS NULL

WHY there are two consent types (DPDP-5, code review 2026-08-07):
    data_gateway records two purposes separately and the React intro screen
    collects them as two independent opt-ins: voice recording (the interview
    audio) and video capture (webcam / gaze proctoring, which is
    biometric-derived data). For a year only the voice type was ever read
    server-side, so a candidate who declined the camera still had proctoring
    events persisted — consent for one purpose silently doing duty for
    another, which is the exact thing DPDP §6(1)/§7(a) purpose limitation
    forbids. ``has_active_consent`` therefore takes the type as an argument;
    the default keeps every voice-path call site unchanged.

    ``_pin_consent_type_constants`` in tests/unit/test_consent_guard.py parses
    data_gateway's router and asserts both literals still agree, because "keep
    in sync" written in a comment is not a mechanism.

REVOCATION IS ALL-OR-NOTHING, by design (not an oversight):
    ``DELETE /consent`` in data_gateway revokes every active row for the user,
    both types at once. A candidate who withdraws mid-interview is withdrawing
    from the interview, not tuning one channel of it, so a partial revoke has
    no product meaning today and a per-type revoke path would only add a state
    ("audio yes, camera no, mid-session") nothing downstream can act on. If
    that changes, the change belongs in data_gateway's router; this gate reads
    whatever the ledger says and needs no edit.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Constants mirror data_gateway/app/routers/consent.py — keep in sync.
# PUBLIC (no underscore) for VIDEO_CONSENT_TYPE because callers outside this
# module have to name it to ask for it; the voice type stays private because it
# is the default and no caller should have to spell it.
_CONSENT_TYPE: str = "interview_voice_recording"
VIDEO_CONSENT_TYPE: str = "video_capture"
_PURPOSE: str = "interview"


async def has_active_consent(
    db: AsyncSession, user_id: str, consent_type: str = _CONSENT_TYPE
) -> bool:
    """Return True iff ``user_id`` has an active consent of ``consent_type``.

    Args:
        db: open async DB session (caller-managed).
        user_id: the JWT ``sub`` claim — expected to be a UUID string. A
            malformed value returns ``False`` rather than raising; the
            gate fails closed and the caller will reject the request.
        consent_type: which ledger purpose to check. Defaults to the voice
            type, so the session-create and WS-connect call sites read exactly
            as they did before this parameter existed. Pass
            ``VIDEO_CONSENT_TYPE`` before persisting anything webcam-derived.
            An unknown type simply matches no row and returns False — the
            fail-closed direction, so a typo blocks processing rather than
            waving it through.

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
            "consent_type": consent_type,
            "purpose": _PURPOSE,
        },
    )
    return result.scalar_one_or_none() is not None
