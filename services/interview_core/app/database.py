"""Async SQLAlchemy engine + session factory for interview_core.

The engine and sessionmaker are initialised once at startup and torn down at
shutdown. Use ``get_db_session()`` as a FastAPI dependency. The LiveKit worker
runs in its own process and calls ``init_engine()`` itself (see
``app/worker/interview_worker.py``), which is why the singletons live at module
scope rather than on ``app.state``. ``init_engine()`` is idempotent so that the
worker's per-operation defensive call reuses one engine instead of stacking up
undisposed pools — see its docstring.

The engine CONFIGURATION (the three pool branches, the pgBouncer ``-pooler``
detection, ``statement_cache_size=0``, ``pool_recycle``) lives in
``shared/db/engine.py`` — see that module for why each branch exists. It used to
live here and in three sibling ``app/database.py`` files, identical only because
someone hand-propagated the last change to all four (XS-08); the breakage from
missing one is invisible in a diff, because nothing in a one-service patch shows
you the other three. This module keeps what is genuinely local: the singletons,
their lifecycle, and the FastAPI dependency.

Cloud / pgBouncer note (unchanged behaviour, now enforced in one place):
  Leave DATABASE_SSL blank for local Postgres; set DATABASE_SSL=require for any
  cloud endpoint. ``config.py`` has already normalised the ``loopback-exempt``
  sentinel to ``""`` by the time we read it, so the value passed below is always
  something asyncpg understands.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from shared.db.engine import build_engine, build_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings

# ---------------------------------------------------------------------------
# Module-level singletons — set in startup, cleared in shutdown
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
    """Ensure the async engine exists. Safe to call any number of times.

    IDEMPOTENT ON PURPOSE. ``build_engine`` returns a brand-new engine with a
    brand-new pool on every call (``shared/tests/test_db_engine.py``
    ::``test_each_call_gets_its_own_engine`` pins that), and the worker's DB
    helpers call this before every single operation — persist turns, read a
    status, resolve a consent subject. Rebinding ``_engine`` each time would
    drop the previous engine without ``dispose()``, leaving its pooled sockets
    open until the garbage collector happened to finalise them. A worker
    process that lives for days would accumulate connections against the same
    database until it hit ``max_connections``. Returning the existing engine
    makes the defensive call cost a pointer comparison.

    The check-then-set needs no lock: there is no ``await`` between the two, so
    within one event loop it cannot interleave, and each worker process has its
    own module state.

    Rebuilding is still reachable, and deliberately so — ``dispose_engine()``
    clears the singletons, so a shutdown/startup cycle (the FastAPI lifespan,
    or a test that swaps ``settings.database_url``) builds a fresh engine from
    the current settings. Anything that wants a *different* engine must dispose
    first; that is the only supported way to swap one.

    No network I/O: SQLAlchemy pools are lazy, so the first socket opens on the
    first checkout.
    """
    global _engine, _session_factory

    if _engine is not None:
        return

    _engine = build_engine(
        database_url=settings.database_url,
        database_ssl=settings.database_ssl,
        pool_size=settings.database_pool_size,
    )
    _session_factory = build_session_factory(_engine)


async def dispose_engine() -> None:
    """Dispose the async engine. Call at application shutdown.

    Clears the sessionmaker as well as the engine. The two are one unit: a
    factory left behind after disposal is bound to a dead engine and would hand
    out sessions that fail at their first query, and ``init_engine()`` keys its
    idempotence off ``_engine`` — leaving a stale factory in place would let the
    pair disagree about whether the module is initialised.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the sessionmaker. Raises if not initialised."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a single-request DB session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
