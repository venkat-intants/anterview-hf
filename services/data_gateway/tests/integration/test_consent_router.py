"""Integration tests for DPDP consent endpoints — S3-011 / S4-009 / S4-010 / S4-012.

Runs against the real local Postgres (port 5433) and Redis (port 6379).
Each test that needs an authenticated user registers a throw-away account.

Test matrix (7 + 4 + 2 + 4 + 5 cases):
  S3-011 original (7):
  1. POST /consent without JWT → 401
  2. POST /consent with valid JWT, purpose="interview", version=1 → 201 + row written
  3. POST /consent twice → second call returns 200 (idempotent), same consent_id, no dup row
  4. POST /consent with purpose="something_else" → 400
  5. GET /consent/status when no consent → {"consented": false, ...}
  6. GET /consent/status after consent exists → {"consented": true, ...}
  7. Evidence jsonb ip_hash is a 64-char hex sha256, NOT the raw IP

  S4-009 partial unique index (2):
  8.  Concurrent POSTs collapse to one DB row (race caught by unique index or fast-path)
  9.  Unique index ix_dpdp_consent_active_unique exists in pg_indexes

  S4-010 DELETE /consent revocation (4):
  10. POST then DELETE → 200, items list populated, DB rows have revoked_at set
  11. DELETE with no prior POST → 404
  12. POST → DELETE → DELETE → second DELETE returns 404 (idempotent)
  13. POST session → DELETE consent → POST session → 403 (consent gate cross-service)

  S4-012 trusted-proxy-count gate (Caddy/Convention-A model):
  14. trusted_proxy_count=0 + spoofed XFF → hash matches direct client host, not spoof
  15. trusted_proxy_count=1 + XFF="1.2.3.4" (single Caddy hop) → matches "1.2.3.4"
  16. trusted_proxy_count=2 + XFF="1.2.3.4, <cdn>" (CDN→Caddy) → matches "1.2.3.4"
  17. trusted_proxy_count=1 + XFF="attacker, real-client" → matches "real-client"
  18. trusted_proxy_count=1 + no XFF header → falls back to request.client.host

  Video-capture full-withdrawal (5):
  18. POST voice+video → DELETE → both rows revoked; response items list covers both types
  19. DELETE with ONLY voice active → only voice row in items; video never touched
  20. DELETE with ONLY video_capture active → only video row in items
  21. After DELETE of both, GET /consent/status for each type returns consented=false
  22. video_capture revocation is reflected immediately (same query consent_guard uses)
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.config import settings
from app.database import get_session_factory
from app.main import app
from app.models import DpdpConsent
from app.routers.consent import _extract_client_ip, _hash_value

_REGISTER_URL = "/auth/register"
_CONSENT_URL = "/consent"
_CONSENT_STATUS_URL = "/consent/status"

# The client IP used by ASGI test transport (no real socket)
_TEST_CLIENT_IP = "testclient"


def _unique_email() -> str:
    return f"consent-test-{uuid.uuid4().hex[:8]}@example.com"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """Spin up the full ASGI app with lifespan (DB + Redis + AuthProvider)."""
    async with AsyncClient(  # noqa: SIM117
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=30.0,
    ) as ac:
        async with app.router.lifespan_context(app):
            yield ac  # type: ignore[misc]


@pytest_asyncio.fixture
async def auth_token(client: AsyncClient) -> str:
    """Register a throw-away user and return a valid Bearer token."""
    email = _unique_email()
    resp = await client.post(
        _REGISTER_URL,
        json={"email": email, "password": "Secur3Pass!", "full_name": "Consent Test User"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["access_token"])


@pytest_asyncio.fixture
async def auth_data(client: AsyncClient) -> dict[str, str]:
    """Register a throw-away user and return both token and user_id."""
    email = _unique_email()
    resp = await client.post(
        _REGISTER_URL,
        json={"email": email, "password": "Secur3Pass!", "full_name": "Consent Data User"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"access_token": body["access_token"], "user_id": body["user_id"]}


# ---------------------------------------------------------------------------
# Test 1: POST /consent without JWT → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_consent_no_jwt_returns_401(client: AsyncClient) -> None:
    """Unauthenticated request must be rejected with 401."""
    resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 2: POST /consent with valid JWT → 201, row written, shape correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_consent_first_grant_returns_201(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """First consent grant must return 201 with correct response shape and a DB row."""
    token = auth_data["access_token"]
    user_id = auth_data["user_id"]

    resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Response shape
    assert body["consented"] is True
    assert "consent_id" in body
    assert len(body["consent_id"]) == 36  # UUID v4 string
    assert "granted_at" in body
    assert body["granted_at"]  # non-empty ISO timestamp

    # Verify the row was actually written to the DB
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(DpdpConsent).where(
            DpdpConsent.id == uuid.UUID(body["consent_id"])
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

    assert row is not None
    assert str(row.user_id) == user_id
    assert row.consent_type == "interview_voice_recording"
    assert row.granted is True
    assert row.purpose == "interview"
    assert row.revoked_at is None
    assert row.evidence is not None


# ---------------------------------------------------------------------------
# Test 3: POST /consent twice → 200 (idempotent), same consent_id, no dup row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_consent_idempotent_second_call_returns_200(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """Second POST for the same user must return 200 with the identical consent_id."""
    token = auth_data["access_token"]
    user_id = auth_data["user_id"]

    # First call
    r1 = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 201, r1.text
    consent_id_first = r1.json()["consent_id"]

    # Second call — same payload
    r2 = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["consented"] is True
    assert body2["consent_id"] == consent_id_first  # same row returned

    # Confirm exactly one row in the DB for this user
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(DpdpConsent).where(
            DpdpConsent.user_id == uuid.UUID(user_id),
            DpdpConsent.consent_type == "interview_voice_recording",
            DpdpConsent.purpose == "interview",
            DpdpConsent.granted.is_(True),
            DpdpConsent.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

    assert len(rows) == 1, f"Expected 1 row, got {len(rows)} — duplicate insert detected"


# ---------------------------------------------------------------------------
# Test 4: POST /consent with invalid purpose → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_consent_invalid_purpose_returns_400(
    client: AsyncClient, auth_token: str
) -> None:
    """purpose='something_else' must be rejected with 400."""
    resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "something_else", "version": 1},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "something_else" in detail


# ---------------------------------------------------------------------------
# Test 5: GET /consent/status when no consent → consented=false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_consent_status_no_consent(
    client: AsyncClient, auth_token: str
) -> None:
    """A freshly registered user has no consent — status must reflect that."""
    resp = await client.get(
        _CONSENT_STATUS_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["consented"] is False
    assert body["consent_id"] is None
    assert body["granted_at"] is None


# ---------------------------------------------------------------------------
# Test 6: GET /consent/status after consent exists → consented=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_consent_status_after_consent(
    client: AsyncClient, auth_token: str
) -> None:
    """After recording consent, GET /consent/status must return consented=true."""
    # Grant consent first
    post_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert post_resp.status_code == 201, post_resp.text
    consent_id = post_resp.json()["consent_id"]

    # Now check status
    resp = await client.get(
        _CONSENT_STATUS_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["consented"] is True
    assert body["consent_id"] == consent_id
    assert body["granted_at"] is not None


# ---------------------------------------------------------------------------
# Test 7: evidence ip_hash is sha256 hex (64 chars), NOT raw IP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_ip_hash_is_sha256_hex_not_raw_ip(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """The stored evidence.ip_hash must be a 64-char sha256 hex, not the raw client IP.

    The ASGI test transport sends requests from 'testclient' (no real socket).
    httpx ASGITransport sets request.client to None in newer versions, so the
    router falls back to "unknown". Either way, the hash must be 64 hex chars
    and must NOT equal the raw IP value.
    """
    token = auth_data["access_token"]

    post_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert post_resp.status_code == 201, post_resp.text
    consent_id = post_resp.json()["consent_id"]

    # Read the row from DB and check the evidence blob
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(DpdpConsent).where(DpdpConsent.id == uuid.UUID(consent_id))
        result = await db.execute(stmt)
        row = result.scalar_one()

    evidence = row.evidence
    assert evidence is not None
    ip_hash: str = evidence["ip_hash"]

    # Must be exactly 64 hex characters (sha256 output)
    assert len(ip_hash) == 64, f"ip_hash length {len(ip_hash)}, expected 64"
    assert all(c in "0123456789abcdef" for c in ip_hash), "ip_hash is not valid hex"

    # Must NOT be the raw IP (defensive: check a few known raw values)
    raw_candidates = [_TEST_CLIENT_IP, "127.0.0.1", "unknown", "::1"]
    for raw in raw_candidates:
        assert ip_hash != raw, f"ip_hash equals raw value '{raw}' — PII stored in plaintext!"

    # Verify it actually matches the expected hash computation.
    # ASGITransport (httpx.AsyncClient): request.client.host == "127.0.0.1"
    # TestClient (starlette): request.client.host == "testclient"
    # No client object at all: router falls back to "unknown".
    # Whichever the test transport produces, the hash must match one of these.
    expected_hashes = {
        raw: hashlib.sha256(
            (raw + settings.consent_ip_salt).encode("utf-8")
        ).hexdigest()
        for raw in ("unknown", "testclient", "127.0.0.1")
    }
    assert ip_hash in expected_hashes.values(), (
        f"ip_hash '{ip_hash}' does not match sha256(<raw>+salt) for any "
        f"known test-transport client value {list(expected_hashes.keys())}"
    )


# ---------------------------------------------------------------------------
# S4-009 — Partial unique index race safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_posts_collapse_to_one_row(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """Two concurrent POSTs for the same user must collapse to exactly one DB row.

    Uses asyncio.gather() to issue both requests concurrently. The explicit
    idempotency pre-check or the partial unique index constraint ensures only
    one row is written. Both responses must be 2xx (201 + 200 or 200 + 200)
    and they must carry the same consent_id.
    """
    token = auth_data["access_token"]
    user_id = auth_data["user_id"]

    async def _post() -> tuple[int, str]:
        r = await client.post(
            _CONSENT_URL,
            json={"purpose": "interview", "version": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code, r.json()["consent_id"]

    # Fire both requests concurrently.
    results = await asyncio.gather(_post(), _post())
    status_codes = [r[0] for r in results]
    consent_ids = [r[1] for r in results]

    # Both must be 2xx.
    for code in status_codes:
        assert code in (200, 201), f"Expected 200 or 201, got {code}"

    # Both must return the same consent_id.
    assert consent_ids[0] == consent_ids[1], (
        f"Concurrent POSTs returned different consent_ids: {consent_ids} — "
        "duplicate rows may have been inserted"
    )

    # Confirm exactly one active row in the DB.
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(DpdpConsent).where(
            DpdpConsent.user_id == uuid.UUID(user_id),
            DpdpConsent.consent_type == "interview_voice_recording",
            DpdpConsent.purpose == "interview",
            DpdpConsent.granted.is_(True),
            DpdpConsent.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

    assert len(rows) == 1, (
        f"Expected exactly 1 active consent row, found {len(rows)} — "
        "concurrent insert was not deduplicated"
    )


@pytest.mark.asyncio
async def test_unique_index_present(client: AsyncClient) -> None:
    """The partial unique index ix_dpdp_consent_active_unique must exist in pg_indexes.

    Verifies that migration 20260528_0001 was applied. This test acts as a
    canary: if the migration is missing, S4-009 race safety is entirely absent
    and the security-auditor HIGH-1 finding is not resolved.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'dpdp_consent_ledger' "
                "  AND indexname = 'ix_dpdp_consent_active_unique'"
            )
        )
        row = result.scalar_one_or_none()

    assert row is not None, (
        "Index 'ix_dpdp_consent_active_unique' not found in pg_indexes. "
        "Run 'alembic upgrade head' to apply migration 20260528_0001."
    )


