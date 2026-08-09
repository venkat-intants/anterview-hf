"""The one async SQLAlchemy engine factory, shared by all four services.

Why this module exists
----------------------
``init_engine()`` existed four times — ``services/data_gateway/app/database.py``,
``services/interview_core/app/database.py``,
``services/feedback_billing/app/database.py``,
``services/admin_ops/app/database.py`` — and an AST diff of the four on
2026-08-07 found them **logically identical**: same three-way branch, same
``-pooler`` substring test, same ``max_overflow=5``, same ``pool_recycle=280``.
The only textual difference is that two of the four carry docstrings.

That is the finding (XS-08), not a reassurance. The four are identical *today*
because someone hand-propagated the last change to all four; ``admin_ops``'s own
docstring records that it had previously failed to keep up and ended up
"handshaking per query". A rule that is only upheld by remembering to paste into
four files is a rule that will be broken, and the breakage is invisible in a
diff — nothing in a one-service patch shows you the other three.

The three branches, and why each exists
---------------------------------------
``DATABASE_SSL`` set + a ``-pooler`` host  ->  ``NullPool``
    A pgBouncer endpoint already pools server-side. Pooling on top of it means
    SQLAlchemy hands back a connection that pgBouncer has since re-assigned to
    another client, and ``statement_cache_size=0`` is what stops asyncpg from
    issuing named prepared statements that pgBouncer's transaction mode rejects
    with "prepared statement ... does not exist". Both settings are one defence:
    either alone still fails.

``DATABASE_SSL`` set + a DIRECT host  ->  ``QueuePool``
    No server-side pooler, so the client-side pool is the only one there is.
    Without it every request pays a fresh TLS + auth handshake (~1s+ over the
    WAN when the database is in a far region) — the cause of the multi-second
    page loads this branch was added to fix. asyncpg's prepared-statement cache
    is deliberately left ON here: a pooled connection survives across requests,
    so a repeated query costs one round trip instead of prepare+execute.
    ``pool_recycle`` is set on THIS branch only, because it defends against a
    specific cloud behaviour — the provider suspending an idle instance and
    dropping connections — that local Postgres does not have.

``DATABASE_SSL`` blank  ->  ``QueuePool``, no ``connect_args``
    Local Postgres over a loopback socket. No TLS, no pooler, and no idle
    autosuspend to recycle around.

``pool_pre_ping`` is on in all three: it costs one cheap round trip at checkout
and converts "the peer closed this socket while it sat in the pool" from a
mid-command error into a transparent reconnect.

Why primitives rather than a ``Settings`` object
------------------------------------------------
Not because the field names diverge — checked on 2026-08-07, all four spell
these ``database_url`` / ``database_ssl`` / ``database_pool_size`` identically;
the DEP-ROOT naming divergence is in the S3 fields, not these. The reason is
the direction of the dependency: a helper typed against ``Settings`` would make
``shared/`` import from ``services/``, which the four service images cannot
satisfy, and it would freeze today's agreement on names into a hard
requirement. ``shared/s3.py`` takes primitives for the same reason. Only the
DEFAULT of ``database_pool_size`` differs by service (10 in data_gateway and
interview_core, 5 in feedback_billing and admin_ops) — deliberate per-service
sizing, which is exactly why it is a parameter here and not a constant.

Dependency rule
---------------
``shared/`` is COPY'd into all four service images, so it may import only
stdlib + pydantic + structlog and nothing from ``services/`` or ``app.*``.
SQLAlchemy is imported at MODULE level here because all four services already
pin it identically (``SQLAlchemy==2.0.50``, alongside ``asyncpg==0.30.0`` and
``greenlet==3.5.1``), so this adds nothing to any requirements file and a lazy
import would buy nothing. ``shared/tests/test_db_engine.py`` asserts the
allowlist mechanically.

This module performs no I/O. SQLAlchemy pools are lazy — the first socket opens
on the first checkout — so ``build_engine`` is safe to call at startup, and safe
to call in a test with no database running.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

__all__ = [
    "ECHO_SQL",
    "MAX_OVERFLOW",
    "POOLER_HOST_MARKER",
    "POOL_RECYCLE_SECONDS",
    "build_engine",
    "build_session_factory",
    "is_pooler_host",
]

# Neon, Supabase and Prisma Postgres all expose their pgBouncer endpoint as the
# direct host with "-pooler" spliced into it (``ep-x-123-pooler.region.aws...``).
# There is no protocol-level way to ask a Postgres endpoint "are you a pooler?",
# so the hostname is the only signal available before the first connection.
POOLER_HOST_MARKER = "-pooler"

# Headroom above pool_size for request spikes. Small on purpose: overflow
# connections are opened on demand and closed on return, so each one pays the
# full handshake this pool exists to avoid.
MAX_OVERFLOW = 5

# Recycle below the ~300s idle window after which Neon/Prisma suspend an idle
# compute and drop its connections. Recycling first means we discard the socket
# on our own terms instead of discovering it is dead at checkout.
POOL_RECYCLE_SECONDS = 280

# Passed explicitly on every branch rather than left to SQLAlchemy's default,
# because what is being pinned is a DPDP control and not a preference:
# echo=True writes every statement AND its bound parameters to the logs, and
# this platform's parameters are candidate names, emails and transcripts. One
# named constant means a reviewer can see in one place that SQL logging is off
# for all four services, and that turning it on is a deliberate edit here.
ECHO_SQL = False


def is_pooler_host(database_url: str) -> bool:
    """True when *database_url* points at a pgBouncer (server-side pooled) host.

    The URL is parsed rather than substring-searched: ``-pooler`` can legally
    appear in a password, a database name or a query parameter, and a plain
    ``"-pooler" in url`` would then route a direct endpoint down the NullPool
    branch and silently give it a fresh handshake per request. A URL with no
    parseable host answers False, so an unusual DSN (a unix-socket DSN, say)
    degrades to the client-side QueuePool rather than raising at startup.
    """
    return POOLER_HOST_MARKER in (urlsplit(database_url).hostname or "")


def build_engine(
    *,
    database_url: str,
    database_ssl: str,
    pool_size: int,
) -> AsyncEngine:
    """Return the async engine configured for whichever endpoint *database_url* names.

    See the module docstring for the reasoning behind each of the three
    branches. No network I/O happens in this call.

    Args:
        database_url: A ``postgresql+asyncpg://`` DSN. The hostname decides
            between the pgBouncer and direct branches.
        database_ssl: The asyncpg ``ssl`` argument, typically ``"require"``.
            Empty means local Postgres with no TLS, which selects the third
            branch. Services normalise their own sentinels (data_gateway's
            ``loopback-exempt``) to ``""`` before calling.
        pool_size: Client-side pool size. Ignored on the pgBouncer branch,
            where NullPool holds nothing.

    Returns:
        An ``AsyncEngine`` with no connections open.
    """
    if not database_ssl:
        return create_async_engine(
            database_url,
            pool_size=pool_size,
            pool_pre_ping=True,
            echo=ECHO_SQL,
        )

    if is_pooler_host(database_url):
        return create_async_engine(
            database_url,
            connect_args={"ssl": database_ssl, "statement_cache_size": 0},
            poolclass=NullPool,
            pool_pre_ping=True,
            echo=ECHO_SQL,
        )

    return create_async_engine(
        database_url,
        connect_args={"ssl": database_ssl},
        pool_size=pool_size,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        echo=ECHO_SQL,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return the sessionmaker every service builds identically on top of the engine.

    ``expire_on_commit=False`` is load-bearing under asyncio, not a style
    choice: with the default True, touching any attribute of an ORM object
    after ``commit()`` triggers a lazy refresh, and a lazy refresh from async
    code raises ``MissingGreenlet`` instead of doing I/O. Every service already
    passes it; centralising it means a new service cannot forget.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
