# PH3 — acceptance checklist against `docs/AntHire-Phase3.docx`

**Built:** 2026-09-16. **Checked against:** every acceptance criterion in the Phase 3
document, in the document's own order and wording.

**Key:** ✅ done and verified · ⚠️ done, with something you should know · ❌ not done

**How each line was verified** is named, because "done" without that is an opinion.
`unit` = a test in `services/data_gateway/tests/unit/`; `smoke` =
`tests/integration/smoke_ph3_apply.py`, which runs the real endpoints against a real
Postgres 16 and passes 57/57; `db` = asserted directly against the migrated schema;
`e2e` = a Playwright journey in `web/e2e/`, driving the real browser against the real
stack.

**Enforced since 2026-10-05.** This paragraph used to say the opposite — "Run on
demand, NOT in CI — `.github/workflows/ci.yml` has no Playwright step, so nothing gates
these on a pull request… read that as 'this was demonstrated', not 'this is enforced',
until the job exists." The job now exists: `browser (playwright e2e)` in `ci.yml` stands
up Postgres, Redis, Mailpit and MinIO, migrates, runs data_gateway and the dev server,
and runs the suite. It is in `ci-ok`'s `needs`, which is the single required status that
`sync-to-space.yml` gates the deploy on — so every `e2e` citation below is now checked
on a pull request, and a regression in one blocks the deploy rather than waiting for
somebody to remember.

Two caveats that keep this honest. `coding-round` skips by design when no code runner is
up, so it is enforced only where one is. And `review-round` is quarantined with
`test.fixme` — it fails after a released hold and the cause is narrowed but not closed;
the reasoning travels with the spec and in `web/e2e/COVERAGE.md`. Neither is a Phase 3
criterion, and all eight Phase 3 specs pass.

**Added 2026-09-27: `e2e`.** Phase 3 shipped with none. Seven journeys now cover it,
and writing them found three things no unit test could have: the reapplication rule
never ran through the real form, every workflow panel refreshed a cache entry the page
around it was not reading, and three of the four fields the CV parser produces never
reached the screen that asks the candidate to check them. All three are fixed. The
lesson is in the third: each was ✅ on the strength of a test written beside the code
rather than against the product.

---

## Summary

| Story | Criteria | Done | Notes |
|---|---:|---:|---|
| PH3-B1 Source tracking | 10 | 10 | |
| PH3-B2 Requisition approval & budget | 12 | 12 | |
| PH3-B3 JD versioning | 13 | 13 | |
| PH3-B4 Application lifecycle & scheduled publishing | 17 | 17 | 2 ⚠️ closed 2026-09-27 |
| PH3-B5 Candidate confirmation | 12 | 12 | |
| PH3-B6 JD Studio versioning | 13 | 13 | |
| **Total** | **77** | **77** | **0 ⚠️, 0 ❌** |

Plus one story that is not in your document: **PH3-B0**, the shared publish gate. See
the last section — it was pre-work, and it turned out to be a bug fix.

---

## PH3-B1 — Source Tracking

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Application records support a candidate source/channel | ✅ | `enrolments.source` + `source_detail`, migration `c3e5a7b9d1f4`; `db` |
| 2 | Public application URLs can contain a source identifier | ✅ | `GET /apply/{id}?src=…`; `smoke` |
| 3 | The source identifier is captured when the candidate starts/applies | ✅ | echoed on the posting, sent back on submit; `smoke` + `e2e` |
| 4 | Source is persisted against the application | ✅ | written in `enrol_applicant`, the only enrolment-creating code path; `unit` |
| 5 | Source remains associated through workflow progression | ✅ | written once at creation, never rewritten — nothing carries it forward, so nothing can drop it |
| 6 | Existing applications without source information continue to work | ✅ | column is `NOT NULL DEFAULT 'unknown'`; historical rows read as `unknown`; `db` |
| 7 | Source data is company/requisition scoped appropriately | ✅ | on `enrolments`, already company-scoped by composite FK |
| 8 | Available for future funnel and quality-of-hire analytics | ✅ | index `ix_enrolments_source (company_id, source, created_at)` is PH5-C1's exact access path |
| 9 | Tracking does not expose unnecessary candidate personal information | ✅ | channel is a closed vocabulary; `source_detail` is charset-bounded at the database; not on any candidate-facing schema; `unit` |
| 10 | Tests cover tracked and untracked applications | ✅ | 38 tests in `test_ph3_source_tracking.py` |

**Worth knowing.** `unknown` and `direct` are deliberately different values. `unknown`
means nobody was tracking (every pre-PH3 row); `direct` means we were, and the person
arrived with no campaign attached. Collapsing them would make the day this shipped look
like a sudden surge in direct applications. A URL cannot claim to be `unknown` — that
would let a tracked arrival hide in the historical bucket.

An unrecognised `?src=` is recorded as `other` with the raw value kept, never refused: a
malformed campaign tag is the recruiter's mistake, and losing a real applicant to it
would be the worse outcome.

---

## PH3-B2 — Requisition Approval & Budget

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | A requisition has an explicit approval state | ✅ | `approval_status`, migration `e5a7c9d1f3b6` |
| 2 | Supported approval states are clearly defined | ✅ | `draft / pending_approval / approved / rejected`, CHECK-constrained; `db` |
| 3 | An authorized user can submit a requisition for approval | ✅ | `POST /hr/requisitions/{id}/approval/submit`; `smoke` |
| 4 | An authorized approver can approve or reject | ✅ | `…/approval/approve` and `…/reject`; `smoke` |
| 5 | Approval actions are auditable | ✅ | `AuditLog` on submit, approve and reject, with the note; `unit` |
| 6 | Records who approved/rejected and when | ✅ | `approval_decided_by_user_id` + `approval_decided_at`, CHECK-tied so they cannot disagree; `db` |
| 7 | Budget information can be associated with a requisition | ✅ | `budget_amount / currency / basis / period / notes` |
| 8 | Budget constraints do not replace `target_hires` | ✅ | separate columns, both retained; `unit` |
| 9 | Publishing respects the approval state | ✅ | the gate is in `app/publishing.py`, so all three public surfaces get it at once; `smoke` proves the board, the posting and the draft endpoint all refuse |
| 10 | Unauthorized users cannot approve | ✅ | the approve/reject routes require `super_admin`; an `hr_manager` fails the dependency before the handler runs |
| 11 | Existing requisitions continue to function safely | ✅ | grandfathered to `approved` by the migration — `db` proves a live opening stayed live across it |
| 12 | Tests cover approval, rejection, authorization and publishing | ✅ | 44 tests in `test_ph3_requisition_approval.py` + 8 `smoke` checks |

