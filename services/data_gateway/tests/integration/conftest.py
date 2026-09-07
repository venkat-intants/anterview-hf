"""Refuse to run the integration suite against a remote database.

WHY THIS EXISTS
---------------
These tests boot the real ASGI app, which resolves ``DATABASE_URL`` from
``services/data_gateway/.env``. On a developer machine that file points at the
shared Neon instance — the same database the deployed Space uses — so a plain
``pytest tests`` wrote test fixtures straight into it.

That is not hypothetical. On 2026-09-07 the live database held **571** rows from
this suite: 567 ``retention-test-*`` and ``consent-test-*`` users plus assorted
``crosstest-*`` and ``@e2e.local`` accounts, against 23 real ones. They had to
be deleted by hand, and the retention tests in particular create users, consent
ledger entries and sessions and then assert on *counts*, so they were also
racing whatever else lived there.

CI is unaffected either way: the workflow sets ``DATABASE_URL`` to a localhost
Postgres service container, which passes this guard untouched. The guard only
bites where the damage happens — a laptop pointed at production.

THE ESCAPE HATCH
----------------
``ALLOW_REMOTE_TEST_DB=1`` runs them anyway. Deliberately an environment
variable rather than a pytest flag: it has to be awkward enough that nobody
reaches for it to make a red suite go green, and explicit enough that it shows
up in shell history when someone asks how the data got there.
"""

from __future__ import annotations

import os
import sys

import pytest

# Import path mirrors the service's own: tests run with the service root on
# sys.path, and shared/ resolves from the repo root via PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.security import _database_host, _is_loopback_database  # noqa: E402

_OVERRIDE = "ALLOW_REMOTE_TEST_DB"


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Abort collection when the configured database is not local.

    Checked at collection rather than in a fixture so the refusal happens before
    any test body runs — a per-test fixture would still let a module-scoped
    setup write its rows first.
    """
    if os.getenv(_OVERRIDE) == "1":
        return

    # Read the URL the app itself would use, not os.environ: pydantic resolves
    # .env files the plain environment knows nothing about, and reading the
    # wrong source is how a guard passes while the app connects elsewhere.
    try:
        from app.config import settings

        url = settings.database_url
    except Exception:  # noqa: BLE001 — config not importable is not our failure
        return

    if _is_loopback_database(url):
        return

    host = _database_host(url) or "an unparseable host"
    raise pytest.UsageError(
        f"REFUSING TO RUN: the integration suite would write to {host!r}, which is "
        f"not a local database.\n"
        f"\n"
        f"These tests create users, consent-ledger rows and sessions, and assert on "
        f"counts. Run against a throwaway Postgres:\n"
        f"    docker compose up -d postgres\n"
        f"    DATABASE_URL=postgresql+asyncpg://... pytest tests/integration\n"
        f"\n"
        f"Unit tests are unaffected and need no database:\n"
        f"    pytest tests/unit\n"
        f"\n"
        f"To override anyway (it will write to {host}): {_OVERRIDE}=1 pytest ..."
    )
