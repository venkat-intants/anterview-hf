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
    """Start and finish with no engine on the module.

    Disposing on the way IN matters now that ``init_engine()`` is idempotent:
    an engine left behind by anything earlier in the suite would be returned
    unchanged, and these tests would then assert the pool class of some other
    test's DSN instead of the one they just monkeypatched.
    """
    await database_mod.dispose_engine()
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


# ---------------------------------------------------------------------------
# Idempotence
#
# ``build_engine`` returns a NEW engine with a NEW pool on every call. The
# worker's DB helpers call ``init_engine()`` before every single operation, so
# without idempotence each persisted turn, status read and consent lookup would
# orphan the previous engine — undisposed, its pooled sockets held open until
# the GC finalised them — and a worker process that lives for days would climb
# towards the database's max_connections.
# ---------------------------------------------------------------------------


async def test_init_engine_twice_reuses_the_same_engine(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """A second ``init_engine()`` must return the first engine, not build another."""
    first = _engine(DIRECT_URL, "require", monkeypatch)
    first_factory = database_mod.get_session_factory()

    database_mod.init_engine()

    assert database_mod._engine is first
    # The sessionmaker is rebuilt alongside the engine, so it must not be
    # replaced either — a swapped factory would strand any session in flight.
    assert database_mod.get_session_factory() is first_factory


async def test_worker_call_pattern_does_not_accumulate_engines(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """The worker's per-operation defensive call must build exactly one engine.

    Counting ``build_engine`` calls rather than comparing identities is the
    assertion that actually fails if someone reinstates the unconditional
    rebind: identity would still hold for the LAST engine built.
    """
    builds: list[int] = []
    real_build = database_mod.build_engine

    def _counting_build(
        *, database_url: str, database_ssl: str, pool_size: int
    ) -> AsyncEngine:
        builds.append(1)
        return real_build(
            database_url=database_url,
            database_ssl=database_ssl,
            pool_size=pool_size,
        )

    monkeypatch.setattr(settings, "database_url", DIRECT_URL)
    monkeypatch.setattr(settings, "database_ssl", "require")
    monkeypatch.setattr(database_mod, "build_engine", _counting_build)

    # Five helpers firing on one interview's close path.
    for _ in range(5):
        database_mod.init_engine()

    assert len(builds) == 1


async def test_dispose_then_init_builds_a_fresh_engine(
    monkeypatch: pytest.MonkeyPatch, clean_engine: None
) -> None:
    """Disposal is the supported way to swap engines — the lifespan relies on it.

    A shutdown/startup cycle (or a test changing the DSN) must get an engine
    built from the CURRENT settings, otherwise idempotence would pin the process
    to whatever engine it first built.
    """
    first = _engine(POOLED_URL, "require", monkeypatch)
    assert isinstance(first.pool, NullPool)

    await database_mod.dispose_engine()
    # Disposal clears the sessionmaker too: a factory bound to a disposed engine
    # would hand out sessions that fail at their first query.
    with pytest.raises(RuntimeError, match="not initialised"):
        database_mod.get_session_factory()

    second = _engine(LOCAL_URL, "", monkeypatch)
    assert second is not first
    assert isinstance(second.pool, QueuePool)