**Worth knowing — this is stronger than the document asked for.** A user holds exactly one
role, and `create_requisition` requires `hr_manager` while approving requires
`super_admin`. So no single account can both raise and approve a requisition: the
separation of duties is a property of the role model, not a check somebody has to
remember to write. The "cannot approve your own submission" branch I first wrote turned
out to be unreachable and was deleted rather than shipped as dead code.

**One operational dependency you should know about.** A company with no active
`super_admin` has nobody who can approve, and HR's submissions will queue. That is logged
as a warning at submission time rather than refused — blocking HR would not conjure an
approver, and the queue recovers the moment the platform owner provisions one.

**Budget is never public.** Unlike `salary_min`/`salary_max` there is no visibility flag,
because there is no version of a careers page that should carry a hiring budget. A test
asserts no candidate-facing schema carries the field, and that the public queries do not
even select it.

---

## PH3-B3 — JD Versioning

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | A requisition can have multiple JD versions | ✅ | `jd_versions`, migration `d4f6b8c0e2a5` |
| 2 | Each version has a unique version identifier/number | ✅ | `UNIQUE (requisition_id, version)`; `db` |
| 3 | Records when each version was created | ✅ | `created_at` |
| 4 | Records who created/updated the version | ✅ | `created_by_user_id`, resolved to a name in the history view |
| 5 | One version is clearly identified as current | ✅ | partial unique index — at most one `published` row, enforced by the database; `db` |
| 6 | Publishing associates the requisition with the version | ✅ | `job_requisitions.published_jd_version_id`, written in the same transaction as the content copy |
| 7 | Updating creates a new version rather than destroying the previous | ✅ | `record_edit` demotes then inserts; `unit` |
| 8 | Previous versions remain viewable | ✅ | `GET …/jd/versions` returns full content, newest first; `e2e` reads the history in the console |
| 9 | Historical versions cannot be accidentally overwritten | ✅ | reverting is a *publish* of the old version, not an edit of it; `e2e` restores v1 and finds v2 still there |
| 10 | Existing requisitions without version history continue to work | ✅ | `db` — an opening with no JD gets no version, and still functions |
| 11 | History is company/requisition scoped | ✅ | composite FK; `db` proves a cross-tenant insert is refused |
| 12 | **Workflow evaluation criteria remain frozen** | ✅ | asserted three ways: the module, the routes and the migration all contain no `round_criteria` write; `unit` |
| 13 | Tests cover creation, update, publishing and retrieval | ✅ | 31 tests in `test_ph3_jd_versions.py` |

**Worth knowing — the backfill was tested properly.** Rows were seeded *before* the
migration ran and then migrated, which is the only honest way to test a backfill. Results:
an opening with a full advert became published v1 attributed to its creator with its own
`created_at` (not `now()` — claiming the JD was written today would be a false provenance
record in the table that exists to provide provenance); an opening whose advert was only a
skills list still got a version; an opening with no advert at all got none, because
inventing an empty v1 would put a row in a history that never happened.

---

## PH3-B4 — Application Lifecycle & Scheduled Publishing

### Save & Resume

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Candidate application drafts can be saved | ✅ | `application_drafts`, migration `c9e1b3d5f7a2`; `smoke` |
| 2 | Candidate can leave and return later | ✅ | resume link; `smoke` proves a reload keeps progress |
| 3 | Draft data is preserved | ✅ | `smoke` |
| 4 | Candidate can continue from where they left off | ✅ | `/apply/draft#<token>` + `X-Draft-Token`; `smoke` |
| 5 | Draft expiration behavior is defined | ✅ | 30 days, extended on each save, stored not computed |

### Reapplication Rules

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 6 | Requisition supports configurable reapplication rules | ✅ | `reapply_cooldown_days` |
| 7 | Organizations can define cooldown periods | ✅ | "Reapplying" in the opening's Budget & approval panel; blank = no waiting period, 0 = zero, deliberately different; `web` + `e2e` |
| 8 | Cooldown validation occurs during application | ✅ | `e2e` through the real form — which is how the defect below was found; `smoke` |
| 9 | Authorized users can override cooldown restrictions | ✅ | "Let *name* reapply for *role*" on a rejected applicant, with an optional reason; `web` + `e2e` |
| 10 | Existing applications remain unaffected | ✅ | no cooldown configured ⇒ no query is even issued; `unit` |

### Scheduled Publishing

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 11 | Requisition supports a future publish date/time | ✅ | `publish_at`, migration `f6b8d0e2a4c7` |
| 12 | System automatically publishes at the scheduled time | ✅ | adaptive sleep; `e2e` sets a schedule and **waits** for it, telling the publisher nothing; **see below** |
| 13 | Authorized users can modify the schedule | ✅ | `PUT …/publish-schedule` replaces |
| 14 | Cancelled schedules do not publish | ✅ | `DELETE …/publish-schedule`; idempotent; `e2e` |
| 15 | Publishing actions are audited | ✅ | set / updated / cancelled / executed, the last attributed to whoever scheduled it |
| 16 | Draft/cooldown/publishing behaviour is tested | ✅ | 47 + 23 + 27 tests; 40/40 `smoke` |

**✅ Criterion 12 — closed 2026-09-16.** Openings now publish **within a second or two of
the chosen time while the service is running**, and shortly after it next wakes if the
service is asleep.

