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

from shared.s3 import _addressing_config, _resolve_endpoint, s3_client

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
    purpose, not a diff nobody notices."""
    allowed = {
        "__future__",
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
