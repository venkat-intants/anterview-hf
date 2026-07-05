"""Async SQLAlchemy engine + session factory for interview_core.

Mirrors data_gateway/app/database.py. The engine and sessionmaker are
initialised once at startup and torn down at shutdown.  Use
get_db_session() as a FastAPI dependency.

Cloud / pgBouncer note:
  When DATABASE_SSL=require is set, the engine is created with:
    - connect_args={"ssl": "require", "statement_cache_size": 0}
    - poolclass=NullPool   (pgBouncer already pools server-side)
  Leave DATABASE_SSL blank for local Postgres (no SSL, QueuePool is fine).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings

# ---------------------------------------------------------------------------
# Module-level singletons — set in startup, cleared in shutdown
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
    """Create the async engine. Call once at application startup."""
    global _engine, _session_factory

    if settings.database_ssl:
        # Cloud Postgres over SSL. The pool choice depends on the endpoint:
        host = urlsplit(settings.database_url).hostname or ""
        if "-pooler" in host:
            # pgBouncer POOLED endpoint: NullPool so SQLAlchemy doesn't pool on
            # top of pgBouncer, and statement_cache_size=0 because pgBouncer
            # transaction mode rejects named prepared statements.
            _engine = create_async_engine(
                settings.database_url,
                connect_args={"ssl": settings.database_ssl, "statement_cache_size": 0},
                poolclass=NullPool,
                pool_pre_ping=True,
                echo=False,
            )
        else:
            # DIRECT endpoint (no server-side pooler): keep a real client-side
            # pool so connections are REUSED across requests instead of paying a
            # full TLS + auth handshake (~1s+ over the WAN) on EVERY request — the
            # cause of multi-second page loads when the DB is in a far region.
            # Leave asyncpg's prepared-statement cache ON (default): a real session
            # caches statements per pooled connection, so a repeated query costs ONE
            # round-trip instead of prepare+execute. pool_pre_ping + pool_recycle
            # survive the provider's idle autosuspend dropping connections.
            _engine = create_async_engine(
                settings.database_url,
                connect_args={"ssl": settings.database_ssl},
                pool_size=settings.database_pool_size,
                max_overflow=5,
                pool_pre_ping=True,
                pool_recycle=280,
                echo=False,
            )
    else:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            pool_pre_ping=True,
            echo=False,
        )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


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
