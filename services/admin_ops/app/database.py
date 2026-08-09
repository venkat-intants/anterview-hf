"""Async SQLAlchemy engine + session factory for admin_ops.

The three-way pool choice (pgBouncer -> NullPool, direct+SSL -> QueuePool with
pool_recycle, local -> plain QueuePool) and the reasoning behind each branch now
live once in ``shared/db/engine.py``. This module used to carry its own copy of
that branch, as did the other three services; the copies were logically
identical and stayed that way only because someone remembered to paste each
change into four files. This one had already failed that once — its previous
docstring records it "handshaking per query" — which is why it is a shared
module now rather than a comment asking people to be careful.

What remains here is the per-service part: reading this service's Settings, and
owning the module-level engine/factory singletons and their lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from shared.db.engine import build_engine, build_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
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
