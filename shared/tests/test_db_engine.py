"""Regression suite for ``shared.db.engine``.

The bug this guards: the pgBouncer-vs-direct pool branch lived in four
hand-copied ``app/database.py`` files. An AST diff on 2026-08-07 found all four
logically identical, but ``admin_ops``'s own docstring records the pass where
that was not true and the service ended up paying a TLS handshake per query.
Neither failure mode is visible in a diff or a log line — one shows up as
"prepared statement does not exist" under load, the other as "the dashboard is
slow" — so they are pinned here instead.

No test opens a connection. SQLAlchemy pools are lazy: the first socket is
opened at the first checkout, so a fully configured engine can be built and
inspected against a host that does not resolve. That is also the property that
makes ``build_engine`` safe to call at startup, and it is asserted below in
``test_building_an_engine_opens_no_connection`` rather than assumed.

Why the DBAPI is stubbed when asyncpg is absent
-----------------------------------------------
``create_async_engine`` imports the driver named in the URL while building the
engine (verified: blocking ``asyncpg`` on ``sys.meta_path`` makes the call raise
``ImportError`` before any pool is chosen). All four services pin
``asyncpg==0.30.0``, but the CI ``shared`` job installs only what shared/ needs
and does not have it. Skipping there would put this suite in exactly the state
``.github/workflows/ci.yml`` complains about — "existed and passed locally while
never running in CI" — so the fixture below substitutes a minimal DBAPI object
instead. It substitutes ONLY the driver module: the URL, the dialect, the pool
selection and every argument under test are the real ones, and when asyncpg IS
installed (every service venv) the fixture does nothing at all.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import types
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import asyncpg as sa_asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import NullPool, QueuePool

from shared.db.engine import (
    ECHO_SQL,
    MAX_OVERFLOW,
    POOL_RECYCLE_SECONDS,
    build_engine,
    build_session_factory,
    is_pooler_host,
)

# Neither host is ever contacted; they only exercise the two endpoint shapes a
# managed Postgres provider hands out. The "-pooler" infix is how Neon, Prisma
# and Supabase all spell their pgBouncer endpoint.
_POOLER_URL = "postgresql+asyncpg://u:p@ep-cool-sun-a1b2c3-pooler.ap-south-1.aws.neon.tech/db"
_DIRECT_URL = "postgresql+asyncpg://u:p@ep-cool-sun-a1b2c3.ap-south-1.aws.neon.tech/db"
_LOCAL_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/interview"

def _asyncpg_installed() -> bool:
    """Probe the driver the same way SQLAlchemy will — by importing it.

    ``find_spec`` would answer a different question: a spec can exist for a
    package whose import then fails, and it is the import that
    ``create_async_engine`` performs.
    """
    try:
        importlib.import_module("asyncpg")
    except ImportError:
        return False
    return True


_ASYNCPG_INSTALLED = _asyncpg_installed()


@pytest.fixture(autouse=True)
def _dbapi_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the asyncpg dialect something to import when asyncpg is absent.

    See the module docstring. ``monkeypatch`` restores the real classmethod
    after every test, so nothing leaks into the rest of the shared suite.
    """
    if _ASYNCPG_INSTALLED:
        return
    stub = types.SimpleNamespace(paramstyle="format", Error=Exception)
    monkeypatch.setattr(
        sa_asyncpg.PGDialect_asyncpg,
        "import_dbapi",
        classmethod(lambda cls: stub),
    )


def _connect_params(engine: AsyncEngine) -> dict[str, Any]:
    """Return the arguments the engine will actually hand the driver.

    SQLAlchemy merges ``connect_args`` into the parameters parsed out of the
    URL and captures the result in the pool's creator closure; there is no
    public attribute holding it. Reading the closure is therefore the only way
    to assert what asyncpg will really receive, rather than the weaker "we
    passed the right kwargs to create_async_engine". ``test_connect_params_
    reads_real_arguments`` fails loudly if a SQLAlchemy upgrade changes the
    closure shape, so these assertions cannot quietly decay into no-ops.
    """
    creator = engine.pool._creator
    names = creator.__code__.co_freevars
    cells = creator.__closure__ or ()
    return dict(dict(zip(names, cells, strict=True))["cparams"].cell_contents)


