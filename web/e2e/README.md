# Browser end-to-end suite

Playwright specs that drive the real app — the web dev server, data_gateway and
the local datastores — the way a person would. They complement, not replace, the
unit tests (`services/*/tests/unit`, `web/src/__tests__`) and the database smokes
(`services/*/tests/integration`).

## Running it

1. Start the stack: Docker (Postgres, Redis, MinIO, Mailpit), migrations at head,
   `data_gateway` on :8002 and the web dev server on :5174. The repo-root
   `dev-up.ps1` starts the services.

   **Start data_gateway with `RATE_LIMIT_LOGIN_PER_MINUTE=1000` for the run.**
   It allows 5 sign-ins a minute per IP, and the suite signs in many times from
   one. The specs fail with a message saying so if you forget. This is a setting
   for the local test stack only; nothing in the code changes.

   ```powershell
   $env:RATE_LIMIT_LOGIN_PER_MINUTE = '1000'
   cd services/data_gateway; .\.venv\Scripts\python -m uvicorn app.main:app --port 8002
   ```
2. A browser for Playwright, once: `npx playwright install chromium`
   — or reuse an installed one: `E2E_BROWSER_CHANNEL=msedge` (or `chrome`).
3. `npm run e2e` from `web/`. Pass Playwright arguments through, e.g.
   `npm run e2e -- auth.spec.ts` or `npm run e2e -- --headed`.

`global-setup.ts` fails first, with the command to run, if data_gateway or the web
app is not answering.

### Watching it run

| Command (from `web/`) | What you get |
|---|---|
| `npm run e2e -- --headed` | a real browser window doing each step |
| `E2E_SLOWMO=500 npm run e2e -- --headed` | the same, pausing 500 ms between actions |
| `npm run e2e -- --ui` | Playwright's UI: pick tests, step through, time-travel each action |
| `npx playwright show-report` | the HTML report of the last run, with traces of any failure |

In PowerShell set variables first: `$env:E2E_SLOWMO = '500'; npm run e2e -- --headed`.

| Variable | Default | What it changes |
|---|---|---|
| `E2E_WEB_URL` | `http://localhost:5174` | the app under test |
| `E2E_API_URL` | `http://localhost:8002` | data_gateway, for set-up calls |
| `E2E_BROWSER_CHANNEL` | Playwright's Chromium | e.g. `msedge`, `chrome` |
| `E2E_PYTHON` | data_gateway's `.venv` python | runs the provisioning script |
| `E2E_DATABASE_URL` | `DATABASE_URL` from `services/data_gateway/.env` | where accounts are provisioned |
| `E2E_TEST_HOOKS_TOKEN` | — | data_gateway's `TEST_HOOKS_TOKEN`; needed by specs that run a background pass |

### data_gateway settings for an e2e run

Start data_gateway with these for the whole suite. Each is for local testing
only; the service **refuses to start** with `AI_FAKE_MODE` or
`TEST_HOOKS_ENABLED` on when `APP_ENV` is production or staging.

| Setting | Why |
|---|---|
| `RATE_LIMIT_LOGIN_PER_MINUTE=1000` | the suite signs in far more than 5 times a minute from one IP |
| `AI_FAKE_MODE=true` | resume scoring, question generation and embeddings return deterministic stand-ins (`app/fake_ai.py`): no model spend, same answer every run. A CV containing `E2E-SCORE: 85` scores exactly 85 |
| `TEST_HOOKS_ENABLED=true` and `TEST_HOOKS_TOKEN=<32+ chars>` | mounts `/test-hooks/reconcile` and `/test-hooks/reminders`, so a spec runs the scoring pass or the reminder sweep now instead of waiting up to 10 minutes. Every call needs `X-Test-Hooks-Token`; without it the paths answer 404 |

```powershell
$env:RATE_LIMIT_LOGIN_PER_MINUTE = '1000'
$env:AI_FAKE_MODE = 'true'
$env:TEST_HOOKS_ENABLED = 'true'
$env:TEST_HOOKS_TOKEN = '<a random string of 32+ characters>'
cd services/data_gateway; .\.venv\Scripts\python -m uvicorn app.main:app --port 8002
# and for the suite, the same token:
$env:E2E_TEST_HOOKS_TOKEN = '<the same string>'
```

## Test data

Every run provisions its own company and one account per role
(`support/provision_tenant.py`): platform owner, company super admin, HR manager
and candidate, each with a random password. They are written to
`e2e/.auth/tenant.json`, which is git-ignored.

The script **refuses any database that is not on this machine** and any `APP_ENV`
other than development or test. It writes straight to the database on purpose:
the product has no way to create a platform owner, and an account-creating
endpoint added "for tests" would be attack surface production carries too.

Specs set things up through the API (`support/fixtures.ts` — an opening, say)
and exercise the behaviour under test through the UI. Each test creates what it
needs rather than depending on another test having run.

## Selectors

Assert on `data-testid`, roles and user-visible text — never on CSS classes or
DOM shape, which a styling pass changes without changing behaviour. When a spec
needs a hook that does not exist, add a `data-testid` to the component in the
same change.

## Coverage

The full map — every Phase 2 item, where it is tested at each level, and the gaps — is [COVERAGE.md](COVERAGE.md).

| Layer | Covers | Status |
|---|---|---|
| 1 | Sign-in and landing for every role, access refusals, creating an opening, the workflow builder (templates, human gates, publish blocked by issues, publish and read-only) | `auth`, `opening`, `workflow` specs |
| 2 | Candidate path: public apply with consent, shortlist, MCQ, coding, held-not-rejected, decision queue | next — needs a fake LLM mode and a test hook to trigger the background passes, so routine runs spend nothing |
| 3 | Company hiring board, bulk upload, dashboards, candidate account | after layer 2 |
| 4 | Live AI interview (fake media devices) | manual only — it spends real Tavus/Sarvam/LLM budget |

## When it runs

Manually, before a release. It needs the whole local stack, so it is not wired
into CI on push. If that changes, run it on a schedule or `workflow_dispatch`,
never with the interview leg.

## Why `npm run e2e` refuses an empty folder

Playwright exits 0 when no spec matches, which reads as "E2E passed" for a suite
that asserted nothing. `scripts/run-e2e.mjs` refuses to run while `e2e/` holds no
specs and hands off to Playwright otherwise.
