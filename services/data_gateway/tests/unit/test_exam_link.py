"""Unit tests for exam magic-link token mint/hash (HR workflow Phase 2)."""

from __future__ import annotations

from app.exam_link import hash_exam_token, mint_exam_token


def test_mint_is_long_urlsafe_and_unique() -> None:
    a = mint_exam_token()
    b = mint_exam_token()
    assert a != b  # random
    assert len(a) >= 32
    # token_urlsafe output is URL-safe base64 (no +,/,=).
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_is_deterministic_for_same_secret() -> None:
    assert hash_exam_token("tok", "secret") == hash_exam_token("tok", "secret")


def test_hash_is_keyed_different_secret_different_hash() -> None:
    assert hash_exam_token("tok", "secret-A") != hash_exam_token("tok", "secret-B")


def test_hash_differs_per_token() -> None:
    assert hash_exam_token("tok-1", "s") != hash_exam_token("tok-2", "s")


def test_hash_is_hex_sha256_length() -> None:
    h = hash_exam_token("tok", "secret")
    assert len(h) == 64
    int(h, 16)  # hex-decodable (raises if not)
