# Database smokes

Standalone scripts, not pytest modules. Each drives the real code paths against
a live PostgreSQL with pgvector, asserts a list of labelled checks, prints them,
and exits non-zero if any failed. They cover what a unit test with a mocked
session cannot: triggers, constraints, migrations and the SQL itself.

They are not wired into CI — they need a database — so nothing tells you when
one stops being run. That is how the three failures fixed on 2026-09-17 sat on
`main` unnoticed. If you touch a rule these depend on, run them.

## Running them

```powershell
cd services/data_gateway
.\.venv\Scripts\python tests/integration/run_all.py
```

`run_all.py` prepares the databases, runs every smoke in an order that works,
and prints the failed checks of any that did not pass. Prefer it: the order and
the starting state are the part that is easy to get wrong, and getting them
wrong produces failures that look like broken features.

To run one by hand, know which of the two starting states it needs.

**Most smokes** seed (and often `TRUNCATE`) what they need, so they want a clean
database at head, and they collide with the Group B seed's companies:

```powershell
docker exec intants-pg psql -U postgres `
  -c "DROP DATABASE IF EXISTS intants_smoke WITH (FORCE);" -c "CREATE DATABASE intants_smoke;"
$env:PYTHONPATH = '.;../..'
$env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke'
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python tests/integration/smoke_group_c_runner.py
```

**`smoke_group_b_api` and `smoke_group_b_requisitions`** read what the Group B
migration backfilled out of `seed_pre_group_b`, so they need the seed applied at
the revision *before* Group B and the migration run over it — and one database
each, because both change that backfill (the API smoke renames the Python
opening and creates another; the requisitions smoke merges applicants):

```powershell
# …after recreating the database as above:
.\.venv\Scripts\python -m alembic upgrade c5e7a9b1d3f6
.\.venv\Scripts\python tests/integration/seed_pre_group_b.py
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python tests/integration/smoke_group_b_api.py
```

Seeded after head instead, the backfill has already run, there is nothing for it
to collapse, and both fail with empty results.

## The two that need more than a database

| Smoke | Needs | Why it is not routine |
|---|---|---|
| `smoke_group_a_scorecard_retry.py` | `feedback_billing` on :8013, MinIO reachable (`S3_ENDPOINT_URL` and keys), and a real model | Scoring an interview here calls a live model, so a routine run would spend money. Run it deliberately. |
| `smoke_ph3_apply.py` | its own database (`SMOKE_DATABASE_URL`, default `ph3:ph3@…/ph3_smoke`) | Phase 3, and it seeds a whole tenant of its own. |

## Why a smoke breaks when the code is fine

All three failures found on 2026-09-17 were the same shape: a later rule made an
older smoke's **seed** illegal or pointless, and the smoke failed as though the
feature under test were broken.

- **PH3-B2's approval gate.** `smoke_group_e_intake`, `_foundations` and
  `_company_board` seed an opening straight into `job_requisitions`, where
  `approval_status` defaults to draft — and the public surface now requires
  approved. Every public-apply check failed with "not accepting applications".
  They now seed `approval_status='approved'` with an `approval_decided_at`, which
  a CHECK constraint requires.
- **Group C's immutability trigger.** `smoke_group_b_ledger` seeded a workflow as
  published and then inserted its rounds. It now seeds a draft, adds the rounds
  and publishes — the order the application itself uses.
- **Group E's close guard (AC-12).** `smoke_group_b_api` asserted that closing an
  opening with people still in it returns 200. It is now refused with 409 until
  the caller acknowledges them, so the smoke asserts the refusal and then closes
  with `acknowledge_unresolved`.

So when one of these fails, read the seed before reading the feature. The
question to ask is "is this smoke still describing the system we have?"