It previously published within about *one minute*, because the loop slept a fixed
interval and so simply was not looking at 09:00:00 — the tolerance was the whole
interval, every time, for no reason inherent to the design. `_sleep_seconds` now sleeps
until the next schedule actually falls due, **capped at the interval**, which is what
keeps the two properties the fixed sleep had: the loop-pass heartbeat that makes a
stalled publisher visible keeps its cadence, and a schedule created *during* a sleep is
picked up no later than it would have been before. Never worse; usually exact. The cost
is one extra indexed `MIN()` probe per pass — a real cost, not a saving.

This is not a shortcut. `app/scheduling.py` documents the reason at length: a clock
trigger cannot fire while the container is suspended, and the demo Hugging Face Space
sleeps after ~48h — a job pinned to 09:00 is *skipped*, silently, not delayed. So the
publisher is an interval loop, which wakes and finds the work still waiting. On Tier-2
(AWS EKS, always warm) the tolerance is the one-minute figure.

The API returns that sentence alongside any scheduled time, and the console renders it —
a UI that showed only "09:00" would be making a promise the architecture does not offer.

**The gate is re-checked when it fires.** A requisition approved on Monday and rejected on
Tuesday does not publish on Wednesday. It keeps its schedule and logs a warning rather
than being silently unscheduled or silently published.

---

**✅ Criteria 7 and 9 — closed 2026-09-27.** They were marked ✅ on 2026-09-16 on the
strength of an API field and an API endpoint, and corrected to ⚠️ on 2026-09-17: the
criteria say *"Organizations can define"* and *"Authorized users can override"*, and an
HR manager could do neither from the product. The backend was real; the capability the
criteria describe was not reachable by the people they name.

Both are now in the console. The waiting period is a field in the opening's Budget &
approval panel, where blank and 0 mean different things — no waiting period at all, and
a waiting period of zero days — and the override is a two-step action on a rejected
applicant, named for the person and the role so it cannot be pressed on the wrong row.

**And the rule itself did not work.** Closing this meant driving it through the real
form for the first time, which is when `reapply-cooldown.spec.ts` failed: the public
apply endpoint checked "have you applied here before" before it checked the cooldown,
and returned `already_applied` to anyone who had ever been rejected. So nobody was
refused by the waiting period, and nobody was let back in by an override either — the
whole feature was unreachable from the only door candidates use. The `smoke` test that
passed had soft-deleted the enrolment first, which skips that branch.

Both doors — the one-shot form and a saved draft being finished — now share one
predicate (`reapplication.gate`), because they had two hand-written copies of this
decision and only one of them was fixed.

**And a reapplication no longer takes effect on submission.** A security audit found
that it could not: these endpoints are anonymous and identify a person by an address
typed into a public form, and the requisition id is documented as not a secret. So one
unauthenticated request could move a real person's status, overwrite the screening
answers they had already given, attach a stranger's CV to their application, spend an
override HR had granted them, and create a talent-pool consent they never gave.
`final_decision.py` refuses an *authorised* HR manager from hiring over a rejection on
the grounds that reopening someone is "a separate decision this does not make on
anyone's behalf"; letting an anonymous caller make it was the same decision with less
authority behind it.

The application is still accepted anonymously and the cooldown still decides whether it
is accepted at all. What changed is that it is STAGED on the rejected enrolment
(`reapplication.stage`) and applied by `reapplication.confirm` only when a link emailed
to the address is followed — a dedicated `reapply_confirm` token, because
`stage_activation_email` mints nothing for someone who has already claimed their
account and their reapplication would have waited for ever.

The cooldown is re-evaluated at confirmation rather than trusted from submission, and
the override is spent only when it is what allowed the reapplication.

**What a candidate sees.** One screen and one sentence, for every submission. Not
"identical for a refusal and a live application" — identical full stop, for all four
states an address can be in: live application, rejected inside the waiting period,
rejected past it, and never applied here at all.

That is stronger than it was, and it had to be. The reply was made to *match* across
those cases three times, and three times a difference survived somewhere adjacent: a
409 naming the date, then `awaiting_confirmation` plus the real applicant and enrolment
ids, then the draft row left readable. Each time the reply was still computed from
stored state, so the branches stayed reachable — the fourth review found that a rejected
address was answered "accepted" on *every* submission for ever while an address that had
never applied was answered "accepted" once and "already applied" from the second time
on. Two anonymous requests, and a stranger knew a named person had been turned down.

So the reply is now a constant. `ApplicationOut` carries four fields, none of which
depends on what is stored about the address, and every route returns the same object
through one helper. Everything that genuinely differs — you already have an application
with us, you were turned down, you may apply again on this date, confirm this really is
you — is emailed to the address, which is the only place it is the reader's to know.

Held down by `tests/integration/test_ph3_cooldown_indistinguishable.py`: all four states,
both doors, up to three submissions each, comparing status and whole body, plus what
`GET /apply/draft` reads back afterwards.

---

## PH3-B5 — Candidate Application Confirmation

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Resume parsing continues to extract candidate information | ✅ | `app/resume_details.py`; `smoke` reads a real PDF |
| 2 | Extracted information is presented for review | ✅ | all five fields the parser produces — **three were missing until 2026-09-27**, see below; `web` + `e2e` |
| 3 | Candidate can edit incorrect extracted information | ✅ | every field editable; `web` tests |
| 4 | Required fields must be completed before confirmation | ✅ | name required, everything else optional; `smoke` |
| 5 | Candidate explicitly confirms | ✅ | `POST …/confirm`, timestamped |
| 6 | Only confirmed information is used for submission | ✅ | `smoke` + `e2e` — the CV says one name, the candidate corrects it, and HR's console shows the correction |
| 7 | Candidate corrections are persisted | ✅ | `smoke` — their name and years of experience, not the CV's |
| 8 | The existing DPDP consent flow remains intact | ✅ | strengthened, not preserved — see PH3-B4c below |
| 9 | Does not break when parsing produces incomplete information | ✅ | unparsed fields render empty and never block; `unit` + `web` |
| 10 | Existing applications remain compatible | ✅ | the one-shot `POST /apply/{id}` path is unchanged and still works; `smoke` uses it for the cooldown checks |
| 11 | Candidate-facing text follows existing localization | ✅ | EN/HI/TE; **see below** |
| 12 | Tests cover confirmation, corrections, missing fields, incomplete parsing | ✅ | 47 `unit` + 17 `web` + 8 `smoke` |

