"""Trusted-proxy-aware client IP extraction — S4-012.

Problem:
    When the service runs behind one or more reverse proxies (Vercel, Railway,
    nginx, etc.), ``request.client.host`` returns the proxy's IP, NOT the real
    client IP.  Rate-limiters and audit logs that use the proxy IP are trivially
    bypassed — every request appears to come from the same trusted proxy.

Problem with the naive fix (trust all X-Forwarded-For):
    X-Forwarded-For is a user-controlled header.  An attacker can prepend
    arbitrary IPs:

        curl -H "X-Forwarded-For: 1.1.1.1" https://service.example.com

    If the service blindly takes the first (leftmost) XFF value, the attacker
    controls the "real client IP" seen by rate-limiters and audit logs.

Correct fix (right-anchored extraction):
    Each proxy appends its view of the upstream IP to XFF.  The LAST entry is
    always written by the rightmost (most trusted) proxy — the one that
    terminates TLS from the internet.  So:

        X-Forwarded-For: <client>, <proxy1>, ..., <proxyN>

    For ``trusted_proxy_count = N``, take the IP at position
    ``len(parts) - trusted_proxy_count`` (0-indexed from the left).

    Index semantics:
      ``parts[len(parts) - trusted_proxy_count]``
      - With count=1 and XFF="<client>, <proxy>":
          idx = 2-1 = 1 → parts[1] = "<proxy_recorded_upstream>" = client IP.
          Any attacker-prepended entries at index 0 are ignored.
      - With count=2 and XFF="<client>, <p1>, <p2>":
          idx = 3-2 = 1 → parts[1] = what P2 (outermost trusted) saw = client IP.
      - Entries to the LEFT of ``idx`` are attacker-controlled and never returned.

    If ``trusted_proxy_count = 0`` (no proxies in dev), skip XFF entirely and
    use ``request.client.host`` directly.

    If the header has fewer entries than ``trusted_proxy_count`` (malformed or
    short chain), take the leftmost entry as a best-effort fallback.

Edge-case notes:
    - IPv6 addresses contain colons but no comma, so ``,``-splitting is safe.
    - Whitespace around each entry is stripped.
    - An empty string after splitting produces an empty list; we fall back to
      ``request.client.host`` in that case.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request


def get_client_ip(request: Request, trusted_proxy_count: int) -> str:
    """Extract the real client IP respecting ``trusted_proxy_count``.

    Args:
        request: The incoming FastAPI / Starlette ``Request`` object.
        trusted_proxy_count: Number of trusted reverse proxies.  Pass
            ``settings.trusted_proxy_count`` at call sites.
            - ``0``: no proxies; use ``request.client.host`` directly.
            - ``1``: one proxy (Railway/Vercel default).  Returns
              ``parts[len(parts) - 1]``, i.e. what the trusted proxy wrote
              about the upstream it received from.  Attacker-prepended
              entries to the left are all ignored.
            - ``N``: returns ``parts[len(parts) - N]``.  The rightmost
              N-1 entries are trusted inner-proxy hops; the entry at
              index ``len - N`` is what the outermost trusted proxy wrote
              about its upstream (the real client or first untrusted hop).

    Returns:
        The extracted IP string, or ``"unknown"`` when no IP is available.
    """
    if trusted_proxy_count == 0:
        # No proxy in front — request.client.host is authoritative.
        return _direct_host(request)

    xff: str = request.headers.get("x-forwarded-for", "").strip()
    if not xff:
        # Header absent — fall back to direct host (dev / non-proxied path).
        return _direct_host(request)

    parts: list[str] = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts:
        return _direct_host(request)

    # Right-anchored: the real client IP is at index -(trusted_proxy_count)
    # from the right, i.e. parts[len(parts) - trusted_proxy_count].
    idx: int = len(parts) - trusted_proxy_count
    if idx < 0:
        # Header SHORTER than the configured trust count — the request did not
        # traverse the proxy chain we expect. Fall back to the socket peer.
        #
        # This used to clamp to parts[0], which is the worst possible choice:
        # every entry left of the right-anchored index is attacker-PREPENDED,
        # so index 0 is the one value in the header the client fully controls.
        # Anyone could pick their own IP just by sending a short XFF.
        return _direct_host(request)

    return _validated_ip(parts[idx], fallback=_direct_host(request))


def _validated_ip(candidate: str, *, fallback: str) -> str:
    """Return *candidate* only if it parses as an IP address, else *fallback*.

    Fail closed: the selected XFF entry is only trustworthy while the
    deployment really has exactly ``trusted_proxy_count`` proxies in front of
    it. If that ever stops holding, this returns raw header text — and the
    value keys rate-limit buckets, so arbitrary text means a fresh bucket per
    request.
    """
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return fallback
    return candidate


def _direct_host(request: Request) -> str:
    """Return ``request.client.host`` or ``"unknown"`` when client is None."""
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"