# --------------------------------------------------------------------------
# pgBouncer endpoint — the branch that fails as "prepared statement does not exist"
# --------------------------------------------------------------------------


def test_pgbouncer_endpoint_gets_nullpool() -> None:
    """pgBouncer already pools server-side. A client-side pool on top hands back
    a connection pgBouncer has since re-assigned to someone else."""
    engine = build_engine(database_url=_POOLER_URL, database_ssl="require", pool_size=10)
    assert isinstance(engine.pool, NullPool)


def test_pgbouncer_endpoint_disables_the_prepared_statement_cache() -> None:
    """The other half of the same defence: pgBouncer's transaction mode rejects
    named prepared statements, so asyncpg's cache must be off. NullPool alone
    does not prevent this — the failure is per-statement, not per-connection."""
    engine = build_engine(database_url=_POOLER_URL, database_ssl="require", pool_size=10)
    assert _connect_params(engine)["statement_cache_size"] == 0


def test_pgbouncer_endpoint_still_negotiates_tls() -> None:
    """Choosing NullPool must not cost the TLS the branch was entered for — PII
    travels this connection (DPDP)."""
    engine = build_engine(database_url=_POOLER_URL, database_ssl="require", pool_size=10)
    assert _connect_params(engine)["ssl"] == "require"


def test_pgbouncer_endpoint_ignores_pool_size() -> None:
    """NullPool holds nothing, so pool_size is inert here. Asserted so a reader
    of the signature does not assume the parameter is doing something."""
    small = build_engine(database_url=_POOLER_URL, database_ssl="require", pool_size=1)
    large = build_engine(database_url=_POOLER_URL, database_ssl="require", pool_size=50)
    assert isinstance(small.pool, NullPool)
    assert isinstance(large.pool, NullPool)


# --------------------------------------------------------------------------
# Direct cloud endpoint — the branch that fails as "the dashboard is slow"
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pool_size", [5, 10])
def test_direct_endpoint_keeps_a_real_client_side_pool(pool_size: int) -> None:
    """Both service defaults (5 in feedback_billing/admin_ops, 10 in
    data_gateway/interview_core) must reach the pool. Without a client-side pool
    every request pays a fresh TLS + auth handshake, ~1s+ over the WAN."""
    engine = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=pool_size)
    assert isinstance(engine.pool, QueuePool)
    assert not isinstance(engine.pool, NullPool)
    assert engine.pool.size() == pool_size


def test_direct_endpoint_allows_bounded_overflow() -> None:
    """Headroom for spikes, kept small on purpose: an overflow connection is
    opened on demand and closed on return, paying the handshake the pool
    exists to avoid."""
    engine = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=10)
    assert engine.pool._max_overflow == MAX_OVERFLOW == 5


def test_direct_endpoint_recycles_before_idle_autosuspend() -> None:
    """Neon/Prisma suspend an idle compute at around 300s and drop its
    connections. Recycling at 280s discards the socket on our terms instead of
    discovering it is dead at checkout."""
    engine = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=10)
    assert engine.pool._recycle == POOL_RECYCLE_SECONDS == 280


def test_direct_endpoint_leaves_the_prepared_statement_cache_on() -> None:
    """The inverse of the pgBouncer branch, and the reason the two are not one.
    A pooled connection survives across requests here, so caching turns a
    repeated query into one round trip instead of prepare+execute. Setting
    statement_cache_size=0 on both branches would be a silent latency
    regression that no test other than this one would notice."""
    engine = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=10)
    assert "statement_cache_size" not in _connect_params(engine)


def test_direct_endpoint_negotiates_tls() -> None:
    engine = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=10)
    assert _connect_params(engine)["ssl"] == "require"


# --------------------------------------------------------------------------
# Local Postgres — DATABASE_SSL blank
# --------------------------------------------------------------------------


def test_local_endpoint_uses_a_queue_pool() -> None:
    engine = build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 10