**✅ Criterion 11 — closed 2026-09-16.** The draft carries the candidate's language
choice, the emails it triggers honour it, and the confirmation screen's own copy is now in
the i18n bundles: **52 keys across EN / HI / TE**, wired through `useTranslation` exactly
as every other localised page is.

Four tests cover it, and they assert the **rendered strings**, not the presence of keys —
a key present in `en` and missing in `hi` falls back to English silently, which is
precisely the bug, and would pass any test that only checked a key existed. One of them
pins key parity across all three bundles and fails by name (`hi.keepIt was left in
English`) if someone adds an English key and forgets the other two.

**These HI/TE strings carry the same caveat as every other bundle in `i18n.ts`, stated at
the top of that file: they are a first pass for UI coverage and need native-speaker review
before a production or government-bid launch.** That is the repo's existing policy for
HI/TE, not a new exception carved out for this screen. The erasure keys additionally carry
a higher bar — see the `i18n.ts` header — because a mistranslated irreversible delete is a
different kind of mistake from a mistranslated button label. One was already found and
fixed on that basis: the Telugu `deleteDesc` said "cannot be **cancelled**" where the
English and Hindi say "cannot be undone".

**What criterion 11 does NOT cover, stated so this ✅ is not read as more than it is.**
The screen's own copy is fully localised. **Server error text is not.** `errText()` prefers
an `Error.message` over its fallback, and `ApiError.message` is always set — to the
backend's `detail` string, or failing that to a literal `HTTP {status}` — so in every
realistic API failure the English `detail` from `data_gateway` wins over the localised
fallback. Only the two purely client-side validation messages (`errTooBig`, `errNotPdf`,
set without a round trip) are reliably translated.

That is deliberate rather than an oversight, and there is an existing test
(`ResumeApplication.test.tsx:254-266`) that depends on it: showing the server's specific
reason — "you applied before, try again after 2026-12-05" — beats a translated but useless
"could not send your application". Localising every `HTTPException(detail=...)` across
`data_gateway` for EN/HI/TE is real cross-cutting work and is tracked separately rather
than smuggled into this criterion.

**Now closed too: `PublicApply.tsx` and `Careers.tsx`.** Both predate PH3 (added
2026-09-07 in `076fe06`), so they were pre-existing debt rather than a PH3 criterion — but
leaving them meant a candidate who had chosen हिंदी saw English on the advert and the apply
form, then Hindi at the confirmation step, which is not a shippable experience. Both are
now localised: **151 keys across three namespaces × three languages.** The advert reuses
the board's `careers.*` formatters rather than keeping a second copy of the same strings.

The dev-only seeding panel in `PublicApply.tsx` is deliberately **not** localised. It is
excluded from production bundles and is addressed to whoever is running the seeder.

**⚠️ → ✅ Criterion 2 — corrected 2026-09-27.** The screen presented the parsed NAME.
`extract_contact_details` has always returned five fields — name, email, phone,
LinkedIn and GitHub — and the upload handler stored all of them, but `_draft_out`
carried two. So a form headed *"We read these from your CV. Please correct anything
that is wrong"* showed one of the four things it had read, and the candidate typed in a
phone number the parser already had.

The test that should have caught this is the reason it survived. It read
`assert set(ParsedDetails.model_fields) == {"full_name", "email"}` — a remembered list,
written beside the shape it was describing, agreeing with it by construction. It now
calls the parser on a CV and requires the screen to offer exactly the keys that come
back, which fails in both directions: a field that goes missing, and a box the parser
can never fill.

All five are now seeded into the form where the candidate has not already given us
something, each with the *"Your CV says …"* line underneath when the two disagree. The
CV is quoted, never overwritten.

