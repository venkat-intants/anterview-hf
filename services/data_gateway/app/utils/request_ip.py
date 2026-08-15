"""Trusted-proxy-aware client IP extraction — S4-012.

Mirrors ``interview_core/app/utils/request_ip.py``: same right-anchored
algorithm, same fail-closed IP validation, same "unknown" sentinel handling.
The two differences are deliberate and are what this service needs on top:

  * ``extract_client_ip``/``extract_user_agent`` bind ``settings`` so the four
    call sites (consent ledger, rate limiter, HR pipeline audit, Google SSO)
    stay one-argument;
  * the degraded-topology counters below, because in data_gateway the extracted
    value keys the login rate-limit bucket and the DPDP consent-ledger IP hash.

This module lives outside ``app/routers`` on purpose. It used to be private to
``app/routers/consent.py``, which meant ``app/rate_limit.py`` — infrastructure
that runs before a route is chosen — imported a route module to do its job.

Problem:
    When the service runs behind one or more reverse proxies (Caddy, Vercel,
    Railway, nginx…), ``request.client.host`` is the proxy's IP, NOT the real
    client's. Rate-limiters and audit logs keyed on the proxy IP are trivially
    bypassed — every request looks like it came from the same trusted proxy.

Problem with the naive fix (trust the leftmost X-Forwarded-For entry):
    X-Forwarded-For is user-controlled. ``curl -H "X-Forwarded-For: 1.2.3.4"``
    then dictates the "real client IP" the rate-limiter and the consent ledger
    record.

Correct fix (right-anchored extraction):
    Each proxy APPENDS the address it received the request from, so the last
    entry is written by the rightmost (most trusted) proxy. With N trusted
    proxies the real client is at ``hops[len(hops) - N]``; everything to the
    LEFT of that index is attacker-prependable and is never returned.

      X-Forwarded-For: <client>, <proxy1>, ..., <proxyN>

    ``trusted_proxy_count == 0`` (dev, no proxy) skips X-Forwarded-For entirely.

Edge-case notes:
    - IPv6 addresses contain colons but no comma, so ``,``-splitting is safe.
    - Whitespace around each entry is stripped.
    - An empty header after splitting yields an empty list; the socket peer is
      used in that case.

Raw IP values are NEVER logged — callers hash them before storage.
"""

from __future__ import annotations

import ipaddress

import structlog
from fastapi import Request
from prometheus_client import Counter, Gauge

from app.config import settings

log = structlog.get_logger(__name__)

# Two security controls in this service fail open by design — the hop arithmetic
# below and the Redis rate limiter. Failing open is the right call for both (a
# blip must not lock every user out), but a control that switches itself off
# without saying so is indistinguishable from one that is working. These
# counters make the degraded state visible on /metrics so it can be alerted on.
_proxy_hop_underflow = Counter(
    "client_ip_proxy_hop_underflow_total",
    "X-Forwarded-For had fewer hops than TRUSTED_PROXY_COUNT, so the socket peer "
    "was used instead. Sustained non-zero means per-IP rate limiting is degraded "
    "to a single global bucket.",
)

# The other direction of the same misconfiguration. Unlike the underflow, an
# excess hop count is AMBIGUOUS from a single request and is NOT an error:
#
#   * benign — a client prepended entries of its own before the packet reached
#     our edge. The right-anchored index ignores them, which is exactly the
#     attack this module was written to defeat; or
#   * a real proxy was added in front (CDN, WAF) and TRUSTED_PROXY_COUNT was not
#     raised to match. Then the entry we return is that new proxy's address, and
#     every client behind it collapses into one rate-limit bucket — the same
#     silent degradation as the underflow, but ``le=4`` on the setting cannot
#     catch it and the underflow counter never fires.
#
# One request cannot distinguish the two, so this is a signal to CORRELATE, not
# an alert to page on: a stable low ratio of excess:total is ordinary internet
# noise, while a step change at a deploy is the too-low case. Logged at debug
# for the same reason — a per-request warning here would be its own outage.
_proxy_hop_excess = Counter(
    "client_ip_proxy_hop_excess_total",
    "X-Forwarded-For carried MORE hops than TRUSTED_PROXY_COUNT. Expected at a "
    "low background rate (clients may prepend entries); a step change means a "
    "proxy was added in front and TRUSTED_PROXY_COUNT is now too low.",
)