def test_local_endpoint_asks_for_no_tls() -> None:
    """A stray ssl argument here would break every developer's loopback
    Postgres, which is not configured for TLS."""
    assert "ssl" not in _connect_params(build_engine(
        database_url=_LOCAL_URL, database_ssl="", pool_size=10
    ))


def test_local_endpoint_does_not_recycle() -> None:
    """pool_recycle defends against a cloud provider suspending an idle
    compute. Local Postgres does not do that, and recycling anyway would churn
    connections for nothing. -1 is SQLAlchemy's "never"."""
    engine = build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    assert engine.pool._recycle == -1


def test_a_pooler_host_without_ssl_still_takes_the_local_branch() -> None:
    """DATABASE_SSL is checked first in all four services. Reordering the
    branches would send a plaintext pooler URL to NullPool and change the pool
    for a case the services never hit — pinned so the ordering is deliberate."""
    engine = build_engine(database_url=_POOLER_URL, database_ssl="", pool_size=10)
    assert isinstance(engine.pool, QueuePool)


# --------------------------------------------------------------------------
# Pooler detection
# --------------------------------------------------------------------------


def test_pooler_detection_reads_the_host_not_the_whole_url() -> None:
    """The reason this parses the URL instead of running ``"-pooler" in url``:
    the marker can legally appear in a password, a database name or a query
    parameter, and a substring test would then route a DIRECT endpoint to
    NullPool — reinstating the handshake-per-request bug on a service whose
    only sin was its credentials."""
    assert not is_pooler_host("postgresql+asyncpg://u:my-pooler-pw@direct.example.com/db")
    assert not is_pooler_host("postgresql+asyncpg://u:p@direct.example.com/app-pooler")
    assert not is_pooler_host("postgresql+asyncpg://u:p@direct.example.com/db?opt=-pooler")
    assert is_pooler_host(_POOLER_URL)


def test_pooler_detection_survives_a_url_with_no_host() -> None:
    """A DSN with no parseable host (a unix-socket form) must not raise at
    startup; it degrades to the pooled QueuePool path."""
    assert not is_pooler_host("postgresql+asyncpg:///db?host=/var/run/postgresql")


def test_pooler_detection_ignores_host_case() -> None:
    """urlsplit lowercases the hostname, so an operator who pastes the endpoint
    in mixed case still gets the pgBouncer branch rather than a pool on top of
    a pool."""
    assert is_pooler_host("postgresql+asyncpg://u:p@EP-Cool-Sun-POOLER.aws.neon.tech/db")


# --------------------------------------------------------------------------
# Properties every branch shares
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "ssl"),
    [(_POOLER_URL, "require"), (_DIRECT_URL, "require"), (_LOCAL_URL, "")],
    ids=["pgbouncer", "direct", "local"],
)
def test_pre_ping_is_on_in_every_branch(url: str, ssl: str) -> None:
    """One cheap round trip at checkout turns "the peer closed this socket while
    it sat in the pool" from a mid-command error into a transparent reconnect.
    It is the only setting all three branches share, which is exactly why a
    fourth branch added later is likely to be the one that forgets it."""
    engine = build_engine(database_url=url, database_ssl=ssl, pool_size=10)
    assert engine.pool._pre_ping is True


@pytest.mark.parametrize(
    ("url", "ssl"),
    [(_POOLER_URL, "require"), (_DIRECT_URL, "require"), (_LOCAL_URL, "")],
    ids=["pgbouncer", "direct", "local"],
)
def test_sql_echo_is_off_in_every_branch(url: str, ssl: str) -> None:
    """echo=True logs every statement AND its bound parameters — here, candidate
    names, emails and transcripts. This is a DPDP control, not a preference."""
    engine = build_engine(database_url=url, database_ssl=ssl, pool_size=10)
    assert ECHO_SQL is False
    assert engine.sync_engine.echo is False


@pytest.mark.parametrize(
    ("url", "ssl"),
    [(_POOLER_URL, "require"), (_DIRECT_URL, "require"), (_LOCAL_URL, "")],
    ids=["pgbouncer", "direct", "local"],
)
def test_building_an_engine_opens_no_connection(url: str, ssl: str) -> None:
    """The factory must be callable at startup and in tests with no database
    running — which only holds while the pool stays lazy. Neither Neon host
    resolves and nothing listens on the local one, so an eager connect would
    raise here rather than pass."""
    assert isinstance(build_engine(database_url=url, database_ssl=ssl, pool_size=10), AsyncEngine)


