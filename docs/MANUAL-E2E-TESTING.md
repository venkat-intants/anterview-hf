# Manual end-to-end testing — on the live Hugging Face Space

A walk through the whole product **as deployed**, at
`https://venkat95-anterview-hf.hf.space`, in the order the data has to be created,
with what to expect at each step and what a failure would mean.

**Who this is for.** Anyone testing the platform by hand — the product owner before a
demo, a tester before a release, or whoever is checking a deploy did what it said.

**What this is not.** Not the phase acceptance checklists
(`PH3-/PH4-/PH5-ACCEPTANCE-CHECKLIST.md`), which are one-time records of whether a
phase's criteria were met. This is a procedure you re-run.

---

# Part 0 — What the Space lets you do, and what it does not

Part 0 is not setup. The Space is already running; there is nothing to install. It is
here because **three things about the deployed environment change how you test**, and
two of them will stop you in the first five minutes if you do not know them.

## 0.1 Everything you create is permanent, real, shared data

The Space runs against the production datastores: the Neon database, the Upstash
Redis, Backblaze object storage. There is no reset button and no separate test tenant.

So before you start:

- Every company, candidate, application and **DPDP consent row** you create is a real
  row in the real database, alongside anything else the team has.
- Give test data obviously-test names — `ZZ Test College 2026-10-08`, not
  `Acme College` — so whoever looks at this database next can tell what is yours.
- Resumes you upload go into the live bucket.
- Interviews consume real Tavus, Sarvam and Gemini quota, all of which are limited
  (Part 2).
- **Do not run a DPDP erasure or a retention purge against anything you did not
  create.** Erasure is designed to be irreversible.

If any of that is unacceptable for the test you have in mind, the alternative is a
local stack — but note that the four `services/*/.env` files in this repo point at
these same production datastores, so "running locally" does **not** by itself give you
an isolated environment. That is a separate piece of work and this document does not
cover it.

## 0.2 The two testing switches do not exist here, by design

`space/entrypoint.sh:102` sets `APP_ENV=production` by default. Two switches that make
testing easier elsewhere are therefore **impossible** on the Space —
`validate_test_only_switches` in `services/data_gateway/app/config.py` refuses to boot
if either is on in production:

| Switch | What it would do | On the Space |
|---|---|---|
| `AI_FAKE_MODE` | deterministic scores, questions, embeddings — no model calls | **Unavailable.** Every AI call is real and costs real quota |
| `TEST_HOOKS_ENABLED` | `/test-hooks/reconcile`, `/test-hooks/reminders` on demand | **Unavailable.** Background passes run only on their own timers |

Two consequences for your test plan:

- **Rate limits are part of the test.** If scoring or question generation fails, check
  Part 2 before filing a bug — the Gemini free tier is ~10 RPM and the assessment
  panel fires five concurrent calls.
- **Anything scheduled cannot be forced.** Retention, scheduled publishing, reminder
  sweeps and the nightly watchers fire on their timers. You can verify the *effect* a
  day later; you cannot trigger them now.

## 0.3 The email wall — read this before you create any account

**This is the thing that stops most people in the first five minutes.**

HF Spaces block outbound SMTP (ports 25/465/587) at the network level, so no SMTP
relay can ever deliver from a Space — `space/entrypoint.sh:112-116` says so, and sets
`EMAIL_PROVIDER=smtp` by **default**, which it then describes as leaving email
"queuing-but-undeliverable on Spaces". Only an HTTPS API (Resend) can deliver, and
Resend in sandbox delivers **only to the one verified address** until `intants.com` is
verified.

