# Phases 3–6 — Execution Workflow

**Source documents:** `docs/AntHire-Phase3.docx` … `AntHire-Phase6.docx` (added 2026-09-16).
**Written:** 2026-09-16. **Status:** plan of record for Phase 3; outline for Phases 4–6.
**Read with:** `CLAUDE.md` (hard constraints), `docs/ACCEPTED-RISKS.md`, `CONTRIBUTING.md`.

> **Phase numbering.** These docs use `PH3`–`PH6` with story ids (`PH3-B1`). That is a
> *different* numbering from `docs/HR_WORKFLOW_EXPANSION.md`, whose "Phase 0–4" are the
> already-shipped ATS/exam/scheduling groups, and from `CLAUDE.md`'s "Phase 1+". Always
> write the story id (`PH3-B1`), never a bare "Phase 3 item".

---

## 1. Scope at a glance

| Phase | Theme | Stories | Net new tables (est.) |
|---|---|---|---|
| PH3 | Job governance & candidate experience | 6 | ~3 |
| PH4 | Hiring operations & hire completion | 14 | ~18 |
| PH5 | Intelligence, evidence & outcome learning | 5 | ~8 |
| PH6 | Enterprise production readiness | 8 | ~6 |

PH3 is small and additive. PH4 is where the bulk of the build sits. PH5 is analysis built on
PH3/PH4 data — **it cannot recover data those phases did not capture**, which is the main
reason to get PH3-B1 and PH4-A1 right rather than fast. PH6 is hardening plus two integration
builds (SCIM, webhooks).

---

## 2. Ground truth — what PH3 is actually building on

Verified against the code on 2026-09-16, not assumed from the docs.

| The docs say | In this repo it is |
|---|---|
| "application" | `Enrolment` — `services/data_gateway/app/models.py:1277`. One person × one opening. `applicants` is the person. |
| "requisition" | `JobRequisition` — `models.py:1199`. `status IN ('open','paused','closed')`, `target_hires`, `jd_text`, `public_apply_enabled`. |
| "frozen round criteria" | `RoundCriterion` — `models.py:1496`. Already a frozen copy, already the evaluation source of truth. PH3/PH4 must not add a second rubric. |
| "published workflow" | `Workflow` — `models.py:1379`, versioned and immutable once published (migration `20260912_0005_..._published_workflows_immutable`). |
| "publishing a job" | `public_apply_enabled AND status = 'open' AND deleted_at IS NULL` — spelled out independently in **7 modules** (see §3.1). |
| "JD Studio" | **Does not exist.** See §3.2. |
| "resume parsing extracts candidate information" | `apply_extracted_identity()` — `app/applicant_enrichment.py:111`. Extracts **name and email only** today; `parsed_full_name` / `parsed_email` were added explicitly so a candidate could be asked "is this right?". PH3-B5 is the screen that column was built for. |
| "scheduler / background worker" | `app/scheduling.py` — `run_scheduled_job()`, claim-don't-coordinate, 15-minute catch-up loop for missed windows. **Reuse it. Do not add a queue.** |
| "audit" | `AuditLog` — `models.py:991`, written inline in the routers (e.g. `hr_requisitions.py:815`). |
| "consent" | `dpdp_consent_ledger`, written in the **same transaction** as the applicant row (`routers/public_apply.py:759`). See §3.3. |
| "branching" | `WorkflowRound.on_pass_next_round_id` already exists and is already nullable-with-meaning. PH4-O3 completes it; it does not build it. |

**Good news for later phases:** PH4-O3 (branching) and PH4-D4 (new round kinds) both have
scaffolding in place — `on_pass_next_round_id`, and a `kind` CHECK constraint that needs one
migration to admit `job_simulation` / `portfolio`.

---

## 3. Four things that change the plan

### 3.1 The publish predicate is duplicated seven times — fix before PH3-B2 and PH3-B4

`public_apply_enabled AND status = 'open'` is written out by hand in `routers/careers.py`,
`routers/public_apply.py`, `routers/candidate_applications.py`, `routers/hr_requisitions.py`,
`company_board.py`, `requisition_dashboard.py` and `agents/watch_runner.py`.

