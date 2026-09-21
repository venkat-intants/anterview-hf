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

## The three that need more than a database

`run_all.py` handles all three. None is skipped silently — each prints the reason
it did not run, so a green "32/34" can never be mistaken for health.

**`smoke_ph3_apply`** needs a database of its own (it seeds a whole tenant).
`run_all.py` creates the `ph3` role and the `ph3_smoke` database, migrates it and
runs the smoke. Nothing to set up.

**`smoke_group_a_scorecard_retry`** drives a REAL `feedback_billing`: it scores
one interview with whatever `LLM_PROVIDER` names and uploads a real PDF. It runs
only when that service is answering and object storage is configured, so start
both and it joins the run:

```powershell
# a bucket for the scorecards, once
docker exec intants-minio mc mb -p local/intants-interview-scorecards

# feedback_billing against the SAME database the smokes use
cd services/feedback_billing
$env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke'
$env:S3_ENDPOINT_URL = 'http://127.0.0.1:9000'
$env:S3_ACCESS_KEY_ID = 'intants-dev'; $env:S3_SECRET_ACCESS_KEY = 'intants-dev-secret'
$env:S3_REGION = 'us-east-1'
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8013

# then, in the data_gateway shell, the same storage settings plus the bucket
$env:S3_SCORECARD_BUCKET = 'intants-interview-scorecards'
$env:FEEDBACK_BILLING_URL = 'http://127.0.0.1:8013'
.\.venv\Scripts\python tests/integration/run_all.py
```

**`smoke_ph4_wave4`** — offers and preboarding — stores candidate documents in a
bucket, so it needs object storage under `data_gateway`'s names, which are not
`feedback_billing`'s: `S3_ENDPOINT` (no `_URL`) and `S3_BUCKET_NAME`. Without
them it is listed as not run. Set them in the same shell as above:

```powershell
docker exec intants-minio mc mb -p local/intants-uploads   # once
$env:S3_ENDPOINT = 'http://127.0.0.1:9000'; $env:S3_BUCKET_NAME = 'intants-uploads'
```

Before 2026-09-22 it did not skip — boto3 fell back to Amazon's endpoint and the
smoke died on a network error that read like a broken offer flow.

### The PH4 smokes and their databases

Each PH4 smoke's header shows it run against a database of its own (`ph4_w5`,
`ph4_dev`, `ph4_w4`) as the `ph3` role. `run_all.py` never created those, so a
clean run reported eight failures that were not failures. It now points them at
the shared clean database through `SMOKE_DATABASE_URL`, as the postgres user:
nothing in the schema uses row-level security, no PH4 smoke asserts a permission,
and `smoke_ph4_wave4`'s `backdate()` needs superuser to set
`session_replication_role`. The standalone commands in each smoke's header still
work if you would rather run one on its own database.

It makes one model call per run — locally that is whatever `LLM_PROVIDER` is set
to in `services/feedback_billing/.env` (Groq at the time of writing), not a
hosted Gemini key. One interview's worth, so the cost is negligible, but it is a
real call: it is the only check that proves the retry path actually scores
against the round's frozen rubric rather than generic axes.

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
