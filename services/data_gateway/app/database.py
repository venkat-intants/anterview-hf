"""Async SQLAlchemy engine + session factory for data_gateway.

The engine and sessionmaker are initialised once at startup and torn down
at shutdown. Use get_db_session() as a FastAPI dependency.

The engine CONFIGURATION (the three pool branches, the pgBouncer ``-pooler``
detection, ``statement_cache_size=0``, ``pool_recycle``) lives in
``shared/db/engine.py`` — see that module for why each branch exists. It used to
live here and in three sibling ``app/database.py`` files, byte-for-byte
identical only because someone hand-propagated the last change to all four
(XS-08). This module keeps what is genuinely local: the singletons, their
lifecycle, and the FastAPI dependency.

Cloud / pgBouncer note (unchanged behaviour, now enforced in one place):
  Leave DATABASE_SSL blank for local Postgres; set DATABASE_SSL=require for any
  cloud endpoint. ``config.py`` has already normalised the ``loopback-exempt``
  sentinel to ``""`` by the time we read it, so the value passed below is always
  something asyncpg understands.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings

# ---------------------------------------------------------------------------
# Module-level singletons — set in startup, cleared in shutdown
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
    """Create the async engine. Call once at application startup.

    No network I/O: SQLAlchemy pools are lazy, so the first socket opens on the
    first checkout.
    """
    global _engine, _session_factory

    _engine = build_engine(
        database_url=settings.database_url,
        database_ssl=settings.database_ssl,
        pool_size=settings.database_pool_size,
    )
    _session_factory = build_session_factory(_engine)


async def dispose_engine() -> None:
    """Dispose the async engine. Call at application shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


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


# The annotated alias belongs next to the dependency it wraps (DG-1). Eight
# routers used to import it from app/routers/hr_applicants.py, which made a
# single applicant router the de-facto owner of every other router's DB session
# — and pulled that router's S3, embedding and scoring clients into the import
# graph of candidate-facing endpoints that never touch them. hr_applicants.py
# re-exports this name, so existing call sites are unchanged.
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
