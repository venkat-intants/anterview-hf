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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

__all__ = ["s3_client"]


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

    session = aioboto3.Session(
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        region_name=region,
    )
    async with session.client(
        "s3",
        endpoint_url=endpoint_url,
        use_ssl=use_ssl,
        config=_addressing_config(endpoint_url),
    ) as client:
        yield client
