"""Trusted-proxy hop arithmetic, now that it lives outside the consent router.

DG-2 moved it to ``app/utils/request_ip.py``; DG-3 added the excess-hop signal.
The existing behavioural cases live in ``tests/integration/test_consent_router.py``
and still exercise the aliases; what is tested here is what the move and the new
counter added:

  * the layering rule that made the move worth doing — nothing under
    ``app/utils`` may import ``app.routers``, or the inversion is back;
  * the aliases in consent.py are the SAME objects, not copies that can drift;
  * the two degradation counters, in both directions.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.utils import request_ip


def _request(host: str | None = "203.0.113.9", xff: str | None = None) -> Any:
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    return SimpleNamespace(
        client=None if host is None else SimpleNamespace(host=host),
        headers=_Headers(headers),
    )


class _Headers(dict):  # type: ignore[type-arg]
    """Starlette headers are case-insensitive; the extractor relies on that."""

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def _counter_value(counter: Any) -> float:
    return counter._value.get()  # noqa: SLF001 — prometheus_client has no public getter


# ---------------------------------------------------------------------------
# DG-2 — the layering rule the move exists to enforce
# ---------------------------------------------------------------------------
def test_request_ip_does_not_import_any_router() -> None:
    """``app/rate_limit.py`` used to import ``app.routers.consent`` to resolve a
    client IP — infrastructure depending on a route module. If this module ever
    grows a router import the inversion is back, one layer down."""
    source = (
        __import__("pathlib").Path(request_ip.__file__).read_text(encoding="utf-8")
    )
    assert "app.routers" not in source
    assert "from app.routers" not in source


def test_rate_limit_imports_the_helper_not_the_consent_router() -> None:
    import app.rate_limit as rate_limit

    source = (
        __import__("pathlib").Path(rate_limit.__file__).read_text(encoding="utf-8")
    )
    assert "app.routers.consent" not in source
    assert rate_limit.extract_client_ip is request_ip.extract_client_ip


def test_consent_aliases_are_the_same_objects() -> None:
    """Four modules and the consent tests import the private names. Aliases, not
    re-implementations — a copy is how the original drift happened."""
    from app.routers import consent

    assert consent._extract_client_ip is request_ip.extract_client_ip
    assert consent._extract_user_agent is request_ip.extract_user_agent


# ---------------------------------------------------------------------------
# Extraction behaviour
# ---------------------------------------------------------------------------
def test_zero_trusted_proxies_ignores_a_forged_header() -> None:
    ip = request_ip.get_client_ip(_request(xff="1.2.3.4"), 0)
    assert ip == "203.0.113.9"


def test_one_trusted_proxy_ignores_prepended_entries() -> None:
    ip = request_ip.get_client_ip(_request(xff="1.2.3.4, 198.51.100.7"), 1)
    assert ip == "198.51.100.7"


def test_unparseable_selected_hop_falls_back_to_the_socket_peer() -> None:
    """A non-IP would key its own rate-limit bucket and blow up an INET write."""
    ip = request_ip.get_client_ip(_request(xff="not-an-ip"), 1)
    assert ip == "203.0.113.9"


def test_no_client_and_no_usable_header_yields_a_valid_inet_sentinel() -> None:
    ip = request_ip.get_client_ip(_request(host=None, xff="garbage"), 1)
    assert ip == "0.0.0.0"  # noqa: S104 — sentinel, never bound


# ---------------------------------------------------------------------------
# DG-3 — both directions of the silent misconfiguration are now countable
# ---------------------------------------------------------------------------
def test_fewer_hops_than_configured_increments_the_underflow_counter() -> None:
    """Too HIGH a TRUSTED_PROXY_COUNT: every client collapses onto the socket
    peer, i.e. one global rate-limit bucket, while the control reports success."""
    before = _counter_value(request_ip._proxy_hop_underflow)

    ip = request_ip.get_client_ip(_request(xff="198.51.100.7"), 3)

    assert ip == "203.0.113.9"
    assert _counter_value(request_ip._proxy_hop_underflow) == before + 1


def test_more_hops_than_configured_increments_the_excess_counter() -> None:
    """Too LOW is the direction ``le=4`` on the setting cannot catch and the
    underflow counter never sees. Counted, not warned: an extra hop is also what
    a benign client-supplied XFF looks like, so this is a rate to correlate
    against a deploy, not an error to page on."""
    before = _counter_value(request_ip._proxy_hop_excess)

    ip = request_ip.get_client_ip(_request(xff="1.2.3.4, 198.51.100.7"), 1)

    assert ip == "198.51.100.7"
    assert _counter_value(request_ip._proxy_hop_excess) == before + 1


def test_an_exactly_matching_chain_increments_neither_counter() -> None:
    """The signal is worthless if the healthy topology also fires it."""
    under = _counter_value(request_ip._proxy_hop_underflow)
    excess = _counter_value(request_ip._proxy_hop_excess)

    assert request_ip.get_client_ip(_request(xff="198.51.100.7"), 1) == "198.51.100.7"

    assert _counter_value(request_ip._proxy_hop_underflow) == under
    assert _counter_value(request_ip._proxy_hop_excess) == excess


def test_absent_header_is_not_counted_as_a_degradation() -> None:
    """No XFF at all is a health check or a direct hit, not a wrong hop count."""
    under = _counter_value(request_ip._proxy_hop_underflow)
    excess = _counter_value(request_ip._proxy_hop_excess)

    assert request_ip.get_client_ip(_request(), 1) == "203.0.113.9"

    assert _counter_value(request_ip._proxy_hop_underflow) == under
    assert _counter_value(request_ip._proxy_hop_excess) == excess


@pytest.mark.parametrize("header", ["", "   ", " , , "])
def test_empty_header_variants_fall_back_without_indexing(header: str) -> None:
    assert request_ip.get_client_ip(_request(xff=header), 1) == "203.0.113.9"


# ---------------------------------------------------------------------------
# DG-3 — the too-LOW direction now has a signal, not just a counter
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_hop_observation() -> Any:
    """Each test starts with an unsampled topology observation."""
    request_ip._reset_hop_observation()
    yield
    request_ip._reset_hop_observation()


def _gauge_value(gauge: Any) -> float:
    return gauge._value.get()  # noqa: SLF001 — prometheus_client has no public getter


def _drive(xff: str, configured: int, times: int) -> None:
    for _ in range(times):
        request_ip.get_client_ip(_request(xff=xff), configured)


def test_min_observed_hops_is_published_for_alerting() -> None:
    """The alert expression is
    ``client_ip_proxy_hops_min_observed > client_ip_trusted_proxy_count``, so
    both sides have to exist as series before the sample completes."""
    request_ip.get_client_ip(_request(xff="1.2.3.4, 198.51.100.7"), 1)

    assert _gauge_value(request_ip._proxy_hops_min_observed) == 2
    assert _gauge_value(request_ip._trusted_proxy_count_gauge) == 1


def test_a_universally_excessive_chain_warns_once_the_sample_completes() -> None:
    """Two real hops with TRUSTED_PROXY_COUNT=1: the extracted "client" is the
    inner proxy, so every client shares one rate-limit bucket. The excess counter
    alone cannot say this, because a single excess request is also what a benign
    prepending client looks like."""
    import structlog

    with structlog.testing.capture_logs() as logs:
        _drive("1.2.3.4, 198.51.100.7", 1, request_ip._HOP_SAMPLE_SIZE)

    mismatches = [e for e in logs if e["event"] == "consent.client_ip.proxy_hop_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["log_level"] == "warning"
    assert mismatches[0]["configured_proxy_count"] == 1
    assert mismatches[0]["min_observed_hops"] == 2
    assert mismatches[0]["direction"] == "configured_too_low"
    # This is the ONE direction where "the minimum is the real hop count" holds,
    # so it is the one direction allowed to name the value to configure.
    assert "set TRUSTED_PROXY_COUNT to it" in mismatches[0]["detail"]


def test_the_zero_configured_case_is_observed_too() -> None:
    """TRUSTED_PROXY_COUNT left at its safe default of 0 behind a real proxy is
    the most likely form of this misconfiguration, and it returns before the hop
    arithmetic ever runs — so an observation placed after that early return would
    be blind to exactly the case it exists to catch."""
    import structlog

    with structlog.testing.capture_logs() as logs:
        _drive("198.51.100.7", 0, request_ip._HOP_SAMPLE_SIZE)

    mismatches = [e for e in logs if e["event"] == "consent.client_ip.proxy_hop_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["min_observed_hops"] == 1
    assert mismatches[0]["configured_proxy_count"] == 0


def test_a_correct_topology_reports_a_match_and_never_warns() -> None:
    """One real hop, one configured — plus a prepending client on some requests,
    which must NOT be enough to flip the verdict. That is why the statistic is
    the minimum and not the mode or the maximum."""
    import structlog

    with structlog.testing.capture_logs() as logs:
        _drive("198.51.100.7", 1, request_ip._HOP_SAMPLE_SIZE - 5)
        _drive("1.2.3.4, 198.51.100.7", 1, 5)

    assert not [e for e in logs if e["event"] == "consent.client_ip.proxy_hop_mismatch"]
    matches = [e for e in logs if e["event"] == "consent.client_ip.proxy_hop_observation"]
    assert len(matches) == 1
    assert matches[0]["min_observed_hops"] == 1


def test_the_verdict_is_emitted_exactly_once_per_process() -> None:
    """A per-request warning on a misconfigured deployment is its own outage."""
    import structlog

    with structlog.testing.capture_logs() as logs:
        _drive("1.2.3.4, 198.51.100.7", 1, request_ip._HOP_SAMPLE_SIZE * 3)

    verdicts = [
        e
        for e in logs
        if e["event"]
        in ("consent.client_ip.proxy_hop_mismatch", "consent.client_ip.proxy_hop_observation")
    ]
    assert len(verdicts) == 1


def test_a_chain_shorter_than_configured_never_advises_lowering_the_count() -> None:
    """The opposite mismatch: TRUSTED_PROXY_COUNT=3 with a one-hop chain.

    ``min_observed >= real_hop_count`` always holds (clients only prepend), so a
    minimum BELOW the configured value is an upper bound on the real chain, not a
    measurement of it — and the too-high direction is the spoofable one, because
    ``real_index = len(hops) - trusted_proxy_count`` lets a client that prepends
    its way up to the configured length have its own entry returned. Emitting the
    "set TRUSTED_PROXY_COUNT to min_observed_hops" remediation here would hand an
    operator a number the sample cannot justify.
    """
    import structlog

    with structlog.testing.capture_logs() as logs:
        _drive("198.51.100.7", 3, request_ip._HOP_SAMPLE_SIZE)

    mismatches = [e for e in logs if e["event"] == "consent.client_ip.proxy_hop_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["direction"] == "configured_too_high"
    assert mismatches[0]["configured_proxy_count"] == 3
    assert mismatches[0]["min_observed_hops"] == 1
    assert "set TRUSTED_PROXY_COUNT to it" not in mismatches[0]["detail"]
    assert "Do NOT simply lower" in mismatches[0]["detail"]


def test_the_too_high_direction_is_spoofable_which_is_why_it_reads_differently() -> None:
    """Pins the fact the new message asserts. With one real proxy and
    TRUSTED_PROXY_COUNT=2, a client that prepends one entry has that entry
    returned verbatim as the client IP — so the remediation for this direction is
    "check the real chain", never "lower the number to whatever was observed"."""
    forged = request_ip.get_client_ip(_request(xff="9.9.9.9, 198.51.100.7"), 2)

    assert forged == "9.9.9.9"


def test_header_less_requests_do_not_pin_the_minimum_at_zero() -> None:
    """Health probes hit the container directly and carry no X-Forwarded-For. If
    they were sampled the minimum would be 0 in every mixed topology and the
    mismatch would be permanently invisible."""
    _drive("1.2.3.4, 198.51.100.7", 1, 5)
    for _ in range(20):
        request_ip.get_client_ip(_request(), 1)

    assert _gauge_value(request_ip._proxy_hops_min_observed) == 2