**On the parsed field list (your doc's PH3-B5b).** Your document lists Name, Email, Phone,
Location, Education, Experience, Skills as examples. The parser produces **name, email,
phone, LinkedIn and GitHub** deterministically. It does **not** call a language model, and
that is deliberate: `public_apply`'s own rule is that nothing in the application request
path may wait on one, because "an applicant lost because the model was down would be the
worst possible failure for this endpoint" — and a confirmation screen shown *during* the
application is the same path. Location, education and skills would need either an LLM call
in that path or a much less reliable heuristic; showing an empty box labelled "Education"
that can never fill in would be worse than not asking. The ATS scorer still runs
afterwards in the reconciler, unchanged.

---

## PH3-B6 — JD Studio Versioning

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | JD Studio supports multiple JD versions | ✅ | `JdVersionPanel`, mounted under `PostingEditor` |
| 2 | Each version has a unique version number | ✅ | `db` |
| 3 | A recruiter can create/edit a draft version | ✅ | `PUT …/jd/draft`; `web` tests |
| 4 | Existing published versions remain preserved | ✅ | `unit` |
| 5 | The current published version is clearly identified | ✅ | labelled in the header and the history list |
| 6 | A new edit does not overwrite historical content | ✅ | `unit` |
| 7 | Recruiters can view previous versions | ✅ | history list with content, author, dates |
| 8 | Publishing clearly identifies the active JD | ✅ | `unit` + `web` |
| 9 | History records creator and timestamp | ✅ | `web` test asserts the author renders |
| 10 | Existing JD Studio editing continues to work | ✅ | `PATCH /hr/requisitions/{id}` still publishes immediately; 11 existing `PostingEditor` tests unchanged and passing |
| 11 | **JD changes do not silently modify frozen workflow criteria** | ✅ | same three-way assertion as B3-12 |
| 12 | Existing requisitions remain compatible | ✅ | `db` |
| 13 | Versioning behavior is covered by automated tests | ✅ | 13 `web` + 31 `unit` |

**On "JD Studio".** There was no surface by that name. The requisition editor
(`PostingEditor`) is it, and PH3-B6's own Task 7 asks for exactly what was built: *"Add
version information to the existing JD Studio rather than creating a completely separate
interface."*

**Two ways to edit, and the difference is visible.** "Save posting" publishes immediately,
as it always has — a dozen screens depend on that and your compatibility criterion
requires it; what changed is that the previous wording is now kept instead of destroyed.
"Save draft" does not touch what candidates see, because the public surfaces read the
requisition and a draft does not write to it. That is the whole mechanism.

---

## PH3-B0 — the shared publish gate (not in your document)

Pre-work I added before B2 and B4a, because both add a clause to the same predicate.
It turned out to be a bug fix.

`public_apply_enabled AND status = 'open'` was hand-written in seven modules, and the
copies had **already drifted**: the apply endpoint required a published workflow and the
careers board and candidate feed did not. So the board advertised openings that returned
404 on click — the one failure a job board cannot have. The cross-check test that existed
compared three of the five gates and never saw it.

There is now one predicate in `app/publishing.py`, a test that fails the build if another
module starts spelling it out again, and a cross-check that asserts identity rather than
similarity. PH3-B2's approval gate and PH3-B4a's schedule gate were each one line in one
tuple as a result.

`scheduled-publishing.spec.ts` closes the loop in the browser: it reads the careers
board, clicks what is on it, and requires the apply page to open. That is the failure
in its original shape — a listing and a click — rather than a comparison of predicates,
and it is the only form of the test that would have failed on the day the drift
appeared.

---

## Code review — findings and fixes (2026-09-16)

The `code-reviewer` agent CLAUDE.md requires before merge returned
**REQUEST CHANGES**, and it was right. What it found and what was done:

**MUST FIX — a candidate could be permanently unable to save a draft.**
`users.email` is UNIQUE across the whole platform, not per company. The
draft-only guest-user path stored the candidate's REAL address there, so the
second company anyone ever drafted at — or the first, if that address already
belonged to any account anywhere on the platform — hit the unique index, and
the router's blanket `except` turned it into a 503 with no way past it. The
applicant path next door has always minted `guest+{uuid}@applicants.invalid`
for exactly this reason; the draft path now does too, and the real address
lives only on `application_drafts.email`.

Finding the same person again therefore cannot be a lookup by email on `users`
— there is no real address there any more — so it is a lookup on the drafts
that company already holds for that address. Without that, somebody drafting
for a second opening at the same company would mint a second identity and a
second consent record.

**Why the original 40/40 smoke test did not catch it:** it used one company and
a fresh address, which is precisely the case that does not collide. The smoke
test now seeds a second company *and* a pre-existing real account holding the
candidate's address, and is 46/46. Reverting the fix makes it fail — verified,
not assumed.

**Also fixed from the same review:**

- A double-click on "save and finish later" raced two inserts against
  `uq_application_drafts_live`; the loser got a 503. It now resolves to
  "you already have a draft, here it is", which is what the person wanted.
- `update_requisition` called `_owned()` twice — once to check existence and
  again to read `approval_status` the first call had already fetched.
- Migration `c9e1b3d5f7a2` carried a comment claiming `RESTRICT` above code
  that says `CASCADE`. The migration has already run, so the schema is the
  fact: the comment now describes `CASCADE` and says why it is right (erasure
  anonymises rather than deletes, so it never fires there; it is the backstop
  for any future hard-delete path).
- The new `job_requisitions` and `enrolments` columns existed only in raw
  migrations. They are now declared on the ORM classes too, so the next person
  who reaches for the ORM does not get a silent `AttributeError`.

**What the review confirmed as correct:** tenant isolation on every new route,
the consent invariant, the single publish gate, auth on approve/reject, token
handling, no SQL injection, all six `downgrade()` paths, and that no
candidate-facing response leaks budget, approval notes or other applicants.

---

## Security audit — findings and fixes (2026-09-16)

Merging deploys to the live Space, so CLAUDE.md hard constraint 4 applied and
`security-auditor` ran before merge. It returned **BLOCKED** with one CRITICAL,
and it was right.

### CRITICAL — unauthenticated takeover of a candidate's in-progress application

`POST /apply/{id}/draft` treated the **email in the request body as proof of
identity**. It resolved that address to an existing `user_id`, and if that person
had a live draft, `start()` rotated the token and returned the row. One
unauthenticated request — with the apply link, which is not a secret, plus a
candidate's address — yielded their name, phone, current employer, job title,
profile links, every screening answer and their CV filename, **plus a working
token** to alter the draft, replace the CV and submit an application in their
name. The victim's own link died silently. Rate limiting was no defence: one
request sufficed.

**Fixed by removing the class of bug, not the symptom.** Identity is no longer
derived from anything the caller says. Every call mints a fresh draft with a
fresh guest identity; the returned token is the only way back to it. The reuse
branch in `application_drafts.start()` is deleted rather than guarded, and
`_draft_only_guest_user` no longer accepts an email at all, so there is nothing
left to match on.

The cost, stated plainly: saving twice makes two drafts, each reachable only by
its own link, and somebody who loses their link cannot recover it here — that
would mean proving ownership of an address, which this endpoint cannot do. The
draft expires in 30 days. That is a worse experience than the vulnerable
version, and it is the correct trade.

### HIGH — right to erasure silently failed

`apply_activation._link_to_existing` re-pointed `applicants.user_id` and the
consent ledger when a guest activated into an account they already had, but not
`application_drafts.user_id`. Both erasure hooks keyed on `user_id`, so the
draft — name, phone, employer, CV — survived a **completed** erasure and its CV
object was never collected for deletion. Fixed on both sides: activation now
re-points the draft, and the executor matches on `user_id`, the erased address
*and* the linked applicant, so it no longer depends on that repair being correct.

### HIGH — unauthenticated CPU exhaustion

`resume_details.py` ran quadratic regexes over the entire CV text, synchronously
on the event loop, from an anonymous upload. Measured 720ms for the email pattern
alone on 32 KB and 1.6s for the profile patterns, unbounded above that — one
upload could stall every request on the worker, health probe included. Fixed
three ways: bounded repetition in every pattern, input truncated to 20,000
characters, and the call moved to `asyncio.to_thread` with a wall-clock timeout
(the same treatment `_extract_pdf_text` already gets). Now flat at ~9ms whether
the input is 32 KB or 2 MB.

### Also fixed

- **Consent could be recorded against an unverified identity** — resolved by the
  CRITICAL fix. Submission now additionally records consent against the identity
  that ends up *owning* the application, so an audit finds it by real user id.
- **A draft-only data principal had no way to act.** DPDP gives a right to erase,
  not merely to be forgotten on a schedule. `DELETE /apply/draft/{token}` added —
  token-authenticated, deletes the CV object with the row.
- **The draft submit path sent no confirmation email**, dropping a control the
  one-shot path's own comment names: the email to the address on file is how the
  real owner hears about an application they did not make. Added.
- `public_gate_open` defaulted `approval_status` to approved — a fail-open
  default in the module that decides public visibility. Now required, **and
  all three call sites updated** — see the re-review below, because the first
  attempt at this broke them.
- `start_draft` shared a rate-limit bucket with the PATCH routes.
- The mailer now refuses `@applicants.invalid` recipients.

**Verified by walking the actual attack** through the real endpoints against real
Postgres: a stranger who knows the victim's email learns nothing, gets a token
that opens only an empty draft of their own, and the victim's link keeps working.

### What this says about the testing

The 46/46 smoke test drove the happy path and never asked "what if someone else
types this email?". The review found in one pass what those tests were
structurally incapable of seeing — the same shape as the earlier `users.email`
bug, one level up. Both are now covered by tests that fail if the behaviour
returns.

---

## Security re-review (2026-09-16) — two more, one of them mine

The re-review confirmed the CRITICAL, the erasure gap and the ReDoS were
genuinely fixed (it re-measured the regex timings rather than taking the claim).
It returned **STILL BLOCKED** on two HIGHs.

### HIGH — my own fix broke three live call sites

Making `approval_status` required on `public_gate_open` was correct. Updating
only one of the four call sites was not. `company_board.py`, `requisition_dashboard.py`
and `agents/watch_runner.py` still passed the old argument list, so every one of
them raised `TypeError` — a **500 on the HR company board and the requisition
dashboard, and a crashing nightly watcher**, on deploy. None of those modules
even selected the column, so the repair was query *and* call site.

**Why nothing caught it.** 1,509 unit tests passed and `mypy` reported success.
My own test for this asserted `"public_gate_open" in source` — it grepped for a
*name*, and a name cannot tell you whether the call still type-checks. The root
`mypy.ini` CI uses cannot see missing arguments across `app.*` either.

Replaced with a test that parses every call site with `ast` and binds its
keywords against the real signature. It names the file and line, catches the
whole class rather than this instance, and was verified to go red when the fix
is reverted.

### HIGH — the draft path rewrote a returning applicant's record

`submit_draft` updated an existing applicant's row when the submitted address
matched: overwriting `full_name` unconditionally and stamping
`full_name_source='candidate'` and `details_confirmed_at`, which assert to the
reconciler and to HR that the real person confirmed those values. An anonymous
caller with the apply link and somebody's address could rename them in a
company's ATS, back-fill their empty fields and give it false provenance.

The one-shot path refuses exactly this, and its comment records it as a
previously-fixed bug: *"this form replaced their CV, target role, contact
details and scores for anyone who typed their email address, with no proof of
who they were."* The draft path had re-introduced it. Now neither writes: the
draft's values stay on the draft, the CV belongs to the application via
`applied_resume_s3_key`, and the record changes only after activation proves the
address.

### The four "follow-up" items were done too, not deferred

The reviewer accepted these as non-blocking with an owner and a date. They are
closed instead:

- **A stranger could re-grant consent somebody had withdrawn.** The idempotence
  check filtered `revoked_at IS NULL`, so a withdrawal was only sticky against
  people who had not withdrawn. It now matches any prior row, granted or
  revoked; re-consenting still works, through the authenticated consent router,
  rather than as a side effect of a stranger filling in a form.
- **The draft-delete endpoint had no UI**, so the right existed in the API and
  not in the product. Added to the resume page, two-step, with a confirmation of
  what was removed.
- **"Fresh identity per save" had no cleanup.** Every `start_draft` minted a
  guest user and a consent row, unauthenticated and unbounded — and the consent
  ledger is the artefact a DPDP audit reads. The retention pass now also deletes
  orphan guest identities and submitted drafts past a 90-day window, and
  **drains** rather than stopping at 500, which a capped single pass could not.
- **The token rode in the URL path.** The reviewer was right that this was
  *against* this repo's precedent, not following it: exam and interview auth use
  `X-Exam-Token`/`X-Interview-Token` headers with the token in the URL
  *fragment*. Now `X-Draft-Token` plus `/apply/draft#<token>`, so the credential
  never reaches an access log, an edge log or a `Referer`.

Moving those routes surfaced one more thing, caught by testing rather than
review: `/apply/draft` was being shadowed by `/apply/{requisition_id}`, because
FastAPI matches in declaration order. The module docstring had warned about
exactly that for `/apply/activate` and I had ignored it. The block moved above
the parameterised routes, with a test that asserts the ordering.

Two more from the reviewer's list, also closed: the third erasure predicate was
algebraically identical to the first — a route claimed in the comments and
absent from the SQL — and is now a real one keyed on the linked applicant's
address; and the executor's step ORDER (drafts before `users.email` is
anonymised, keys before rows) now has a test, because it was load-bearing and
unasserted.

