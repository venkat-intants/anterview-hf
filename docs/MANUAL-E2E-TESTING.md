# Manual end-to-end testing

A walk through the whole product, in the order the data has to be created, with what
to expect at each step and what a failure would mean.

**Who this is for.** Anyone testing the platform by hand — the product owner before a
demo, a tester before a release, or whoever is checking a deploy did what it said.

**What this is not.** It is not the phase acceptance checklists
(`PH3-/PH4-/PH5-ACCEPTANCE-CHECKLIST.md`). Those are historical records of whether a
phase's criteria were met, written once. This is a procedure you re-run.

---

## ⚠️ READ PART 0 BEFORE STARTING ANYTHING

The `.env` files currently on disk point at **shared and live infrastructure**, not at
local containers. Checked on 2026-10-08, in all four of
`services/*/[.env]`:

| Setting | What it points at today |
|---|---|
| `DATABASE_URL` | the **shared Neon** database — `ep-plain-poetry-b3pcggkm-pooler…neon.tech` |
| `REDIS_URL` | the **live Space's Upstash** — `giving-spider-126027.upstash.io` |
| `S3_ENDPOINT` | live Backblaze B2 |

So running the stack as-is and clicking through it would:

- write your test companies, candidates, applications and **DPDP consent rows** into
  the database the team shares;
- write session and refresh-token keys into **the live Space's Redis**, which is where
  real users' sessions live — a careless `FLUSHDB`, or a collision on a key, logs real
  people out;
- upload your test resumes into the live object store.

`dev-up.ps1`'s own header says *"Datastores are LOCAL containers, not cloud: Postgres
:55432, Redis :6379, MinIO :9000, Mailpit :8025"*. **That describes an intention, not
the files on disk.** Three different sources disagree about the local Postgres port
(`.env.example` says 5433, `dev-up.ps1` says 55432, `docker-compose.yml` declares no
datastore service at all), so there is no single documented local setup to follow.

Part 0 is how you make it safe. Do not skip it.

> **Honesty note on this document.** The journeys, routes, gates and switches below
> were read out of the code on 2026-10-08 and are cited so you can re-check them. The
> Part 0 commands were derived from the repo's own config files rather than executed
> end to end — Docker was not running on this machine when this was written — so treat
> Part 0 as "verify as you go", and correct it here when a step turns out to differ.

---

# Part 0 — Make the environment safe and useful

## 0.1 Start local datastores

Four are needed. `docker-compose.yml` only defines the five application services, so
the datastores are started directly:

```bash
# Postgres with pgvector (the embeddings columns need the extension)
docker run -d --name anterview-pg -p 5433:5432 \
  -e POSTGRES_USER=intants -e POSTGRES_PASSWORD=intants_dev_pw \
  -e POSTGRES_DB=intants_interview \
  pgvector/pgvector:pg16

# Redis
docker run -d --name anterview-redis -p 6379:6379 redis:7-alpine

# MinIO — S3-compatible object store (console on 9001)
docker run -d --name anterview-minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"

# Mailpit — catches every outbound email, web UI on 8025
docker run -d --name anterview-mailpit -p 1025:1025 -p 8025:8025 axllent/mailpit
```

Then enable pgvector:

```bash
docker exec anterview-pg psql -U intants -d intants_interview -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Port collisions to expect.** Another project's containers have previously held
`9000`, `1025` and `8025` on this machine. If a container fails to start, or the stack
hangs at low CPU, run `docker ps -a` and look for `finants-*` containers — **do not
stop or remove those**; change your own published ports instead and update the env
values in 0.2 to match.

## 0.2 Point every service at local, and turn on the testing switches

For each of the four services, edit `services/<service>/.env`:

```ini
APP_ENV=development

DATABASE_URL=postgresql+asyncpg://intants:intants_dev_pw@localhost:5433/intants_interview
DATABASE_SSL=
REDIS_URL=redis://localhost:6379/1

# S3 -> MinIO. NOTE: feedback_billing and admin_ops read S3_ENDPOINT_URL,
# while data_gateway and interview_core read S3_ENDPOINT. Set BOTH in all four,
# or boto3 silently talks to real AWS with these dev keys.
S3_ENDPOINT=http://localhost:9000
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
S3_USE_SSL=false

