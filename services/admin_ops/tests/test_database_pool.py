"""SH-3: admin_ops must choose its connection pool the same way its siblings do.

admin_ops branched two ways (SSL -> NullPool, else QueuePool), so against a
DIRECT Neon endpoint every analytics query paid a fresh TCP connect plus a full
TLS handshake while data_gateway / feedback_billing / interview_core reused a
pooled connection. These tests pin the three-way branch: the pool class is
asserted straight off the engine, which needs no database — SQLAlchemy builds
the pool eagerly at create_async_engine() and opens sockets lazily.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.pool import NullPool, QueuePool

from app import database as database_mod
from app.config import settings

# Neon marks its pgBouncer endpoint by putting "-pooler" in the hostname; the
# direct endpoint is the same host without it. Both point nowhere.
POOLED_URL = "postgresql+asyncpg://u:p@ep-demo-a1b2-pooler.ap-south-1.aws.neon.tech/db"
DIRECT_URL = "postgresql+asyncpg://u:p@ep-demo-a1b2.ap-south-1.aws.neon.tech/db"
LOCAL_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


@pytest_asyncio.fixture()
async def clean_engine() -> AsyncGenerator[None, None]:
    """Dispose whatever engine the test built so it cannot leak into the suite."""
    yield
    await database_mod.dispose_engine()


def _pool(url: str, ssl: str, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "database_ssl", ssl)
    database_mod.init_engine()
    # The engine is a module singleton with no public accessor; the session
    # factory only exposes it indirectly, so reach for it here.
    engine = database_mod._engine
    assert engine is not None
    return engine.pool


async def test_pooler_host_uses_nullpool(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """pgBouncer already pools server-side — pooling on top of it breaks
    prepared statements in transaction mode."""
    assert isinstance(_pool(POOLED_URL, "require", monkeypatch), NullPool)


async def test_direct_ssl_host_keeps_a_real_pool(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """The regression this whole finding is about: a direct endpoint must reuse
    connections rather than re-handshake TLS on every analytics query."""
    pool = _pool(DIRECT_URL, "require", monkeypatch)
    assert not isinstance(pool, NullPool)
    assert isinstance(pool, QueuePool)


async def test_direct_ssl_pool_sized_from_settings(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """A "real pool" of size 0 would reuse nothing, so assert it is actually
    sized from DATABASE_POOL_SIZE and not left at some default."""
    monkeypatch.setattr(settings, "database_pool_size", 7)
    pool = _pool(DIRECT_URL, "require", monkeypatch)
    assert isinstance(pool, QueuePool)
    assert pool.size() == 7


async def test_local_no_ssl_uses_a_real_pool(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """Local Postgres has no pooler in front of it either."""
    pool = _pool(LOCAL_URL, "", monkeypatch)
    assert not isinstance(pool, NullPool)
    assert isinstance(pool, QueuePool)
