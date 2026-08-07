"""XS-08: this service builds its engine from shared/db/engine.py.

``init_engine`` used to carry its own copy of the three-branch pool logic,
identical to the copy in each of the other three services. Identical only for as
long as someone remembers to paste into all four — and the drift is invisible in
review, because a one-service diff never shows you the other three. admin_ops
had already fallen behind once and ended up paying a TLS handshake per query.

The branches themselves are tested in shared/tests/test_db_engine.py. What is
tested here is the seam: that this service's settings reach the shared factory
unaltered, and that the pgBouncer branch really does come out the other side.
Both are things a well-meaning "just inline it back" edit would break.

No database is contacted. SQLAlchemy pools are lazy — the first socket opens on
the first checkout — so building an engine against a fictional DSN is free.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

import app.database as database
from app.config import settings

_POOLER_URL = "postgresql+asyncpg://u:p@ep-x-123-pooler.ap-south-1.aws.neon.tech/db"
_DIRECT_URL = "postgresql+asyncpg://u:p@ep-x-123.ap-south-1.aws.neon.tech/db"


@pytest.fixture()
def isolated_engine() -> Iterator[None]:
    """Restore the module singletons after each test.

    ``init_engine`` writes process globals that the health check and the score
    router read. Leaving a test's engine behind would make an unrelated later
    test's failure depend on collection order.
    """
    saved_engine = database._engine
    saved_factory = database._session_factory
    try:
        yield
    finally:
        database._engine = saved_engine
        database._session_factory = saved_factory


def test_init_engine_delegates_to_the_shared_factory(
    monkeypatch: pytest.MonkeyPatch, isolated_engine: None
) -> None:
    """The service's own settings, passed through by keyword, unmodified.

    Pinned by name because the shared signature is keyword-only: a positional
    reordering there is meant to be a loud failure here, not a silently
    mismatched pool size.
    """
    captured: dict[str, Any] = {}

    def _fake_build_engine(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(database, "build_engine", _fake_build_engine)
    monkeypatch.setattr(database, "build_session_factory", lambda engine: object())
    monkeypatch.setattr(settings, "database_url", _DIRECT_URL)
    monkeypatch.setattr(settings, "database_ssl", "require")
    monkeypatch.setattr(settings, "database_pool_size", 5)

    database.init_engine()

    assert captured == {
        "database_url": _DIRECT_URL,
        "database_ssl": "require",
        "pool_size": 5,
    }


def test_pooler_endpoint_gets_nullpool_end_to_end(
    monkeypatch: pytest.MonkeyPatch, isolated_engine: None
) -> None:
    """One pass through the REAL shared factory, not the stub above.

    The delegation test proves the arguments leave here correctly; on its own it
    would still pass if ``shared.db.engine`` were imported from somewhere that
    no longer built a Neon-safe engine. This asserts a property of the object
    that actually comes back, on the branch where getting it wrong hurts most:
    pooling on top of pgBouncer hands back connections it has since reassigned.

    Only the pool class is asserted here. ``connect_args`` (the
    ``statement_cache_size=0`` half of the same defence) is not reachable from a
    built engine without reading SQLAlchemy internals, and
    shared/tests/test_db_engine.py already covers it properly.
    """
    monkeypatch.setattr(settings, "database_url", _POOLER_URL)
    monkeypatch.setattr(settings, "database_ssl", "require")

    database.init_engine()

    engine = database._engine
    assert isinstance(engine, AsyncEngine)
    assert isinstance(engine.pool, NullPool)


def test_session_factory_does_not_expire_on_commit(
    monkeypatch: pytest.MonkeyPatch, isolated_engine: None
) -> None:
    """Load-bearing under asyncio, not a style choice.

    With the default ``expire_on_commit=True``, touching any attribute of an ORM
    object after commit triggers a lazy refresh, and a lazy refresh from async
    code raises MissingGreenlet instead of doing I/O — a crash on the scorecard
    write path, after the row is already committed.
    """
    monkeypatch.setattr(settings, "database_url", _DIRECT_URL)
    monkeypatch.setattr(settings, "database_ssl", "")

    database.init_engine()

    assert database.get_session_factory().kw["expire_on_commit"] is False


def test_get_session_factory_raises_before_init(isolated_engine: None) -> None:
    """The score router treats this RuntimeError as 'no DB configured' and
    degrades instead of 500ing, so the exception type is part of the contract."""
    database._engine = None
    database._session_factory = None

    with pytest.raises(RuntimeError, match="init_engine"):
        database.get_session_factory()
