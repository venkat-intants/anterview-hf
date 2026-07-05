"""Alembic environment configuration for data_gateway.

Uses SQLAlchemy 2.0 async pattern. The database URL is read from
app.config.settings (which reads DATABASE_URL from .env) — no URL
is hardcoded or stored in alembic.ini.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from alembic import context

# ---------------------------------------------------------------------------
# Make sure the service root is importable regardless of cwd.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings  # noqa: E402

# Alembic Config object — gives access to alembic.ini values.
config = context.config

# Interpret the config file for Python logging (if present).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Pull DATABASE_URL from app settings so alembic.ini stays secret-free.
# ---------------------------------------------------------------------------
config.set_main_option("sqlalchemy.url", settings.database_url)

# target_metadata is intentionally None: this project writes migrations by
# hand and treats them (not the ORM) as the schema source of truth. The
# nos_competencies.embedding column is a pgvector halfvec(3072) that cannot be
# expressed with a standard SQLAlchemy type, so the ORM models in app/models.py
# are a deliberate partial mirror. Autogenerate / `alembic check` would report
# permanent false drift for that column and the migration-only indexes, so the
# S5-008 CI gate verifies a single linear head instead (see ci.yml).
target_metadata = None


# ---------------------------------------------------------------------------
# Offline mode — generate SQL without a live DB connection.
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — run migrations against a live async connection.
# ---------------------------------------------------------------------------
def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # When DATABASE_SSL is set (Prisma / pgBouncer), inject the asyncpg
    # connect_args so the migration runner speaks TLS and disables the
    # prepared-statement cache that pgBouncer transaction mode rejects.
    connect_args: dict[str, object] = {}
    if settings.database_ssl:
        connect_args = {
            "ssl": settings.database_ssl,
            "statement_cache_size": 0,
        }

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
