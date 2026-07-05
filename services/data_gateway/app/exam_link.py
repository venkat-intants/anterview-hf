"""Exam magic-link tokens (HR workflow Phase 2).

The token is an OPAQUE random secret (``secrets.token_urlsafe``). It is NOT a
JWT — all binding (which exam, which applicant, which tenant, expiry, revocation,
single-use) lives in the ``exam_assignments`` row. We persist only
``hmac_sha256(token, exam_link_secret)``; the raw token exists solely in the
shared URL. This mirrors how refresh tokens and consent-ledger evidence are
hashed elsewhere in this repo, so a DB read can never recover a live link.

Using HMAC with a dedicated ``exam_link_secret`` (never the auth ``jwt_secret``)
means a leaked exam secret cannot forge user sessions, and an attacker who reads
``token_hash`` still cannot derive a working token.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 256-bit opaque token — infeasible to brute-force against the unique token_hash.
_TOKEN_BYTES = 32


def mint_exam_token() -> str:
    """Return a fresh URL-safe opaque token (raw — store only its hash, share this)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_exam_token(raw_token: str, secret: str) -> str:
    """Keyed HMAC-SHA256 hex of the raw token. Deterministic → indexable lookup."""
    return hmac.new(secret.encode(), raw_token.encode(), hashlib.sha256).hexdigest()
