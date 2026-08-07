"""Async SQLAlchemy engine + session factory for feedback_billing.

The engine construction itself lives in ``shared/db/engine.py`` (XS-08). It used
to live here, in a block that was byte-for-byte identical to the same block in
the other three services — the pgBouncer/direct/plain branch split, the
``-pooler`` host test, ``max_overflow=5``, ``pool_recycle=280``. Four copies stay
identical only for as long as someone remembers to paste into all four, and the
failure is invisible in review: nothing in a one-service diff shows you the
other three. ``admin_ops``' own docstring recorded that it had already fallen
behind once and ended up handshaking per query.

What stays here is what is genuinely per-service: the module-level singletons
and their lifecycle, which the FastAPI lifespan and the health check drive. Read
``shared/db/engine.py`` for why each pool branch exists.

Note that ``settings.database_ssl`` has already been through
``validate_database_ssl`` by the time it arrives here, so the ``loopback-exempt``
sentinel is normalised to ``""`` and never reaches asyncpg.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from shared.db.engine import build_engine, build_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
    """Build the process-wide engine and session factory. No I/O — pools are lazy."""
    global _engine, _session_factory

    _engine = build_engine(
        database_url=settings.database_url,
        database_ssl=settings.database_ssl,
        pool_size=settings.database_pool_size,
    )
    _session_factory = build_session_factory(_engine)


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