PH3-B2 adds `AND approval_status = 'approved'`. PH3-B4 adds `AND (publish_at IS NULL OR
publish_at <= now())`. Applying those by hand in seven places is how an unapproved requisition
stays live on the one surface somebody missed — and the acceptance criterion that fails
("an unapproved requisition cannot accidentally become publicly available") is precisely the
one nobody tests across all seven.

**→ New story PH3-B0, not in the docs, do it first.** One predicate, one module, all seven
call sites moved to it, plus an executable check that no new one appears. `ops/ci/` already has
this pattern (`check_alert_rules.py`, `check_env_parity.py`) — follow it.

### 3.2 "JD Studio" does not exist — PH3-B6 is version-awareness on the requisition editor

PH3-B6 says "add version-aware authoring to JD Studio" and lists "existing JD Studio editing
functionality continues to work" as an acceptance criterion. There is no JD Studio. What
exists is:

- `jd_text`, a plain column on `job_requisitions`, edited via `PATCH /hr/requisitions/{id}`;
- `routers/jd.py`, which uploads a JD **PDF** — but writes it to the *legacy global* `jobs`
  table, which has no `company_id` and serves the self-serve practice-interview path, **not**
  `job_requisitions`.

**Resolved (D-1, 2026-09-16): the requisition JD editor *is* JD Studio.** PH3-B6's own Task 7
settles it — *"Add version information to the existing JD Studio rather than creating a
completely separate interface. Keep it simple."* So B6 adds version-awareness to the editor
that exists; it does not build a new surface. Production-grade here means the *semantics* are
right (draft vs published vs historical, provenance, "which version was live on this date"),
not that the editor grows features the doc did not ask for. The doc likewise defers the diff
viewer: "view history is enough … unless the implementation naturally supports it."

One tidy-up this exposes: `routers/jd.py` writes JD PDFs to the legacy global `jobs` table and
has nothing to do with requisitions. It is not in PH3-B6's scope, but do not mistake it for the
versioning target, and do not wire it to `jd_versions` without a separate decision.

### 3.3 A saved draft would hold a CV before consent — PH3-B4c takes consent at first save

Today consent and PII are committed together, deliberately: *"there is no moment at which the
CV exists without the record of permission to hold it"* (`public_apply.py:769`). Hard
constraint 3 in `CLAUDE.md` says the same thing.

PH3-B4's "Save & Resume" stores a partially-completed application — name, email, phone, and
plausibly the uploaded CV — **before** the candidate reaches the consent checkbox at submit.
That breaks the invariant.

**Resolved (D-3, 2026-09-16): consent at first save.** A draft cannot be persisted until consent
is granted. The checkbox moves to step 1 of *saving* rather than of *applying*, so the invariant
holds unchanged — no row exists without its ledger entry — and a candidate who never saves a
draft still sees today's flow.

What this means concretely for PH3-B4c:

- `POST /public/apply/{id}/draft` writes the `dpdp_consent_ledger` entry in the **same
  transaction** as the draft row, exactly as `_record_apply_consent` does for the applicant
  today. Reuse that function; do not write a second consent path.
- Consent granted at draft time covers the submitted application too — the existing check is
  idempotent per `(user_id, consent_type, purpose)`, so submit must not demand it twice.
- Revoking consent, or an erasure request, must reach drafts. The draft table therefore needs
  an `ERASED_TABLES` entry, and `services/admin_ops/tests/test_erasure_inventory.py` will fail
  the build until it has one — which is the correct behaviour.