def test_a_new_queue_pool_holds_no_connections() -> None:
    """The laziness claim above, checked where a pool can actually hold
    something: QueuePool opens its first socket at the first checkout, not at
    construction. NullPool has no equivalent state to inspect because it holds
    no connections by construction."""
    engine = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=10)
    assert engine.pool.checkedout() == 0
    assert engine.pool.checkedin() == 0


def test_each_call_gets_its_own_engine() -> None:
    """A builder, not a singleton: each service keeps its own init/dispose
    lifecycle, so disposing one engine must not close another's connections."""
    first = build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    second = build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    assert first is not second
    assert first.pool is not second.pool


# --------------------------------------------------------------------------
# Session factory
# --------------------------------------------------------------------------


def test_session_factory_does_not_expire_on_commit() -> None:
    """Load-bearing under asyncio, not a style choice: with the default True,
    touching any attribute after commit() triggers a lazy refresh, and a lazy
    refresh from async code raises MissingGreenlet instead of doing I/O."""
    factory = build_session_factory(
        build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    )
    assert factory.kw["expire_on_commit"] is False


def test_session_factory_produces_async_sessions() -> None:
    factory = build_session_factory(
        build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    )
    assert factory.class_ is AsyncSession


def test_session_factory_is_bound_to_the_engine_it_was_given() -> None:
    """Two services in one process (the HF Space runs all four under
    supervisord) must not end up sharing a bind."""
    first = build_engine(database_url=_LOCAL_URL, database_ssl="", pool_size=10)
    second = build_engine(database_url=_DIRECT_URL, database_ssl="require", pool_size=5)
    assert build_session_factory(first).kw["bind"] is first
    assert build_session_factory(second).kw["bind"] is second


# --------------------------------------------------------------------------
# The assertions above cannot quietly stop asserting
# --------------------------------------------------------------------------


def test_connect_params_reads_real_arguments() -> None:
    """_connect_params reads a SQLAlchemy-private closure. If an upgrade renames
    or restructures it, every connect_args assertion in this file would start
    reading nothing — so the shape is asserted explicitly, and the URL-derived
    fields prove the dict is the merged one the driver receives rather than
    just the connect_args we passed in."""
    engine = build_engine(database_url=_POOLER_URL, database_ssl="require", pool_size=10)
    creator = engine.pool._creator
    assert "cparams" in creator.__code__.co_freevars, (
        "SQLAlchemy changed how connect args are captured; _connect_params is now blind"
    )
    params = _connect_params(engine)
    assert params["user"] == "u"
    assert params["database"] == "db"


# --------------------------------------------------------------------------
# shared/ stays importable from all four service images
# --------------------------------------------------------------------------


def test_factory_stays_dependency_light() -> None:
    """shared/ is COPY'd into every service image, so an import of ``app.*`` or
    ``services.*`` here would break all four at container start. SQLAlchemy is
    on the allowlist because all four already pin SQLAlchemy==2.0.50, so this
    module adds nothing to any requirements file — a claim that stops being
    true the moment a heavier dependency is added, which is what this guards."""
    allowed = {"__future__", "urllib", "sqlalchemy", "pydantic", "structlog", "typing"}
    source = pathlib.Path(__file__).parent.parent / "db" / "engine.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    assert roots <= allowed, f"disallowed imports in db/engine: {sorted(roots - allowed)}"


def test_the_package_init_re_exports_without_adding_dependencies() -> None:
    """``from shared.db import build_engine`` is the documented entry point, so
    the re-export is part of the contract the four services will adopt against.
    It must also pull in nothing beyond engine.py itself."""
    import shared.db

    assert shared.db.build_engine is build_engine
    assert shared.db.build_session_factory is build_session_factory

    tree = ast.parse(
        (pathlib.Path(__file__).parent.parent / "db" / "__init__.py").read_text(encoding="utf-8")
    )
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert modules == {"shared.db.engine"}
