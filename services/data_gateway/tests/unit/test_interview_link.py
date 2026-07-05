"""Unit tests for interview magic-link tokens + JWT extra_claims (HR Phase 3)."""

from __future__ import annotations

from shared.auth.jwt import issue_access_token, verify_access_token

from app.interview_link import hash_interview_token, mint_interview_token


def test_mint_unique_urlsafe() -> None:
    a, b = mint_interview_token(), mint_interview_token()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_deterministic_and_keyed() -> None:
    assert hash_interview_token("tok", "s") == hash_interview_token("tok", "s")
    assert hash_interview_token("tok", "secret-A") != hash_interview_token("tok", "secret-B")
    assert len(hash_interview_token("tok", "s")) == 64  # sha256 hex


def test_jwt_extra_claims_added() -> None:
    tok = issue_access_token(
        "u1", ["guest_candidate"], "secret", extra_claims={"session_id": "sess-123"}
    )
    payload = verify_access_token(tok, "secret")
    assert payload["session_id"] == "sess-123"
    assert payload["sub"] == "u1"
    assert payload["roles"] == ["guest_candidate"]


def test_jwt_extra_claims_cannot_override_identity() -> None:
    # A caller must NOT be able to forge sub/roles via extra_claims (setdefault guard).
    tok = issue_access_token(
        "u1", ["candidate"], "secret",
        extra_claims={"sub": "attacker", "roles": ["super_admin"], "session_id": "s"},
    )
    payload = verify_access_token(tok, "secret")
    assert payload["sub"] == "u1"
    assert payload["roles"] == ["candidate"]
    assert payload["session_id"] == "s"
