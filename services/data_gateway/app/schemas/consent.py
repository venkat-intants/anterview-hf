"""Pydantic schemas for the DPDP consent endpoints — S3-011 / S4-010.

ConsentRequest              — POST /consent body: purpose + version
ConsentResponse             — response shape for both 200 (idempotent) and 201 (new)
ConsentStatus               — GET /consent/status response
RevokedConsentItem          — one revoked row (consent_type, consent_id, revoked_at)
ConsentRevocationResponse   — DELETE /consent response (S4-010); covers ALL consent types
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConsentRequest(BaseModel):
    """Body for POST /consent.

    purpose must be "interview" — the only valid value for Day-1.
    version identifies the consent text revision the user agreed to.
    consent_type selects which kind of processing is being consented to.
      Defaults to "interview_voice_recording" (the original Day-1 type) so
      existing callers keep working unchanged. "video_capture" is the Phase A
      addition for candidate webcam / proctoring.
    """

    purpose: str = Field(description="Consent purpose identifier. Must be 'interview'.")
    version: int = Field(ge=1, description="Consent document version number (>=1).")
    consent_type: str = Field(
        default="interview_voice_recording",
        description=(
            "Kind of processing consented to: 'interview_voice_recording' (audio, "
            "default) or 'video_capture' (candidate webcam / proctoring)."
        ),
    )


class ConsentResponse(BaseModel):
    """Response returned on POST /consent (201 first time, 200 if idempotent)."""

    consented: bool
    consent_id: str = Field(description="UUID of the dpdp_consent_ledger row")
    granted_at: str = Field(description="ISO-8601 UTC timestamp of consent grant")


class ConsentStatus(BaseModel):
    """Response returned on GET /consent/status."""

    consented: bool
    consent_id: str | None = Field(
        default=None,
        description="UUID of active consent row, or null if none",
    )
    granted_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp of grant, or null if none",
    )


class RevokedConsentItem(BaseModel):
    """One revoked consent row returned inside ConsentRevocationResponse."""

    consent_type: str = Field(description="The consent type that was revoked")
    consent_id: str = Field(description="UUID of the revoked dpdp_consent_ledger row")
    revoked_at: str = Field(description="ISO-8601 UTC timestamp of this revocation")


class ConsentRevocationResponse(BaseModel):
    """Response returned on DELETE /consent — S4-010 (DPDP §11 right to withdraw).

    DPDP §11 mandates that a data principal can withdraw ALL consents at once.
    DELETE /consent therefore revokes every active consent row for the user —
    both 'interview_voice_recording' (voice/audio) and 'video_capture'
    (webcam / proctoring biometric) — in a single atomic call.

    revoked:  Always True when HTTP 200 is returned.
    items:    List of each consent type that was revoked in this call.
              Empty only when HTTP 404 is returned (no active consents exist).
    """

    revoked: bool
    items: list[RevokedConsentItem] = Field(
        description=(
            "Each active consent row that was revoked. Includes all consent types "
            "(interview_voice_recording, video_capture) that had an active row."
        )
    )