And both consoles create staff accounts **without a password on purpose**
(`PlatformOwnerConsole.tsx:116` — *"No password sent: the server generates a random
bootstrap hash and emails a 'set your password' link. Never ship a known default."*).
The UI's own toast says *"a set-password link was emailed."*

So: **creating a super admin or an HR manager through the UI gives you an account you
cannot log into.** Not a bug — a correct security decision colliding with an
undeliverable transport.

### The workaround: create accounts through the API with a password you choose

`CreateUserBody` (`services/data_gateway/app/routers/admin_hr.py:116`) accepts an
optional `password`. Supply one and no email is needed. `must_change_password` stays
true, so the first login still forces a rotation — you are not creating a standing
weak credential, you are choosing the bootstrap value instead of letting it be random
and unknowable.

You need a `platform_owner` access token first. Sign in as the owner in the browser,
open DevTools → Network, click any authenticated request, and copy the `Authorization:
Bearer …` header value.

```bash
SPACE=https://venkat95-anterview-hf.hf.space
OWNER_TOKEN='<paste the platform_owner bearer token>'

# 1. A company (platform_owner only). Note the slug in the response.
curl -s -X POST "$SPACE/admin/companies" \
  -H "Authorization: Bearer $OWNER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"ZZ Test College 2026-10-08"}'

# 2. Its one super admin — WITH a password, so no email is required.
curl -s -X POST "$SPACE/admin/companies/<company-id>/admin" \
  -H "Authorization: Bearer $OWNER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"zz-sa@example.test","full_name":"ZZ Super Admin","password":"<choose one>"}'
```

Then sign in as that super admin, take *their* token, and create the HR manager and
interviewer the same way:

```bash
SA_TOKEN='<the super admin bearer token>'

curl -s -X POST "$SPACE/admin/hr-managers" \
  -H "Authorization: Bearer $SA_TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"zz-hr@example.test","full_name":"ZZ HR","password":"<choose one>"}'

curl -s -X POST "$SPACE/admin/interviewers" \
  -H "Authorization: Bearer $SA_TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"zz-iv@example.test","full_name":"ZZ Interviewer","password":"<choose one>"}'
```

Each first login lands on `/change-password` and forces a rotation. That is the
intended behaviour and the first thing J1 asks you to confirm.

**If you would rather test the email path itself** — which is worth doing at least
once — create the account with the single Resend-verified address as its email, and
watch for the set-password message. Everything else in this document works without
email.

### Checking whether email went anywhere

As the platform owner there are two views for exactly this question:

```
GET /admin/email-events            # recent sends and their outcomes
GET /admin/email-events/summary    # the counts
```

Use them whenever an expected email does not arrive, before assuming the feature is
broken. A message recorded as accepted-but-undelivered is the Resend sandbox limit;
nothing recorded at all is the SMTP default.

## 0.4 Before you start: is the Space even up?

```bash
SPACE=https://venkat95-anterview-hf.hf.space
curl -s -o /dev/null -w "health  -> %{http_code}\n" "$SPACE/health"
curl -s -o /dev/null -w "SPA     -> %{http_code}\n" "$SPACE/"
curl -s -o /dev/null -w "auth/me -> %{http_code}\n" "$SPACE/auth/me"
```

Expect `200`, `200`, `401`. The `401` is correct — it proves the API is routed and
enforcing auth rather than serving the SPA shell.

**If every route returns 503 or the Space shows `RUNTIME_ERROR`:** check the Neon
database quota before suspecting the deploy. Neon is serverless and billed by
wake-ups; when it is over plan, `space/entrypoint.sh`'s `alembic upgrade head` aborts
the container before any service starts, and every route 503s. That has happened
before and looked exactly like a bad release.

Also note the Space **sleeps** when idle. The first request after a sleep can take
tens of seconds while it wakes — that is not a latency bug, and it is worth warming it
with the curl above before timing anything in J7.

---

# Part 1 — The journeys

Run them **in order**. Each creates the data the next needs, which is why "a quick
test of just the interview" fails — there is no candidate yet.

All paths below are relative to `https://venkat95-anterview-hf.hf.space`.

## J1 — Platform owner: the forced rotation, then a company

| | |
|---|---|
| **Console** | `/platform` |
| **Sign in as** | the `platform_owner` account (`support@intants.com`) |

1. Go to `/login` and sign in as the owner.
2. If this is a fresh credential, **expect** to land on `/change-password` and be
   unable to navigate away — that is `must_change_password`. If you reach a console
   instead, the forced rotation is broken and a bootstrap credential stays live.
3. **Expect** `/platform` after rotating.
4. Create a company with a name containing spaces and capitals, e.g.
   `ZZ Test College 2026-10-08`.
5. **Expect** a **slug** derived from the name — `zz-test-college-2026-10-08`. Write it
   down: the careers board lives at `/careers/<slug>` and J4 needs it. There is no
   rename endpoint, so it will not change under you.
6. Create its **one** super admin — via the API with a password (0.3), unless you are
   deliberately testing email.
7. **Also check:** `/platform` shows the companies table, the feature flags and the
   DPDP audit; the owner's own `company_id` is null, because an owner belongs to no
   company.

## J2 — Super admin: staff, and the boundaries

| | |
|---|---|
| **Console** | `/superadmin` |

1. Sign in as the super admin; rotate the password when prompted.
2. **Expect** `/superadmin`, headed with the company name from J1.
3. **Expect the careers-link card**, showing the full public URL
   `https://venkat95-anterview-hf.hf.space/careers/<slug>`, with copy and open
   buttons. Copy it — that is J4's starting point.
4. Create an **HR manager** and an **interviewer** (API, with passwords).
5. **Negative — tenancy.** Try `/platform`. **Expect refusal.** A company super admin
   is deliberately *not* a superset of the platform owner.
6. **Negative — remit.** Try `/hr/applicants`. **Expect refusal.** A super admin runs
   hiring operations; only `hr_manager` is ever shown a named candidate. Designed
   boundary, not a bug.

## J3 — HR manager: requisition → approval → published workflow

| | |
|---|---|
| **Console** | `/hr` |

1. Sign in as HR, rotate the password. **Expect** `/hr` with the careers-link card in
   the right-hand column, the stat strip and the attention panel.
2. `/hr/requisitions` → create a requisition (role, department, location).
3. Send it for **approval**; approve it as the super admin at `/superadmin/approvals`.
   **Expect** `approval_status` to become `approved` — nothing publishes without it.
4. `/hr/requisitions/<id>/workflow` → build the workflow: stages, rounds, the exam and
   interview steps.
5. **Publish** the workflow.
6. Turn on **Accept applications** on the requisition dashboard.
7. **Expect** the per-opening apply link `…/apply/<requisition-id>` with a copy button
   and **no warning**. If you see *"will not accept applications until a workflow is
   published"*, step 5 did not take.

**The six gates.** An opening appears publicly only when all of these hold
(`services/data_gateway/app/publishing.py` — one module, so every surface agrees):

| | gate |
|---|---|
| 1 | `deleted_at IS NULL` |
| 2 | `status = 'open'` |
| 3 | `public_apply_enabled` is on |
| 4 | `approval_status = 'approved'` |
| 5 | `closes_at` is null or in the future |
| 6 | a **published** workflow exists for it |

*"I switched it on and nothing shows"* is almost always 4 or 6. Walk the list.

## J4 — The public doors (no login at all)

Use a **private window**, or you will be testing as the signed-in HR user and the
anonymous paths never run.

1. Open `/careers/<slug>`.
   - **Expect** the board, the company name, and the opening from J3.
   - **Empty state:** turn off *Accept applications* in another window and reload.
     **Expect** a polite *"no open roles right now"* — **200, not 404**. A board for an
     active company must never 404.
   - **Unknown company:** `/careers/does-not-exist` → **404**.
   - **Case:** `/careers/ZZ-TEST-COLLEGE-2026-10-08` → still works. Printed links get
     typed in mixed case.
2. Click through to the role and **apply with no account**: name, email, resume PDF.
3. **Expect** a confirmation that does not reveal whether that email was already
   known. Re-submit the **same** email and compare the two responses — wording,
   fields, and roughly the timing. They must be indistinguishable. Both doors are
   anonymous, so whatever they reveal is revealed to whoever typed the address.
4. **The draft door too:** start an application, *Save for later*, resume from the
   link, submit. The ticked consent box must survive the round trip — it is stored on
   the draft, not the form.
5. **Upload checks:**
   - a PDF with a **leading BOM or blank lines** before `%PDF-` → **accepted** (real
     mail gateways emit these);
   - a **JPEG renamed `cv.pdf`** → **400**, not 500;
   - a file over the cap → a clean *too large*, not a hang.

## J5 — ATS screening

Back as HR.

1. `/hr/applicants` — **expect** the J4 application with resume text extracted and a
   score. **This is a real model call** — if it is missing or errored, check Part 2's
   rate limits before filing anything.
2. Open the applicant; confirm the resume renders and the extracted text is sensible.
3. **Bulk upload** several resume PDFs. Expect a 202 and candidates appearing as the
   background pass works through them. Caps are 500 files / 250 MB per batch; over
   either, expect a clear refusal.
4. `/hr/pipeline` — move a candidate between stages; confirm `/hr`'s counts follow.
5. `/hr/stages-at-risk` — expect **every** at-risk application, not a fixed-length
   sample.

## J6 — Exam round

1. `/hr/question-banks` → create a bank. Add questions **three ways** — each is a
   separate code path:
   - manually;
   - by **AI generation** (a real model call here);
   - by **import** — download the template, fill it, upload it.
2. **Import edge cases, all of which should behave:**
   - a `.csv` **renamed `.xlsx`** → refused, *"is it a valid .xlsx or .csv?"*;
   - a real `.xlsx` **renamed `.csv`** → **imported** (dispatch is by content, not
     name);
   - a CSV from a non-English Excel (**Windows-1252**, accented character) →
     **imported**, with a replacement character in that cell;
   - a CSV with a **stray NUL in one cell** → that **row** is reported and the others
     still import;
   - a question beginning **`BMI,`** → **imported** (it is a question, not a bitmap);
   - a **PDF** uploaded to the importer → 400.
3. Review flow: `/hr/question-banks/reviews`, then `/superadmin/question-reviews`.
   **Two-person rule:** confirm an author cannot approve their own question.
4. `/hr/exams` → author an exam from the bank, attach it to the requisition's exam
   round, tick **Require camera proctoring**.
5. Open the exam link in a private window (`/exam`).
   - **Expect** the camera-consent notice **before** the exam starts. Decline → cannot
     start. Accept → it opens.
   - **Accept again on another round:** expect *already granted* — **and** an
     `exam.camera_notice.accepted` audit row to exist anyway, once per round. The
     second grant mints no ledger row, so the acceptance itself is the evidence.
6. Take the exam. Trigger a proctoring event deliberately (look away, switch tab).
   **Expect** the event recorded — and **no video frame leaving the browser**. Confirm
   in the Network tab; that guarantee is the product's headline proctoring claim.
7. `/hr/exams/<id>/results` — grading, attempt detail, proctoring summary with its
   severity weighting.

## J7 — The live interview

The one that needs real credentials, real quota and real attention.

1. **Warm the Space first** (0.4) — a cold start will ruin your latency numbers.
2. `/hr/interviews` → generate an interview invite; copy the magic link.
3. Open it in a private window (`/interview-invite`).
   - **Expect** the consent notice, the **avatar picker** (6 avatars, 3 male / 3
     female — read from `app.avatars.AVATARS`), and the **language choice** (EN/HI/TE).
4. Start the interview.
   - **Expect:** the avatar renders and speaks; lip-sync tracks the audio; your
     microphone is picked up; the interviewer asks, listens, and follows up
     intelligently.
   - **Watch the clock.** On the free Tavus plan the avatar is cut at **~3 minutes**
     mid-session. That is a vendor quota, not a bug. **The session must continue
     voice-only** — that fallback is the thing being verified.
   - **Measure turn latency.** The NFR is **p95 under 2 seconds** from you finishing
     speaking to the avatar starting. Time several turns with a stopwatch. It is the
     one number a demo lives or dies on, and on the Space it includes real network
     latency to Mumbai/Singapore, which is the honest figure.
5. Run once per language. HI and TE have had **no native-speaker review** — judge
   whether the questions are comprehensible and log anything that is not.
6. Let it reach the end (~10 minutes) or end it.
7. **Expect** `/interview/<id>/complete`, then a **scorecard** at `/scorecard/<id>`
   with the four canonical axes and a **downloadable PDF**.
8. `/hr/panel` — the four-specialist panel. **Expect no field anywhere that expresses
   hire or reject:** `decision_authority` is the literal `"human_only"`, because DPDP
   scrutinises automated decision-making. This is structural, not a prompt promise.

## J8 — Decision, offer, preboarding

1. `/hr/requisitions/<id>/decisions` — record a decision using a reason from the
   governed vocabulary (`/superadmin/decision-reasons` defines it). **Expect** free
   text to be refused where the vocabulary applies.
2. `/hr/offer-templates` → a template. `/hr/offers` → an offer. Approve at
   `/superadmin/offer-approvals`.
3. Send the offer; open the candidate link (`/offer`) in a private window; accept.
4. **Preboarding:** upload a requested document as the candidate. **Expect** the
   consent step first, and the document visible to HR at `/hr/offers/<id>`.
5. `/task` — a job-simulation or portfolio task via its magic link, if the workflow
   has one.

## J9 — Analytics and the copilots

1. `/hr/analytics` — funnel, quality-of-hire, channel analytics. Numbers must agree
   with what you created: one application means the funnel says one.
2. `/admin/overview`, `/admin/analytics`, `/admin/interviews` as the `admin` role.
3. **The copilots** (HR, super-admin, platform, analytics):
   - **Expect** citations on claims, and **proposals rather than actions** — an agent
     can read and draft, never write. Approving a proposal fires it with **your**
     credentials through the normal endpoint; watch the Network tab and confirm the
     request is yours.
   - **Negative:** ask the super-admin copilot to name a specific candidate. **Expect
     it to be unable to.** Only `hr_manager` is ever offered a tool that returns a
     named candidate.
   - If a copilot answers but never looks anything up, that is a `GROQ_MODEL` problem
     — not every Groq model supports tool calling.
4. `/hr/library` — upload a PDF, DOCX and TXT; tag one *HR only* and one *all company
   staff*. Ask a copilot something a document answers and **expect a citation back to
   it**. Confirm an *HR only* document never surfaces to a non-HR console.

## J10 — DPDP rights

The compliance story, and what a government buyer will actually probe.

**On the Space, be careful here:** erasure is irreversible. Run it only against the
test candidate you created in J4.

1. As the candidate, open `/consent` and review what was recorded.
2. **Withdrawal — and note there is no screen for it.**

   `web/src/api/consent.ts` exports only `getConsentStatus` and `postConsent`; nothing
   in the frontend calls `DELETE /consent`, and `PH4-ACCEPTANCE-CHECKLIST.md` says so
   too. The route is implemented and tested; the product has no button.
   **That absence is itself a finding to raise** — a DPDP §11 right exercisable only by
   hand-crafting a request is not meaningfully available to a candidate.

   ```bash
   curl -i -X DELETE "$SPACE/consent" \
     -H "Authorization: Bearer <candidate access token>"
   ```

   Then check:
   - every active consent of those types is revoked;
   - in-flight sessions are stamped `consent_withdrawn`;
   - `audit_log` has a **`dpdp_consent.withdrawn` row per revoked consent**, naming the
     type, the door (`DELETE /consent`), the scope, and how many sessions it stopped —
     carrying **no IP and no user agent**, because audit rows survive erasure;
   - **it applied at every company**, not one. Deliberate: DPDP §11 is "without
     restriction".
3. **Erasure:** submit a request for your test candidate; confirm free-text fields are
   redacted and the audit trail survives.
4. **Retention:** `RETENTION_DRY_RUN` defaults to **`true`**, so the nightly purge logs
   what it would delete and deletes nothing. You cannot trigger it on the Space (0.2) —
   check the log the next day. Whether to flip it is a posture decision, not a test
   step.
5. Confirm the consent modal links to `docs/DATA-FLOW.md`'s sub-processor record, and
   that what the modal promises matches what the code does.

## J11 — Negative and security checks

Each is a real defect that was fixed. Worth confirming the fix is in what you are
testing. **Four of these are not runnable on the Space** — they need an env change and
a restart, which on a Space means editing secrets and redeploying. Do those in a
pre-production environment instead; they are listed so nobody assumes they were
covered.

| Check | Expect | On the Space? |
|---|---|---|
| **1 KB PDF whose `/Pages` tree is a DAG** declaring thousands of pages, to the apply form | refused or truncated in **under a second**; login stays responsive throughout | ✅ yes |
| **1 KB DOCX with ~20,000 nested paragraphs** to `/hr/library` | parsed in well under a second | ✅ yes |
| Hammer `/auth/change-password` with wrong current passwords | rate-limited (429), not unlimited | ✅ yes |
| A PDF **over the Caddy cap** for its prefix | a clean 413 | ✅ yes |
| `hr_manager` requesting an `admin` route | refused | ✅ yes |
| PEM key material in `JWT_SECRET` | **401 with a log line naming the key problem** — never a 500 on every request | ❌ needs a restart |
| `REDIS_URL=redis://` a remote host with `APP_ENV=production` | **refuses to boot** | ❌ needs a restart |
| `OPENPYXL_DEFUSEDXML=false` then import an `.xlsx` | refused, and a CRITICAL boot line | ❌ needs a restart |
| `AI_FAKE_MODE=true` with `APP_ENV=production` | **refuses to boot** | ❌ needs a restart (and is itself the 0.2 guarantee) |

---

# Part 2 — Known limits, so you do not chase ghosts

Real constraints of the current demo tier. None is a bug to file.

| Limit | What you will see |
|---|---|
| **HF blocks outbound SMTP** network-wide | with `EMAIL_PROVIDER=smtp` (the default) email queues and never delivers. See 0.3 |
| **Resend** delivers only to one verified address until `intants.com` is verified | new-account set-password emails do not arrive. Check `/admin/email-events` |
| **Tavus** free plan | the avatar cuts at **~3 minutes** mid-interview; the session should fall back to voice-only |
| **Sarvam** credits are account-level, not per-key | a new key does not restore a drained account |
| **Gemini** free tier ~10 RPM | the panel fires 5 concurrent calls and 429s. A new free key will **not** help |
| **Groq** serves no embeddings API | semantic applicant search needs `GEMINI_API_KEY` whatever `LLM_PROVIDER` says |
| `AVATAR_PROVIDER=custom` | not built. The worker logs `unknown avatar_provider…; falling back to simli` as a **warning** and runs on a US-hosted avatar — `docs/ACCEPTED-RISKS.md` AR-4 |
| **Neon** is serverless, billed by wake-ups | every route 503 / `RUNTIME_ERROR` usually means the DB is over quota, not a bad deploy |
| **The Space sleeps** when idle | the first request after a sleep takes tens of seconds. Warm it before timing J7 |
| Staff consoles are **English-only** by design | only candidate-facing text is EN/HI/TE. Not a translation gap |

---

# Part 3 — What to record

One row per journey. The middle column is the one that matters — a bare ✅ six months
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
| J10 DPDP: consent, withdrawal, erasure | | |
| J11 Negative and security checks (the five runnable ones) | | |

For anything that fails, capture: the route, the exact message, the HTTP status, and
the time (so it can be matched to the Space's logs). On the Space you cannot read a
service window, so the timestamp is what makes a failure findable afterwards.

**What this pass does not cover,** and should not be claimed as covered: the four
restart-dependent checks in J11, and anything on a timer — retention, scheduled
publishing, reminder sweeps, the nightly watchers. Those need an environment where
`TEST_HOOKS_ENABLED` is allowed.
