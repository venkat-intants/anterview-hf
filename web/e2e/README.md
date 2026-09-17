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
(`support/provision_tenant.py`): platform owner, company super admin, HR manager,
interviewer and candidate, each with a random password. They are written to
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
| 1 | Sign-in and landing for every role, access refusals, creating an opening, the workflow builder (templates, human gates, publish blocked by issues, publish and read-only) | done — `auth`, `opening`, `workflow` specs |
| 2 | The main journeys: public apply with consent → scored → shortlist → MCQ from the emailed link → decision queue → hire or reject with a reason; held-not-rejected; what the candidate is shown about themselves | done — the three `journey-*` specs |
| 3 | Bulk upload, the human-review round, the company hiring board, one person applying twice, an opening dashboard with real candidates | done — `bulk-upload`, `review-round`, `company-board`, `same-person-two-openings`, `opening-dashboard` |
| 3b | Coding rounds | done — `coding-round`. Needs a code runner: `scripts/piston-up.ps1`, then `EXECUTION_PROVIDER=piston`. The spec skips with that instruction when none is answering |
| 3c | PH4 Wave 1: a human interview scored by a named interviewer — HR assigns from the candidate drawer, the interviewer reads the kit, keeps private notes, scores (by mouse and keyboard) and submits, HR reads the evidence, and nothing moves; decisions choose a reason category | done — `interview-scorecard`, and the reason category in the two decision journeys |
| 4 | Live AI interview (fake media devices) | manual only — it spends real Tavus/Sarvam/LLM budget |

### The main journeys

Three specs, each a whole story with two people in it. They are the reason
`AI_FAKE_MODE` and `TEST_HOOKS_ENABLED` exist: scoring and question generation
cost nothing and return the same answer every run, and the background passes run
on demand instead of on a ten-minute timer.

| Spec | The story | What it pins down |
|---|---|---|
| `journey-hire.spec.ts` | A candidate applies from the public page, is scored, is shortlisted, sits the exam that arrives by email, passes, and is hired | scoring moves nobody; the workflow starts at the human shortlist gate; the runner advances a candidate on a real result; hiring is refused until a reason is written, and the ledger names the person who decided |
| `journey-held.spec.ts` | The same, but they fail the exam | a threshold holds, it never rejects — nothing automated writes a rejection; the queue shows them with the reason they stopped and offers "Let them continue"; the rejection that does come is a person's, with their reason |
| `journey-candidate-view.spec.ts` | That held candidate claims the account their confirmation email offered, and reads their own application | their stage reads "Under review" in words, and no score, percentage or threshold appears anywhere on the page |

They take about two minutes together. Run just them with
`npm run e2e -- journey-`.

Steps two people share — applying through the public form, sitting an exam from
an emailed link, shortlisting from the applicant board — live in
`support/journeys.ts`, so a spec reads as what someone did. `support/mail.ts`
reads Mailpit, which catches every email the local stack sends; nothing leaves
the machine. `support/pdf.ts` builds the CV a candidate uploads, with the score
the fake scorer will read out of it.

## Give it the machine

The suite drives ONE data_gateway process (one uvicorn worker, one event loop),
one Vite dev server and one set of containers. Anything else you run against
them is not background noise — it is a queue in front of every assertion.

Running the vitest suite, a smoke run or a profiler alongside it took these
three journeys from 2.6 minutes and green to 9 minutes with two failures, at a
different step each run: the exam verdict one run, the activation the next, the
invitation email the next. Nothing was broken; each step was simply slower than
the assertion waiting on it. `py-spy dump` is worse than it looks here, because
it pauses the process it samples.

So: run it on its own, and be suspicious of a "flaky" result that arrived while
something else was using the stack. If you need to know whether a slow step is
the server or the test, time the endpoint directly — the API answers these
journeys' calls in tens of milliseconds when nothing else is asking.

## When it runs

Manually, before a release. It needs the whole local stack, so it is not wired
into CI on push. If that changes, run it on a schedule or `workflow_dispatch`,
never with the interview leg.

## Why `npm run e2e` refuses an empty folder

Playwright exits 0 when no spec matches, which reads as "E2E passed" for a suite
that asserted nothing. `scripts/run-e2e.mjs` refuses to run while `e2e/` holds no
specs and hands off to Playwright otherwise.
