# Pointing the Space at a different database

Written for the operator, not the author. Follow it top to bottom.

This exists because on 2026-09-25 the demo's Neon project used its entire
monthly compute allowance in under a week and the Space went dark, and the
recovery turned out to need facts scattered across four files and two vendor
consoles. It should be a ten-minute job.

---

## 0. Before you start, know which failure you are recovering from

Two very different problems look identical from outside — every route dead:

| Symptom | What it is |
|---|---|
| `runtime.errorMessage` ends in `InsufficientResourcesError: ... exceeded the quota` | The database is refusing connections. **Not a code problem.** |
| Anything else | Suspect the deploy. |

Check it without leaving the terminal:

```bash
curl -sL https://huggingface.co/api/spaces/Venkat95/Anterview-hf \
  | python -c "import sys,json;r=json.load(sys.stdin)['runtime'];print(r['stage']);print((r.get('errorMessage') or '')[-400:])"
```

The API 307-redirects to the capitalised `owner/space`, so use `-L`.

**Since 2026-09-26 the Space no longer dies on an unreachable database.** It
retries with backoff and then starts anyway, so the services come up and report
the database as down on their own health endpoints. A migration that fails
*after* connecting still aborts, deliberately — serving on a half-applied
schema risks writing data the next deploy cannot read.

---

## 1. Create the database

Any Postgres 16 with the **pgvector** extension available. On Neon's free plan
the compute allowance is per project, so a new project starts with a fresh one.

Nothing else needs to change. In particular you do **not** need to set
`DATABASE_SSL` — `space/entrypoint.sh` already defaults it to `require`, which
is what a managed provider expects, and defaults `APP_ENV` to `production`.

---

## 2. Set one secret

In the Space's **Settings → Variables and secrets**, set:

```
DATABASE_URL = postgresql+asyncpg://USER:PASSWORD@HOST/DBNAME
```

Note the `+asyncpg` — the driver is not optional. Do not append `?sslmode=...`;
SSL is handled by `DATABASE_SSL`.

---

## 3. Apply the free-tier profile, or the database will die again

**This is the step people skip.** The default poller cadence keeps a
serverless database permanently awake, because on those plans the unit that
costs money is a **wake-up, not a query** — one cheap `SELECT` keeps the
compute alive for the whole autosuspend window. Five loops between them
queried ~19,000 times a day with nobody using the product.

Slowing them down is **not** enough: it cut queries 99× and compute only from
~24 h/day to ~16 h/day. On a metered plan the loops have to be **off**.

Copy the block at the bottom of [`space.env.example`](../space.env.example)
into the Space's secrets — and set `WATCHERS_ENABLED=false`, which lives at its
own entry further up that file. That profile is ~28 wake-ups a day, roughly
2 h/day of compute.

**Each switch costs you something**, and the block says what. Briefly: queued
email is not sent (cheap today only because Resend is still sandboxed — turn
it back on the moment a sending domain is verified, or set-password links
silently never arrive); deadline reminders do not go out; a failed CV upload
does not self-heal; nightly watchers do not run; and a job scheduled for 09:00
goes live by about 10:00.

**On a paid plan or an always-on Postgres — including the Tier-2 AWS RDS
target — delete that whole block.** None of it applies and every line of it
degrades the product.

---

## 4. Restart and watch it migrate

Restart the Space. On boot it probes the database, then runs
`alembic upgrade head`, then starts the services. A fresh database gets the
entire migration chain, which ends by printing a generated platform-owner
password **once** — capture it from the logs, or set `PLATFORM_OWNER_PASSWORD`
beforehand to choose your own.

---

## 5. Verify, in this order

```bash
# 1. The container is up at all.
curl -sL https://huggingface.co/api/spaces/Venkat95/Anterview-hf \
  | python -c "import sys,json;print(json.load(sys.stdin)['runtime']['stage'])"
# expect: RUNNING

# 2. Routes answer. 401 means the app is up and the route exists;
#    404 on the control means routing is sane and not a blanket catch-all.
for p in /hr/library /hr/pools /hr/rediscovery/universe /hr/definitely-not-a-route; do
  printf '%-34s %s\n' "$p" \
    "$(curl -s -o /dev/null -w '%{http_code}' https://venkat95-anterview-hf.hf.space$p)"
done
# expect: 401, 401, 401, 404
```

A **503 on everything including the control** means the database is unreachable
again — go back to step 0.

---

## 6. If you are moving data, not starting fresh

You cannot `pg_dump` a database that is refusing connections, so on a quota
lockout the old data is unreachable until the allowance resets (monthly, on the
project's own billing date — the Neon console shows it).

The sequence that keeps your options open:

1. Stand up the new database now and run on it, so you are not blocked.
2. When the old project's allowance resets, `pg_dump` it **immediately**, before
   anything else. That turns "locked out" into "have a file".
3. Decide then whether to restore it. Restoring into a database that has been
   live for weeks is a **merge**, not a restore — colliding primary keys and a
   conflicting `alembic_version`. Keeping the dump as an archive is usually the
   safer answer.

The one thing worth real care: `dpdp_consent_ledger` is the record of what each
candidate agreed to, and under the DPDP Act it is the evidence you would point
at if asked to substantiate a consent. Do not discard it casually.