---

## What I could not verify, and why

Stated plainly so nobody reads this checklist as claiming more than it proves.

1. ~~Nothing has been deployed.~~ **Merged 2026-09-16** — PR #23 into `main` as
   `76e8dc2`, which triggers `sync-to-space.yml` and force-pushes the live Space.
   **The deploy's own outcome is unverified**: the permission gate blocked every
   deploy-status check after the merge, so "it merged" is proven and "the Space
   is serving it" is not. Check the Space before treating PH3 as live.
2. ~~The migrations have not been run against your shared Neon database.~~
   **Done 2026-09-16.** All six applied; head at `c9e1b3d5f7a2`. Verified after:
   all 7 requisitions grandfathered to `approved`, 0 publicly live before and 0
   after (nothing went dark), all 8 enrolments reading `source = 'unknown'`, no
   NULLs introduced.
3. **No real person has completed an application on a real phone.** Still open, and
   still the item no amount of further work here can close — jsdom does not lay out,
   so nothing in the test suite can prove a page *looks* right on a handset.

   What has been closed is the part automation genuinely can reach:
   `CandidatePhone.test.tsx` audits all three candidate pages for widths a 360px
   phone cannot fit (`w-[420px]`, `min-w-[400px]`) and for multi-column grids with no
   responsive prefix — the two most common ways a form starts scrolling sideways on a
   handset. 360px is the reference because it is the common Android width in the
   target market, and a test pins the constant so it cannot be widened to make a
   failure disappear. Both checks were verified to fail when the defect is introduced.
