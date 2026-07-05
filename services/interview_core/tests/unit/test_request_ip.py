"""Unit tests for S4-012 — get_client_ip() trusted-proxy extraction.

These tests are fully synchronous and have no external dependencies.
They exercise the right-anchored XFF extraction logic in app/utils/request_ip.py.

Algorithm:
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    idx   = max(0, len(parts) - trusted_proxy_count)
    return parts[idx]

How XFF is built by proxies (RFC 7239 / de-facto standard):
    Each proxy APPENDS the IP of the upstream peer it received the request from.
    So for a chain of  Client(C) → Proxy₁(P1) → Proxy₂(P2) → Service:
        P1 writes C's IP to XFF.
        P2 appends P1's IP.
        Service sees: X-Forwarded-For: C, P1

    trusted_proxy_count=2 (both proxies are trusted):
        idx = len([C, P1]) - 2 = 0 → parts[0] = C  ← real client

    trusted_proxy_count=1 (only P2, the outermost, is trusted):
        idx = len([C, P1]) - 1 = 1 → parts[1] = P1

    With a spoofing attempt — attacker sends XFF: "bad" before the chain:
        Service sees: X-Forwarded-For: bad, C, P1  (P2 appended C; P1 appended P2)
        count=2: idx = 3 - 2 = 1 → parts[1] = C  ← still the real client ✓
        The attacker-controlled "bad" is at index 0 and is never returned.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Request

from app.utils.request_ip import get_client_ip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    xff: str | None = None,
    client_host: str | None = "127.0.0.1",
) -> Request:
    """Build a minimal mock Request exercising only get_client_ip() attributes.

    ``xff=None`` means the header is absent (returns empty default string).
    ``client_host=None`` means ``request.client`` is ``None``.
    """
    mock_request = MagicMock(spec=Request)

    def _headers_get(key: str, default: str = "") -> str:
        if key.lower() == "x-forwarded-for":
            return xff if xff is not None else default
        return default

    mock_request.headers.get = MagicMock(side_effect=_headers_get)

    if client_host is None:
        mock_request.client = None
    else:
        mock_client = MagicMock()
        mock_client.host = client_host
        mock_request.client = mock_client

    return mock_request


# ---------------------------------------------------------------------------
# Core extraction logic
# ---------------------------------------------------------------------------


def test_single_proxy_two_entry_xff() -> None:
    """count=1, XFF='1.2.3.4, 10.0.0.1' → '10.0.0.1'.

    This represents a 2-hop chain or a spoofed 1-hop chain:
        Client(?) → attacker-prepended 1.2.3.4 → Railway(10.0.0.1) → Service
        Railway appends what IT received from: "10.0.0.1" becomes the last entry.
        count=1: idx = 2-1 = 1 → parts[1] = "10.0.0.1"
        (Railway's view of the upstream — not the attacker-prepended "1.2.3.4")
    """
    request = _make_request(xff="1.2.3.4, 10.0.0.1", client_host="10.0.0.1")
    result = get_client_ip(request, trusted_proxy_count=1)
    # idx = max(0, 2 - 1) = 1 → parts[1] = "10.0.0.1"
    assert result == "10.0.0.1"


def test_single_proxy_single_entry() -> None:
    """count=1, XFF='203.0.113.42' (1 entry) → '203.0.113.42'.

    Normal case: client → single proxy → service.
    The proxy wrote the client's IP. No prior XFF. idx = 1-1 = 0 → first entry.
    """
    request = _make_request(xff="203.0.113.42")
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == "203.0.113.42"


def test_two_proxies_three_entry_xff() -> None:
    """count=2, XFF='1.2.3.4, 10.0.0.1, 10.0.0.2' → '10.0.0.1'.

    Chain: Client(1.2.3.4) → Proxy₁(10.0.0.1) → Proxy₂(10.0.0.2) → Service.
    Proxy₁ appended 1.2.3.4. Proxy₂ appended 10.0.0.1.
    With count=2 (both proxies trusted):
        idx = 3 - 2 = 1 → parts[1] = "10.0.0.1"
    This is the IP Proxy₁ forwarded — which is the IP the outermost trusted
    proxy received from (the real client as seen at the network boundary).
    """
    request = _make_request(xff="1.2.3.4, 10.0.0.1, 10.0.0.2")
    result = get_client_ip(request, trusted_proxy_count=2)
    assert result == "10.0.0.1"


def test_count_zero_returns_direct_host() -> None:
    """count=0 → XFF is ignored; returns request.client.host directly.

    Dev environment: no proxy in front of the service. request.client.host
    is authoritative. XFF may be present but must not be trusted at all.
    """
    request = _make_request(xff="1.2.3.4, 10.0.0.1", client_host="192.168.0.1")
    result = get_client_ip(request, trusted_proxy_count=0)
    assert result == "192.168.0.1"


def test_no_xff_header_falls_back_to_client_host() -> None:
    """No X-Forwarded-For header present → fall back to request.client.host."""
    request = _make_request(xff=None, client_host="10.10.10.10")
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == "10.10.10.10"


# ---------------------------------------------------------------------------
# Spoofing neutralisation
# ---------------------------------------------------------------------------


def test_spoof_extra_ip_neutralised() -> None:
    """Attacker-prepended XFF entry is ignored by right-anchored extraction.

    Normal (no spoofing), single proxy:
        Client → Railway(10.0.0.1) → Service
        XFF arrives: "1.2.3.4"  (Railway wrote client IP)
        count=1, idx=0 → "1.2.3.4" ✓

    With spoofing: attacker sends header "X-Forwarded-For: 9.9.9.9".
        Railway appends 1.2.3.4 (what it received from).
        XFF arrives: "9.9.9.9, 1.2.3.4"
        count=1, idx=2-1=1 → "1.2.3.4" ← Railway's upstream; NOT "9.9.9.9" ✓
    """
    request = _make_request(xff="9.9.9.9, 1.2.3.4")
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == "1.2.3.4"
    assert result != "9.9.9.9", "Attacker-controlled value must never be returned."


def test_multiple_spoofed_entries_neutralised() -> None:
    """Multiple attacker-prepended entries are all ignored.

    Attacker sends: "X-Forwarded-For: a.b.c.d, e.f.g.h".
    Proxy appends real client: "1.2.3.4".
    Service sees: "a.b.c.d, e.f.g.h, 1.2.3.4".
    count=1, idx=3-1=2 → "1.2.3.4" ✓
    """
    request = _make_request(xff="a.b.c.d, e.f.g.h, 1.2.3.4")
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == "1.2.3.4"
    assert result != "a.b.c.d"
    assert result != "e.f.g.h"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_xff_shorter_than_proxy_count_returns_leftmost() -> None:
    """XFF has fewer entries than trusted_proxy_count → idx clamped to 0.

    Only 1 XFF entry but trusted_proxy_count=2: idx = max(0, 1-2) = max(0,-1) = 0
    → returns parts[0] as best-effort.
    """
    request = _make_request(xff="1.2.3.4", client_host="10.0.0.1")
    result = get_client_ip(request, trusted_proxy_count=2)
    assert result == "1.2.3.4"


def test_empty_xff_header_falls_back_to_client_host() -> None:
    """XFF header is present but empty string → fall back to request.client.host."""
    request = _make_request(xff="", client_host="172.16.0.1")
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == "172.16.0.1"


def test_whitespace_around_ips_stripped() -> None:
    """XFF entries with surrounding whitespace are normalised before extraction."""
    request = _make_request(xff="  1.2.3.4  ,  10.0.0.1  ")
    result = get_client_ip(request, trusted_proxy_count=1)
    # idx = 2-1 = 1 → "10.0.0.1" (stripped)
    assert result == "10.0.0.1"


def test_none_client_returns_unknown() -> None:
    """request.client is None (some test-harness configs) → returns 'unknown'."""
    request = _make_request(xff=None, client_host=None)
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == "unknown"


def test_ipv6_address_extracted_correctly() -> None:
    """IPv6 addresses contain colons but no commas — comma-split is safe.

    Chain: Client(2001:db8::1) → Proxy(::ffff:10.0.0.1) → Service.
    With count=1, idx=2-1=1 → "::ffff:10.0.0.1" (what the proxy saw).
    """
    ipv6_client = "2001:db8::1"
    ipv6_proxy_upstream = "::ffff:10.0.0.1"
    request = _make_request(xff=f"{ipv6_client}, {ipv6_proxy_upstream}")
    result = get_client_ip(request, trusted_proxy_count=1)
    assert result == ipv6_proxy_upstream


def test_xff_with_exactly_count_entries() -> None:
    """XFF has exactly trusted_proxy_count entries → idx=0 → leftmost entry."""
    request = _make_request(xff="192.0.2.1, 10.0.0.2")
    result = get_client_ip(request, trusted_proxy_count=2)
    # idx = max(0, 2-2) = 0 → "192.0.2.1"
    assert result == "192.0.2.1"


def test_config_rejects_negative_trusted_proxy_count() -> None:
    """Pydantic Field(ge=0) rejects negative values at Settings parse time.

    A negative trusted_proxy_count would cause idx = len(parts) - negative =
    len(parts) + abs(negative), which is always out-of-bounds → IndexError on
    every proxied request.  The ge=0 constraint catches this at startup.
    """
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Settings(trusted_proxy_count=-1)
