# Contributing to Intants AI Voice Interview Platform

## Database Migrations

`data_gateway` owns all migrations and they are **written by hand** (the ORM in
`app/models.py` is a partial mirror — the `nos_competencies.embedding` pgvector
column cannot be ORM-mapped, so autogenerate is not used).

When you add or change a table/column:

1. Add the column to the relevant model in `app/models.py`.
2. Create a migration: `cd services/data_gateway && poetry run alembic revision -m "describe_change"` and hand-write the `op.add_column(...)` / `op.create_table(...)` in `upgrade()` plus the reverse in `downgrade()`.
3. Set `down_revision` to the **current head** (`poetry run alembic heads`) — never reuse a revision id, and never point two migrations at the same `down_revision` (that branches the history).
4. Apply locally with `poetry run alembic upgrade head`.

CI asserts a **single linear migration head**; a branched or duplicate-id migration fails the build. This gate is real — see the `migrations` job in `.github/workflows/ci.yml`.

Note that `alembic heads` **imports every module under `versions/`**, so a migration that imports a third-party package (one imports `bcrypt`) makes that package a dependency of the CI job too.

---

## Running the suite the way CI sees it

Green locally is not the same as green in CI, and the difference has cost this repo two red builds:

**1. `.env` files.** Each service has one, `pydantic-settings` reads it automatically, and CI has none. A test that depends on a value from `.env` passes on your machine and fails in CI. This is exactly how `tests/unit/test_config.py` shipped broken — it exercised production-mode `Settings` without supplying `DATABASE_SSL`, which `.env` was quietly providing.

Before pushing anything that touches config or settings:

```bash
cd services/<svc>
mv .env .env.hid
PYTHONPATH=<repo-root> APP_ENV=test \
  DATABASE_URL=postgresql+asyncpg://ci:ci@localhost:5432/ci \
  REDIS_URL=redis://localhost:6379/0 \
  JWT_SECRET=ci-only-not-a-real-secret-0123456789abcdef \
  ... (see the `services` job env block for the full list) \
  ./.venv/Scripts/python.exe -m pytest tests/ -q -m "not integration"
mv .env.hid .env          # ALWAYS restore
```

The same trap applies to Docker repros: mounting the working tree carries `.env` in with it.

**2. Run ruff from inside the service directory**, not the repo root:

```bash
cd services/<svc> && ruff check app/ tests/     # picks up that service's pyproject.toml
```

From the repo root there is no config to find, so ruff silently falls back to its **default** ruleset — far weaker than the project's. A root-level `ruff check services/` that passes proves very little. Same for `shared/`: use `ruff check --config shared/pyproject.toml shared/`.

**3. mypy runs from the repo root**, never from inside a service — `services/` has an `__init__.py`, so invoking mypy from within a service makes it see every file under two module names and refuse to check anything:

```bash
PYTHONPATH=<repo-root> mypy services/<svc>/app
```
