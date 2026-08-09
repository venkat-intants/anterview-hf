"""XS-08: the pool branch survived the move to ``shared/db/engine.py``.

``init_engine()`` existed four times, identical only because someone
hand-propagated the last change to all four. It now delegates to
``shared.db.engine.build_engine``. The shared module has its own tests
(``shared/tests/test_db_engine.py``, including the ``statement_cache_size``
half of the pgBouncer defence); these pin the thing a shared module cannot pin
for us — that THIS service still ends up with the same pool for the same DSN as
it did when it built its own engine.

The pool class is asserted straight off the engine, which needs no database:
SQLAlchemy builds the pool eagerly at ``create_async_engine()`` and opens
sockets lazily.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool, QueuePool

from app import database as database_mod
from app.config import settings

# Neon/Prisma mark their pgBouncer endpoint by putting "-pooler" in the
# hostname; the direct endpoint is the same host without it. Both point nowhere.
POOLED_URL = "postgresql+asyncpg://u:p@ep-demo-a1b2-pooler.ap-south-1.aws.neon.tech/db"
DIRECT_URL = "postgresql+asyncpg://u:p@ep-demo-a1b2.ap-south-1.aws.neon.tech/db"
LOCAL_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


@pytest_asyncio.fixture()
async def clean_engine() -> AsyncGenerator[None, None]:
    """Dispose whatever engine the test built so it cannot leak into the suite."""
    yield
    await database_mod.dispose_engine()


def _engine(url: str, ssl: str, monkeypatch: pytest.MonkeyPatch) -> AsyncEngine:
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "database_ssl", ssl)
    database_mod.init_engine()
    # The engine is a module singleton with no public accessor; the session
    # factory only exposes it indirectly, so reach for it here.
    engine = database_mod._engine
    assert engine is not None
    return engine


async def test_pooler_host_uses_nullpool(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """pgBouncer already pools server-side; pooling on top of it hands back a
    connection pgBouncer has since reassigned to another client."""
    assert isinstance(_engine(POOLED_URL, "require", monkeypatch).pool, NullPool)


async def test_direct_ssl_host_keeps_a_real_pool(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """A direct endpoint must REUSE connections: without a client-side pool
    every request pays a fresh TLS + auth handshake (~1s+ over the WAN)."""
    assert isinstance(_engine(DIRECT_URL, "require", monkeypatch).pool, QueuePool)


async def test_direct_ssl_pool_sized_from_settings(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """A "real pool" of size 0 would reuse nothing — assert it is actually
    sized from DATABASE_POOL_SIZE and not left at some default."""
    monkeypatch.setattr(settings, "database_pool_size", 7)
    pool = _engine(DIRECT_URL, "require", monkeypatch).pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == 7


async def test_local_no_ssl_uses_a_real_pool(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """Local Postgres has no pooler in front of it either."""
    assert isinstance(_engine(LOCAL_URL, "", monkeypatch).pool, QueuePool)


async def test_session_factory_refuses_before_init(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """The worker calls ``init_engine()`` defensively before every DB helper.

    If the factory ever returned something usable before initialisation, that
    defensive call would become optional and the next helper to skip it would
    fail at a random first query instead of at startup.
    """
    monkeypatch.setattr(database_mod, "_session_factory", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        database_mod.get_session_factory()
