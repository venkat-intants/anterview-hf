#!/usr/bin/env python3
"""Run every database smoke, in the order and on the databases they need.

    cd services/data_gateway
    .\\.venv\\Scripts\\python tests/integration/run_all.py

Why this exists. The smokes are standalone scripts that each assume a database
in a particular state, and nothing recorded what that state was. Run in the
wrong order they fail with empty results and foreign-key errors that read like
broken features, so the honest reading of a red smoke became guesswork — and
three of them sat broken on ``main`` for days because of it (see README.md).

Two stages, because there are two incompatible starting states:

  1. ``smoke_group_b_api`` and ``smoke_group_b_requisitions`` read what the
     Group B migration backfilled out of ``seed_pre_group_b``. They need a
     database seeded at the revision BEFORE Group B and then migrated to head —
     and one each, because both mutate that backfill: the API smoke renames the
     Python opening and creates another, the requisitions smoke merges
     applicants. Either running first makes the other's assertions false.

  2. Every other smoke seeds (and often TRUNCATEs) what it needs, and collides
     with the Group B seed's companies. They get a clean database at head.

Two of them need more than a database, and are handled after the rest:
``smoke_ph3_apply`` gets its own database, created here; and
``smoke_group_a_scorecard_retry`` runs only when a real feedback_billing is
answering and object storage is configured, because it scores an interview with
a live model and uploads a real PDF (README.md has the command). Neither is
skipped silently — a skip prints its reason, so a run that covers less than the
whole set says so rather than reporting a healthy-looking total.

Exits non-zero if any smoke fails, and prints the failed checks of each.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE_ROOT = HERE.parents[1]
PY_EXE = str(SERVICE_ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY_EXE).exists():  # POSIX layout
    PY_EXE = str(SERVICE_ROOT / ".venv" / "bin" / "python")

# The revision just before Group B, so the seed is what the backfill reads.
PRE_GROUP_B = "c5e7a9b1d3f6"
BACKFILL_PAIR = ["smoke_group_b_api.py", "smoke_group_b_requisitions.py"]

# These do not run in the main loop; each is handled by its own stage below,
# and each is skipped with a reason rather than silently, so "32/34" can never
# be mistaken for health.
SPECIAL = {
    "smoke_group_a_scorecard_retry.py",
    "smoke_ph3_apply.py",
    "smoke_ph4_wave4.py",
}

# smoke_ph4_wave4 puts offer documents in object storage, so it needs a bucket.
# Without one, boto3 falls back to Amazon's default endpoint and the smoke dies
# on a network error that reads like a product failure — which is exactly how
# the 2026-09-22 pipeline test first reported it. Named here, skipped with a
# reason when absent. (data_gateway reads S3_ENDPOINT, not S3_ENDPOINT_URL.)
S3_FOR_DOCUMENTS = {
    "S3_ENDPOINT": os.environ.get("S3_ENDPOINT", ""),
    "S3_ACCESS_KEY_ID": os.environ.get("S3_ACCESS_KEY_ID", ""),
    "S3_SECRET_ACCESS_KEY": os.environ.get("S3_SECRET_ACCESS_KEY", ""),
    "S3_BUCKET_NAME": os.environ.get("S3_BUCKET_NAME", ""),
}

# smoke_ph3_apply seeds a whole tenant of its own and wants its own database.
PH3_DB = os.environ.get("SMOKE_PH3_DB", "ph3_smoke")
PH3_URL = f"postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/{PH3_DB}"

# smoke_group_a_scorecard_retry drives a REAL feedback_billing: it scores one
# interview with whatever LLM_PROVIDER names and uploads a real PDF. So it runs
# only when that service is up and object storage is configured — start them
# and it joins the run (README.md has the command).
FEEDBACK_BILLING_URL = os.environ.get("FEEDBACK_BILLING_URL", "http://127.0.0.1:8013")
S3_FOR_SCORECARDS = {
    "S3_ENDPOINT_URL": os.environ.get("S3_ENDPOINT_URL", ""),
    "S3_ACCESS_KEY_ID": os.environ.get("S3_ACCESS_KEY_ID", ""),
    "S3_SECRET_ACCESS_KEY": os.environ.get("S3_SECRET_ACCESS_KEY", ""),
    "S3_SCORECARD_BUCKET": os.environ.get("S3_SCORECARD_BUCKET", ""),
}

DB_NAME = os.environ.get("SMOKE_DB_NAME", "intants_smoke")
DB_URL = os.environ.get(
    "SMOKE_DATABASE_URL",
    f"postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/{DB_NAME}",
)
PG_CONTAINER = os.environ.get("SMOKE_PG_CONTAINER", "intants-pg")

ENV = {
    **os.environ,
    "PYTHONUTF8": "1",
    "PYTHONPATH": f".{os.pathsep}..{os.sep}..",
    "DATABASE_URL": DB_URL,
    # The PH4 smokes read SMOKE_DATABASE_URL, and each defaults to a database
    # of its own (ph4_w5, ph4_dev, ph4_w4) as the `ph3` role — databases this
    # runner never created, so a clean run reported eight failures that were
    # not failures. They run on the shared clean database like everything else
    # now. As the postgres user rather than `ph3`: nothing in the schema uses
    # row-level security and no PH4 smoke asserts a permission, and
    # smoke_ph4_wave4's backdate() needs superuser to set
    # session_replication_role.
    "SMOKE_DATABASE_URL": DB_URL,
}


# LOCAL ONLY. These smokes drop and recreate databases, write test tenants and
# upload test documents. Pointed at anything but this machine they would do
# that to real data — which happened once already (2026-09-07: 571 test rows
# in the live Neon database, deleted by hand; see tests/integration/conftest.py).
# The database is checked before anything runs and the whole run refuses.
# Storage and the scoring service are checked where they are used, and a
# non-local one skips its smoke with the reason rather than being sent test
# files. There is no override: a smoke has no business on a remote host.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_local(url: str) -> bool:
    host = urllib.parse.urlsplit(url.replace("+asyncpg", "")).hostname or ""
    return host in _LOCAL_HOSTS


def _refuse_unless_local() -> None:
    if not _is_local(DB_URL):
        host = urllib.parse.urlsplit(DB_URL.replace("+asyncpg", "")).hostname
        sys.exit(f"refusing: the smoke database is on {host!r}, not this machine. "
                 "run_all.py only ever runs against local Postgres.")


Result = tuple[str, bool, str, str]
results: list[Result] = []


def _setup(args: list[str], label: str) -> None:
    proc = subprocess.run(
        args, cwd=SERVICE_ROOT, env=ENV, capture_output=True, text=True, timeout=900
    )
    if proc.returncode != 0:
        print(f"SETUP FAILED: {label}\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
        sys.exit(1)


def _fresh_database() -> None:
    subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres",
         "-c", f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE);",
         "-c", f"CREATE DATABASE {DB_NAME};"],
        capture_output=True, text=True, check=True,
    )


def _seeded_at_head() -> None:
    """A database holding pre-Group-B data that the migration then backfilled."""
    _fresh_database()
    _setup([PY_EXE, "-m", "alembic", "upgrade", PRE_GROUP_B], "upgrade to pre-Group-B")
    _setup([PY_EXE, f"tests/integration/{Path('seed_pre_group_b.py').name}"], "seed")
    _setup([PY_EXE, "-m", "alembic", "upgrade", "head"], "upgrade to head")


def _run(names: list[str], env: dict[str, str] | None = None) -> None:
    for name in names:
        started = time.time()
        proc = subprocess.run(
            [PY_EXE, f"tests/integration/{name}"],
            cwd=SERVICE_ROOT, env=env or ENV, capture_output=True, text=True, timeout=900,
        )
        ok = proc.returncode == 0
        results.append((name, ok, proc.stdout, proc.stderr))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - started:.0f}s)", flush=True)


skipped: list[str] = []


def _run_ph3() -> None:
    """Phase 3's apply smoke, on its own database, created here."""
    try:
        subprocess.run(
            ["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres",
             "-c", f"DROP DATABASE IF EXISTS {PH3_DB} WITH (FORCE);",
             "-c", "DROP ROLE IF EXISTS ph3;",
             "-c", "CREATE ROLE ph3 LOGIN PASSWORD 'ph3';",
             "-c", f"CREATE DATABASE {PH3_DB} OWNER ph3;"],
            capture_output=True, text=True, check=True, timeout=120,
        )
        subprocess.run(
            ["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-d", PH3_DB,
             "-c", "CREATE EXTENSION IF NOT EXISTS vector;",
             "-c", "GRANT ALL ON SCHEMA public TO ph3;"],
            capture_output=True, text=True, check=True, timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        skipped.append(f"smoke_ph3_apply.py — could not create {PH3_DB}: {exc}")
        return

    env = {**ENV, "DATABASE_URL": PH3_URL, "SMOKE_DATABASE_URL": PH3_URL, "DATABASE_SSL": ""}
    proc = subprocess.run(
        [PY_EXE, "-m", "alembic", "upgrade", "head"],
        cwd=SERVICE_ROOT, env=env, capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        skipped.append(f"smoke_ph3_apply.py — migrating {PH3_DB} failed:\n{proc.stderr[-600:]}")
        return
    print(f"— {PH3_DB}, created and migrated to head", flush=True)
    _run(["smoke_ph3_apply.py"], env)


def _run_scorecard_retry() -> None:
    """The one smoke that needs a second service and object storage."""
    missing = [k for k, v in S3_FOR_SCORECARDS.items() if not v]
    if missing:
        skipped.append(
            "smoke_group_a_scorecard_retry.py — object storage is not configured "
            f"({', '.join(missing)} unset). See README.md."
        )
        return
    for what, url in (("S3_ENDPOINT_URL", S3_FOR_SCORECARDS["S3_ENDPOINT_URL"]),
                      ("FEEDBACK_BILLING_URL", FEEDBACK_BILLING_URL)):
        if not _is_local(url):
            skipped.append(f"smoke_group_a_scorecard_retry.py — {what} is not local; "
                           "smokes only write to local services.")
            return
    try:
        with urllib.request.urlopen(f"{FEEDBACK_BILLING_URL}/health/live", timeout=5) as r:
            r.read()
    except OSError as exc:
        skipped.append(
            f"smoke_group_a_scorecard_retry.py — no feedback_billing at "
            f"{FEEDBACK_BILLING_URL} ({exc}). See README.md."
        )
        return
    print(f"— feedback_billing answering at {FEEDBACK_BILLING_URL}", flush=True)
    _run(["smoke_group_a_scorecard_retry.py"],
         {**ENV, "FEEDBACK_BILLING_URL": FEEDBACK_BILLING_URL, **S3_FOR_SCORECARDS})


def _run_wave4() -> None:
    """Offers and preboarding, which store candidate documents in a bucket."""
    missing = [k for k, v in S3_FOR_DOCUMENTS.items() if not v]
    if missing:
        skipped.append(
            "smoke_ph4_wave4.py — object storage is not configured "
            f"({', '.join(missing)} unset). See README.md."
        )
        return
    if not _is_local(S3_FOR_DOCUMENTS["S3_ENDPOINT"]):
        skipped.append("smoke_ph4_wave4.py — S3_ENDPOINT is not local; "
                       "smokes only upload to a local bucket.")
        return
    _run(["smoke_ph4_wave4.py"], {**ENV, **S3_FOR_DOCUMENTS})


def main() -> int:
    _refuse_unless_local()
    for name in BACKFILL_PAIR:
        _seeded_at_head()
        print(f"— seeded before Group B, migrated to head, for {name}", flush=True)
        _run([name])

    _fresh_database()
    _setup([PY_EXE, "-m", "alembic", "upgrade", "head"], "upgrade to head")
    print("— clean database at head", flush=True)
    _run(sorted(
        p.name for p in HERE.glob("smoke_*.py")
        if p.name not in SPECIAL and p.name not in BACKFILL_PAIR
    ))
    # The scorecard smoke shares this database with feedback_billing, so it goes
    # after the rest rather than on a database of its own.
    _run_scorecard_retry()
    _run_wave4()
    _run_ph3()

    failed = [r for r in results if not r[1]]
    if failed:
        print("\n================ failures ================")
        for name, _ok, out, err in failed:
            print(f"\n--- {name} ---")
            for line in [x for x in out.splitlines() if "FAIL" in x][:10]:
                print("   ", line)
            for line in err.strip().splitlines()[-6:]:
                print("  !", line)

    if skipped:
        print("\n================ not run ================")
        for reason in skipped:
            print(f"  - {reason}")

    total = len(results) + len(skipped)
    print(f"\n{len(results) - len(failed)}/{total} smokes passed"
          f"{f', {len(skipped)} not run' if skipped else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