4. ~~`ops/ci/check_coverage_floors.py` and the rest of the `invariants` CI job were
   not run.~~ **Run 2026-09-16.** All green, plus a new gate
   (`ops/ci/check_routing_contract.py`) described below.
5. ~~The Hindi and Telugu copy for the new candidate screens does not exist.~~
   **Done 2026-09-16** — 52 keys across EN/HI/TE, native-speaker review still
   required before launch (the standing policy for every HI/TE bundle in this
   repo). `PublicApply.tsx` / `Careers.tsx` remain English-only, but both predate
   PH3; see PH3-B5 criterion 11.

---

## The deploy path (added after the final security pass)

The security audit's blocking findings were not in the feature code — they were
in the two config files that decide whether the feature is reachable at all, and
none of them is visible to a test that runs a service:

| | Was | Now |
|---|---|---|
| `/apply*`, `/careers*` in `Caddyfile` + `space/Caddyfile` | **absent since the routers landed** — on the Space an XHR fell through to `try_files {path} /index.html` and returned index.html with **HTTP 200**, so the whole public candidate surface shipped dead *and reported success* | proxied to `data_gateway:8002` in both; browser navigation still the SPA's via the pre-existing `@spa_nav` rewrite |
| `X-Draft-Token` in both Caddy log filters | absent — a live 30-day credential to a named candidate's name, phone, employer, screening answers and CV written verbatim into a persistent access log (CWE-532), undoing the `#fragment` design | redacted in both |
| `X-Draft-Token` in `allow_headers` | absent — every draft call, including the DPDP self-serve delete, blocked at CORS preflight on every split-origin deploy (`render.yaml`, Oracle/Vercel, local Vite). Same-origin Space would have stayed green | allowed |
| 90-day purge of submitted drafts | queued the CV object for deletion, but `submit_draft` hands that same key to `applicants.resume_s3_key` and `enrolments.applied_resume_s3_key` — so every Save & Resume applicant lost their CV 90 days after applying | only `status = 'draft'` objects are deleted; pinned by 5 tests |
| The "already applied" branch | nothing adopts the uploaded CV, so once the purge stopped deleting it, it had no deletion path at all | released and deleted inline, pointer cleared before the object |

Both Caddyfiles and the CORS list were hand-maintained and had now lagged the
code twice (precedent: 09483b5, *"the agent layer was dead on the Space"*). That
class is now a build failure: `ops/ci/check_routing_contract.py` asserts every
`APIRouter(prefix=...)` is matched by a `handle` block in **both** Caddyfiles and
that every `Header(alias="X-…")` is both redacted and CORS-allowed. It has 16
tests of its own, and was verified to go red on the exact pre-fix state.

---

## Production hardening (after the deploy)

Everything the reviews raised as non-blocking has now been closed:

| Was | Now |
|---|---|
| **Scheduled publishing could starve, across tenants.** Nothing clears `publish_at` when a requisition stops being publishable, so a closed or rejected one is permanently past due — and the claim query is `ORDER BY publish_at LIMIT 100`, so those sort *first*. A hundred of them platform-wide and every pass skipped a hundred and published nothing, for ever. One company's abandoned schedules could stall another's openings. | The claim query filters on `approval_status` and `status`. Fixed in the **one query** rather than by clearing `publish_at` at each transition — that alternative needs every future call site to remember, which is the same hand-maintained-invariant shape that has already lagged this repo twice. Blocked rows stay visible via a bounded `blocked_backlog` count, warned **on change** rather than once a minute for ever. |
| **`delete_draft` claimed an erasure it had not performed.** It deleted the rows, committed, then tried the object and swallowed any failure into a warning nothing reads. On a storage outage a candidate was told in three languages that their CV was removed while it sat in the bucket — and the row that pointed at it was already gone, so nothing could find it again. | The object goes **first**, and its failure fails the request with a 503 that says nothing was removed. The remaining window — object deleted, commit fails — leaves a row pointing at an absent key, which is visible, harmless to retry, and strictly the better failure. |
| `escapeValue: false` was safe only while no `dangerouslySetInnerHTML` existed anywhere. | `react/no-danger` is a lint **error**. The invariant is enforced, not remembered. |
| The `_sleep_seconds` docstring claimed "never worse than the fixed interval". | True of *lateness*, false of *load* — the pass-frequency ceiling rose ~60×. Now scoped explicitly. |
| Telugu said the delete "cannot be **cancelled**". | Now "cannot be taken back", matching the English and Hindi. |