- Draft expiry (B4's Task 4) is a retention rule, not just a cleanup job: expired drafts hold
  PII and must be purged on the existing retention cron, not left to a manual sweep.

### 3.4 Scheduled publishing will fire late on the demo tier — say so in the AC

`app/scheduling.py` exists and is the right harness, but its own docstring records why: the HF
Space suspends after ~48h and cron windows are *skipped*, not delayed. The catch-up loop
recovers a missed window within ~15 minutes **of the container waking up**, which on a cold
Space is not 09:00.

PH3-B4's "system automatically publishes at scheduled time" therefore needs a stated tolerance
("within 15 minutes of the scheduled time on a warm instance; on next wake otherwise") rather
than an implied promise. On Tier-2 (AWS EKS, always warm) the tolerance is the 15-minute
figure. Write it into the AC; do not let it be discovered in a demo.

---

## 4. PH3 — ordered execution plan

The doc lists B1 → B6. That is not the dependency order. This is:

```
PH3-B0  publish predicate, one place          <- pre-work, blocks B2 + B4a
   |
   +-- PH3-B1  source tracking                <- independent, unblocks PH5-C1 + PH6-I2
   |
   +-- PH3-B3  JD versioning (data)  ------>  PH3-B6  JD version UI
   |
   +-- PH3-B2  approval + budget              <- approval primitive, reused by PH4-O6
   |      |
   |      +-- PH3-B4a  scheduled publishing
   |
   +-- PH3-B4b  reapplication cooldown
          |
          +-- PH3-B4c  save & resume drafts ---> PH3-B5  candidate confirmation
                                                     |
                                                     +-- PH3-B5b  scorer field extension
```

**PH3-B4 is three stories, not one.** Requisition-side scheduled publishing, requisition-side
cooldown rules and candidate-side drafts share nothing but a doc heading. Splitting them keeps
the risky endpoint rework (B4c + B5) isolated at the end, where it can be done as one coherent
change to `POST /public/apply/{requisition_id}`.

### The sequence

| # | Story | Size | Migration | Touches |
|---|---|---|---|---|
| 1 | **PH3-B0** publish predicate | S | — | 7 modules + new `ops/ci` check |
| 2 | **PH3-B1** source tracking | S | `enrolments.source` | public apply, careers links, HR views |
| 3 | **PH3-B3** JD versioning (data) | M | `jd_versions` + backfill | requisition PATCH, careers read |
| 4 | **PH3-B2** approval + budget | M | `approval_status` + budget cols | requisitions, publish gate, HR UI |
| 5 | **PH3-B4a** scheduled publishing | M | `publish_at` + audit | `scheduling.py` job, publish gate |
| 6 | **PH3-B6** JD version UI | S–M | — | requisition editor (D-1) |
| 7 | **PH3-B4b** reapplication cooldown | S | `reapply_cooldown_days` | submit path, HR override |
| 8 | **PH3-B4c** save & resume drafts | L | `application_drafts` | candidate apply flow, consent (D-3) |
| 9 | **PH3-B5** candidate confirmation | M | — | candidate apply flow, EN/HI/TE |
| 10 | **PH3-B5b** scorer field extension | M | — | `feedback_billing` resume scorer (D-4) |

Sizes are relative, not estimates in days. **Two tracks can run in parallel** once B0 lands: a
requisition-governance track (B1 → B3 → B2 → B4a → B4b) and a candidate-experience track
(B6 after B3; then B4c → B5).

### Per-story notes that are not in the doc

**PH3-B1.** Define the source vocabulary now with PH6-I2 (job boards) in view — job-board
applications will flow back carrying a source, and a vocabulary invented twice is two
vocabularies. Store `unknown` explicitly rather than NULL-meaning-two-things: PH5-C1 requires
"Unknown/Untracked, not silently discarded". Normalise and allowlist the inbound `?src=` value;
it arrives from the open web and ends up in an analytics `GROUP BY`.

**PH3-B3.** The backfill is the risky half: every requisition with `jd_text` becomes v1, and
`PATCH` stops overwriting. Ship the migration + backfill and the version-minting write path as
separate PRs, so the backfill can be verified against the shared Neon dev DB before the write
path changes underneath it. Add a regression test asserting that editing a JD leaves
`round_criteria` rows byte-identical — that guard is named in both B3 and B6, and it is the one
thing in PH3 that could silently change how candidates are assessed.

**PH3-B2.** Two state machines now meet on one row. Keep `status` (`open/paused/closed`) as the
*operational* lifecycle and add `approval_status` (`draft/pending_approval/approved/rejected`)
as the *governance* gate — do not merge them, and do not let `approved` imply `open`. Budget is
money, `target_hires` is headcount; the doc is explicit that neither replaces the other.
**Build the approval mechanics generically enough to be reused by PH4-O6** (workflow review and
approval), which is the same shape: submit → approve/reject → version-locked → audited.

*Approver (D-2, resolved 2026-09-16): a company's `super_admin` approves requisitions raised by
its own `hr_manager`s.* No new role, no RBAC migration — it reuses the hierarchy and the
server-side company scoping that already exists. Two consequences to implement explicitly: a
`super_admin` must not be able to approve another company's requisition (the scoping is already
server-side, but the AC "unauthorized users cannot approve" needs a cross-tenant test, not an
assumption), and an `hr_manager` must not be able to approve its own — self-approval defeats the
gate. Decide and document what happens when a `super_admin` raises a requisition itself: either
it needs `platform_owner` approval, or it is auto-approved with an audit entry saying so. Silent
self-approval is the one outcome to avoid.

