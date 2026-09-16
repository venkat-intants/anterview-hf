# PH3 — acceptance checklist against `docs/AntHire-Phase3.docx`

**Built:** 2026-09-16. **Checked against:** every acceptance criterion in the Phase 3
document, in the document's own order and wording.

**Key:** ✅ done and verified · ⚠️ done, with something you should know · ❌ not done

**How each line was verified** is named, because "done" without that is an opinion.
`unit` = a test in `services/data_gateway/tests/unit/`; `smoke` =
`tests/integration/smoke_ph3_apply.py`, which runs the real endpoints against a real
Postgres 16 and passes 57/57; `db` = asserted directly against the migrated schema.

---

## Summary

| Story | Criteria | Done | Notes |
|---|---:|---:|---|
| PH3-B1 Source tracking | 10 | 10 | |
| PH3-B2 Requisition approval & budget | 12 | 12 | |
| PH3-B3 JD versioning | 13 | 13 | |
| PH3-B4 Application lifecycle & scheduled publishing | 17 | 16 | 1 ⚠️ — timing tolerance |
| PH3-B5 Candidate confirmation | 12 | 11 | 1 ⚠️ — parsed field list |
| PH3-B6 JD Studio versioning | 13 | 13 | |
| **Total** | **77** | **75** | **2 ⚠️, 0 ❌** |

Plus one story that is not in your document: **PH3-B0**, the shared publish gate. See
the last section — it was pre-work, and it turned out to be a bug fix.

---

## PH3-B1 — Source Tracking

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Application records support a candidate source/channel | ✅ | `enrolments.source` + `source_detail`, migration `c3e5a7b9d1f4`; `db` |
| 2 | Public application URLs can contain a source identifier | ✅ | `GET /apply/{id}?src=…`; `smoke` |
| 3 | The source identifier is captured when the candidate starts/applies | ✅ | echoed on the posting, sent back on submit; `smoke` |
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
| 8 | Previous versions remain viewable | ✅ | `GET …/jd/versions` returns full content, newest first |
| 9 | Historical versions cannot be accidentally overwritten | ✅ | reverting is a *publish* of the old version, not an edit of it |
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
| 4 | Candidate can continue from where they left off | ✅ | `/apply/draft/:token`; `smoke` |
| 5 | Draft expiration behavior is defined | ✅ | 30 days, extended on each save, stored not computed |

### Reapplication Rules

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 6 | Requisition supports configurable reapplication rules | ✅ | `reapply_cooldown_days` |
| 7 | Organizations can define cooldown periods | ✅ | 0–1095 days; NULL means none |
| 8 | Cooldown validation occurs during application | ✅ | `smoke` — a rejected candidate is refused and told the date |
| 9 | Authorized users can override cooldown restrictions | ✅ | `POST /hr/enrolments/{id}/reapply-override`, audited; `smoke` |
| 10 | Existing applications remain unaffected | ✅ | no cooldown configured ⇒ no query is even issued; `unit` |

### Scheduled Publishing

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 11 | Requisition supports a future publish date/time | ✅ | `publish_at`, migration `f6b8d0e2a4c7` |
| 12 | System automatically publishes at the scheduled time | ⚠️ | **see below** |
| 13 | Authorized users can modify the schedule | ✅ | `PUT …/publish-schedule` replaces |
| 14 | Cancelled schedules do not publish | ✅ | `DELETE …/publish-schedule`; idempotent |
| 15 | Publishing actions are audited | ✅ | set / updated / cancelled / executed, the last attributed to whoever scheduled it |
| 16 | Draft/cooldown/publishing behaviour is tested | ✅ | 47 + 23 + 27 tests; 40/40 `smoke` |

**⚠️ Criterion 12 — the honest version.** Openings publish **within about one minute of
the chosen time while the service is running**, and **shortly after it next wakes** if the
service is asleep. Not "at 09:00 exactly".

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

## PH3-B5 — Candidate Application Confirmation

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Resume parsing continues to extract candidate information | ✅ | `app/resume_details.py`; `smoke` reads a real PDF |
| 2 | Extracted information is presented for review | ✅ | `ResumeApplication.tsx` |
| 3 | Candidate can edit incorrect extracted information | ✅ | every field editable; `web` tests |
| 4 | Required fields must be completed before confirmation | ✅ | name required, everything else optional; `smoke` |
| 5 | Candidate explicitly confirms | ✅ | `POST …/confirm`, timestamped |
| 6 | Only confirmed information is used for submission | ✅ | `smoke` — submission is refused until confirmed, and the **candidate's** corrected name is what lands |
| 7 | Candidate corrections are persisted | ✅ | `smoke` — their name and years of experience, not the CV's |
| 8 | The existing DPDP consent flow remains intact | ✅ | strengthened, not preserved — see PH3-B4c below |
| 9 | Does not break when parsing produces incomplete information | ✅ | unparsed fields render empty and never block; `unit` + `web` |
| 10 | Existing applications remain compatible | ✅ | the one-shot `POST /apply/{id}` path is unchanged and still works; `smoke` uses it for the cooldown checks |
| 11 | Candidate-facing text follows existing localization | ⚠️ | **see below** |
| 12 | Tests cover confirmation, corrections, missing fields, incomplete parsing | ✅ | 47 `unit` + 17 `web` + 8 `smoke` |

**⚠️ Criterion 11 — partial.** The draft carries the candidate's language choice and the
emails it triggers honour it. The **new confirmation screen's own copy is English only** —
it is not yet in the i18n bundles. That is a translation task, not a code change, and I
did not invent Hindi and Telugu strings for a hiring product; they should be written by
someone who will be held to them.

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
  default in the module that decides public visibility. Now required.
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

## What I could not verify, and why

Stated plainly so nobody reads this checklist as claiming more than it proves.

1. **Nothing has been deployed.** Everything runs locally and in tests. No branch, no
   commit, no push — that is yours to trigger.
2. ~~The migrations have not been run against your shared Neon database.~~
   **Done 2026-09-16.** All six applied; head at `c9e1b3d5f7a2`. Verified after:
   all 7 requisitions grandfathered to `approved`, 0 publicly live before and 0
   after (nothing went dark), all 8 enrolments reading `source = 'unknown'`, no
   NULLs introduced.
3. **The confirmation screen has not been used by a real person on a phone.** It
   typechecks, lints, passes 17 behavioural tests and builds, but nobody has applied for
   a job with it.
4. **`ops/ci/check_coverage_floors.py` and the rest of the `invariants` CI job were not
   run** — they need the full CI environment. The floors themselves hold:
   `data_gateway` measured 65% against a floor of 62%.
5. **The Hindi and Telugu copy for the new candidate screens does not exist** (see
   PH3-B5 criterion 11).

---

## Verification totals

| | |
|---|---|
| `data_gateway` unit tests | 1,504 passed |
| `admin_ops` tests | 158 passed |
| `shared` tests | 676 passed |
| Web tests | 936 passed |
| End-to-end smoke against real Postgres | 57/57 |
| `ruff` | clean |
| `mypy` (root config, as CI runs it) | clean, 115 files |
| Alembic | 6 new migrations, single linear head, applied cleanly |
| Erasure inventory (table + column) | both satisfied |
| Web build | succeeds |