# Module-level, not per-request: a misconfiguration fires this on every request,
# and an unthrottled WARNING per request is its own outage.
_underflow_warned = False


# ---------------------------------------------------------------------------
# DG-3 — the too-LOW direction of the same misconfiguration.
#
# The too-high direction has ``_proxy_hop_underflow`` above and an alert rule.
# The too-low direction had nothing, and it is the more likely mistake: deploy
# behind Caddy with TRUSTED_PROXY_COUNT left at its safe default of 0 and every
# client resolves to the edge's socket address, so per-IP rate limiting becomes
# one global bucket and every consent-ledger row carries the same IP hash. The
# control reports success and protects nothing.
#
# Why a per-request counter cannot solve it. A single request carrying MORE hops
# than configured is ambiguous — a client may legitimately have prepended
# entries of its own before the packet reached our edge, which is exactly the
# attack the right-anchored index defeats. So ``_proxy_hop_excess`` can only ever
# say "excess happens", never "the configuration is wrong", and a Counter is
# monotonic: it cannot express the statistic that DOES distinguish the two.
#
# That statistic is the MINIMUM hop count over many header-bearing requests.
# Proxies only ever APPEND, and clients only ever prepend, so the minimum
# observed over a sample IS the number of infrastructure hops — a misconfigured
# deployment shows min > configured on every single request, while ordinary
# client noise leaves plenty of requests at exactly the configured count. The
# sample deliberately EXCLUDES requests with no X-Forwarded-For at all
# (health probes and direct-to-container traffic), which would otherwise pin the
# minimum at 0 and hide the mismatch in any mixed topology.
#
# Two outputs, because the two audiences differ:
#   * one log event, emitted ONCE after _HOP_SAMPLE_SIZE header-bearing requests,
#     which is what an engineer reads after a deploy. Not a startup assertion:
#     at startup no request has been seen, so the real topology is unknowable —
#     the earliest honest moment is after the first N requests.
#   * two Gauges, so the mismatch is alertable without parsing logs:
#       client_ip_proxy_hops_min_observed > client_ip_trusted_proxy_count
#     Gauges rather than a Counter precisely because the value can go DOWN, which
#     is the whole point.
#
# The sample can also come out BELOW the configured count, which is the opposite
# misconfiguration and carries the opposite remediation — see the branch in
# _observe_hop_count. The log event carries a ``direction`` field so the two are
# distinguishable in a query without parsing prose.
# ---------------------------------------------------------------------------
_HOP_SAMPLE_SIZE = 50

_proxy_hops_min_observed = Gauge(
    "client_ip_proxy_hops_min_observed",
    "Smallest number of X-Forwarded-For hops seen on any request that carried "
    "the header. Clients can only ADD hops, so this is the real infrastructure "
    "hop count. Alert when it exceeds client_ip_trusted_proxy_count: the "
    "configured value is too low and per-IP rate limiting is one global bucket.",
)

_trusted_proxy_count_gauge = Gauge(
    "client_ip_trusted_proxy_count",
    "The configured TRUSTED_PROXY_COUNT, published so the observed minimum "
    "above can be compared against it in a single alert expression.",
)

# Sample state. Ints rather than a list: only the count and the running minimum
# are ever read, and keeping 50 integers to compute one of them is storage for
# its own sake.
_hop_sample_count = 0
_hop_min_observed: int | None = None
_hop_observation_reported = False


def _reset_hop_observation() -> None:
    """Clear the sampling state. For tests only — the process never resets."""
    global _hop_sample_count, _hop_min_observed, _hop_observation_reported
    _hop_sample_count = 0
    _hop_min_observed = None
    _hop_observation_reported = False