---

## Verification totals

Measured against `main` **as merged into this branch**, not against the branch's
original fork point. The first run of these numbers was taken on a base that
predated `96b51bf` and so silently omitted `test_phase2_pipeline_defects.py` and
two web suites — the counts were real but were not "current main + this change",
which is the only number worth quoting before a deploy.

**Re-measured 2026-10-07**, after merging `origin/main` at `ef33267` (the PyJWT
migration, the upload content-type allow-list and the bounded PDF/spreadsheet
parsers). The previous numbers in this table were from 2026-10-05 and every one of
them had moved; two were wrong in a way worth naming, because they were the two
this branch's own work changed — `ops/ci` read 41 against an actual 109, and the
routing gate read "24 prefixes, 4 headers" against 31 and 9. A totals table that
nobody re-runs is a table that certifies the wrong tree.

| | |
|---|---|
| `data_gateway` unit tests | 3,165 passed |
| `admin_ops` tests | 206 passed |
| `shared` tests | 876 passed |
| Web tests | 1,906 passed, 164 files |
| Web typecheck (`npm run typecheck`) | clean |
| `ruff` (all four services, `app/` + `tests/`) | clean |
| `mypy` (root config, as CI runs it) | clean — 156 / 19 / 18 files |
| `ops/ci` gate tests | 109 passed |
| Routing / header contract gate | OK — 31 prefixes, 9 headers, 9 upload caps |
| Accepted-risk claims gate | OK — every justification still holds |
| Alembic | single linear head (`f1b3d5a7c9e2`) |

**Three rows were deliberately NOT re-run here, and are left to CI rather than
restated on stale evidence.** The integration smoke (60/60) and the erasure
inventory both need a live migrated Postgres, which this pass did not stand up.
`bandit` cannot be checked on a Windows machine at all: the committed baseline
stores forward-slash paths, Windows bandit emits `services/admin_ops/app\config.py`
with a backslash, so **zero** baseline entries match and all 19 known MEDIUM+
findings are reported as new — `ci.yml`'s own comment warns about exactly this in
the opposite direction. `interview_core`'s mypy is the same shape: one error here
(`Module "livekit" has no attribute "rtc"`) that CI does not have, because the
Linux wheel provides `rtc` and this checkout's does not. Neither is a finding.

---

## Browser coverage (added 2026-09-27)

Phase 3 shipped with none. **Eight** Playwright journeys now drive the real browser
against the real stack, and all eight pass. The table below lists seven; the eighth,
`apply-indistinguishable.spec.ts` (B4b), was added later and has its own row at the end.

**Corrected 2026-10-07.** This paragraph said "Seven … They are not run by CI", which
contradicted this document's own header two screens up. Both halves were stale: the
eighth spec landed with PH3-B4b, and the `browser (playwright e2e)` job has gated every
pull request since 2026-10-05. The count and the CI claim are the two things in this
section a reader would take on trust, so they are corrected rather than left to rot — and
the header is the authority on enforcement, not this paragraph.

On timing: the suite is memory-hungry and has been flaky as a batch on an 8 GB machine
(two specs wait on real clock time — a scheduled publish and an emailed confirmation).
Locally the honest figure is "about eleven minutes with headroom", not a guarantee;
in CI it runs in about five and a half with `EMAIL_POLL_INTERVAL_SECONDS=2`.

| Spec | Stories | What only a browser can say |
|---|---|---|
| `requisition-approval.spec.ts` | B2 | HR has no Approve control at all, an unapproved opening refuses the public without telling them why, and the approver's note reaches the person who has to act on it |
| `jd-versions.spec.ts` | B3, B6 | a draft is edited while a candidate has the advert open, and the advert does not move until it is published |
| `scheduled-publishing.spec.ts` | B4a, B0 | an opening publishes itself with nothing in the test telling it to, and what the careers board lists opens when clicked |
| `application-confirmation.spec.ts` | B5 | the CV says one name, the candidate says another, and HR sees the candidate's |
| `save-and-resume.spec.ts` | B4, B4c | saving is offered and inert before consent, and a genuinely new browsing context comes back to the answers already given |
| `reapply-cooldown.spec.ts` | B4 | a rejected candidate is refused by the waiting period with nothing leaked on screen, let through by an override, and — after confirming by email — is back in **HR's own view** as `new`. The confirmation leg was added after a review found the spec passed while the enrolment stayed rejected and the override went unspent: it proved the form accepted a submission, not that criterion 9 works |
| `apply-source.spec.ts` | B1 | a campaign tag never costs an applicant, and is never shown to the person being counted |
| `apply-indistinguishable.spec.ts` | B4b | one address walked through four states renders **one** screen, compared for equality rather than searched for forbidden words. Dates deliberately unmasked. The API matrix in `test_ph3_cooldown_indistinguishable.py` renders nothing, and the panel is a second place the leak can come back — `awaiting_confirmation`, `already_applied` and the ids were each removed after a review found them telling a stranger that a named person had been turned down |

**Three defects were found by writing them**, each in a criterion already marked ✅:

1. **The reapplication rule never ran.** `already_applied` was checked before the
   cooldown, so the door candidates use never reached the rule. Detailed under B4.
2. **Every workflow panel saved into a cache entry nobody read.** `GovernancePanel`,
   `JdVersionPanel` and `PostingEditor` refreshed `['requisition', id]`; the page that
   renders all three reads `['hr', 'requisition', id]`. Submitting an opening for
   approval worked and the badge still said "Not submitted" until a reload. Every unit
   test handed the panel a requisition as a prop — which is exactly the wiring the bug
   was in.
3. **Three of the four parsed CV fields never reached the screen.** Detailed under B5.

The common shape is worth naming: all three passed tests written *beside* the code and
failed the first test written *against the product*. That is the argument for this
layer existing, not the coverage number.
