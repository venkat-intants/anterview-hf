"""Stable entry point for interview_core (B-036).

Why this exists
---------------
On Windows, uvicorn 0.32.x only switches to the asyncio SelectorEventLoop when
it runs with a reload/worker *subprocess* — see
``uvicorn.loops.asyncio.asyncio_setup``, which sets
``WindowsSelectorEventLoopPolicy`` *only* when ``use_subprocess=True``. In plain
single-process mode (``uvicorn app.main:app`` with no ``--reload``) it leaves
Python's default Windows **ProactorEventLoop**, whose IOCP accept path raises::

    OSError: [WinError 64] The specified network name is no longer available

whenever a client connection is reset mid-accept (e.g. a health-check probe that
closes early). That error has been crashing the dev server.

uvicorn chooses its loop *before* it imports the ASGI app, so setting the policy
inside ``app.main`` would be too late. We pin the SelectorEventLoop here, before
``uvicorn.run`` creates the loop. For single-process runs uvicorn's
``asyncio_setup`` is a no-op and leaves our policy in place; for reload/worker
runs uvicorn sets the SelectorEventLoop itself — so the loop is correct either
way. The guard is ``sys.platform == "win32"`` only, so Linux/EKS production keeps
its normal (epoll) loop and is completely unaffected.

Run with:  poetry run python run.py
"""

from __future__ import annotations

import asyncio
import sys

# MUST run before uvicorn is imported / creates its event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402  (import after the loop policy is set, by design)

from app.config import settings  # noqa: E402


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
