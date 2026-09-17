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

Exits non-zero if any smoke fails, and prints the failed checks of each.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE_ROOT = HERE.parents[1]
PY_EXE = str(SERVICE_ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY_EXE).exists():  # POSIX layout
    PY_EXE = str(SERVICE_ROOT / ".venv" / "bin" / "python")

# The revision just before Group B, so the seed is what the backfill reads.
PRE_GROUP_B = "c5e7a9b1d3f6"
BACKFILL_PAIR = ["smoke_group_b_api.py", "smoke_group_b_requisitions.py"]

# Not run here, for reasons that are about money and scope, not health:
#   scorecard_retry — needs feedback_billing, MinIO and a real model
#   ph3_apply       — Phase 3, and it wants a database of its own
SKIP = {"smoke_group_a_scorecard_retry.py", "smoke_ph3_apply.py"}

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
}

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


def _run(names: list[str]) -> None:
    for name in names:
        started = time.time()
        proc = subprocess.run(
            [PY_EXE, f"tests/integration/{name}"],
            cwd=SERVICE_ROOT, env=ENV, capture_output=True, text=True, timeout=900,
        )
        ok = proc.returncode == 0
        results.append((name, ok, proc.stdout, proc.stderr))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - started:.0f}s)", flush=True)


def main() -> int:
    for name in BACKFILL_PAIR:
        _seeded_at_head()
        print(f"— seeded before Group B, migrated to head, for {name}", flush=True)
        _run([name])

    _fresh_database()
    _setup([PY_EXE, "-m", "alembic", "upgrade", "head"], "upgrade to head")
    print("— clean database at head", flush=True)
    _run(sorted(
        p.name for p in HERE.glob("smoke_*.py")
        if p.name not in SKIP and p.name not in BACKFILL_PAIR
    ))

    failed = [r for r in results if not r[1]]
    if failed:
        print("\n================ failures ================")
        for name, _ok, out, err in failed:
            print(f"\n--- {name} ---")
            for line in [x for x in out.splitlines() if "FAIL" in x][:10]:
                print("   ", line)
            for line in err.strip().splitlines()[-6:]:
                print("  !", line)

    print(
        f"\n{len(results) - len(failed)}/{len(results)} smokes passed"
        f"  (not run here: {', '.join(sorted(SKIP))})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
