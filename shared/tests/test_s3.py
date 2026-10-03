"""Regression suite for ``shared.s3``.

The bug this guards: seven hand-rolled aioboto3 constructions across four
services, three of which had missed both the path-style-addressing fix and the
``use_ssl`` argument. Each copy looked correct on its own, so the drift only
surfaced as an environment-specific upload or pre-sign failure.

No test here opens a socket. An aioboto3 client is built entirely from service
model JSON on disk, so it can be constructed and inspected offline — but ONLY
when credentials are passed explicitly. With empty credentials botocore engages
its default chain, whose EC2 instance-metadata leg blocks for minutes on a
non-AWS host; that was measured, it is the reason every test that enters the
context manager supplies a key pair, and it is asserted structurally in
``test_empty_credentials_defer_to_the_botocore_chain`` without ever entering.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any

import pytest
from botocore.config import Config as BotoConfig

from shared.s3 import (
    _addressing_config,
    _resolve_endpoint,
    aclose_s3_clients,
    s3_client,
)

# Neither endpoint is ever contacted; they only exercise the two addressing modes.
_MINIO_ENDPOINT = "http://localhost:9000"
_R2_ENDPOINT = "https://acct.r2.cloudflarestorage.com"

# Explicit throwaway credentials keep the botocore credential chain out of every
# test that builds a real client. They are never sent anywhere.
_KEY = "test-access-key"
_SECRET = "test-secret-key"


async def _client_meta(**kwargs: Any) -> tuple[str, Any, str]:
    """Enter the factory and return (endpoint_url, config.s3, region_name)."""
    params: dict[str, Any] = {
        "endpoint": None,
        "region": "auto",
        "access_key": _KEY,
        "secret_key": _SECRET,
    }
    params.update(kwargs)
    async with s3_client(**params) as client:
        return client.meta.endpoint_url, client.meta.config.s3, client.meta.region_name


# --------------------------------------------------------------------------
# Path-style addressing — the fix three of seven call sites had missed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", [_MINIO_ENDPOINT, _R2_ENDPOINT])
async def test_custom_endpoint_forces_path_style_addressing(endpoint: str) -> None:
    """MinIO and R2 are reached via a custom endpoint, where virtual-host style
    would resolve to ``bucket.localhost:9000`` / ``bucket.<acct>.r2...`` and
    fail at DNS. This is the fix admin_ops and both feedback_billing call sites
    never received."""
    endpoint_url, config_s3, _ = await _client_meta(endpoint=endpoint)

    assert endpoint_url == endpoint
    assert config_s3 == {"addressing_style": "path"}


async def test_real_aws_keeps_virtual_host_addressing() -> None:
    """The rule is conditional, not unconditional: path-style is the deprecated
    style on real AWS S3, so an empty endpoint must leave botocore's default
    alone rather than pin it."""
    endpoint_url, config_s3, _ = await _client_meta(endpoint="", region="ap-south-1")

    assert endpoint_url == "https://s3.ap-south-1.amazonaws.com"
    assert config_s3 is None


def test_addressing_config_is_decided_by_the_endpoint_alone() -> None:
    """The unit behind the rule, so a future caller cannot get a config that
    disagrees with its endpoint."""
    assert _addressing_config(None) is None

    config = _addressing_config(_R2_ENDPOINT)
    assert isinstance(config, BotoConfig)
    assert config.s3 == {"addressing_style": "path"}


# --------------------------------------------------------------------------
# Endpoint resolution — "" is not a valid endpoint, it means "real AWS"
# --------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", None])
def test_empty_endpoint_resolves_to_none(empty: str | None) -> None:
    """Every service stores this as ``str`` defaulting to ``""``; botocore's
    sentinel for "use the regional URL" is ``None`` and it does not accept the
    empty string."""
    assert _resolve_endpoint(empty) is None


def test_a_set_endpoint_is_passed_through_unchanged() -> None:
    assert _resolve_endpoint(_MINIO_ENDPOINT) == _MINIO_ENDPOINT


# --------------------------------------------------------------------------
# use_ssl — defaults must not silently downgrade the two services that lack it
# --------------------------------------------------------------------------


async def test_derived_endpoint_is_https_by_default() -> None:
    """admin_ops and feedback_billing have no ``s3_use_ssl`` setting at all, so
    they will call this without the argument. The default has to be TLS."""
    endpoint_url, _, _ = await _client_meta(endpoint="", region="ap-south-1")

    assert endpoint_url.startswith("https://")


async def test_use_ssl_false_downgrades_only_the_derived_endpoint() -> None:
    """``use_ssl`` picks the scheme botocore derives when no endpoint is given
    — this is the whole of its effect, and pinning it here documents that."""
    endpoint_url, _, _ = await _client_meta(endpoint="", region="ap-south-1", use_ssl=False)

    assert endpoint_url == "http://s3.ap-south-1.amazonaws.com"


async def test_an_explicit_endpoint_scheme_beats_use_ssl() -> None:
    """Why omitting ``use_ssl`` was invisible in three call sites: an endpoint
    that carries its own scheme wins, and every real deployment sets one. A
    reader must not conclude from that silence that the flag is decorative — it
    is load-bearing for the empty-endpoint (real AWS) path above."""
    endpoint_url, _, _ = await _client_meta(endpoint=_R2_ENDPOINT, use_ssl=False)

    assert endpoint_url == _R2_ENDPOINT


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


async def test_supplied_credentials_reach_the_signer() -> None:
    """The factory must actually wire credentials through — a client that
    silently signed anonymously would fail only against a real bucket."""
    async with s3_client(
        endpoint=_R2_ENDPOINT,
        region="auto",
        access_key=_KEY,
        secret_key=_SECRET,
    ) as client:
        frozen = await client._request_signer._credentials.get_frozen_credentials()

    assert frozen.access_key == _KEY
    assert frozen.secret_key == _SECRET


def test_empty_credentials_defer_to_the_botocore_chain() -> None:
    """``""`` must become ``None``: an empty string is a *present* credential
    that botocore signs with, yielding 403 SignatureDoesNotMatch, whereas
    ``None`` engages the default chain.

    Asserted by reading the source rather than by building a client, because
    building one with no credentials is precisely the case that blocks on EC2
    instance metadata from a non-AWS host — the test would hang for minutes.
    """
    source = inspect.getsource(s3_client)

    assert "aws_access_key_id=access_key or None" in source
    assert "aws_secret_access_key=secret_key or None" in source


# --------------------------------------------------------------------------
# The factory is usable the way the call sites will use it
# --------------------------------------------------------------------------


async def test_factory_is_an_async_context_manager_that_closes_cleanly() -> None:
    """The seven call sites are all ``async with`` blocks; entering and exiting
    must release the underlying aiohttp session rather than leak it per call."""
    async with s3_client(
        endpoint=_MINIO_ENDPOINT,
        region="auto",
        access_key=_KEY,
        secret_key=_SECRET,
    ) as client:
        assert hasattr(client, "put_object")
        assert hasattr(client, "generate_presigned_url")
        assert hasattr(client, "delete_object")


def test_all_arguments_are_keyword_only() -> None:
    """Five same-typed strings in a row: positional calls would let an endpoint
    and a region swap places and still typecheck. Keyword-only makes that
    unrepresentable, so it is part of the contract, not a style choice."""
    # signature() follows the __wrapped__ that asynccontextmanager's functools
    # .wraps sets, so this reports the real parameters rather than the
    # decorator's (*args, **kwds).
    params = inspect.signature(s3_client).parameters

    positional = [
        name
        for name, p in params.items()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert positional == []
    assert set(params) == {"endpoint", "region", "access_key", "secret_key", "use_ssl"}


def test_factory_takes_primitives_not_a_settings_object() -> None:
    """The root of DEP-1: the four services spell these fields differently
    (``s3_endpoint`` vs ``s3_endpoint_url``, and only two define
    ``s3_use_ssl``). A ``settings`` parameter here would have to know all four
    shapes, which is the divergence this module exists to remove."""
    params = inspect.signature(s3_client).parameters

    assert "settings" not in params
    assert "config" not in params, "the addressing rule is derived, never passed in"


# --------------------------------------------------------------------------
# shared/ stays importable from all four service images
# --------------------------------------------------------------------------


def test_factory_stays_dependency_light() -> None:
    """shared/ is COPY'd into every service image, so an import of ``app.*`` or
    ``services.*`` here would break all four at container start. aioboto3 and
    botocore are on the allowlist only because all four services already pin
    them identically; widening it further should be a decision someone makes on
    purpose, not a diff nobody notices.

    ``asyncio`` was added on purpose, in round 13 of PH3-B4b: the client cache
    needs a per-loop lock and the running loop as part of its key. It is
    stdlib, every service already runs an event loop, and ``aioboto3`` could
    not work without one — so it widens the allowlist by nothing in practice.
    The reason this is written down rather than just added: this test was RED
    on the branch that introduced the import, and the gate set being reported
    at the time did not include ``shared/tests`` at all."""
    allowed = {
        "__future__",
        "asyncio",
        "collections",
        "contextlib",
        "typing",
        "aioboto3",
        "botocore",
        "pydantic",
        "structlog",
    }
    source = pathlib.Path(__file__).parent.parent / "s3.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    assert roots <= allowed, f"disallowed imports in shared/s3.py: {sorted(roots - allowed)}"


def test_importing_shared_does_not_drag_in_botocore() -> None:
    """The module-level aioboto3 import is only acceptable because it is
    opt-in: ``shared/__init__.py`` must stay empty so a service that never
    touches object storage does not pay the botocore import at startup."""
    init = pathlib.Path(__file__).parent.parent / "__init__.py"

    assert init.read_text(encoding="utf-8").strip() == ""


# --------------------------------------------------------------------------
# Pre-signed links are SigV4 — R2 and B2 refuse SigV2 (found by PH4-A4)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("endpoint", "region"), [(_MINIO_ENDPOINT, "us-east-1"),
                                                  (_R2_ENDPOINT, "auto"),
                                                  ("", "ap-south-1")])
async def test_presigned_links_are_signature_v4_on_every_endpoint(endpoint: str, region: str) -> None:
    async with s3_client(endpoint=endpoint, region=region, access_key="AKIAEXAMPLE",
                         secret_key="secret") as s3:
        url = await s3.generate_presigned_url(
            "get_object", Params={"Bucket": "b", "Key": "k"}, ExpiresIn=300)
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url and "X-Amz-Expires=300" in url
    assert "AWSAccessKeyId=" not in url  # the SigV2 form


async def test_custom_endpoints_keep_path_style_with_sigv4() -> None:
    endpoint_url, config_s3, _ = await _client_meta(endpoint=_R2_ENDPOINT)
    assert config_s3 == {"addressing_style": "path"}


# ==========================================================================
# THE CLIENT CACHE — round 13's MEDIUM-2.
#
# The cache landed in round 12 as the fix for a state-correlated event-loop
# stall on two ANONYMOUS endpoints: building an aioboto3 client is 180-450 ms
# of synchronous botocore work, and only the refusing branches of
# `POST /apply/...` delete the CV they were made to upload, so that stall was
# readable from a concurrent request and told a caller which of five states an
# address was in.
#
# It shipped with no tests. Round 13's audit then checked each of the three
# guards `docs/ACCEPTED-RISKS.md` AR-10 claims "hold it" against a hypothetical
# revert of the cache, and every one stayed green: two are structural checks on
# `public_apply` and the third is unrelated config bounds. So the half of the
# fix the register calls "the half that makes the property true" was the only
# half nothing held.
#
# These are that half. Every one of them goes red if the cache is removed —
# which is the property, not the API.
#
# No socket is opened here, for the reason in the module docstring: a client is
# built from on-disk service model JSON, and credentials are passed explicitly
# so botocore never engages its instance-metadata chain.
# ==========================================================================


async def _acquire() -> Any:
    """One trip through the factory, returning the client it yields."""
    async with s3_client(
        endpoint=_MINIO_ENDPOINT,
        region="us-east-1",
        access_key=_KEY,
        secret_key=_SECRET,
        use_ssl=False,
    ) as client:
        return client


async def test_the_same_loop_and_settings_reuse_one_client() -> None:
    """THE TEST THAT GOES RED IF THE CACHE IS REMOVED.

    Without the cache each call builds its own client, so these two are
    different objects and the 180-450 ms synchronous construction is paid on
    every upload and every delete. That cost is the channel; this identity is
    the fix.
    """
    await aclose_s3_clients()
    try:
        first = await _acquire()
        second = await _acquire()
        assert first is second, (
            "the factory built a second client for identical settings on one "
            "event loop, so the cache is not in effect — every S3 call pays "
            "180-450 ms of synchronous construction again, and on the anonymous "
            "apply doors that cost is state-correlated (see AR-10 residue 2)"
        )
    finally:
        await aclose_s3_clients()


async def test_the_client_is_not_closed_when_the_caller_exits() -> None:
    """The contract is still a context manager; the client outlives it.

    This is the part that would break call sites if it regressed the other way:
    `s3_client` keeps its `async with` shape so none of the thirty-odd call
    sites changed, but closing on exit would make the cache useless and a
    second caller would get a dead client.
    """
    await aclose_s3_clients()
    try:
        client = await _acquire()
        # Usable after the context manager exited: a closed aiobotocore client
        # raises on attribute access through its generated API.
        assert client.meta.endpoint_url == _MINIO_ENDPOINT
        assert await _acquire() is client
    finally:
        await aclose_s3_clients()


async def test_different_settings_do_not_share_a_client() -> None:
    """Two services in one process, or a credential rotation, must not collide.

    The key carries the endpoint, region, both credentials and `use_ssl`, so a
    different S3 configuration gets its own client rather than silently reusing
    one signed for somewhere else.
    """
    await aclose_s3_clients()
    try:
        minio = await _acquire()
        async with s3_client(
            endpoint=_R2_ENDPOINT,
            region="auto",
            access_key=_KEY,
            secret_key=_SECRET,
        ) as r2:
            assert r2 is not minio
        async with s3_client(
            endpoint=_MINIO_ENDPOINT,
            region="us-east-1",
            access_key="a-different-key",
            secret_key=_SECRET,
            use_ssl=False,
        ) as rotated:
            assert rotated is not minio
    finally:
        await aclose_s3_clients()


async def test_closing_empties_the_cache_and_is_safe_to_repeat() -> None:
    """`aclose_s3_clients` is called from four lifespans and must not be fussy.

    Safe when nothing is cached, safe twice, and a later caller rebuilds rather
    than receiving the client that was just closed.
    """
    from shared import s3 as s3mod

    await aclose_s3_clients()
    assert not s3mod._clients
    await aclose_s3_clients()  # twice, from an empty state

    first = await _acquire()
    assert s3mod._clients
    await aclose_s3_clients()
    assert not s3mod._clients, "the cache still holds entries after closing"
    assert not s3mod._stacks, "a stack was left behind, so a connector leaked"

    second = await _acquire()
    assert second is not first, "a closed client was handed back out"
    await aclose_s3_clients()


async def test_concurrent_first_callers_share_one_client() -> None:
    """The per-loop lock. Without it the first N concurrent callers each build.

    That matters beyond waste: two clients means two aiohttp connectors, and
    the one that loses the race is never closed by `aclose_s3_clients` because
    it was never the cached entry.
    """
    import asyncio

    await aclose_s3_clients()
    try:
        clients = await asyncio.gather(*[_acquire() for _ in range(5)])
        assert len({id(c) for c in clients}) == 1, (
            "concurrent first callers built more than one client, so the "
            "double-checked lock is not holding"
        )
    finally:
        await aclose_s3_clients()


def test_a_closed_loop_is_pruned_rather_than_reused() -> None:
    """A client belongs to the loop that built it; the suite makes many loops.

    Deliberately NOT an async test: it needs to own the loops. Keyed on the
    loop OBJECT rather than `id(loop)` because CPython reuses addresses once an
    object is collected — a reused id would be a cache HIT, and the prune only
    runs on a miss, so the stale entry would never be seen.
    """
    import asyncio

    from shared import s3 as s3mod

    loop_a = asyncio.new_event_loop()
    try:
        first = loop_a.run_until_complete(_acquire())
        assert len(s3mod._clients) == 1
    finally:
        loop_a.close()

    # The entry survives its loop's close — nothing can await its aclose now.
    assert len(s3mod._clients) == 1

    loop_b = asyncio.new_event_loop()
    try:
        second = loop_b.run_until_complete(_acquire())
        assert second is not first, "a client from a closed loop was handed out"
        assert len(s3mod._clients) == 1, (
            "the closed loop's entry was not pruned, so a long test run "
            f"accumulates one client per loop: {len(s3mod._clients)} entries"
        )
        loop_b.run_until_complete(aclose_s3_clients())
    finally:
        loop_b.close()


async def test_the_client_is_constructed_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COUNTS CONSTRUCTIONS, which identity never can.

    Round 14's requested defeat of the five tests above, and it worked: every
    one of them compares object identity, so ANY eviction policy satisfies all
    of them. The reviewer added a plausible "rotated credentials" TTL — a
    `_built_at` map and a five-minute expiry that pops the entry and rebuilds —
    and all 27 tests stayed green, in a service that would then reinstate the
    full 180-450 ms synchronous construction every five minutes, on the
    `_refuse`-only CV delete. That is precisely the state-correlated stall the
    cache exists to remove.

    Identity cannot see it because an evicted-and-rebuilt client is a different
    object only on the call that rebuilds; every pair of calls inside one TTL
    window still compares equal. The number of constructions is the only
    quantity that is actually the point, so this test spies on it.

    Both halves matter: exactly one construction across many acquisitions, and
    still exactly one after a later acquisition — so a cap, a TTL, an LRU or a
    per-call rebuild all show up here.
    """
    import aioboto3

    from shared import s3 as s3mod

    await aclose_s3_clients()

    real_session = aioboto3.Session
    built = 0

    class _CountingSession(real_session):  # type: ignore[misc, valid-type]
        def client(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            nonlocal built
            built += 1
            return super().client(*args, **kwargs)

    monkeypatch.setattr(s3mod.aioboto3, "Session", _CountingSession)
    try:
        for _ in range(6):
            await _acquire()
        assert built == 1, (
            f"the client was constructed {built} times across six acquisitions. "
            "Each construction is 180-450 ms of SYNCHRONOUS botocore work that "
            "blocks the event loop, and on the anonymous apply doors that stall "
            "is state-correlated because only the refusing branches delete the "
            "CV they were made to upload (AR-10 residue 2). Identity-based "
            "tests cannot see this: an eviction policy rebuilds and still "
            "returns one object per window."
        )

        # A later acquisition must not rebuild either — this is the half that
        # catches a TTL whose window happens to be longer than the loop above.
        await _acquire()
        assert built == 1, f"a later acquisition rebuilt the client ({built} total)"
    finally:
        await aclose_s3_clients()