def _observe_hop_count(observed_hops: int, trusted_proxy_count: int) -> None:
    """Fold one request's hop count into the topology observation.

    Called for every request that carries a non-empty X-Forwarded-For, INCLUDING
    when ``trusted_proxy_count`` is 0 — that configuration ignores the header for
    IP extraction, but "0 configured, 1 real hop" is the single most likely form
    of this misconfiguration and skipping it would leave the fix blind to it.
    """
    global _hop_sample_count, _hop_min_observed, _hop_observation_reported

    _trusted_proxy_count_gauge.set(trusted_proxy_count)

    if _hop_min_observed is None or observed_hops < _hop_min_observed:
        _hop_min_observed = observed_hops
        _proxy_hops_min_observed.set(observed_hops)

    _hop_sample_count += 1
    if _hop_observation_reported or _hop_sample_count < _HOP_SAMPLE_SIZE:
        return

    _hop_observation_reported = True
    if _hop_min_observed == trusted_proxy_count:
        log.info(
            "consent.client_ip.proxy_hop_observation",
            configured_proxy_count=trusted_proxy_count,
            min_observed_hops=_hop_min_observed,
            samples=_hop_sample_count,
            detail="TRUSTED_PROXY_COUNT matches the observed proxy chain.",
        )
        return

    # The two directions are NOT the same finding and must not share a
    # remediation. ``min_observed >= real_hop_count`` always holds — proxies only
    # append, clients only prepend — so the inference "the minimum IS the real
    # hop count, set the config to it" is only sound when the minimum is ABOVE
    # the configured value. Below it, that same sentence would tell an operator
    # to lower TRUSTED_PROXY_COUNT to a number the sample cannot justify, and
    # ``real_index = len(hops) - trusted_proxy_count`` means a count set higher
    # than the true chain is the SPOOFABLE direction: a client that prepends
    # enough entries to reach the configured length has its own forged entry
    # selected verbatim. Lowering blind is therefore not a safe default action.
    if _hop_min_observed > trusted_proxy_count:
        log.warning(
            "consent.client_ip.proxy_hop_mismatch",
            direction="configured_too_low",
            configured_proxy_count=trusted_proxy_count,
            min_observed_hops=_hop_min_observed,
            samples=_hop_sample_count,
            detail=(
                "Every sampled X-Forwarded-For carried MORE hops than "
                "TRUSTED_PROXY_COUNT. Because proxies only append and clients "
                "only prepend, the minimum observed IS the real infrastructure "
                "hop count: set TRUSTED_PROXY_COUNT to it. Until then the "
                "extracted client IP is a proxy address, so per-IP rate limiting "
                "and the DPDP consent-ledger IP hash are degraded to a single "
                "bucket."
            ),
        )
        return

    log.warning(
        "consent.client_ip.proxy_hop_mismatch",
        direction="configured_too_high",
        configured_proxy_count=trusted_proxy_count,
        min_observed_hops=_hop_min_observed,
        samples=_hop_sample_count,
        detail=(
            "The shortest X-Forwarded-For sampled carried FEWER hops than "
            "TRUSTED_PROXY_COUNT, so those requests underflow and fall back to "
            "the socket peer: per-IP rate limiting and the DPDP consent-ledger IP "
            "hash are keyed on the edge address, not the client. Do NOT simply "
            "lower TRUSTED_PROXY_COUNT to min_observed_hops — clients can only "
            "ADD entries, so that number is an upper bound on the real chain and "
            "not a measurement of it. Check the deployment's actual proxy chain: "
            "while TRUSTED_PROXY_COUNT exceeds it, any header long enough to "
            "satisfy the count must contain client-prepended entries, and one of "
            "those is what gets returned as the client IP."
        ),
    )