# Email -> Mailpit. Every message the product sends becomes readable at
# http://localhost:8025 instead of needing a verified sending domain.
EMAIL_PROVIDER=smtp
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USER=
SMTP_PASSWORD=
SMTP_USE_TLS=false

# --- the two switches that make manual testing possible ---
AI_FAKE_MODE=true
TEST_HOOKS_ENABLED=true
TEST_HOOKS_TOKEN=<any string of at least 32 characters>
```

**What those two switches buy you** (`services/data_gateway/app/config.py`, the
"Local testing only" block):

- `AI_FAKE_MODE=true` — resume scoring, question generation, embeddings and match
  reasons return deterministic stand-ins instead of calling out. You can walk the
  whole ATS and exam flow without spending Gemini or Groq quota, and without a
  rate-limited run looking like a product bug.
- `TEST_HOOKS_ENABLED=true` — mounts `/test-hooks`, so you can drive the background
  passes on demand instead of waiting for their timers:
  `POST /test-hooks/reconcile` and `POST /test-hooks/reminders`, each requiring an
  `X-Test-Hooks-Token` header equal to `TEST_HOOKS_TOKEN`. The token must be ≥32
  characters or the service refuses to boot.

Both switches **refuse to start** if `APP_ENV` is production or staging — that is
deliberate, and it is also your proof that you are not pointed at production.

> Run the full journey a **second** time with `AI_FAKE_MODE=false` before you trust a
> release. Fake mode exercises every screen and every state transition, but it does
> not exercise the models, the prompts, or the EN/HI/TE language handling.

## 0.3 Verify you are NOT on shared infrastructure

Do this every time, before you create any data. It takes ten seconds and it is the
step that protects the team's database.

```bash
grep -h "^DATABASE_URL\|^REDIS_URL" services/*/.env
```

Every line must say `localhost`. If any line contains `neon.tech` or `upstash.io`,
**stop** and finish 0.2.

A second, stronger check — the integration-test suite has a guard that refuses to run
against a non-local database, so borrow it:

```bash
cd services/data_gateway && python -m pytest tests/integration -q --collect-only 2>&1 | tail -3
```

If it prints `REFUSING TO RUN: … which is not a local database`, your env is still
pointed at Neon.

## 0.4 Migrate and bootstrap the owner account

```bash
cd services/data_gateway
export PLATFORM_OWNER_PASSWORD='<a password you choose>'
.venv/Scripts/python -m alembic upgrade head
```

The migration `20260625_0002_c3e5f7a9b1d3_platform_owner_hierarchy` creates
`support@intants.com` as a `platform_owner`. It reads the password from
`PLATFORM_OWNER_PASSWORD`; **if that variable is unset it generates a random one and
prints it to stdout exactly once**, so capture the output. `must_change_password` is
set, so the first login will force a rotation — that is the first thing you test.

On a re-run the existing owner's password is never overwritten, so you can migrate
again safely.

## 0.5 Start the stack

```powershell
.\dev-up.ps1
```

Six windows open: `data_gateway :8002`, `interview_core :8001`,
`feedback_billing :8003`, `admin_ops :8004`, the LiveKit worker (no port), and
`web :5174`. The worker must be its own process — it spawns a subprocess per
interview and cannot live inside uvicorn.

**Smoke the stack before testing anything through the UI:**

```bash
for p in 8001 8002 8003 8004; do
  printf "%s -> " "$p"; curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$p/health"
done
curl -s -o /dev/null -w "web -> %{http_code}\n" http://localhost:5174/
```

Five `200`s. Anything else and the journeys below will fail for reasons that have
nothing to do with the feature you are testing. Check that service's window: a
`ValueError` at boot is one of the config validators doing its job, and the message
names the variable.

---

# Part 1 — The journeys

Run them **in order**. Each creates the data the next one needs, which is why a
"quick test of just the interview" usually fails — there is no candidate yet.

Record results as you go using the table in Part 3.

## J1 — Platform owner: the forced rotation, then a company

| | |
|---|---|
| **Console** | `/platform` |
| **Sign in as** | `support@intants.com` |

1. Go to `http://localhost:5174/login` and sign in as the owner.
2. **Expect:** you are sent to `/change-password` and cannot navigate away. This is
   `must_change_password` from the seed. If you land on a console instead, the forced
   rotation is broken — a seeded credential would stay live.
3. Rotate the password. **Expect:** you arrive at `/platform`.
4. Create a company. Give it a name with spaces and capitals, e.g.
   `Acme Skills University`.
5. **Expect:** the company appears with a **slug** derived from the name —
   `acme-skills-university`. Note it down; the careers board lives at
   `/careers/<slug>` and you will need it in J4. There is no rename endpoint, so the
   slug will not change under you.
6. Create **one super admin** for that company. **Expect:** an email in Mailpit
   (`http://localhost:8025`) carrying a set-password link.
7. **Also check:** `/platform` shows the DPDP audit and feature flags, and the owner's
   own `company_id` is null — an owner belongs to no company.

**Why this one matters most:** everything downstream is tenant-scoped to the company
created here. If the slug is wrong, J4 cannot work.

## J2 — Super admin: staff for one company only

| | |
|---|---|
| **Console** | `/superadmin` |

1. Open the set-password link from Mailpit, set a password, sign in.
2. **Expect:** `/superadmin`, headed with the company name from J1.
3. **Expect** the careers-link card, showing the full public URL
   `http://localhost:5174/careers/acme-skills-university`, with copy and open buttons.
   Copy it — that is J4's starting point.
4. Create an **HR manager**. Expect a set-password email in Mailpit.
5. Create an **interviewer**.
6. **Negative check — tenancy.** Try to reach `/platform`. **Expect** to be refused: a
   company super admin is deliberately *not* a superset of the platform owner.
7. **Negative check — remit.** Try `/hr/applicants`. **Expect** to be refused. A
   super admin runs hiring operations; only `hr_manager` is ever shown a named
   candidate. This is a designed boundary, not a bug.

## J3 — HR manager: a requisition through to a published workflow

| | |
|---|---|
| **Console** | `/hr` |

1. Set the HR password from Mailpit, sign in. **Expect** `/hr`, with the same
   careers-link card in the right-hand column, the stat strip, and the attention panel.
2. `/hr/requisitions` → create a requisition. Fill the role, department, location.
3. Send it for **approval**. The super admin approves it at
   `/superadmin/approvals`. **Expect** the requisition's `approval_status` to become
   `approved`; this gate was added in PH3-B2 and nothing publishes without it.
4. Back in `/hr/requisitions/<id>`, build the **workflow** at
   `…/workflow` — stages, rounds, the exam and interview steps.
5. **Publish** the workflow.
6. Turn on **Accept applications** on the requisition dashboard.
7. **Expect**, on the dashboard: the per-opening apply link `…/apply/<requisition-id>`
   with a copy button, and **no** warning. If you see *"will not accept applications
   until a workflow is published"*, step 5 did not take.

**The six gates.** An opening appears on the public board only when all of these hold
(`services/data_gateway/app/publishing.py` — one module, so every surface agrees):

| | gate |
|---|---|
| 1 | `deleted_at IS NULL` |
| 2 | `status = 'open'` |
| 3 | `public_apply_enabled` is on |
| 4 | `approval_status = 'approved'` |
| 5 | `closes_at` is null or in the future |
| 6 | a **published** workflow exists for it |

"I switched it on and nothing shows" is almost always 4 or 6. Walk the list rather
than guessing.

## J4 — The public doors (no login at all)

Use a **private/incognito window**, or you will be testing as the signed-in HR user
and the anonymous paths will never run.

1. Open `/careers/<slug>`.
   - **Expect:** the board, the company name, and the opening from J3.
   - **Then test the empty state:** turn off *Accept applications* in another window
     and reload. **Expect** a polite *"no open roles right now"* — **200, not a 404**.
     A careers board for an active company must never 404.
   - **And an unknown company:** `/careers/does-not-exist` → **404**.
   - **And case:** `/careers/ACME-SKILLS-UNIVERSITY` → still works; a printed link
     gets typed in mixed case.
2. Click through to the role, then **apply with no account**: name, email, and a
   resume PDF.
3. **Expect** a confirmation that does not reveal whether that email was already
   known. Then re-submit the *same* email and compare the two responses — wording,
   fields, and roughly the timing. They must be indistinguishable. Both doors are
   anonymous, so anything they reveal is revealed to whoever typed the address.
4. **Test the draft door too:** start an application, use *Save for later*, then
   resume it from the link and submit. The ticked consent box must survive the round
   trip — it is stored on the draft, not on the form.
5. **Upload checks**, on the apply form:
   - a PDF with a **leading BOM or blank lines** before `%PDF-` → must be **accepted**
     (real mail gateways emit these);
   - a **JPEG renamed `cv.pdf`** → must be **refused with a 400**, not a 500;
   - a file over the size cap → a clean *too large* message, not a hang.

## J5 — ATS screening

Back as the HR manager.

1. `/hr/applicants` — **expect** the application from J4, with the resume text
   extracted and a score (a deterministic stand-in while `AI_FAKE_MODE=true`).
2. Open the applicant. Check the resume renders and the extracted text is sensible.
3. **Bulk upload:** several resume PDFs at once. Expect a 202 and the candidates
   appearing as the background pass processes them. Caps are 500 files / 250 MB per
   batch — a batch over either must be refused with a clear message.
4. `/hr/pipeline` — move the candidate between stages and confirm the counts on `/hr`
   follow.
5. `/hr/stages-at-risk` — expect it to list every at-risk application, not a
   fixed-length sample.

## J6 — Exam round

1. `/hr/question-banks` → create a bank. Add questions **three ways**, because each is
   a separate code path:
   - **manually**;
   - by **AI generation** (deterministic in fake mode);
   - by **import** — download the template, fill it, upload it.
2. **Import edge cases, all of which should now behave:**
   - a `.csv` **renamed `.xlsx`** → refused with *"is it a valid .xlsx or .csv?"*;
   - a real `.xlsx` **renamed `.csv`** → **imported correctly** (dispatch is by
     content, not by name);
   - a CSV saved from a non-English Excel (**Windows-1252**, with an accented
     character) → **imported**, with a replacement character in that cell;
   - a CSV with a **stray NUL byte in one cell** → that **row** is reported and *the
     others still import*;
   - a question whose text begins **`BMI,`** → **imported** (it is a question, not a
     bitmap);
   - a **PDF** uploaded to the importer → refused with a 400.
3. Questions needing review go to `/hr/question-banks/reviews`; a super admin reviews
   at `/superadmin/question-reviews`. **Two-person rule:** confirm the author cannot
   approve their own question.
4. `/hr/exams` → author an exam from the bank, attach it to the requisition's exam
   round, and tick **Require camera proctoring**.
5. Send the exam link. Open it in a private window (`/exam`).
   - **Expect** the camera-consent notice **before** the exam starts. Decline →
     cannot start. Accept → the exam opens.
   - **Accept a second time** on another round: **expect** the response to say
     *already granted* — and an `exam.camera_notice.accepted` row to exist in
     `audit_log` **anyway**, once per round. The grant mints no new ledger row, so the
     acceptance itself is the evidence.
6. Take the exam. Trigger a proctoring event deliberately (look away, or open another
   tab). **Expect** the event recorded — and **no video frame ever leaving the
   browser**; check the Network tab to confirm.
7. `/hr/exams/<id>/results` — grading, the attempt detail, and the proctoring summary
   with its severity weighting.

## J7 — The live interview

This is the one that needs real credentials and real attention.

1. `/hr/interviews` → generate an interview invite for the candidate. Copy the magic
   link.
2. Open it in a private window (`/interview-invite`).
   - **Expect** the consent notice, the **avatar picker** (6 avatars, 3 male / 3
     female), and the **language choice** (EN / HI / TE).
3. Start the interview.
   - **Expect:** the avatar renders and speaks; lip-sync tracks the audio; your
     microphone is picked up; the interviewer asks a question, listens, and asks an
     intelligent follow-up.
   - **Watch the clock.** On the free Tavus plan the avatar is cut at **~3 minutes**
     mid-session. That is a vendor quota, not a bug. **Expect the session to continue
     voice-only** rather than dying — that fallback is the thing to verify.
   - **Measure turn latency.** The NFR is **p95 under 2 seconds** from you finishing
     speaking to the avatar starting. Time several turns with a stopwatch; it is the
     one number a demo lives or dies on.
4. Run it once per language. HI and TE have had no native-speaker review, so judge
   whether the questions are *comprehensible*, and log anything that is not.
5. Let it reach the end (~10 minutes), or end it.
6. **Expect** `/interview/<id>/complete`, then a **scorecard** at `/scorecard/<id>`
   with the four canonical axes, and a **downloadable PDF**.
7. `/hr/panel` — the four-specialist assessment panel. **Expect** no field anywhere
   that expresses hire or reject: `decision_authority` is the literal `"human_only"`,
   by design, because DPDP scrutinises automated decision-making.

## J8 — Decision, offer, preboarding

1. `/hr/requisitions/<id>/decisions` — record a decision with a reason from the
   governed vocabulary (`/superadmin/decision-reasons` defines it). **Expect** free
   text to be refused where the vocabulary applies.
2. `/hr/offer-templates` → a template; `/hr/offers` → an offer. Super admin approves at
   `/superadmin/offer-approvals`.
3. Send the offer. Open the candidate link (`/offer`) in a private window. Accept it.
4. **Preboarding:** upload a requested document as the candidate. **Expect** the
   consent step first, and the document to appear for HR at `/hr/offers/<id>`.
5. `/task` — a job-simulation or portfolio task through its magic link, if the
   workflow has one.

## J9 — Analytics and the copilots

1. `/hr/analytics` — the funnel, quality-of-hire, channel analytics. Numbers must
   agree with what you created: if you made one application, the funnel says one.
2. `/admin/overview`, `/admin/analytics`, `/admin/interviews` as the `admin` role.
3. **The copilots** (HR, super-admin, platform, analytics). Ask each one something.
   - **Expect** citations on claims, and **proposals rather than actions**: an agent
     can draft, never write. When you approve a proposal it fires with *your*
     credentials through the normal endpoint — watch the Network tab and confirm the
     request is yours.
   - **Negative check:** ask the super-admin copilot to name a specific candidate.
     **Expect** it to be unable to. Only `hr_manager` is ever offered a tool that
     returns a named candidate.
   - If a copilot answers but never looks anything up, that is a `GROQ_MODEL` problem
     — not every Groq model supports tool calling.
4. `/hr/library` — upload a PDF, DOCX and TXT to the document corpus; tag one *HR
   only* and one *all company staff*. Ask a copilot something the document answers and
   **expect a citation back to it**. Confirm an *HR only* document never surfaces to a
   non-HR console.

## J10 — DPDP rights

The compliance story, and the part a government buyer will actually probe.

1. As the candidate, open `/consent` and review what was recorded.
2. **Withdraw consent — and note that there is no screen for this.**

   `web/src/api/consent.ts` exports only `getConsentStatus` and `postConsent`; a
   repo-wide search finds **no frontend caller of `DELETE /consent`**, and
   `PH4-ACCEPTANCE-CHECKLIST.md` says so too. The route is fully implemented and
   tested; the product has no button for it. **That absence is itself a finding to
   re-raise, not a gap in this document** — a DPDP §11 right that is only exercisable
   by hand-crafting a request is not meaningfully available to a candidate.

   So test it with a request. Take the candidate's access token from DevTools
   (Application → the auth cookie, or the `Authorization` header on any XHR) and:

   ```bash
   curl -i -X DELETE http://127.0.0.1:8002/consent \
     -H "Authorization: Bearer <candidate access token>"
   ```

   Then check:
   - every active consent of those types is revoked;
   - in-flight sessions are stamped `consent_withdrawn`;
   - `audit_log` has a **`dpdp_consent.withdrawn` row per revoked consent**, naming
     the type, the door (`DELETE /consent`), the scope, and how many sessions it
     stopped — and carrying **no IP address and no user agent**, because audit rows
     survive erasure;
   - **it applied at every company**, not one. That is deliberate: DPDP §11 is
     "without restriction".
3. **Erasure:** submit a request, then confirm the candidate's free-text fields are
   redacted and the audit trail survives.
4. **Retention:** `RETENTION_DRY_RUN` defaults to **`true`**, so the nightly purge
   *logs what it would delete and deletes nothing*. Confirm the log, then decide
   separately whether to flip it — that is a posture decision, not a test step.
5. Check the consent modal links to `docs/DATA-FLOW.md`'s sub-processor record, and
   that what the modal promises matches what the code does.

## J11 — Negative and security checks

These are quick, and each one is a real defect that was fixed — worth confirming the
fix is actually deployed in what you are testing.

| Check | Expect |
|---|---|
| Upload a **1 KB PDF whose `/Pages` tree is a DAG** declaring thousands of pages | refused or truncated in **under a second**; login stays responsive throughout |
| Upload a **1 KB DOCX with ~20,000 nested paragraphs** to `/hr/library` | parsed in well under a second |
| Hammer `/auth/change-password` with wrong current passwords | rate-limited (429), not unlimited |
| Paste PEM key material into `JWT_SECRET` and restart | **401 with a log line naming the key problem** — never a 500 on every request |
| Set `REDIS_URL` to `redis://` a **remote** host with `APP_ENV=production` | the service **refuses to boot** |
| Set `OPENPYXL_DEFUSEDXML=false` and import an `.xlsx` | refused; and the boot log carries a CRITICAL line |
| Upload a PDF **over the Caddy cap** for its prefix | a clean 413, and the cap is never *below* the handler's own limit |
| Sign in as `hr_manager` and request an `admin` route | refused |

---

# Part 2 — Known limits, so you do not chase ghosts

These are real constraints of the current demo tier. None is a bug to file.

| Limit | What you will see |
|---|---|
| **Resend** delivers only to one verified address until `intants.com` is verified | new-account set-password emails **silently fail** on the live Space. Locally, Mailpit catches everything — which is why Part 0 points there |
| **Tavus** free plan | the avatar cuts at **~3 minutes** mid-interview. The session should fall back to voice-only |
| **Sarvam** credits are account-level, not per-key | a new key does not restore a drained account |
| **Gemini** free tier is ~10 RPM | the assessment panel fires 5 concurrent calls and 429s. A new free key will **not** help; use `AI_FAKE_MODE` or `LLM_PROVIDER=groq` |
| **Groq** serves no embeddings API | semantic applicant search needs `GEMINI_API_KEY` whatever `LLM_PROVIDER` says |
| `AVATAR_PROVIDER=custom` | not built. The worker logs `unknown avatar_provider…; falling back to simli` as a **warning** and runs on a US-hosted avatar — see `docs/ACCEPTED-RISKS.md` AR-4 |
| **Neon** is serverless and billed by wake-ups | if the live Space 503s on every route, check the database quota before suspecting the deploy |
| Staff consoles are **English-only** by design | only candidate-facing text is EN/HI/TE. Not a translation gap |

---

# Part 3 — What to record

One row per journey. The middle column is the one that matters: a bare ✅ six months
from now tells nobody anything.

| Journey | What you actually saw | Result |
|---|---|---|
| J1 Platform owner → company + super admin | | |
| J2 Super admin → HR + interviewer, tenancy refusals | | |
| J3 Requisition → approval → published workflow | | |
| J4 Careers board + both anonymous apply doors | | |
| J5 ATS screening + bulk upload | | |
| J6 Exam: bank, import edge cases, camera proctoring | | |
| J7 Live interview, per language + turn latency | | |
| J8 Decision → offer → preboarding | | |
| J9 Analytics + copilots (citations, no writes) | | |
| J10 DPDP: consent, withdrawal, erasure, retention | | |
| J11 Negative and security checks | | |

For anything that fails, capture: the route, the exact message, the service window's
log lines, and whether `AI_FAKE_MODE` was on. Those four facts are usually enough to
find the cause without reproducing it.

**Before calling a release good,** re-run J4, J6 and J7 with `AI_FAKE_MODE=false`.
Fake mode proves the screens and the state machine. It proves nothing about the
models, the prompts, or the languages.
