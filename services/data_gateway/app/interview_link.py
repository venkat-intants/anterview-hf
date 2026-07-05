"""Interview magic-link tokens (HR workflow Phase 3).

Opaque 256-bit secret; all binding (which invite, applicant, tenant, expiry,
single-use) lives in the interview_invites row. We persist only
hmac_sha256(token, interview_link_secret) — the raw token lives only in the
shared URL #fragment. Mirrors exam_link.py but uses a SEPARATE secret
(interview_link_secret) so a leaked interview secret forges neither exam links
nor JWTs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_TOKEN_BYTES = 32  # 256-bit


def mint_interview_token() -> str:
    """Return a fresh URL-safe opaque token (raw — store only its hash, share this)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_interview_token(raw_token: str, secret: str) -> str:
    """Keyed HMAC-SHA256 hex of the raw token. Deterministic → indexable lookup."""
    return hmac.new(secret.encode(), raw_token.encode(), hashlib.sha256).hexdigest()