def get_client_ip(request: Request, trusted_proxy_count: int) -> str:
    """Extract the real client IP respecting *trusted_proxy_count*.

    Args:
        request: The incoming FastAPI / Starlette ``Request``.
        trusted_proxy_count: Number of trusted reverse proxies in front of the
            app.
            - ``0``: no proxies; ``request.client.host`` is authoritative and
              X-Forwarded-For is ignored entirely.
            - ``1``: one proxy (our Caddy topology). Returns
              ``hops[len(hops) - 1]`` — what the trusted proxy recorded about
              its upstream. Attacker-prepended entries are all ignored.
            - ``N``: returns ``hops[len(hops) - N]``.

    Returns:
        A string that always parses as an IP address, or the socket peer's
        ``"unknown"`` normalised to the ``0.0.0.0`` sentinel by
        :func:`validated_ip` when nothing usable is available.
    """
    direct_host = _direct_host(request)

    xff: str = request.headers.get("X-Forwarded-For", "")
    hops: list[str] = [h.strip() for h in xff.split(",") if h.strip()]

    # Sampled BEFORE the trusted_proxy_count == 0 early return, on purpose: that
    # branch is itself the most common too-low misconfiguration (DG-3), and an
    # observation placed after it could never see the case it exists to catch.
    # Requests with no header contribute nothing — see _observe_hop_count.
    if hops:
        _observe_hop_count(len(hops), trusted_proxy_count)

    if trusted_proxy_count == 0:
        # No trusted proxies configured — ignore X-Forwarded-For entirely. A
        # client-supplied header could spoof any IP; the socket address is the
        # only safe option in this topology.
        return direct_host

    if not xff:
        # No header — the request did not pass through the expected proxy chain.
        # Tested on the RAW header, not on ``hops``: a header that is present but
        # yields no usable entries ("  ,  ") is a broken proxy chain, and it must
        # keep falling through to the underflow branch below so it is counted.
        return direct_host

    real_index: int = len(hops) - trusted_proxy_count

    if real_index < 0:
        # Fewer hops than expected (e.g. the proxy chain is not fully
        # established in staging). Fall back to the socket address rather than
        # picking a potentially attacker-controlled entry.
        #
        # Falling back is correct, but doing it silently is not: if
        # trusted_proxy_count is misconfigured this branch fires for EVERY
        # request, every client resolves to the same proxy address, and per-IP
        # rate limiting quietly becomes one global bucket. The control still
        # reports success while protecting nothing. Warn (once per process, so a
        # misconfiguration under load does not flood the log) and count it, so
        # the wrong topology announces itself instead of being discovered later.
        _proxy_hop_underflow.inc()
        global _underflow_warned
        if not _underflow_warned:
            _underflow_warned = True
            log.warning(
                "consent.client_ip.proxy_hop_underflow",
                configured_proxy_count=trusted_proxy_count,
                observed_hops=len(hops),
                detail=(
                    "X-Forwarded-For has fewer hops than TRUSTED_PROXY_COUNT; "
                    "falling back to the socket peer for every such request. "
                    "Per-IP rate limiting and consent-ledger IP hashing are "
                    "degraded until this matches the real topology."
                ),
            )
        return direct_host

    if real_index > 0:
        # More hops than configured — see the _proxy_hop_excess comment above for
        # why this is counted rather than warned or rejected.
        _proxy_hop_excess.inc()
        log.debug(
            "consent.client_ip.proxy_hop_excess",
            configured_proxy_count=trusted_proxy_count,
            observed_hops=len(hops),
        )

    return validated_ip(hops[real_index], fallback=direct_host)


def extract_client_ip(request: Request) -> str:
    """``get_client_ip`` bound to ``settings.trusted_proxy_count``.

    The app-facing entry point. Kept separate from the pure core above so the
    hop arithmetic can be exercised without patching global settings.
    """
    return get_client_ip(request, settings.trusted_proxy_count)


def extract_user_agent(request: Request) -> str:
    """Extract the User-Agent header, defaulting to the empty string."""
    return request.headers.get("User-Agent", "")


def validated_ip(candidate: str, *, fallback: str) -> str:
    """Return *candidate* only if it parses as an IP address.

    Fail closed on anything else. Two reasons this is not cosmetic:

    1. The XFF entry we select is only trustworthy while the deployment really
       does have exactly ``trusted_proxy_count`` proxies in front of it. If that
       assumption is ever wrong, this function returns raw attacker-chosen
       header text — and the value feeds the rate-limit bucket key
       (``rl:{bucket}:{ip}``), so arbitrary text means a fresh bucket per
       request against login, register and password reset.
    2. ``audit_log.ip_address`` is a Postgres INET column. A non-IP string
       raises InvalidTextRepresentation, which rolls back the whole
       transaction — so garbage here 500s an unrelated write instead of being
       quietly wrong.

    The caller's fallback is the direct socket peer, which is normally a real
    address. Note the literal "unknown" (used when ``request.client`` is None)
    deliberately does NOT parse, so it is normalised here too.
    """
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        log.warning("consent.client_ip.unparseable", length=len(candidate))
        try:
            ipaddress.ip_address(fallback)
        except ValueError:
            # Both unusable — surface a sentinel that is still a valid INET so
            # an audit write cannot fail on it.
            return "0.0.0.0"  # noqa: S104 — sentinel, never bound to a socket
        return fallback
    return candidate


def _direct_host(request: Request) -> str:
    """Return ``request.client.host``, or ``"unknown"`` when client is None."""
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"