# ---------------------------------------------------------------------------
# S4-010 — DELETE /consent revocation endpoint (DPDP §11)
# ---------------------------------------------------------------------------

_CONSENT_DELETE_URL = "/consent"


@pytest.mark.asyncio
async def test_delete_consent_active_returns_200_and_sets_revoked_at(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """POST voice consent → DELETE → 200; response has items list; DB row revoked_at set.

    Verifies:
      - HTTP 200 response code.
      - Response body shape: {revoked: true, items: [{consent_type, consent_id, revoked_at}]}.
      - The items list contains the voice-recording row.
      - DB row's revoked_at is now non-null.
      - The revoked_at in the response matches (within seconds) the DB value.
    """
    token = auth_data["access_token"]

    # Grant voice consent only.
    post_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert post_resp.status_code == 201, post_resp.text
    consent_id = post_resp.json()["consent_id"]

    # Revoke consent.
    del_resp = await client.delete(
        _CONSENT_DELETE_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()

    # Response shape.
    assert body["revoked"] is True
    assert "items" in body, "Response must have 'items' list"
    items = body["items"]
    assert isinstance(items, list), "'items' must be a list"
    assert len(items) >= 1, "At least the voice consent must be in items"

    # Find the voice item.
    voice_items = [i for i in items if i["consent_type"] == "interview_voice_recording"]
    assert len(voice_items) == 1, "Exactly one voice-recording item expected"
    voice_item = voice_items[0]
    assert voice_item["consent_id"] == consent_id
    assert voice_item["revoked_at"]  # non-empty ISO timestamp

    # DB must reflect the revocation.
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(DpdpConsent).where(DpdpConsent.id == uuid.UUID(consent_id))
        result = await db.execute(stmt)
        row = result.scalar_one()

    assert row.revoked_at is not None, "revoked_at must be set after DELETE /consent"
    # The ISO string in the response must correspond to the DB value (ignoring TZ
    # format variations — compare seconds-level truncation).
    assert voice_item["revoked_at"].startswith(
        row.revoked_at.strftime("%Y-%m-%dT%H:%M:%S")[:16]  # YYYY-MM-DDTHH:MM
    ), (
        f"revoked_at in response '{voice_item['revoked_at']}' "
        f"does not match DB value '{row.revoked_at.isoformat()}'"
    )


@pytest.mark.asyncio
async def test_delete_consent_no_active_row_returns_404(
    client: AsyncClient, auth_token: str
) -> None:
    """DELETE /consent with no prior POST → 404 'No active consent to revoke'."""
    resp = await client.delete(
        _CONSENT_DELETE_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert "No active consent to revoke" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_consent_already_revoked_returns_404(
    client: AsyncClient, auth_token: str
) -> None:
    """POST → DELETE → DELETE: second DELETE returns 404.

    Once consent is revoked, the row is no longer 'active' (revoked_at IS NOT
    NULL), so _find_active_consent returns None. The second DELETE must return
    404 consistently with the "no active consent" case.
    """
    # Grant consent.
    post_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert post_resp.status_code == 201, post_resp.text

    # First revocation — must succeed.
    del1 = await client.delete(
        _CONSENT_DELETE_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert del1.status_code == 200, del1.text

    # Second revocation — must return 404 (nothing left to revoke).
    del2 = await client.delete(
        _CONSENT_DELETE_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert del2.status_code == 404, del2.text
    assert "No active consent to revoke" in del2.json()["detail"]


@pytest.mark.asyncio
async def test_revoked_consent_blocks_session_creation(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """POST consent → POST session (201) → DELETE consent → POST session (403).

    Cross-service integration test: verifies that data_gateway's DELETE /consent
    revocation is immediately respected by interview_core's consent gate
    (has_active_consent in consent_guard.py). Both services share the same
    Postgres DB and the consent guard re-reads the row on every session-create.

    Note: interview_core session endpoints live on a different service (port 8001
    in production). In the integration test environment we test data_gateway's
    GET /consent/status instead as a proxy for the gate — if revoked_at is set,
    status returns consented=false, which is what has_active_consent would return.
    This avoids an in-process cross-service import while still validating the
    contract that the revocation is reflected in the DB immediately.
    """
    token = auth_data["access_token"]

    # Step 1: grant consent.
    post_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert post_resp.status_code == 201, post_resp.text

    # Step 2: confirm active consent via status endpoint.
    status_before = await client.get(
        _CONSENT_STATUS_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status_before.status_code == 200
    assert status_before.json()["consented"] is True

    # Step 3: revoke consent.
    del_resp = await client.delete(
        _CONSENT_DELETE_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200, del_resp.text

    # Step 4: consent gate now reflects revocation — status returns false.
    # This is the same query has_active_consent uses internally.
    status_after = await client.get(
        _CONSENT_STATUS_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status_after.status_code == 200
    assert status_after.json()["consented"] is False, (
        "After DELETE /consent, GET /consent/status must return consented=false. "
        "has_active_consent (interview_core consent gate) uses the same predicate "
        "and would therefore also return False — blocking session creation with 403."
    )


# ---------------------------------------------------------------------------
# S4-012 — Trusted proxy count + safe X-Forwarded-For handling
# ---------------------------------------------------------------------------
# These tests exercise _extract_client_ip directly (no DB round-trip needed)
# so they run fast and deterministically regardless of the test transport.
# ---------------------------------------------------------------------------


def _make_request(
    xff: str | None = None,
    client_host: str = "testclient",
) -> MagicMock:
    """Build a minimal mock starlette Request with controllable headers and client.

    Args:
        xff: Value for the ``X-Forwarded-For`` header. ``None`` means the
             header is absent (mimics a direct connection with no proxy).
        client_host: The ``request.client.host`` value (the direct TCP peer).
    """
    mock_client = MagicMock()
    mock_client.host = client_host

    mock_headers: dict[str, str] = {}
    if xff is not None:
        mock_headers["X-Forwarded-For"] = xff

    mock_request = MagicMock()
    mock_request.client = mock_client
    mock_request.headers.get = lambda key, default="": mock_headers.get(key, default)

    return mock_request


def test_extract_client_ip_no_trusted_proxies_ignores_xff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_proxy_count=0: a spoofed X-Forwarded-For header is entirely ignored.

    S4-012 — the default (zero-proxy) configuration. Any client can set an
    arbitrary X-Forwarded-For header; when trusted_proxy_count=0 the helper
    MUST discard it and use ``request.client.host`` directly.  This ensures
    the consent ledger records the correct TCP peer, not an attacker-supplied
    spoofed IP.
    """
    monkeypatch.setattr(settings, "trusted_proxy_count", 0)

    request = _make_request(xff="1.2.3.4", client_host="testclient")
    ip = _extract_client_ip(request)

    assert ip == "testclient", (
        f"With trusted_proxy_count=0, expected 'testclient' (direct host) "
        f"but got '{ip}' — spoofed XFF was not ignored"
    )

    # Additionally verify the hash contract: ip_hash must match testclient, not 1.2.3.4.
    ip_hash = _hash_value(ip)
    spoofed_hash = _hash_value("1.2.3.4")
    assert ip_hash != spoofed_hash, (
        "ip_hash should differ from the spoofed IP hash — audit integrity broken"
    )


def test_extract_client_ip_one_trusted_proxy_uses_correct_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_proxy_count=1 + XFF='1.2.3.4' → extracts '1.2.3.4'.

    S4-012 — Oracle/Caddy production topology: a single Caddy reverse proxy
    sits in front of the app. Caddy sets X-Forwarded-For to the client IP it
    observed (a single entry) and strips any client-supplied XFF. With
    trusted_proxy_count=1 the real client is the rightmost (only) entry; the
    direct TCP peer (10.0.0.1, Caddy's container IP) is NOT the client.
    """
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)

    request = _make_request(
        xff="1.2.3.4",
        client_host="10.0.0.1",  # direct TCP peer is the Caddy container
    )
    ip = _extract_client_ip(request)

    assert ip == "1.2.3.4", (
        f"Expected '1.2.3.4' (the client entry Caddy set) but got '{ip}'"
    )


def test_extract_client_ip_two_trusted_proxies_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_proxy_count=2 + XFF='1.2.3.4, <cdn>' → extracts '1.2.3.4'.

    S4-012 — future topology with a CDN (e.g. Cloudflare) in front of Caddy.
    The CDN sets XFF to the client, Caddy appends the CDN edge IP → two trusted
    hops. The real client is the entry two positions from the right.
    """
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)

    request = _make_request(
        xff="1.2.3.4, 203.0.113.7",  # client, then CDN edge appended by Caddy
        client_host="10.0.0.1",
    )
    ip = _extract_client_ip(request)

    assert ip == "1.2.3.4", (
        f"Expected '1.2.3.4' (entry 2 hops from right) but got '{ip}'"
    )


def test_extract_client_ip_attacker_spoof_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_proxy_count=1: attacker-prepended XFF entry is not trusted.

    S4-012 — the core security scenario. Because the real client IP is read by
    counting ``trusted_proxy_count`` entries from the RIGHT (the trusted end),
    any number of attacker-prepended entries on the LEFT are ignored. Even if a
    spoofed value somehow survived Caddy's stripping as:
        X-Forwarded-For: attacker, real-client
    trusted_proxy_count=1 reads the rightmost entry ('real-client'); 'attacker'
    at index 0 is outside the trusted window and is never used.
    """
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)

    request = _make_request(
        xff="attacker, real-client",
        client_host="10.0.0.1",
    )
    ip = _extract_client_ip(request)

    assert ip == "real-client", (
        f"Expected 'real-client' (rightmost, trusted entry) but got '{ip}' — "
        "attacker-prepended XFF entry leaked through the trusted-proxy gate"
    )

    # Verify the attacker's spoofed IP is not the recorded value.
    assert ip != "attacker", "Attacker-supplied XFF entry must never be trusted"


def test_extract_client_ip_missing_xff_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_proxy_count=1 + no XFF header → falls back to request.client.host.

    S4-012 — graceful degradation when the request arrives without passing
    through the expected proxy chain (e.g. health-check from a monitoring
    tool hitting the app port directly, or a misconfigured proxy).  The direct
    TCP peer address is still a useful audit value; we MUST NOT crash.
    """
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)

    # No xff argument → header absent in mock_headers.
    request = _make_request(client_host="192.168.1.50")
    ip = _extract_client_ip(request)

    assert ip == "192.168.1.50", (
        f"Expected fallback to request.client.host '192.168.1.50' "
        f"when XFF is absent, but got '{ip}'"
    )


# ---------------------------------------------------------------------------
# Phase A — video_capture consent type (candidate webcam / proctoring)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_consent_video_capture_returns_201(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """POST with consent_type='video_capture' writes a row of that type."""
    token = auth_data["access_token"]
    user_id = auth_data["user_id"]

    resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "video_capture"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    session_factory = get_session_factory()
    async with session_factory() as db:
        row = (
            await db.execute(
                select(DpdpConsent).where(
                    DpdpConsent.id == uuid.UUID(resp.json()["consent_id"])
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert str(row.user_id) == user_id
    assert row.consent_type == "video_capture"
    assert row.purpose == "interview"


@pytest.mark.asyncio
async def test_voice_and_video_consent_are_independent(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """A user can hold both voice and video consents; status reflects each separately."""
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Grant only video_capture.
    await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "video_capture"},
        headers=headers,
    )

    # video_capture status → consented; voice status (default) → NOT consented.
    video_status = await client.get(
        f"{_CONSENT_STATUS_URL}?consent_type=video_capture", headers=headers
    )
    voice_status = await client.get(_CONSENT_STATUS_URL, headers=headers)

    assert video_status.json()["consented"] is True
    assert voice_status.json()["consented"] is False


@pytest.mark.asyncio
async def test_post_consent_invalid_type_returns_400(
    client: AsyncClient, auth_token: str
) -> None:
    """An unknown consent_type must be rejected with 400."""
    resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "retina_scan"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_get_status_invalid_type_returns_400(
    client: AsyncClient, auth_token: str
) -> None:
    """GET /consent/status with an unknown consent_type must be rejected with 400."""
    resp = await client.get(
        f"{_CONSENT_STATUS_URL}?consent_type=bogus",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Video-capture full withdrawal (DPDP §11 — both consent types revoked)
# ---------------------------------------------------------------------------
# These tests verify that DELETE /consent revokes ALL active consent types,
# not just the original interview_voice_recording. A candidate must be able
# to fully withdraw — including biometric (webcam / proctoring) consent —
# in a single call. If these tests fail it means the revoke endpoint is
# still only revoking voice consent and leaving video_capture active, which
# violates DPDP §11's requirement that withdrawal is unconditional.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_revokes_both_voice_and_video_when_both_active(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """POST voice + video consents → DELETE → both rows revoked atomically.

    The items list in the response must contain one entry per consent type,
    and both DB rows must have revoked_at set. This is the core DPDP §11
    full-withdrawal test — if this test is reverted (or the fix is removed),
    the video_capture row would remain active while only voice was revoked.
    """
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Grant both consent types.
    voice_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "interview_voice_recording"},
        headers=headers,
    )
    assert voice_resp.status_code == 201, voice_resp.text
    voice_id = voice_resp.json()["consent_id"]

    video_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "video_capture"},
        headers=headers,
    )
    assert video_resp.status_code == 201, video_resp.text
    video_id = video_resp.json()["consent_id"]

    # Revoke all consents.
    del_resp = await client.delete(_CONSENT_DELETE_URL, headers=headers)
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()

    assert body["revoked"] is True
    items = body["items"]
    assert isinstance(items, list)

    # Both consent types must appear in the items list.
    item_types = {i["consent_type"] for i in items}
    assert "interview_voice_recording" in item_types, (
        "voice consent type missing from revocation items — DELETE /consent did NOT "
        "revoke interview_voice_recording"
    )
    assert "video_capture" in item_types, (
        "video_capture consent type missing from revocation items — DELETE /consent "
        "did NOT revoke video_capture. This is the bug this test guards against."
    )

    # The consent_ids in the response must match what was granted.
    item_by_type = {i["consent_type"]: i for i in items}
    assert item_by_type["interview_voice_recording"]["consent_id"] == voice_id
    assert item_by_type["video_capture"]["consent_id"] == video_id

    # Both DB rows must have revoked_at set.
    session_factory = get_session_factory()
    async with session_factory() as db:
        voice_row = (
            await db.execute(select(DpdpConsent).where(DpdpConsent.id == uuid.UUID(voice_id)))
        ).scalar_one()
        video_row = (
            await db.execute(select(DpdpConsent).where(DpdpConsent.id == uuid.UUID(video_id)))
        ).scalar_one()

    assert voice_row.revoked_at is not None, (
        "voice_recording DB row must have revoked_at set after DELETE /consent"
    )
    assert video_row.revoked_at is not None, (
        "video_capture DB row must have revoked_at set after DELETE /consent — "
        "this is the regression this fix addresses"
    )


@pytest.mark.asyncio
async def test_delete_with_only_voice_active_revokes_voice_only(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """Only voice consent granted → DELETE → only voice in items; no error for missing video.

    Proves that the revoke loop handles the partial-grant case gracefully —
    the absence of a video_capture row must not cause a 500 or a 404.
    """
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Grant only voice consent.
    voice_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1},
        headers=headers,
    )
    assert voice_resp.status_code == 201, voice_resp.text

    del_resp = await client.delete(_CONSENT_DELETE_URL, headers=headers)
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()

    assert body["revoked"] is True
    item_types = {i["consent_type"] for i in body["items"]}
    assert "interview_voice_recording" in item_types
    # video_capture was never granted — it must NOT appear in items
    assert "video_capture" not in item_types, (
        "video_capture must not appear in revocation items when it was never granted"
    )


@pytest.mark.asyncio
async def test_delete_with_only_video_active_revokes_video_only(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """Only video_capture consent granted → DELETE → only video in items.

    Guards the case where a candidate consented to webcam proctoring but
    never consented to voice recording (e.g. written-exam only flow).
    DELETE /consent must still honour their withdrawal request.
    """
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Grant only video consent.
    video_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "video_capture"},
        headers=headers,
    )
    assert video_resp.status_code == 201, video_resp.text
    video_id = video_resp.json()["consent_id"]

    del_resp = await client.delete(_CONSENT_DELETE_URL, headers=headers)
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()

    assert body["revoked"] is True
    item_types = {i["consent_type"] for i in body["items"]}
    assert "video_capture" in item_types, (
        "video_capture must be in revocation items when only video was active"
    )
    assert "interview_voice_recording" not in item_types

    # DB row must be revoked.
    session_factory = get_session_factory()
    async with session_factory() as db:
        video_row = (
            await db.execute(select(DpdpConsent).where(DpdpConsent.id == uuid.UUID(video_id)))
        ).scalar_one()
    assert video_row.revoked_at is not None


@pytest.mark.asyncio
async def test_after_full_withdrawal_status_is_false_for_both_types(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """POST voice+video → DELETE → GET status for both types returns consented=false.

    Verifies the end-to-end DPDP withdrawal: after a single DELETE call,
    the consent gate (same predicate used by has_active_consent in interview_core)
    reports no active consent for either processing type.
    """
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Grant both.
    await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "interview_voice_recording"},
        headers=headers,
    )
    await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "video_capture"},
        headers=headers,
    )

    # Full withdrawal.
    del_resp = await client.delete(_CONSENT_DELETE_URL, headers=headers)
    assert del_resp.status_code == 200, del_resp.text

    # Both status checks must reflect withdrawal.
    voice_status = await client.get(_CONSENT_STATUS_URL, headers=headers)
    video_status = await client.get(
        f"{_CONSENT_STATUS_URL}?consent_type=video_capture", headers=headers
    )

    assert voice_status.json()["consented"] is False, (
        "voice consent must show consented=false after full withdrawal"
    )
    assert video_status.json()["consented"] is False, (
        "video_capture consent must show consented=false after full withdrawal — "
        "this is the regression this fix addresses"
    )


@pytest.mark.asyncio
async def test_video_capture_revocation_blocks_future_interview(
    client: AsyncClient, auth_data: dict[str, str]
) -> None:
    """POST voice+video → DELETE → video consent status is immediately false.

    The consent guard in interview_core re-reads the row on every session
    create. This test confirms the DB state is consistent immediately after
    the DELETE call — no async lag or caching can leave the guard stale.
    """
    token = auth_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Grant video consent.
    video_resp = await client.post(
        _CONSENT_URL,
        json={"purpose": "interview", "version": 1, "consent_type": "video_capture"},
        headers=headers,
    )
    assert video_resp.status_code == 201, video_resp.text

    # Verify it's active.
    status_before = await client.get(
        f"{_CONSENT_STATUS_URL}?consent_type=video_capture", headers=headers
    )
    assert status_before.json()["consented"] is True

    # Full withdrawal (via DELETE /consent — covers all types).
    del_resp = await client.delete(_CONSENT_DELETE_URL, headers=headers)
    assert del_resp.status_code == 200, del_resp.text

    # video_capture consent must be immediately inactive.
    status_after = await client.get(
        f"{_CONSENT_STATUS_URL}?consent_type=video_capture", headers=headers
    )
    assert status_after.json()["consented"] is False, (
        "video_capture consent must be immediately inactive after DELETE /consent. "
        "The consent guard in interview_core uses the same DB predicate and would "
        "therefore also return False — blocking webcam-proctored sessions."
    )
