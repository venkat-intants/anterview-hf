"""``alembic upgrade head`` must complete against a database that holds
PRE-BRANCH ``exam_integrity_events`` rows.

WHY THIS FILE EXISTS. Two boot-fatal defects got through every other gate in
this repository, and both were the same shape:

  * a3c5e7f9b1d4 added a CHECK on ``event_type`` that validated existing rows,
    while the deployed client had long been writing ``copy``/``paste`` into a
    column with no constraint and no vocabulary check;
  * b4d6f8a0c2e4 then ran ``UPDATE ... SET event_metadata = NULL`` while that
    CHECK was live — and a NOT VALID CHECK IS enforced on UPDATE, against the
    whole new row version, so a row with a junk ``event_type`` aborts it.

Either one aborts ``alembic upgrade head``; ``space/entrypoint.sh`` treats a
migration failure on a reachable database as fatal, so the service does not
boot and every route 503s.

Nothing else here can catch this class. Every other suite, and all 16 CI jobs,
migrate a FRESHLY CREATED EMPTY database — where these constraints have no
rows to fail against, so the chain is always green. The only test that can
prove "migrating cannot abort at boot" is one that migrates a database with
legacy rows in it, which is what this does: it builds the pre-branch state on
its OWN throwaway database, seeds exactly the rows the old unvalidated
endpoint could write, and then runs the real chain.

It uses its own database and its own alembic subprocesses precisely so it can
never disturb the shared test database other integration tests rely on.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.config import settings

pytestmark = pytest.mark.integration

#: The revision immediately before the camera-proctoring work — the last point
#: at which exam_integrity_events.event_type had no CHECK constraint.
PRE_CAMERA_REVISION = "f2a4c6e8b0d3"

SERVICE_DIR = Path(__file__).resolve().parents[2]


def _dsn(database: str) -> str:
    """An asyncpg DSN for *database* on the same server as settings.database_url."""
    base = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    head, _, _old = base.rpartition("/")
    return f"{head}/{database}"


def _alembic(revision: str, database: str) -> subprocess.CompletedProcess[str]:
    """Run alembic against *database*, in a subprocess.

    A subprocess, not alembic's Python API, because ``app.config.settings`` is
    a module-level singleton already bound to the shared test database: there
    is no way to repoint it in-process without leaking that change into every
    other test in the session.
    """
    env = {
        **os.environ,
        "DATABASE_URL": f"postgresql+asyncpg://{_dsn(database).split('://', 1)[1]}",
        "DATABASE_SSL": "",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=str(SERVICE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest_asyncio.fixture
async def throwaway_db() -> AsyncIterator[str]:
    """A database of this test's own, dropped afterwards whatever happens."""
    name = f"chain_probe_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_dsn("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    try:
        conn = await asyncpg.connect(_dsn(name))
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        finally:
            await conn.close()
        yield name
    finally:
        admin = await asyncpg.connect(_dsn("postgres"))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _seed_legacy_rows(database: str) -> None:
    """Write the rows only the PRE-BRANCH endpoint could produce.

    It validated ``event_type`` no further than ``[:40]`` and accepted a
    free-form ``metadata`` dict with no checks, both from the same
    unauthenticated magic-link request. So: junk event types, and candidate
    PII in event_metadata — including on a row whose event type is perfectly
    valid, which is what proves the purge is not silently skipped.
    """
    conn = await asyncpg.connect(_dsn(database))
    try:
        company = uuid.uuid4()
        await conn.execute(
            "INSERT INTO companies (id, name, slug) VALUES ($1, 'Legacy co', $2)",
            company, f"legacy-{company.hex[:8]}",
        )
        exam = uuid.uuid4()
        await conn.execute(
            "INSERT INTO exams (id, company_id, title) VALUES ($1, $2, 'Legacy exam')",
            exam, company,
        )
        rnd = uuid.uuid4()
        await conn.execute(
            "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, position)"
            " VALUES ($1, $2, $3, 1, 'R1', 1)",
            rnd, exam, company,
        )
        applicant = uuid.uuid4()
        await conn.execute(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
            " VALUES ($1, $2, 'Legacy Candidate', 'Tester')",
            applicant, company,
        )
        attempt = uuid.uuid4()
        await conn.execute(
            "INSERT INTO exam_attempts (id, company_id, exam_id, applicant_id, round_id,"
            " started_at) VALUES ($1, $2, $3, $4, $5, now())",
            attempt, company, exam, applicant, rnd,
        )
        # (event_type, event_metadata) — the whole point of each row:
        rows = [
            # A type the five-name CHECK rejects. Aborted a3c5e7f9b1d4.
            ("copy", None),
            # A type NO version of the CHECK has ever allowed, with PII.
            ("screenshot", '{"name": "Legacy Candidate"}'),
            # Truncated junk, as `body.event_type[:40]` would store it.
            ("x" * 40, '{"phone": "9999999999"}'),
            # VALID type, but PII present: if the purge is skipped or rolled
            # back wholesale, this row still holds the PII and the test fails.
            ("tab_blur", '{"note": "leaked"}'),
        ]
        for event_type, metadata in rows:
            await conn.execute(
                "INSERT INTO exam_integrity_events (id, attempt_id, company_id, event_type,"
                " started_at, created_at, event_metadata)"
                " VALUES ($1, $2, $3, $4, now(), now(), $5::jsonb)",
                uuid.uuid4(), attempt, company, event_type, metadata,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upgrade_head_completes_on_a_database_holding_pre_branch_rows(
    throwaway_db: str,
) -> None:
    first = _alembic(PRE_CAMERA_REVISION, throwaway_db)
    assert first.returncode == 0, f"baseline migration failed:\n{first.stderr}"

    await _seed_legacy_rows(throwaway_db)

    result = _alembic("head", throwaway_db)
    assert result.returncode == 0, (
        "`alembic upgrade head` ABORTED on a database holding pre-branch "
        "exam_integrity_events rows. This is the boot-fatal failure this file "
        "exists to catch: entrypoint.sh treats it as fatal and the service "
        "will not start. Every other suite migrates an empty database and "
        f"cannot see it.\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    conn = await asyncpg.connect(_dsn(throwaway_db))
    try:
        # The DPDP purge (b4d6f8a0c2e4) actually happened — for EVERY row, not
        # just the conforming ones. A single UPDATE rolls back whole, so this
        # is what catches "the migration ran but silently achieved nothing".
        left = await conn.fetchval(
            "SELECT count(*) FROM exam_integrity_events WHERE event_metadata IS NOT NULL"
        )
        assert left == 0, f"{left} rows still carry event_metadata after the purge"

        # The rows themselves survive: this clears PII, it does not delete a
        # candidate's proctoring history.
        assert await conn.fetchval("SELECT count(*) FROM exam_integrity_events") == 4

        # And the constraint is still NOT VALID, so the next upgrade cannot
        # abort either.
        assert (
            await conn.fetchval(
                "SELECT convalidated FROM pg_constraint"
                " WHERE conname = 'ck_exam_integrity_events_event_type'"
            )
            is False
        )

        # Still a live constraint for anything written from here on.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO exam_integrity_events (id, attempt_id, company_id, event_type,"
                " started_at, created_at) SELECT $1, attempt_id, company_id, 'screenshot',"
                " now(), now() FROM exam_integrity_events LIMIT 1",
                uuid.uuid4(),
            )
    finally:
        await conn.close()
