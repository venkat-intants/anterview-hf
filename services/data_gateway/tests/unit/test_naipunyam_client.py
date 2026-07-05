"""Unit tests for NaipunyamClient and CircuitBreaker — S5-003a.

All tests run without any real network calls.  httpx.AsyncClient is patched
at the ``app.naipunyam.client`` module level so that ``NaipunyamClient``
never opens a real socket.

Test matrix (10 tests):
  1.  test_client_rejects_empty_base_url
  2.  test_client_rejects_empty_client_id
  3.  test_client_rejects_empty_client_secret
  4.  test_get_profile_calls_correct_url
  5.  test_get_profile_caches_token
  6.  test_get_profile_raises_naipunyam_error_on_non_2xx
  7.  test_circuit_breaker_starts_closed
  8.  test_circuit_breaker_opens_after_threshold
  9.  test_circuit_breaker_rejects_when_open
 10.  test_circuit_breaker_closes_after_recovery
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.naipunyam.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.naipunyam.client import NaipunyamClient, NaipunyamError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, json_body: object) -> MagicMock:
    """Return a MagicMock that behaves like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


def _fake_token_response() -> MagicMock:
    return _make_response(200, {"access_token": "tok-abc", "expires_in": 3600})


def _fake_profile_response(uid: str = "UID-001") -> MagicMock:
    return _make_response(
        200,
        {
            "uid": uid,
            "name": "Ravi Kumar",
            "email": "ravi@example.com",
            "phone": "9000000000",
            "preferred_language": "te",
            "skills": ["Python", "FastAPI"],
        },
    )


# ---------------------------------------------------------------------------
# NaipunyamClient construction validation
# ---------------------------------------------------------------------------


def test_client_rejects_empty_base_url() -> None:
    """Empty base_url must raise ValueError at construction time."""
    with pytest.raises(ValueError, match="base_url"):
        NaipunyamClient(base_url="", client_id="cid", client_secret="sec")


def test_client_rejects_empty_client_id() -> None:
    """Empty client_id must raise ValueError at construction time."""
    with pytest.raises(ValueError, match="client_id"):
        NaipunyamClient(
            base_url="https://naipunyam.example.com",
            client_id="",
            client_secret="sec",
        )


def test_client_rejects_empty_client_secret() -> None:
    """Empty client_secret must raise ValueError at construction time."""
    with pytest.raises(ValueError, match="client_secret"):
        NaipunyamClient(
            base_url="https://naipunyam.example.com",
            client_id="cid",
            client_secret="",
        )


# ---------------------------------------------------------------------------
# get_profile — correct URL + token caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_calls_correct_url() -> None:
    """get_profile must call GET /v1/users/{uid}/profile with a Bearer token."""
    client = NaipunyamClient(
        base_url="https://naipunyam.example.com",
        client_id="cid",
        client_secret="sec",
    )
    uid = "UID-042"

    # Patch the underlying httpx.AsyncClient methods
    client._http.post = AsyncMock(return_value=_fake_token_response())  # type: ignore[method-assign]
    client._http.get = AsyncMock(return_value=_fake_profile_response(uid))  # type: ignore[method-assign]

    profile = await client.get_profile(uid)

    assert profile.uid == uid
    assert profile.name == "Ravi Kumar"
    assert "Python" in profile.skills

    # Assert the GET was called with the expected path
    call_args = client._http.get.call_args
    assert f"/v1/users/{uid}/profile" in str(call_args)
    # Assert Authorization header was set
    headers = call_args.kwargs.get("headers") or call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("headers", {})
    assert "Bearer tok-abc" in str(headers)

    await client.aclose()


@pytest.mark.asyncio
async def test_get_profile_caches_token() -> None:
    """_ensure_token must not call POST /oauth/token twice within the TTL window."""
    client = NaipunyamClient(
        base_url="https://naipunyam.example.com",
        client_id="cid",
        client_secret="sec",
    )

    client._http.post = AsyncMock(return_value=_fake_token_response())  # type: ignore[method-assign]
    client._http.get = AsyncMock(return_value=_fake_profile_response())  # type: ignore[method-assign]

    # Two back-to-back calls — token must only be fetched once
    await client.get_profile("UID-001")
    await client.get_profile("UID-001")

    assert client._http.post.call_count == 1

    await client.aclose()


@pytest.mark.asyncio
async def test_get_profile_raises_naipunyam_error_on_non_2xx() -> None:
    """A 404 response from the profile endpoint must raise NaipunyamError."""
    client = NaipunyamClient(
        base_url="https://naipunyam.example.com",
        client_id="cid",
        client_secret="sec",
    )

    client._http.post = AsyncMock(return_value=_fake_token_response())  # type: ignore[method-assign]
    client._http.get = AsyncMock(return_value=_make_response(404, {"detail": "not found"}))  # type: ignore[method-assign]

    with pytest.raises(NaipunyamError) as exc_info:
        await client.get_profile("UNKNOWN")

    assert exc_info.value.status_code == 404

    await client.aclose()


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_starts_closed() -> None:
    """A freshly created CircuitBreaker must be in the CLOSED state."""
    cb = CircuitBreaker("test", failure_threshold=5, recovery_timeout=30.0)
    assert cb.state == "closed"


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    """After failure_threshold=5 consecutive failures the breaker must OPEN."""
    cb = CircuitBreaker("test", failure_threshold=5, recovery_timeout=30.0)

    async def _always_fail() -> None:
        raise RuntimeError("boom")

    for _ in range(5):
        with pytest.raises(RuntimeError):
            await cb.call(_always_fail)

    assert cb.state == "open"

    # The 6th call must raise CircuitOpenError, not RuntimeError
    with pytest.raises(CircuitOpenError):
        await cb.call(_always_fail)


@pytest.mark.asyncio
async def test_circuit_breaker_rejects_when_open() -> None:
    """While OPEN, every call must raise CircuitOpenError without calling fn."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=9999.0)

    async def _fail_once() -> None:
        raise RuntimeError("trigger")

    with pytest.raises(RuntimeError):
        await cb.call(_fail_once)

    assert cb.state == "open"

    call_count = 0

    async def _should_not_be_called() -> str:
        nonlocal call_count
        call_count += 1
        return "should not reach here"

    with pytest.raises(CircuitOpenError):
        await cb.call(_should_not_be_called)

    assert call_count == 0, "fn must not be called when circuit is OPEN"


@pytest.mark.asyncio
async def test_circuit_breaker_closes_after_recovery() -> None:
    """OPEN → wait recovery_timeout → HALF_OPEN → successful calls → CLOSED."""
    cb = CircuitBreaker(
        "test",
        failure_threshold=1,
        recovery_timeout=0.05,  # 50 ms — short for testing
        half_open_max_calls=2,
    )

    async def _fail() -> None:
        raise RuntimeError("trigger open")

    # Open the breaker
    with pytest.raises(RuntimeError):
        await cb.call(_fail)
    assert cb.state == "open"

    # Wait for recovery_timeout to elapse
    await asyncio.sleep(0.1)

    # Next call transitions to HALF_OPEN and succeeds → probe counter increments
    success_count = 0

    async def _succeed() -> str:
        nonlocal success_count
        success_count += 1
        return "ok"

    # Two successful probes → breaker should close (half_open_max_calls=2)
    result1 = await cb.call(_succeed)
    assert result1 == "ok"
    assert cb.state in {"half_open", "closed"}

    result2 = await cb.call(_succeed)
    assert result2 == "ok"
    assert cb.state == "closed"
    assert success_count == 2
