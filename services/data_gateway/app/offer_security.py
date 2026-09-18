"""The credentials of an offer — PH4-A3/A4. No database, no I/O.

THE LINK
An offer reaches the candidate as ``{base}/offer#<token>``: a 256-bit random
token carried in the URL fragment (never sent to a server as part of a URL) and
presented as the ``X-Offer-Token`` header. Only ``hmac_sha256(token, secret)``
is stored. The raw link does exist once more: in the outbound email until it
is sent — the outbox clears a message's body once it is delivered or has
finally failed (app.mailer).

THE CODE
Reading an offer needs the link. ANSWERING it needs more: a six-digit code sent
to the candidate's email at the moment they choose to accept or decline. A
forwarded or leaked link lets someone read an offer; it does not let them
accept or refuse a job on the candidate's behalf. Codes are hashed, live ten
minutes, allow five attempts, and are bound to the purpose they were issued
for.

THE PREBOARDING SESSION
Documents are the most sensitive thing an offer carries, so the link alone does
not open them either: a code, then an hour-long session token (returned to the
page, never emailed, stored hashed) presented as ``X-Offer-Session``.

THE EXPORT
An HRMS handoff is a JSON payload signed with HMAC-SHA256 over its canonical
form (sorted keys, no whitespace, UTF-8). The receiver recomputes the digest
with the shared key; ``key_id`` names the key, so a rotation is visible.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from app.config import settings

_TOKEN_BYTES = 32
CODE_DIGITS = 6
MAX_CODE_ATTEMPTS = 5


def _derive(label: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"ph4:{label}".encode(),
                    hashlib.sha256).hexdigest()


def _link_secret() -> str:
    return settings.offer_link_secret or _derive("offer_link")


def _export_secret() -> str:
    return settings.hrms_export_secret or _derive("hrms_export")


def mint_offer_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_offer_token(raw: str) -> str:
    return hmac.new(_link_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()


def mint_code() -> str:
    """Six digits, uniformly drawn — not random.randint."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(offer_id: str, purpose: str, code: str) -> str:
    """Bound to the offer and the purpose: a decline code cannot accept."""
    msg = f"{offer_id}:{purpose}:{code.strip()}".encode()
    return hmac.new(_link_secret().encode(), msg, hashlib.sha256).hexdigest()


def codes_match(stored_hash: str, offer_id: str, purpose: str, code: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_code(offer_id, purpose, code))


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def hash_session_token(raw: str) -> str:
    msg = f"session:{raw}".encode()
    return hmac.new(_link_secret().encode(), msg, hashlib.sha256).hexdigest()


def export_key_id() -> str:
    """Names the key without being a cheap test of a guessed one."""
    return hmac.new(_export_secret().encode(), b"key-id", hashlib.sha256).hexdigest()[:12]


def sign_export(payload: dict[str, Any]) -> str:
    return hmac.new(_export_secret().encode(), canonical(payload), hashlib.sha256).hexdigest()


def verify_export(payload: dict[str, Any], signature: str) -> bool:
    return hmac.compare_digest(sign_export(payload), signature)