**PH3-B4a.** Ride `scheduling.run_scheduled_job()`. Publishing is a write with a public
consequence, so the job must be idempotent and must re-check the approval gate **at fire time,
not at schedule time** — a requisition scheduled while approved and rejected an hour later must
not publish.

**PH3-B4c + PH3-B5.** These restructure the same endpoint and should be planned as one piece of
work even if they merge as two PRs.

On B5's field list: the doc's Task 1 names Name / Email / Phone / Location / Education /
Experience / Skills, but prefixes it with "For example" and then instructs "use the existing
parsed candidate data model rather than creating duplicate candidate records". Today
`apply_extracted_identity()` produces **name and email**; phone, years of experience, current
company/title and the profile links are collected by the form and already live on `applicants`.

So B5 ships confirming everything the system actually holds — parsed fields *and* form fields —
with anything unparsed shown as an empty editable field rather than a blocker. That is also what
Task 4 requires: *"Don't prevent the candidate from applying merely because an optional resume
field couldn't be extracted."* Extending the scorer to return location / education / skills is
real work in `feedback_billing`'s resume scorer, so it is sequenced as **PH3-B5b** behind B5;
the confirmation screen picks those fields up with no further UI change once they exist.

---

## 5. Definition of done — every PR in every phase

Derived from `.github/workflows/ci.yml` and `CLAUDE.md`. None of these are optional; most fail
the build on their own.

- **Branch + commit.** `feat/<name>` / `fix/<name>`; Conventional Commits; no direct commits to
  `main`; never `--no-verify` or `--no-gpg-sign`.
- **Lint + types.** `ruff` (0.7.4) and `mypy` (1.20.2) from the **repo root** `mypy.ini`, which
  is deliberately non-strict. The per-service `strict = true` blocks are never loaded.
- **Coverage floor.** Per service, enforced as a ratchet: `data_gateway` 62, `interview_core`
  74, `feedback_billing` 87, `admin_ops` 85. A PR that drops coverage fails. PH3 is almost all
  `data_gateway`.
- **Migrations.** Single linear Alembic head (CI job `migrations`). One head per PR; rebase
  rather than branch the head.
- **Erasure inventory.** *Any new table* must be classified in `ERASED_TABLES` or
  `EXCLUDED_TABLES` in `services/admin_ops/app/erasure_executor.py`, or
  `test_erasure_inventory.py` fails. This is a per-PR gate, not a phase-end cleanup. PH3 adds
  ~3 tables; PH4 adds ~18.
- **Web.** `npm run typecheck && lint && test && build`. TypeScript strict, no `any`.
- **Review.** `code-reviewer` agent before merge. `security-auditor` additionally for anything
  touching auth, PII, tenant scope, file upload or an external integration — which is most of
  PH4 and all of PH6.
- **Docs in the same change.** `CLAUDE.md`'s documentation rule: if a capability changes, grep
  the docs for its name in the same PR. A stale claim is a defect. Where a value cannot be
  verified, write "unverified" rather than a plausible number.

