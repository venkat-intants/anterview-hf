"""The one S3 / R2 / MinIO client factory, shared by all four services.

Why this module exists
----------------------
Seven hand-rolled ``aioboto3.Session(...).client("s3", ...)`` constructions
existed across the four services. Each is six lines long and looks correct in
isolation, which is exactly why the drift between them survived review: nothing
in a diff shows you the other six. Two fixes were made to some copies and not
to others, and the result is a bug class that only appears in one deployment
environment — the kind that reads as "S3 is flaky" rather than as a defect.

The state found on 2026-08-07:

===========================================  ==========  =======  ============
call site                                    path-style  use_ssl  creds→None
===========================================  ==========  =======  ============
``data_gateway/app/s3_upload.py``            yes         yes      yes
``data_gateway/.../resume.py`` (presign)     yes         yes      yes
``data_gateway/.../resume.py`` (delete)      yes         yes      yes
``interview_core/app/s3.py``                 yes         yes      **no**
``admin_ops/app/s3_client.py``               **no**      **no**   yes
``feedback_billing/app/pdf_render.py``       **no**      **no**   **no**
``feedback_billing/.../scorecard.py``        **no**      **no**   **no**
===========================================  ==========  =======  ============

Three of the seven miss both fixes. All three are the ``s3_endpoint_url``
spelling — the drift tracks the Settings field name, because that is the axis
along which the code was copied.

The three behaviours being centralised
--------------------------------------
``addressing style``
    A CUSTOM endpoint (MinIO, Cloudflare R2) requires PATH-style addressing —
    bucket in the path, not as a subdomain. Virtual-host style resolves to
    ``bucket.localhost:9000`` or ``bucket.<acct>.r2.cloudflarestorage.com``,
    neither of which exists, so the request fails at DNS. Real AWS S3 is the
    opposite case (virtual-host is the supported style), which is why this is
    conditional on a custom endpoint rather than set unconditionally. It
    matters most for the two PRE-SIGN call sites that omit it: a pre-signed URL
    is signed over the host, so the wrong addressing style does not fail at
    generation time — it hands the caller a URL that 404s later, away from any
    log line that would explain it.

``use_ssl``
    Only decides the scheme of the endpoint botocore DERIVES when *endpoint* is
    empty (real AWS). An explicit ``http://`` or ``https://`` endpoint carries
    its own scheme and wins outright. That is why omitting the flag was
    invisible: every deployment sets a scheme-bearing endpoint or none at all.
    It is centralised because the default must be True — the two services whose
    Settings have no ``s3_use_ssl`` field at all (admin_ops, feedback_billing)
    talk to R2 over TLS, and a factory defaulting to False would silently
    downgrade them.

``credential fallback ("" → None)``
    Not cosmetic. An empty string is a *present* credential: botocore signs
    with it and the peer returns 403 SignatureDoesNotMatch. ``None`` instead
    engages botocore's default credential chain (env vars, ``~/.aws``,
    container role, then EC2 instance metadata). Callers should therefore keep
    treating "credentials unset" as a condition to check BEFORE calling this —
    on a non-AWS host (Railway, HF Space) the instance-metadata leg of that
    chain blocks on an unroutable address for minutes before giving up, so
    "no credentials" degrades into a hang rather than a clean error.

Why primitives rather than a ``Settings`` object
------------------------------------------------
The four services spell the same field differently — ``s3_endpoint`` in
data_gateway and interview_core, ``s3_endpoint_url`` in admin_ops and
feedback_billing — and only two of them define ``s3_use_ssl``. A helper taking
``Settings`` would have to know all four shapes, i.e. it would encode the very
divergence it exists to remove, and ``shared/`` would gain knowledge of
``services/``. Primitives keep the mapping at each call site where the field
name actually lives.

Dependency rule
---------------
``shared/`` is COPY'd into all four service images, so it may import only
stdlib + pydantic + structlog and nothing from ``services/`` or ``app.*``.
``aioboto3``/``botocore`` are imported at module level here because all four
services already pin them identically (``aioboto3==15.5.0``,
``botocore==1.40.61``), so this adds nothing to any requirements file.
``shared/__init__.py`` is empty, so a service that never touches object storage
never pays the botocore import cost. ``shared/tests/test_s3.py`` asserts both
the allowlist and the empty ``__init__``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

__all__ = ["aclose_s3_clients", "s3_client"]

# ---------------------------------------------------------------------------
# THE CLIENT IS CACHED, AND THAT IS A PRIVACY FIX, NOT A PERFORMANCE ONE.
#
# `aioboto3.Session(...).client("s3", ...)` is expensive and SYNCHRONOUS:
# botocore loads its service-model JSON off disk and builds the API object
# without yielding. Measured on loopback MinIO, 180-450 ms per construction,
# and an `asyncio.sleep(5ms)` monitor showed event-loop stalls of 349-533 ms
# that match construct+call almost exactly — i.e. essentially none of it
# yields.
#
# Why that mattered. `POST /apply/{requisition_id}` and `POST /apply/draft/
# submit` are anonymous, and must answer identically whether the submitted
# address has a live application, is in a rejection cooldown, was rejected and
# the window elapsed, was rejected with an override, or has never applied here
# (docs/ACCEPTED-RISKS.md AR-10). `_refuse` deletes the CV it was made to
# upload, so the DELETE ran on two of those five states and not the other
# three. With a client built per call, that was ~390 ms of state-correlated
# loop blocking:
#
#   * round 11 found it INSIDE the reply pad, where it overran a 400 ms budget
#     on the refusing states on healthy storage, every request;
#   * round 11's fix scheduled the delete after the response, which took it out
#     of the pad and left it on the same single-threaded worker — where round
#     12 read it off a CONCURRENT request, 24/25 correct on one probe, with no
#     pad and no alert covering it.
#
# Caching removes the term rather than relocating it: after the first call the
# same measurement is 3-8 ms with 10-15 ms of lag. It also removes it from the
# CV UPLOAD, which pays the identical cost on every state and is the jitter
# AR-10 credits with being the only reason those oracles did not converge
# sooner. Scheduling and caching were never alternatives; this is the half that
# makes the property true.
#
# Keyed by the RUNNING EVENT LOOP OBJECT as well as the connection settings.
# An aioboto3 client binds to the loop that created it, and the test suite
# builds a fresh loop per test — a cache keyed only on settings would hand out
# a client belonging to a closed loop.
#
# The loop OBJECT, not `id(loop)`. `id()` is a memory address and CPython
# reuses addresses once an object is collected, so a fresh loop can be handed
# a dead loop's id — and since the stale entry would then be a cache HIT, the
# prune below (which only runs on a miss) would never see it. Keying on the
# object cannot collide: a new loop is a different key, so it misses, prunes
# and builds. The cost is a strong reference to each loop until the next miss
# clears it, which is bounded and only reachable from tests.
_clients: dict[tuple[Any, ...], Any] = {}
_stacks: dict[tuple[Any, ...], AsyncExitStack] = {}
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _prune_closed_loops() -> None:
    """Drop cached clients whose event loop has been closed.

    Only reachable from tests — a service has one loop for its lifetime — and
    that is exactly why it exists: without it a long pytest run accumulates one
    client per loop per settings tuple, each holding an aiohttp connector open.

    Tested on `is_closed()` rather than on identity, so an entry is dropped
    because its loop is actually finished and not merely because some other
    loop is running now. Closing the stack would need that dead loop, so the
    entry is dropped WITHOUT closing it: the connector dies with its loop, and
    awaiting an aclose on a closed loop would raise here instead.
    """
    for key in [k for k in _clients if isinstance(k[0], asyncio.AbstractEventLoop) and k[0].is_closed()]:
        _clients.pop(key, None)
        _stacks.pop(key, None)
    for loop in [lp for lp in _locks if lp.is_closed()]:
        _locks.pop(loop, None)


async def aclose_s3_clients() -> None:
    """Close every cached client. Call from a service's lifespan shutdown.

    Safe to call when nothing is cached, and safe to call twice. A process that
    exits without calling it leaks nothing that matters — the sockets go with
    the process — but a reloading dev server or a test that wants a clean slate
    should.
    """
    for key in list(_stacks):
        stack = _stacks.pop(key)
        _clients.pop(key, None)
        # Shutdown must not fail on a socket that is already gone.
        with contextlib.suppress(Exception):
            await stack.aclose()
    # The locks go too, or a long test run keeps one per loop it ever used.
    # Harmless in a service (one loop, called once at shutdown); this is for
    # the suite, which is the only caller that makes loops in bulk.
    _locks.clear()


def _resolve_endpoint(endpoint: str | None) -> str | None:
    """Normalise an endpoint setting to what botocore expects.

    Every service stores this as ``str`` defaulting to ``""`` (meaning "real
    AWS"), but botocore's sentinel for that is ``None`` — an empty string is
    not treated as absent and raises deep inside endpoint resolution. The
    conversion is one expression, and it was still the first line of all seven
    copies.
    """
    return endpoint or None


def _addressing_config(endpoint_url: str | None) -> BotoConfig | None:
    """Force path-style addressing when talking to a custom endpoint.

    Returns ``None`` for real AWS so botocore keeps its own default
    (virtual-host), which is the style AWS actually supports. See the module
    docstring for why the two pre-sign call sites made this worth centralising.
    """
    if endpoint_url is None:
        return None
    return BotoConfig(s3={"addressing_style": "path"})


# Signature Version 4, always. botocore still pre-signs S3 URLs with the legacy
# SigV2 scheme unless told otherwise, and Cloudflare R2 and Backblaze B2 accept
# only SigV4 — a SigV2 link to either answers 403 while MinIO, locally, serves
# it happily. Found by PH4-A4's document downloads; every pre-signed link on the
# platform (CV previews, scorecard PDFs) takes the same path.
_SIGV4 = BotoConfig(signature_version="s3v4")


def _client_config(endpoint_url: str | None) -> BotoConfig:
    """SigV4 everywhere, plus path-style addressing for a custom endpoint."""
    addressing = _addressing_config(endpoint_url)
    return _SIGV4 if addressing is None else _SIGV4.merge(addressing)


@asynccontextmanager
async def s3_client(
    *,
    endpoint: str | None,
    region: str,
    access_key: str | None,
    secret_key: str | None,
    use_ssl: bool = True,
) -> AsyncIterator[Any]:
    """Yield a configured async S3 client for AWS S3, Cloudflare R2 or MinIO.

    No network I/O happens on entry *as long as credentials are supplied*.
    Client construction is local (service model JSON off disk); it is the
    credential chain that can reach the network, and that is only engaged when
    *access_key* / *secret_key* are empty. See the module docstring.

    Args:
        endpoint: Custom endpoint URL, or ``""``/``None`` for real AWS S3.
            A custom endpoint switches addressing to path-style.
        region: AWS region name. ``"auto"`` for Cloudflare R2.
        access_key: Access key ID; ``""``/``None`` defers to botocore's chain.
        secret_key: Secret access key; same fallback.
        use_ssl: Scheme for the endpoint botocore derives when *endpoint* is
            empty. Ignored when *endpoint* carries its own scheme, which it
            always does in practice. Defaults True so the two services with no
            ``s3_use_ssl`` setting are not silently downgraded to plaintext.

    Yields:
        An active aioboto3 S3 client. Typed ``Any`` because aioboto3 ships no
        stubs; the call sites already treat it that way.
    """
    endpoint_url = _resolve_endpoint(endpoint)
    loop = asyncio.get_running_loop()
    key = (loop, endpoint_url, region, access_key, secret_key, use_ssl)

    client = _clients.get(key)
    if client is None:
        # One lock per loop, so two concurrent first-callers build one client
        # rather than two. Re-checked inside the lock because the loser of the
        # race must use the winner's.
        lock = _locks.setdefault(loop, asyncio.Lock())
        async with lock:
            client = _clients.get(key)
            if client is None:
                _prune_closed_loops()
                session = aioboto3.Session(
                    aws_access_key_id=access_key or None,
                    aws_secret_access_key=secret_key or None,
                    region_name=region,
                )
                stack = AsyncExitStack()
                client = await stack.enter_async_context(
                    session.client(
                        "s3",
                        endpoint_url=endpoint_url,
                        use_ssl=use_ssl,
                        config=_client_config(endpoint_url),
                    )
                )
                _clients[key] = client
                _stacks[key] = stack

    # NOT closed on the way out — that is the whole point. The contract stays
    # an async context manager so no call site changes, but the client outlives
    # the `async with`; `aclose_s3_clients` owns its lifetime.
    yield client
