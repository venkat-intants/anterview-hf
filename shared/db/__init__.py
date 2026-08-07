"""Database plumbing shared by all four services.

Today that is one factory: :func:`build_engine`, the single async SQLAlchemy
engine constructor. It exists because the pgBouncer-vs-direct pool branch had
been written once per service and had to be hand-propagated to four files every
time it changed — see ``shared/db/engine.py`` for the full reasoning and for
what each branch defends against.

Typical use, in a service's ``app/database.py``::

    from shared.db import build_engine, build_session_factory

    def init_engine() -> None:
        global _engine, _session_factory
        _engine = build_engine(
            database_url=settings.database_url,
            database_ssl=settings.database_ssl,
            pool_size=settings.database_pool_size,   # per-service default
        )
        _session_factory = build_session_factory(_engine)

The engine/sessionmaker lifecycle (the module-level singletons, ``dispose`` at
shutdown, and the ``get_db_session`` FastAPI dependency) deliberately stays in
each service: it is bound to that service's app lifespan and, in data_gateway's
case, to a ``DbSessionDep`` annotation that eight routers import.
"""

from shared.db.engine import (
    ECHO_SQL,
    MAX_OVERFLOW,
    POOL_RECYCLE_SECONDS,
    POOLER_HOST_MARKER,
    build_engine,
    build_session_factory,
    is_pooler_host,
)

__all__ = [
    "ECHO_SQL",
    "MAX_OVERFLOW",
    "POOLER_HOST_MARKER",
    "POOL_RECYCLE_SECONDS",
    "build_engine",
    "build_session_factory",
    "is_pooler_host",
]