### Invariants that must survive all four phases

Load-bearing and structural, not stylistic. Every phase has stories that would erode them if
implemented carelessly:

1. **Agents cannot write.** `ToolEffect` has only `read` and `draft`. PH5-E1/E2/E3 add retrieval
   surfaces to the copilot; none of them may add a write path.
2. **AI never decides a hiring outcome.** `PanelVerdict.decision_authority` is the literal
   `"human_only"`. PH4-D3 (similarity), PH4-D4 (simulations) and PH5-E4 (calibration) each carry
   an explicit "must not auto-reject" criterion. Enforce it in the type, not the prompt.
3. **Frozen `round_criteria` is the evaluation source of truth.** PH3-B3/B6, PH4-A1/A5 and
   PH4-D1 must all read it, and none may fork it.
4. **A console cannot read outside its remit.** `data_class` is validated at `ToolSpec`
   construction. PH5-E2/E3 add document and candidate retrieval — both inherit this, or the
   service refuses to start.
5. **Published things are immutable.** Workflows already are. PH3-B3 (JD versions), PH4-A1
   (submitted scorecards), PH4-A3 (sent offers) and PH4-D1 (published assessment questions) all
   restate the same rule: supersede, never mutate.

---

## 6. PH4–PH6 — the spine

Lower resolution, enough to sequence. Re-plan each phase properly before starting it.

### PH4 — order is driven by A1

**PH4-A1 (human scorecards) is the keystone.** PH4-A5 (kits), PH4-O5 (workload/calibration),
PH5-C1 (quality-of-hire) and PH5-E4 (outcome learning) all depend on it, and PH5's dependency is
on *data captured from day one* — scorecards not written in PH4 are not analysable in PH5. Build
it first and build it right.

```
A1 scorecards ---+-- A5 interview kits
                 +-- O5 panel workload & calibration
                 +-- (PH5-C1, PH5-E4)

A2 scheduling + loops ---+-- O5
                         +-- (PH6-I2 calendar)

A3 offers --- A4 documents & preboarding --- (PH6-I2 e-sign, HRMS)

O2 workflow simulation --- O3 branching   (O2's isolation harness is the hard part;
                                           O3 rides it, and O2 then validates branches)

O6 workflow approval      <- reuses the PH3-B2 approval primitive
O4 decision reason codes  <- small, independent, feeds PH5-C1/E4
O1 stage owners + SLAs    <- independent

D1 question banks / D2 accommodations / D3 code quality / D4 simulations & portfolio
                          <- assessment track, independent of the A/O tracks
```

Two schema notes worth knowing now: `WorkflowRound.kind` has a CHECK constraint admitting only
`mcq/coding/ai_interview/human_review`, so PH4-D4 needs a migration to widen it; and PH4-A3's
secure offer link should get its **own token context**, not a reuse of the interview magic-link
secret — the doc says so, and it is right.

### PH5 — pull C2 forward

**PH5-C2 (governed metric layer) should start before the rest of PH5, and arguably before PH4's
new dashboards.** Its stated purpose is to fix metrics computed independently in different
endpoints — the doc attributes conversion rates of 175% and 400% to exactly that. PH4-O1 (SLA
views) and PH4-O5 (workload) add more dashboards. Every bespoke metric query written in PH4 is
one more to migrate in PH5.

Minimum viable discipline if C2 cannot start early: **no new bespoke metric SQL in PH4 without a
named definition** that C2 can later adopt.

Dependencies: C1 needs PH3-B1 + PH4-A1. E4 needs PH4-A1 and C2. E1 (citations) precedes E2
(document RAG), which is where prompt-injection isolation gets tested for real.

### PH6 — write F1's checklist first, sign it last

PH6-F1 (security review and production sign-off) is the last gate, but its **checklist should be
written at the start of PH4** so the intervening stories are built against it rather than audited
against it afterwards. The same applies to PH6-F5 (accessibility): the audit is cheap if
components were built to the standard and expensive if they were not.

