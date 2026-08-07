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
