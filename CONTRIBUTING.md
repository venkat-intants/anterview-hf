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

CI asserts a **single linear migration head**; a branched or duplicate-id migration fails the build.