PH6-F2 (backup/restore/DR) has a prerequisite the demo tier cannot satisfy: local dev runs
against **shared Neon**, and a restore drill needs an isolated environment to restore *into*.
See `docs/ACCEPTED-RISKS.md` before scoping it.

PH6-I1 (SCIM) and PH6-I2 (webhooks + integrations) are the only net-new builds. I2 explicitly
says to reuse the existing outbox/retry/reconciliation pattern rather than add a queue — that is
`app/mailer.py`'s outbox worker plus `app/reconciliation.py`, and it is the right call.

---

## 7. Decisions — settled 2026-09-16

| # | Decision | Settled as |
|---|---|---|
| **D-1** | What is "JD Studio" in PH3-B6? | **The existing requisition JD editor is JD Studio.** Add version-awareness to it; do not build a separate surface. This is what PH3-B6 Task 7 asks for. See §3.2. |
| **D-2** | Who approves a requisition (PH3-B2)? | **A company's `super_admin`**, for requisitions raised by its own `hr_manager`s. No new role. Cross-tenant approval and self-approval must both be refused and tested. See §4. |
| **D-3** | How does a saved draft (PH3-B4c) hold PII without breaking the consent invariant? | **Consent at first save.** The ledger entry is written in the same transaction as the draft row, reusing `_record_apply_consent`. Drafts join the erasure inventory and the retention cron. See §3.3. |
| **D-4** | Does PH3-B5 confirm only what the parser produces today? | **Yes** — parsed fields plus the form fields already held, with unparsed fields empty and editable, never blocking. Scorer extension is sequenced as **PH3-B5b**. See §4. |

Two smaller questions are deliberately left to the story that hits them, because the code will
answer them better than a plan can:

- **What a `super_admin` raising its own requisition does** (needs `platform_owner` approval, or
  auto-approves with an audit entry saying so) — decide in PH3-B2, but decide it explicitly.
- **The source vocabulary** for PH3-B1 — agree it before the migration, with PH6-I2's job-board
  ingestion in view, since inbound board applications will carry a source of their own.

---

## 8. Status — PH3 is built (2026-09-16)

Every story in §4 is implemented, in the order given. Six migrations
(`c3e5a7b9d1f4` → `c9e1b3d5f7a2`), one linear head, applied and verified against a
throwaway Postgres 16 — including the two backfills, exercised against rows seeded
*before* the migrations ran, which is the only way to test a backfill honestly.

What was actually corrected against the plan:

- **PH3-B0 was larger than "tidy-up".** The hand-copied predicates had already drifted:
  the careers board and the candidate feed lacked the published-workflow gate that the
  apply endpoint enforced, so both advertised openings that 404'd on click. The existing
  cross-check test compared three of the five gates and never saw it. That is fixed, and
  the test now asserts identity rather than similarity.
- **PH3-B6 needed no separate surface.** The requisition editor (`PostingEditor`) is JD
  Studio; `JdVersionPanel` sits beneath it, as the story's own Task 7 asks.
- **PH3-B2's separation of duties turned out to be structural.** A user holds exactly one
  role, and `create_requisition` requires `hr_manager` while approval requires
  `super_admin` — so no account can do both. The "may not approve your own" branch that
  seemed necessary is unreachable and was deleted rather than shipped as dead code.
- **PH3-B4 split three ways** as planned (B4a scheduled publishing, B4b cooldown, B4c
  drafts), which kept the apply-endpoint rework isolated at the end.
- **PH3-B5b did not extend the LLM scorer.** `public_apply`'s own rule — nothing in that
  request path may wait on a language model — applies equally to a confirmation screen
  shown during the application, so extraction is deterministic (`app/resume_details.py`)
  and runs in microseconds. The scorer still runs afterwards in the reconciler.

Verification: 1,504 data_gateway unit tests, 158 admin_ops, 676 shared, 936 web; `ruff`
and `mypy` clean; `tests/integration/smoke_ph3_apply.py` proves the whole candidate
journey end to end against real Postgres (40/40).

**Next: re-plan PH4.** PH3-B2's approval mechanics are the primitive PH4-O6 reuses, and
they now exist to be read rather than imagined.
