# PH4 — acceptance checklist against `docs/AntHire-Phase4.docx`

**Built:** 2026-09-17. **Checked against:** every acceptance criterion in the Phase 4
document, in the document's own order and wording — all 317 of them across 15 stories.

**Delivery (decision D4-4):** wave by wave. Each wave merges and deploys on its own, only
after CI, a code review and a security sign-off.

| Wave | Stories | State |
|---|---|---|
| 1 | A1 Human interview scorecards · A5 Interview kits · O4 Decision reason codes | **Live.** See the deployment line below. |
| 2 | O6 Workflow review & approval · O3 Branching workflows · O2 Workflow simulation & dry run · O1 Stage owners, SLAs & exceptions | **Built and signed off** (code review and security). PR #29, CI green; its migrations are on shared Neon. Waiting to merge. |
| 3 | A2 Interview scheduling & loops · O5 Panel workload & calibration | **Built and signed off.** PR #30, stacked on #29, CI green. Its migrations go to Neon just before it merges. |
| 4 | A3 Offer lifecycle · A4 Documents & preboarding | **In progress.** The backend is built and signed off (code review and security); the screens are being built. No criterion is marked done until its screen exists. |
| 5 | D1 Question banks · D2 Accommodations · D3 Code quality & similarity · D4 Job simulations & portfolio | **In progress.** Designed; being built one story at a time, D1 first. |

**Deployment:** Wave 1 is **live** since 2026-09-17: PR #27 merged as `c3fb463`, its three migrations applied to shared Neon (`f4b6d8e0a2c3`), and the Space verified serving it (the interviewer and decision-reason routes answer, and the web bundle carries the new screens).

**Key:** ✅ done and verified · ⚠️ done, with something you should know · ❌ not done · ⏳ not done yet (a later wave, or its screen is still being built)

**The bar for ✅** is that the person the criterion names can do it **in the product**, not
only through the API. Phase 3 was marked against a looser bar and had to be corrected.

**How each line was verified.** Tests live under `services/data_gateway/tests/` and `web/`.
- `unit`: `unit/test_ph4_wave1.py`, `test_ph4_wave1_hardening.py`, `test_ph4_wave2.py`, `test_ph4_wave3.py`.
- `db`: `integration/test_ph4_scorecard_guarantees.py`, `test_ph4_wave2_guarantees.py`, `test_ph4_wave3_guarantees.py`. These run against a real migrated Postgres, and every refusal is checked for its *reason*, not just for failing.
- `smoke`: real Postgres through the real endpoints — `smoke_ph4_scorecards.py` (Wave 1, 109/109), `smoke_ph4_wave2.py` (57/57), `smoke_ph4_wave3.py` (81/81).
- `ui`: the web test suite, 1216 tests, and the screen named on the line.
- `e2e`: the Playwright browser suite against a local stack, 24 passed — including `interview-scorecard.spec.ts` (A1/A5), `workflow.spec.ts` (O6 review and approval), `interview-scheduling.spec.ts` (A2/O5) and the decision journeys (O4).

---

## Summary

| Story | Wave | Criteria | Done | Notes |
|---|---:|---:|---:|---|
| PH4-A1 Human Interview Scorecards | 1 | 15 | 15 |  |
| PH4-A5 Interview Kits | 1 | 15 | 14 | 1 ⚠️ |
| PH4-O4 Structured Decision Reason Codes | 1 | 13 | 13 |  |
| PH4-O6 Workflow Review & Approval | 2 | 14 | 13 | 1 ⚠️ |
| PH4-O3 Branching Workflows | 2 | 15 | 15 |  |
| PH4-O2 Workflow Simulation & Dry Run | 2 | 31 | 30 | 1 ⚠️ |
| PH4-O1 Stage Owners, SLAs & Exception Paths | 2 | 13 | 12 | 1 ⚠️ |
| PH4-A2 Interview Scheduling + Loops | 3 | 29 | 26 | 3 ⚠️ |
| PH4-O5 Panel Workload & Calibration | 3 | 15 | 15 |  |
| PH4-A3 Offer Lifecycle | 4 | 35 | 0 | ⏳ in progress |
| PH4-A4 Documents & Preboarding | 4 | 33 | 0 | ⏳ in progress |
| PH4-D1 Reusable Question Banks | 5 | 15 | 0 | ⏳ in progress |
| PH4-D2 Candidate Accommodations | 5 | 13 | 0 | ⏳ in progress |
| PH4-D3 Code Quality & Similarity Evidence | 5 | 30 | 0 | ⏳ in progress |
| PH4-D4 Job Simulations & Portfolio | 5 | 31 | 0 | ⏳ in progress |
| **Total** | | **317** | **153** | **7 ⚠️, 0 ❌, 157 ⏳** |

**Wave 1:** 43 criteria — 42 ✅, 1 ⚠️, 0 ❌.  
**Wave 2:** 73 criteria — 70 ✅, 3 ⚠️, 0 ❌.  
**Wave 3:** 44 criteria — 41 ✅, 3 ⚠️, 0 ❌.  

---

## PH4-A1 — Human Interview Scorecards  ·  Wave 1

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | A human interview round can have one or more assigned interviewers | ✅ | HR: candidate drawer → **Human interview → Assign interviewers** (one or many per round). `smoke` |
| 2 | Each interviewer receives an individual scorecard | ✅ | one `interviewer_scorecards` row per interviewer, candidate and round — the row IS the assignment; unique live index; `db` |
| 3 | Scorecard criteria come from the round's frozen round_criteria | ✅ | composite FK `(round_id, competency_id)` → `round_criteria`: a score against any other competency cannot be written; `db` |
| 4 | Interviewers can provide a score for each applicable criterion | ✅ | Interviewer: **My interviews → scorecard**, a 1–5 control per criterion, or *not assessed*; `ui` `smoke` |
| 5 | Interviewers can provide supporting evidence/notes | ✅ | evidence per criterion + overall summary; private working notes kept separately (A5); `ui` `smoke` |
| 6 | Interviewers submit their scorecards independently | ✅ | no read of anyone else's scorecard from the interviewer console (404, not 403); an HR manager on the panel is blinded until they submit, cannot withdraw themselves while another HR manager could, and cannot join once others' scorecards are readable; `unit` `smoke` |
| 7 | Submitted scorecards become immutable or otherwise protected from silent modification | ✅ | database triggers refuse any change except the audited **correction** (original kept, superseded) and DPDP redaction; `db` |
| 8 | The system records interviewer identity and submission timestamp | ✅ | `interviewer_user_id`, `submitted_at` on the row and the audit trail; the HR drawer shows who and *Submitted <date>*; `ui` `db` |
| 9 | Late/non-submitted scorecards have an explicit state | ✅ | `assigned` / `in_progress` / `submitted` / `withdrawn` stored; **late** derived from the due date at read time (never stale). Badges in the interviewer console, tags in the HR drawer, *N late* in the decision queue; `unit` `ui` |
| 10 | Multiple interviewers' evaluations remain independently attributable | ✅ | one row per interviewer, never merged; corrections keep the chain (`corrects_id` / `superseded_by_id`); `db` |
| 11 | Scorecards are visible to authorized HR users | ✅ | HR managers: candidate drawer **Human interview** section — submitted scores, evidence, summary, correction history. See note on super admins below; `ui` `smoke` |
| 12 | Scorecards feed the decision/evidence layer without automatically deciding the candidate's outcome | ✅ | decision queue shows *N/M scorecards in*; nothing in the scorecard modules can change a status (AST test: no decision writer referenced, no `UPDATE enrolments`); `unit` `smoke` |
| 13 | AI cannot modify or submit a human interviewer's scorecard | ✅ | agents hold no write tool (`ToolEffect` is read/draft only), no agent code references the scorecard modules (AST test), and every write route needs a human interviewer/HR session; `unit` |
| 14 | Scorecard activity is audit logged | ✅ | `scorecard.assigned`, `.started`, `.submitted`, `.correction_opened`, `.withdrawn` — reasons recorded as *given / length*, never the text; `smoke` |
| 15 | Tests cover submission, permissions, multiple interviewers and incomplete/late submissions | ✅ | `test_ph4_wave1.py`, `test_ph4_wave1_hardening.py`, `test_ph4_scorecard_guarantees.py` (real Postgres), `smoke_ph4_scorecards.py`, `InterviewerConsole` / `InterviewerScorecard` / `CandidateDrawer` UI tests |

**Worth knowing.**

- **A panel is assigned per application, not once for the round.** HR assigns interviewers to a candidate's round from that candidate's drawer. There is no standing "this round is always interviewed by these three people" setting. A standing panel belongs with interview loops and scheduling (PH4-A2, Wave 3).
- **Company super admins cannot read scorecards.** That is deliberate and follows the platform rule that a super admin runs hiring operations and is not an HR manager: only `hr_manager` reads named-candidate evidence.
- **Hardening from the security review is in this wave.**
  - Google and Naipunyam sign-in now refuse any non-candidate account by default. They previously let a new `interviewer` account through.
  - A withdrawn assignment loses all access.
  - Nothing new can be written about a candidate who has been erased.
  - Erasure withdraws open assignments and redacts every written field while keeping the scores.
  - An interviewer's private notes are purged 90 days after the decision, but only on a deployment with `RETENTION_DRY_RUN=false`.

---

## PH4-A5 — Interview Kits  ·  Wave 1

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Each human interview round can have an associated interview kit | ✅ | `interview_kits`, one per round; HR edits it in **Workflow builder → round → Interview kit** — on a live workflow too; `ui` `smoke` |
| 2 | Interviewers can access the kit for interviews assigned to them | ✅ | Interviewer: scorecard page → **Interview kit** tab, only through their own assignment (404 otherwise, and once withdrawn); `ui` `smoke` |
| 3 | The kit displays the round's applicable evaluation criteria | ✅ | criteria read straight from the frozen `round_criteria`, with weights and anchors; `smoke` |
| 4 | The kit displays relevant competency/role guidance | ✅ | anchors (weak / adequate / strong), *what to evaluate*, *what to look for*; `ui` |
| 5 | The kit can contain suggested interview questions or probes | ✅ | HR's suggested probes, shown apart from the probes frozen into the rubric; `ui` `smoke` |
| 6 | Interviewers can use the kit while conducting the interview | ✅ | kit and private notes sit beside the scorecard on the same page; `ui` |
| 7 | The kit does not modify the frozen round_criteria | ✅ | the kit is guidance keyed by competency id, validated against the frozen criteria on every write; it cannot add one; `unit` `smoke` |
| 8 | Interviewer notes remain separate from the official scorecard submission | ✅ | `interviewer_notes` is its own table: never in the HR evidence view, never in the submission; `smoke` |
| 9 | Authorized HR users can configure interview kit content | ✅ | HR managers: **Workflow builder → round → Interview kit** (`PUT /hr/rounds/{id}/kit`), on draft and live workflows; `ui` `smoke` |
| 10 | Interviewers cannot modify protected rubric/criteria definitions | ✅ | no route edits criteria except publishing a new workflow version; the kit endpoint refuses an unknown criterion; `unit` |
| 11 | Kit access follows existing company/role permissions | ✅ | company-scoped queries throughout; interviewer reads go through the ownership query; `smoke` |
| 12 | Candidate-facing users cannot access internal interview kit content | ✅ | candidate roles are refused by the role gates on `/interviewer/*` and `/hr/*`, and the SPA routes; `unit` `ui` |
| 13 | Kit usage/content changes are appropriately audited | ⚠️ | **content changes** are audited, naming the parts that changed (not their text). **Kit views are not logged** — see below |
| 14 | Existing human scorecards continue to work if no custom kit is configured | ✅ | with no kit written, the kit still shows the frozen criteria, anchors and probes; `smoke` |
| 15 | Tests cover permissions, criteria display and kit/scorecard integration | ✅ | `test_ph4_wave1.py` kit section, `smoke_ph4_scorecards.py` kit section, `RoundInspectorKit.test.tsx`, `InterviewerScorecard.test.tsx` |

**⚠️ Criterion 13 — kit views are not logged.** "Appropriately audited" is interpreted as follows:
- Changes to a kit's content are audited, naming the section that changed.
- An interviewer opening a kit is not logged, because a kit holds no candidate data.
- An HR manager reading submitted scorecards is not logged either, which matches every other HR read of candidate data on the platform.

The security review rated view auditing as future work rather than a blocker: independence is now enforced when the panel is assigned, not detected afterwards. If you want *who viewed the evidence* on record, say so. It is a small addition.

---

## PH4-O4 — Structured Decision Reason Codes  ·  Wave 1

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | HR can select a structured reason when recording a final candidate decision | ✅ | a required reason dropdown on every screen that records one: **Decision queue**, **Pipeline** (expanded row), **Applicants → Reject**; `ui` `smoke` |
| 2 | Existing free-text decision notes remain available | ✅ | the free-text reason is kept alongside the category, required as before; `smoke` |
| 3 | The selected reason is stored with the immutable decision record | ✅ | `stage_transitions.reason_code` + `reason_label` on the append-only ledger; a database trigger refuses a new hire/reject with no code, whichever code path tries; `db` |
| 4 | Decision reasons are company-scoped | ✅ | `decision_reasons` keyed by company; `smoke` |
| 5 | The system supports configurable reason categories, including examples such as: Skills / competency fit, Experience, Role fit, Interview performance, Compensation / availability, Position closed, Other | ✅ | the seven defaults seeded per company on first use; Super admin → **Decision reasons** adds, renames, retires and restores; `ui` `smoke` |
| 6 | Selecting Other allows the HR user to provide an explanation | ✅ | *Other* requires an explanation of at least 10 characters, in the UI and on the server; `unit` `smoke` |
| 7 | Historical decisions remain readable if the reason taxonomy changes later | ✅ | the ledger stores a **snapshot of the label** as chosen; a used reason can only be re-cased or re-spaced, never renamed to mean something else; `smoke` |
| 8 | Reason selection cannot itself trigger an automated candidate outcome | ✅ | choosing a reason does nothing on its own — it is a field on a human's decision request; `unit` |
| 9 | Only authorized human users can record the final decision | ✅ | hire/reject only through the guarded decision writer, HR managers only — the applicant board's old no-reason reject path and the hold release were closed in this wave; `unit` `smoke` |
| 10 | Reason creation/change is captured in the audit trail | ✅ | `decision_reason.created` / `.updated` with before and after; `smoke` |
| 11 | Stored reasons are structured so they can be aggregated in future analytics | ✅ | a stable machine code per company, indexed `(company_id, reason_code, occurred_at)` for PH5 analytics; `db` |
| 12 | Existing decisions without a structured reason remain valid | ✅ | both columns nullable; the trigger checks new rows only, so every earlier decision stays valid and readable; `db` |
| 13 | Unit and integration tests cover permissions, persistence, historical compatibility, and decision flows | ✅ | `test_ph4_wave1.py` O4 section, `test_group_e_decisions.py`, ledger-trigger tests in `test_ph4_scorecard_guarantees.py`, `smoke_ph4_scorecards.py`, and the Group B/E decision smokes updated; UI tests for all three screens |

**Worth knowing.**

- **The pipeline's one-click hire and reject icons are gone.** A reason is now mandatory and there was no room for the picker on a collapsed row, so decisions are made from the expanded row.
- **A decision's free-text reason is not redacted when a candidate is erased.** This was already true before this wave, and applies to both the audit log and the ledger. It is recorded as accepted risk **AR-5** in `docs/ACCEPTED-RISKS.md`, with an owner and a trigger.

---

## PH4-O6 — Workflow Review & Approval  ·  Wave 2

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Workflow versions can be submitted for review | ✅ | HR: **Workflow builder → Review panel → Submit for review** (optional note). Validation and a fresh dry run run first; any error refuses the submission and is listed inline. Every active super admin of the company is notified; `ui` `smoke` `e2e` |
| 2 | Authorized reviewers can approve a workflow version | ✅ | Super admin: **Workflow reviews → version → Approve → Confirm approval** (optional note). The approve route takes the super-admin gate only (D4-2); the database accepts `approved` only from `in_review`; `ui` `db` `smoke` `e2e` |
| 3 | Authorized reviewers can reject/request changes | ✅ | Super admin: **Workflow reviews → version → Request changes**, a note of at least 10 characters required in the UI and on the server. HR gets the version back as *Changes requested*, editable, with the note on the Review panel. There is no terminal *reject* — see below; `ui` `unit` `smoke` |
| 4 | Only approved versions can be published/activated | ✅ | `publish()` refuses anything not approved, in words; the `workflows` trigger refuses `published` unless the row was approved *before* the update, so approve-and-publish in one statement fails too. The builder shows **Publish** only on an approved version; `db` `unit` `ui` `e2e` |
| 5 | Draft versions remain editable | ✅ | a draft and a *Changes requested* version are editable; withdrawing or reopening returns a version to draft; only *In review* and *Approved* lock it; `db` `smoke` |
| 6 | Published/approved versions cannot be silently modified in place | ⚠️ | the version row, its rounds, criteria and branches are frozen by trigger from submission on (in review, approved, published, archived); reopening an approved version clears the approval. **The exam content a live version points at is not frozen** — see below; `db` `unit` |
| 7 | Approval status is version-specific | ✅ | `review_status` lives on each version's own row; every new version — *Edit as new version* included — must be inserted as an unreviewed draft (trigger on INSERT), and a live version's review is closed; `db` |
| 8 | Reviewer, timestamp and action are recorded | ✅ | `workflow_review_events` (append-only) records action, actor, note, time and the content fingerprint; `reviewed_by` / `reviewed_at` on the row. Shown in **Review panel → Show history** and the super admin's **History** card; `db` `ui` `smoke` |
| 9 | Approval/rejection actions are audited | ✅ | `workflow.review.submitted` / `.withdrawn` / `.approved` / `.changes_requested` / `.reopened` audit rows with version, note and fingerprint; `unit` `smoke` (4 of 4 actions audited) |
| 10 | Unauthorized users cannot approve their own workflow unless explicitly permitted by policy | ✅ | nobody approves or sends back a version they submitted or authored — 403 from the service, refused by the trigger, and the submitter cannot be rewritten in the approving statement. There is no policy switch to allow it; `unit` `db` `e2e` (HR's own approve call refused 403) |
| 11 | Validation must pass before approval | ✅ | submission requires a publishable validation report and a dry run with no errors; approval re-validates at that moment and re-checks the fingerprint (the re-validation at approval has no test of its own); `e2e` (*a version with problems cannot go to review*) `ui` `unit` |
| 12 | Existing workflow validation and coverage checks remain enforced | ✅ | `publish()` still runs the full validation and coverage report after approval; the header's Publish stays disabled, with the reason, while issues remain; `ui` `e2e` |
| 13 | A workflow cannot bypass approval through an alternate API/UI path | ✅ | `publish()` is the only writer of `status = 'published'` and `approve()` the only writer of `approved` (a unit test counts it); the trigger holds the whole lifecycle for any writer — API, script or import; `db` `unit` |
| 14 | Tests cover approval, rejection, authorization and version isolation | ✅ | `test_ph4_wave2.py` O6 section, `test_ph4_wave2_guarantees.py` O6 section (every refusal checked for its reason; version isolation via *born an unreviewed draft*), `smoke_ph4_wave2.py` review section, `ReviewPanel` / `WorkflowReviews` / `WorkflowBuilder` / `LifecycleSteps` UI tests, `workflow.spec.ts` (*a second person approves a version before it goes live*) |

**⚠️ Criterion 6 — the exam a live version uses stays editable.** The version itself (settings, rounds, criteria, branches) is frozen by the database from the moment it is submitted, and publishing re-checks a fingerprint that includes the content of every exam round the version points at, so nothing can change between approval and going live. After publishing, though, the exam is its own object: its questions can still be edited until the first candidate attempts it (the existing exam lock), and that edit is not reviewed. Interview kits and stage owners/SLAs also stay editable on approved and live versions — deliberately, as operational guidance that moves nobody. PH4-D1 (Wave 5) is adding a database lock on published exam content, which closes this; until it ships, the gap stands.

**Also worth knowing.**

- **Only a company super admin can approve** (decision D4-2). HR managers cannot review each other's versions. A company with no active super admin can submit but never publish: the submission notifies nobody and logs a warning.
- **There is no outright reject.** A reviewer asks for changes with a note; HR edits and resubmits, or discards the draft.
- **Versions already live when this shipped** were recorded as approved by the migration, and every existing draft as an unreviewed draft.
- **Once a version is live, the builder no longer shows its review history** — the Review panel is on drafts only. The history stays in `workflow_review_events` and the audit log.
- The super admin reads the last dry run recorded for the version but cannot run one; submitting always runs a fresh one.

---

## PH4-O3 — Branching Workflows  ·  Wave 2

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | HR can configure conditional transitions between workflow rounds | ✅ | HR: **Workflow builder → round → Routing** — *If below the threshold* (another round, or hold for a person) and *Fast-track* (a score and a destination, set together); `ui` `smoke` |
| 2 | Existing on_pass_next_round_id logic is reused where applicable | ✅ | the pass branch IS `on_pass_next_round_id`, still maintained by add / remove / reorder and shown read-only in the Routing panel; the two new branches are columns beside it; `ui` `unit` |
| 3 | Workflow authors can visually see branches in the workflow builder | ✅ | the canvas states each branch on its round card — *below threshold → X*, *fast-track → Y (≥90%)* — with dashed / dotted markers and a legend; the pass branch is the vertical order. See below; `ui` |
| 4 | Branch conditions are clearly defined and validated | ✅ | pass = at or above the advance threshold; fast-track = at or above a higher score; below = under the threshold. Validation refuses a half-set fast-track, one at or below the threshold, and one on a human review; `unit` `db` (pair and range CHECKs) `smoke` |
| 5 | Every possible branch must lead to a valid next round or an allowed terminal human-decision state | ✅ | `route_after_result` returns only *advance to a round*, *complete* (the final human decision) or *hold*; branch targets are composite FKs to a round of the same workflow; `unit` `db` |
| 6 | Invalid destinations are rejected during workflow validation | ✅ | the builder refuses a destination outside the workflow or the round itself (409); validation names a branch pointing nowhere; the FK refuses it at the database; `unit` `db` `smoke` |
| 7 | Cyclic/invalid workflow paths are detected | ✅ | validation walks every branch, not just the pass chain: each cycle is named (*A → B → A*) and every round no path reaches is listed, both in one pass; `unit` `smoke` |
| 8 | Branching works correctly when the workflow is executed for a real candidate | ✅ | the runner calls the same `route_after_result`: on a live version 95% fast-tracked, 70% passed on, 30% was routed to *Second look*, and no status became an outcome. Branches are followed only with auto-advance on — see below; `smoke` |
| 9 | held candidates can be routed to the appropriate human-review/decision path | ✅ | a below-threshold branch can send a candidate to a human-review round; without one they are held and appear in the **Decision queue**, where a person continues or decides; `smoke` `ui` |
| 10 | No branch may create an automatic AI terminal rejection | ✅ | there is no rejected destination to point at — every exit ends at a round, a hold or the final human decision; `unit` (`test_no_route_can_ever_be_a_rejection`) |
| 11 | O2 simulation can execute and validate all configured branches before publishing | ✅ | the dry run builds one synthetic candidate per branch exit, reached by its shortest path, through the runner's routing; submission for review runs it; `unit` (`test_every_branch_is_exercised_by_some_scenario`) `smoke` |
| 12 | Published workflow versions remain immutable | ✅ | the published-version trigger covers the new columns like every other; `db` (`test_a_published_versions_branches_are_frozen_too`) |
| 13 | Branch configuration changes are audited | ✅ | an edit to a round's routing writes `workflow.branch.updated` with before and after; so does every route that removing or reordering rounds rewrites (the pass chain included), with the cause — *round_removed* (and which) or *rounds_reordered*; `smoke` |
| 14 | Existing non-branching workflows continue to work unchanged | ✅ | a round with no new branch routes exactly as before (pass → next round or final decision; below → hold) and a linear workflow validates as before; the earlier Group B–E smokes still run their linear workflows, adapted to go through the review gate; `unit` `smoke` |
| 15 | Automated tests cover all supported branch paths and invalid configurations | ✅ | `test_ph4_wave2.py` O3 sections (routing, cycles, reachability, bad branches named in words, same-workflow targets), `test_ph4_wave2_guarantees.py` O3 section, `smoke_ph4_wave2.py` branching and real-candidate sections, `RoundRouting` and `workflowCanvasJourney` UI tests |

**Worth knowing.**

- **Branches are followed only when the workflow advances rounds automatically** (the default). With it switched off, a candidate below the threshold is held rather than routed, and a pass waits for a person, as before. The dry run warns about branches on such a workflow.
- **Branches are drawn as labelled lines on each round card, not as arrows.** Each card states *below threshold → X* and *fast-track → Y (≥90%)*, with a legend; the pass branch is the vertical order of the cards.
- **On a human review the fail branch reads *If not passed***, because there is no score; a human review cannot fast-track.
- Branching on real candidates is proven through the runner in `smoke_ph4_wave2.py`; no browser test drives a branch.

---

## PH4-O2 — Workflow Simulation & Dry Run  ·  Wave 2

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| | **Simulation** | | |
| 1 | Authorized users can run a simulation against a draft workflow | ✅ | HR: **Workflow builder → Dry run → Run dry run** (also step 2 of the lifecycle strip), on any draft; HR managers only, company from the session. The super admin reads the last recorded run on the review page; `ui` `smoke` |
| 2 | Simulation does not create a real candidate application | ✅ | the module writes only `workflow_simulations` and one audit row — an AST test forbids every candidate writer and any other INSERT; the smoke counts `applicants` and `enrolments` before and after; `unit` `smoke` |
| 3 | Simulation does not modify real candidate lifecycle state | ✅ | no call to `record_transition`, `record_round_move`, `record_result` or `on_shortlisted`, no UPDATE of `enrolments`; `stage_transitions` and `round_results` unchanged by a run; `unit` `smoke` |
| 4 | Simulation does not create real interview invitations | ✅ | no `interview_invites` or `exam_assignments` writes (AST test and row counts); `unit` `smoke` |
| 5 | Simulation does not send real candidate-facing emails | ✅ | `enqueue_email` and `create_notification` are forbidden calls in the module; `email_events` and `notifications` unchanged by a run; `unit` `smoke` |
| 6 | Simulation does not trigger real external side effects | ✅ | the module imports nothing that leaves the database — no mailer, no notifier, no model client, no storage; its only writes are its own result and the audit row; `unit` |
| 7 | Simulation uses representative/test candidate data | ⚠️ | synthetic candidates *SIM-001…*, each a score placed where a branch turns (at the threshold, 10 points under, at the fast-track bar). No résumé or profile, and the apply → shortlist step is not simulated — see below; `unit` `smoke` |
| 8 | Simulation follows the configured workflow order and transition logic | ✅ | every step goes through `route_after_result`, the function the runner itself calls, from the first round — and the runner's *auto-advance off* rule too: there, a simulated candidate is held below the threshold or *waits for a person to move them on* after a pass, as a real one would; `unit` `ui` `smoke` |
| | **Validation** | | |
| 9 | Simulation identifies invalid workflow configuration | ✅ | the full structural check runs inside the dry run — unknown round types, too many rounds, dangling or self-pointing branches, loops, unreachable rounds — each an **Error** on its round; `unit` |
| 10 | Simulation identifies missing required round configuration | ✅ | a test round with no questions, an attached exam still in draft or empty, a scored round without an advance threshold: each an **Error**; `unit` (`test_a_draft_exam_is_an_execution_blocking_error`) |
| 11 | Simulation identifies invalid transitions | ✅ | a branch pointing outside the workflow or at its own round is an error; a fast-track going where the pass branch already goes is a warning; `unit` |
| 12 | Simulation identifies unreachable rounds | ✅ | *Unreachable: no branch from the first round ever gets here* on each such round, found by a walk over every branch; `unit` |
| 13 | Simulation identifies invalid branching conditions where branching is configured | ✅ | a half-set fast-track, a fast-track at or below the advance threshold, a fast-track on a human review; branches on a workflow with auto-advance off are a warning; `unit` `smoke` |
| 14 | Simulation identifies missing evaluation criteria where required | ✅ | an AI interview assessing no competency is an error; a human review with no criteria is a warning (no scorecards can be assigned for it); role-coverage gaps are warnings; `unit` |
| 15 | Simulation identifies incompatible round configuration | ✅ | a human review with a fast-track, a test round without questions, a draft exam are errors; a 0% or 100% threshold, a >60-day deadline, an SLA with no owner are warnings; `unit` |
| 16 | Simulation identifies other execution-blocking errors before publication | ✅ | any path that cannot finish is an error naming its scenario; submission for review is refused while the dry run has errors, and the Review panel lists them; `unit` `ui` |
| | **Results** | | |
| 17 | Simulation produces a clear execution trace | ✅ | **Dry run → Show N scenarios**: each synthetic candidate's path round by round — outcome, where it went, how it ended (*reaches a decision* / *is held for you* / *could not finish*); `ui` `smoke` |
| 18 | Each simulated round shows its simulated result/state | ✅ | each round is listed with **OK / Warning / Error** in words and its findings; each scenario step shows that round's simulated outcome; `ui` `unit` |
| 19 | Errors and warnings are clearly distinguished | ✅ | severity written out (*ERROR* / *WARNING*) and coloured, counted apart (*2 errors · 1 warning*); overall *Passed* / *Needs attention* / *Failed*; `ui` `unit` |
| 20 | HR can identify where the workflow failed | ✅ | each finding is attached to the round it concerns; a path that fails names the scenario and the step where it stopped; `ui` `unit` |
| 21 | HR can re-run the simulation after fixing configuration | ✅ | **Run again** after any edit; a result older than the version's current content is marked *Stale* (content fingerprint), never shown as current; `ui` `smoke` |
| 22 | Successful simulation indicates that the workflow can execute under the tested scenario, but does not automatically publish it | ✅ | a run publishes nothing and says so (*A passing dry run publishes nothing*); going live still needs submission, a super admin's approval and Publish; `ui` `unit` |
| | **Safety** | | |
| 23 | Simulation cannot modify production candidate records | ✅ | no write path to any candidate table exists in the module (AST test); eight candidate tables are unchanged by a run; `unit` `smoke` |
| 24 | Simulation cannot cause real candidate notifications | ✅ | no email or notification can be produced (forbidden calls; counts unchanged); `unit` `smoke` |
| 25 | Simulation cannot create real offers/documents | ✅ | the only INSERT the module can make is into `workflow_simulations` — asserted by the test, so any future offer or document table is covered too; `unit` |
| 26 | Simulation cannot make real hiring decisions | ✅ | `record_final_decision` and every status writer are forbidden calls; every scenario ends at the final decision (a person's) or a hold, never an outcome; `unit` `smoke` |
| 27 | Simulation cannot bypass existing workflow authorization | ✅ | `POST /hr/workflows/{id}/simulate` sits behind the HR gate, company from the session; another company's workflow is 404; no session is refused; `unit` `smoke` |
| 28 | Simulation activity is auditable | ✅ | each run writes `workflow.simulated` (who, version, status, counts, fingerprint) and an append-only result row naming who ran it; the panel shows *by <name>*; `unit` `db` `smoke` |
| | **Compatibility** | | |
| 29 | Existing published workflows continue to operate normally | ✅ | a run touches no workflow row; versions live before this wave were recorded as approved by the migration and keep running; `smoke` (the Group C runner and immutability smokes, seeded through the review gate) |
| 30 | Draft workflows can still be edited after a simulation | ✅ | a run locks nothing: the draft stays editable and the last result turns *Stale* once it changes; `unit` (writes only its own table) `ui` |
| 31 | Tests cover successful and failed simulation scenarios | ✅ | `test_ph4_wave2.py` O2 section (a clean workflow passes; a draft exam, a loop, a dangling branch fail; every branch exercised; scenario count bounded; nothing written but its own row), `test_ph4_wave2_guarantees.py` (results append-only), `smoke_ph4_wave2.py` dry-run section, `DryRunPanel` UI tests |

**⚠️ Criterion 7 — the test candidates are scores, not profiles.** Each synthetic candidate (SIM-001, …) is a score placed exactly where a branch turns: at the advance threshold, 10 points under it, or at the fast-track bar. They carry no résumé, profile or language, and the walk starts at the first round — so the step before it, scoring on apply and the ATS shortlist threshold, is not simulated.

**Also worth knowing.**

- The dry run exercises every branch exit once (one candidate per exit, by the shortest path to it), not every combination of branches — at most 37 scenarios.
- The **Dry run** panel is on drafts in the builder. Submitting for review always runs a fresh one, and the super admin reads the latest on the review page.
- A result is marked *Stale* as soon as the version's content changes; results are append-only and cannot be edited.

---

## PH4-O1 — Stage Owners, SLAs & Exception Paths  ·  Wave 2

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Each applicable workflow stage can have an assigned owner/team | ⚠️ | HR: **Workflow builder → round → Stage owner & SLA**, and **Settings → Final decision — owner & SLA**; editable on a live version too. An owner is one HR manager — there is no team — see below; `ui` `smoke` |
| 2 | A stage can have an SLA duration | ✅ | SLA in hours, 1–8760, per round and for the final decision; one setting per stage (unique indexes); `ui` `db` `smoke` |
| 3 | SLA countdown is based on the appropriate stage-entry timestamp | ✅ | `enrolment_stage_entered_at`: the latest ledger entry that changed the status or the round, so a note that moves nothing does not restart the clock; state derived at read time (on track / due soon at 75% / overdue); `unit` `db` `smoke` |
| 4 | Overdue stages are clearly identified | ✅ | **Decision queue**: *Overdue since <time> · <owner>* badge; **HR home → Stages at risk**: *overdue* in words with a red marker; the owner is notified once per stage entry by the reminder sweep; `ui` `smoke` |
| 5 | HR can see stages approaching or exceeding their SLA | ✅ | **HR home → Stages at risk** (six shown, *View all*) and the **Stages at risk** page: every application against its stage SLA at any stage — exam and AI rounds included — worst first, filterable, with time over and open exceptions; each row opens the candidate's drawer. Plus the **Decision queue**'s *Overdue or due soon only* filter; `ui` `smoke` |
| 6 | Exceptions can be explicitly recorded | ✅ | HR: candidate drawer → **Exceptions → Raise exception** — a reason of 10–1000 characters and an owner (defaults to the raiser); `ui` `smoke` |
| 7 | Exception records include reason, owner and timestamp | ✅ | each exception stores reason, owner, who raised it, when, and the stage; on resolution who, when and a note. The drawer shows all of it; `db` `ui` `smoke` |
| 8 | Authorized users can resolve an exception | ✅ | any HR manager of the company: **Exceptions → Resolve** (optional note) or **Reassign to**; a resolved exception cannot be resolved again; `ui` `smoke` |
| 9 | Exception handling does not silently alter candidate outcome | ✅ | raising, reassigning and resolving call no status or round writer (AST test); the smoke checks the candidate's status is unchanged; the drawer says so; `unit` `smoke` `ui` |
| 10 | SLA/ownership information is visible in appropriate HR views | ✅ | **Decision queue** (SLA badge with owner, open-exception count), **HR home → Stages at risk** (owner), the builder's stage settings, and the super admin's review page (*Stage owners & SLAs*); `ui` |
| 11 | Existing workflows without SLA configuration continue to work | ✅ | no SLA row means no SLA state and no badge; an exception on a stage without an SLA still counts; nothing else changes for such a workflow; `unit` `db` |
| 12 | Stage ownership and exception actions are audited | ✅ | `stage.settings.updated` (owner and SLA, before and after), `stage.exception.raised` / `.resolved` / `.reassigned` — the reason recorded as its length, never its text; `unit` `smoke` |
| 13 | Tests cover SLA calculation, overdue states, permissions and exception handling | ✅ | `test_ph4_wave2.py` O1 section (SLA states, the clock, no candidate writer, reason kept out of the audit log, HR gate), `test_ph4_wave2_guarantees.py` O1 section, `smoke_ph4_wave2.py` SLA and exception section (overdue board, owner told once, owner must be HR, anonymous refused), `StageSettings` / `StagesAtRiskWidget` / `ExceptionsSection` / `DecisionQueue` UI tests |

**⚠️ Criterion 1 — an owner is one person, not a team.** Each stage takes a single owner, who must be an active HR manager of the company. Interviewers and super admins cannot own a stage, and there is no team or group to assign.

**Also worth knowing.**

- **A held candidate's clock restarts when they are held**, and runs against the SLA of the round they were held on, not the final decision's.
- The stage owner is told once, by the reminder sweep, when an application in their stage runs overdue; a candidate who moves on and comes back is announced again.
- An exception's reason is kept on its row and redacted on erasure; the audit log records only its length.

---

## PH4-A2 — Interview Scheduling + Loops  ·  Wave 3

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| | **Individual Scheduling** | | |
| 1 | Authorized HR users can configure interviewer availability | ✅ | HR: **Interview panel → Availability** — choose an interviewer, add or remove windows in local time; an overlapping window is refused in words; `ui` `smoke` |
| 2 | Interviewers can have available time windows | ✅ | Interviewer: **My interviews → My availability**, their own windows only; a window is at most 14 days and never overlaps another (exclusion constraint); `ui` `db` `smoke` `e2e` |
| 3 | Candidates can select an available slot when self-scheduling is enabled | ✅ | Candidate: **My applications → Your interviews → Choose a time** on a loop HR set to *Let the candidate choose times*; only times inside every interviewer's availability and clear of bookings, 2 h to 21 days ahead, are offered; `ui` `smoke` `e2e` |
| 4 | Candidate timezone is captured and displayed correctly | ⚠️ | stored per loop as a validated IANA name; HR picks it when creating the loop (default *Asia/Kolkata*) and the candidate's browser zone replaces it when they book. Candidates and emails see times in that zone, named — see below; `unit` `smoke` `ui` |
| 5 | Scheduled times are stored in a timezone-safe format | ✅ | every instant is `timestamptz`; a time with no offset is refused, not guessed; the zone is used only to display; `unit` `db` `smoke` |
| 6 | A slot cannot be double-booked | ✅ | exclusion constraints: an interviewer's live sessions, and a candidate's sessions plus the loop's gap, cannot overlap; a booking must be a slot free at that moment, once; a lost race is a 409 *That time was just taken*; `db` `smoke` |
| 7 | Interviewer conflicts are prevented | ✅ | an interviewer cannot sit two overlapping live sessions, across candidates and loops, and moving a session onto another is refused; `db` `smoke` |
| 8 | HR can manually schedule an interview | ✅ | HR: candidate drawer → **Interviews → Add session** with a start time; a time outside the panel's availability is refused with **Schedule anyway** offered, not taken; `ui` `smoke` `e2e` |
| | **Interview Loops** | | |
| 9 | HR can create an interview loop for a candidate | ✅ | HR: candidate drawer → **Interviews → New loop** — title, candidate timezone, gap between sessions, whether the candidate chooses times; `ui` `smoke` `e2e` |
| 10 | A loop can contain multiple interview sessions | ✅ | up to 8 sessions per loop; `ui` `smoke` (a three-session loop) |
| 11 | Each session can have its own interview round/type | ✅ | each session is tied to one human-review round of the candidate's workflow — its title, criteria and kit; `ui` `smoke` |
| 12 | Each session can have its own interviewer(s) | ✅ | one to six interviewers per session, each through the A1 eligibility and independence rules; `ui` `smoke` |
| 13 | Each session can have its own duration | ✅ | 15 minutes to 8 hours per session; the end time is derived from it (CHECK); `ui` `db` `smoke` |
| 14 | Multiple sessions can be scheduled for the same candidate on the same day | ✅ | several sessions on one day, separated by the loop's gap; `db` `smoke` |
| 15 | The system prevents overlapping sessions for the candidate | ✅ | exclusion constraint on the candidate's sessions, each extended by the gap (`blocked_until`); an overlapping or inside-the-gap session is refused in words; `db` `smoke` |
| 16 | The system prevents interviewer conflicts across loop sessions | ✅ | the interviewer exclusion spans every loop and candidate; slot search subtracts each panel member's bookings and needs all of them free; `db` `unit` `smoke` |
| 17 | Configurable gaps/buffers can exist between sessions | ✅ | a gap of 0–240 minutes per loop, set when the loop is created, enforced by the database and by slot search on both sides; `unit` `db` `smoke` |
| 18 | The candidate receives the complete loop schedule as one coordinated itinerary | ✅ | **Send to candidate** emails ONE itinerary — every session's time in their zone, duration and place — in the candidate's language, with an in-app notice; a self-scheduling loop sends it when the last time is booked; `smoke` `ui` |
| 19 | Each session maintains its own interview and scorecard relationship | ⚠️ | each session row links each interviewer to their A1 scorecard, and the drawer shows its status; a scorecard is per candidate, round and interviewer, so two sessions sharing both share one — see below; `smoke` `ui` |
| 20 | The loop has an overall status | ✅ | *draft* / *scheduled* / *completed* / *cancelled*, derived from the sessions by one rule, shown on the loop; `ui` `smoke` |
| 21 | Individual sessions have their own status | ✅ | *awaiting slot* / *scheduled* / *completed* / *no-show* / *cancelled*, each a tag; **Mark completed** / **Mark no-show** once it has started; `ui` `smoke` |
| 22 | HR can view the complete loop from the candidate/application view | ✅ | candidate drawer → **Interviews**: every loop, session (day and time in the candidate's zone, and the reader's own), round, interviewer and scorecard status; `ui` `e2e` |
| | **Changes & Calendar** | | |
| 23 | Candidate-initiated rescheduling is not supported | ✅ | the candidate's only write route is *book a slot that is awaiting one*; a booked session refuses another booking; **Your interviews** shows no move or cancel control; `unit` `smoke` `e2e` |
| 24 | Authorized HR users can modify or cancel scheduled sessions/loops | ⚠️ | HR: **Reschedule** (time, duration, place), **Set a time** for a session still waiting on the candidate, **Cancel** a session, **Cancel loop**; candidate and panel are told, calendars update, and a cancelled interview hands back the untouched scorecards only it held. A session's interviewers and a loop's settings cannot be changed — see below; `ui` `smoke` |
| 25 | Scheduling changes are audit logged | ✅ | `loop.created` / `.sent` / `.cancelled` / `.closed_on_decision`, `session.created` / `.rescheduled` / `.cancelled` / `.completed` / `.no_show` / `.booked_by_candidate`, `availability.added` / `.removed`, plus new `interview_invite.*` rows for AI invites (untested) — reasons as a length only; `smoke` |
| 26 | ICS calendar information can be generated for scheduled sessions | ✅ | HR: **Interviews → Download calendar (.ics)**; candidate: **Your interviews → Add to calendar (.ics)**. RFC 5545, UTC times, cancelled sessions marked cancelled. Interviewers get none — see below; `unit` `smoke` `ui` |
| 27 | Existing single-interview scheduling continues to work | ✅ | the AI-interview invites on **Interviews** work as before; their reschedule dialog now pre-fills local time (it showed UTC as local); `unit` (`test_group_a_reliability.py` reschedule tests) `ui` |
| 28 | Existing invite.scheduled_at behavior remains compatible where applicable | ✅ | `interview_invites.scheduled_at` and its 24 h / 1 h reminders are unchanged; a new time still re-arms them, and is now audited; `unit` `ui` |
| 29 | Existing email/outbox infrastructure is reused for scheduling notifications | ✅ | itinerary, choose-your-time, change / cancel and 24 h / 1 h reminder emails go through `enqueue_email` with dedupe keys, in EN / HI / TE; the reminders are a stage of the existing sweep; `unit` `smoke` |

**⚠️ Criterion 4 — the candidate's zone is chosen for them unless they pick a time.** HR sets the candidate's timezone when creating a loop (default *Asia/Kolkata*); it is taken from the candidate — their browser — only when they book a slot themselves. A candidate on a fixed-time loop never confirms it. Their times are always shown with the zone named, so the instant is never ambiguous, but it is not converted to where they actually are.

**⚠️ Criterion 19 — one scorecard can span two sessions.** A1 scorecards are one per candidate, round and interviewer. If a loop has two sessions for the same round with the same interviewer, both link the one scorecard. (`smoke_ph4_wave3.py` builds exactly this.)

**⚠️ Criterion 24 — what HR cannot change.**

- **The panel.** There is no way to change a session's interviewers: cancel the session and add a new one.
- **Loop settings.** A loop's timezone, gap and self-scheduling cannot be edited after it is created.
- **Cancelling and a begun scorecard.** Cancelling hands back each scorecard the cancelled sessions alone held and the interviewer had not started. One they had begun is left for HR to withdraw from the drawer's **Human interview** section.

**Also worth knowing.**

- **Interviewers get no calendar file**; HR and the candidate can download one, and emails link to *My applications* rather than attaching one. Every change to a sent schedule raises the file's `SEQUENCE`, so calendar apps update the entry they already hold.
- **A session added to a loop already sent** is announced to its panel; the candidate sees it on *My applications* but is emailed only when HR presses **Send to candidate** again, which sends an updated itinerary.
- **The round picker lists the opening's live version.** A candidate still on an older version cannot be scheduled from the drawer — the server refuses the round. Same as A1's assign form.
- **A final decision cancels every interview not yet held** and tells the interviewers; the candidate learns from the decision email.
- **Self-scheduling needs availability.** It offers only times inside every panel member's windows, on a 15-minute grid, 2 hours to 21 days ahead.

---

## PH4-O5 — Panel Workload & Calibration  ·  Wave 3

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | HR can view interviewer workload across upcoming interview sessions | ✅ | HR: **Interview panel → Workload** — one card per schedulable interviewer and HR manager, most flagged and most loaded first; `ui` `smoke` `e2e` |
| 2 | Workload includes scheduled interviews and assigned candidates | ✅ | each card counts live sessions, hours and loops in the period, and open scorecards (assigned or in progress — the candidates they owe); `unit` `smoke` `ui` |
| 3 | Interview loops with multiple interviewers are represented correctly | ✅ | a session with several interviewers counts once for each of them; *Loops* counts distinct loops per person; `unit` `smoke` |
| 4 | HR can identify interviewer over-allocation or scheduling conflicts | ✅ | flags in words: over the daily or weekly limit (per-person limits editable on the card; default 4 / 15), sessions outside their availability, overdue scorecards, and an overlap detector. A flag, never a refusal; `unit` `ui` `smoke` |
| 5 | Workload can be viewed by relevant time period | ✅ | **This week / Next week / Next 30 days**; the API takes any period up to 92 days; days are counted in India time; `ui` `unit` |
| 6 | Interviewers only see candidates and information they are authorized to access | ✅ | Interviewer: **My interviews → Upcoming interviews** lists only sessions they sit on, with their own scorecard link; they edit only their own availability (another's is 404); every `/hr` panel route refuses them; `unit` `smoke` `ui` |
| 7 | Scorecards continue to use the same frozen round_criteria | ✅ | a session's scorecard IS an A1 scorecard, made by `interviewer_scorecards.assign`, so the composite FK to the frozen `round_criteria` still holds; `smoke` `db` (Wave 1 guarantees) |
| 8 | Calibration views can compare scoring patterns across interviewers without changing submitted scorecards | ✅ | HR: **Interview panel → Calibration** — per interviewer: scorecards, mean, 1–5 distribution, *not assessed* rate and the paired gap to the panel on the same candidates; read-only, filtered by opening and round. 30, 90 or 180 days (calibration may look back up to a year); `unit` `ui` `smoke` |
| 9 | HR can identify meaningful scoring differences between interviewers | ✅ | *Scores consistently higher / lower than the panel on the same candidates* when the paired gap is ≥0.75 over ≥5 shared judgements, and only with ≥5 candidates behind it; `unit` `ui` |
| 10 | Submitted scorecards remain immutable | ✅ | the A1 triggers are unchanged; calibration reads current submitted scorecards and the smoke finds them identical after it runs; `db` `smoke` |
| 11 | Calibration insights cannot automatically change candidate status or hiring decisions | ✅ | the calibration code holds no UPDATE, INSERT or DELETE beyond its audit row, and scheduling and panel code call no status writer (AST tests); `unit` `smoke` |
| 12 | No AI-generated calibration recommendation can become a hiring decision | ✅ | no model is called and nothing is recommended: plain arithmetic, and the test fails if an LLM provider or *recommend* appears in the code; `unit` |
| 13 | Relevant workload/calibration actions are audited | ✅ | `panel.calibration.viewed` (who, period, filters), `panel.capacity.updated`, `availability.added` / `.removed`; opening the workload is not logged — it names no candidate; `smoke` |
| 14 | Existing scheduling and scorecard functionality continues to work | ✅ | AI-interview invites are untouched; the drawer's A1 scorecard assignment is unchanged — scheduling calls the same `assign` and tells the interviewer itself; the interviewer console gains *Upcoming interviews* and *My availability*; `unit` `ui` |
| 15 | Tests cover workload calculation, panel assignments, permissions, and calibration calculations | ✅ | `test_ph4_wave3.py` (workload maths, multi-interviewer sessions, calibration maths and suppression, audience gates, no writes), `test_ph4_wave3_guarantees.py` (panel exclusion), `smoke_ph4_wave3.py` workload and calibration sections (permissions, independence, scorecards untouched), `WorkloadTab` / `CalibrationTab` / `PanelAvailabilityTab` / `InterviewPanel` / `InterviewerConsole` UI tests |

**Worth knowing.**

- **Per-competency means are computed and returned but not shown** on the Calibration tab.

- **Small panels see little.** Figures are withheld for an interviewer who scored fewer than 5 distinct candidates within the filter — an average over one candidate *is* that candidate's score — and a difference is flagged only at 0.75 points or more over 5 or more shared judgements. A small company will mostly see *Too few candidates to compare*.
- **An HR manager who still owes a scorecard** for a candidate and round sees nobody else's scores for it in calibration either (the A1 independence rule).
- **The workload shows counts, not the sessions themselves.** Open scorecards are counted whatever the period; days are counted in India time; over-allocation is a flag and nothing is refused.
- **Workload views are not logged; calibration views are.** The workload names no candidate.

---

## PH4-A3 — Offer Lifecycle  ·  Wave 4

| # | Acceptance criterion | |
|---|---|---|
| | **Offer Creation** | |
| 1 | An authorized HR user can create an offer for a candidate with a human-approved hiring decision | ⏳ |
| 2 | An offer is associated with the correct requisition and candidate/enrolment | ⏳ |
| 3 | An offer can use an existing offer template | ⏳ |
| 4 | Offer details can include compensation and relevant employment terms | ⏳ |
| 5 | Draft offers are editable by authorized users | ⏳ |
| 6 | Candidate compensation information is protected by appropriate permissions | ⏳ |
| | **Approval** | |
| 7 | An offer can be submitted for approval | ⏳ |
| 8 | Authorized approvers can approve or reject the offer | ⏳ |
| 9 | Approval actions record the approver and timestamp | ⏳ |
| 10 | An unapproved offer cannot be sent to the candidate | ⏳ |
| 11 | Approval/rejection actions are auditable | ⏳ |
| | **Candidate Delivery** | |
| 12 | An approved offer can be sent securely to the candidate | ⏳ |
| 13 | Candidate receives a secure offer link | ⏳ |
| 14 | Candidate can access the offer without exposing the offer to unauthorized users | ⏳ |
| 15 | Offer delivery uses the existing secure magic-link pattern where appropriate | ⏳ |
| 16 | Offer access and important actions are audit logged | ⏳ |
| | **Acceptance / Decline** | |
| 17 | Candidate can accept an offer | ⏳ |
| 18 | Candidate can decline an offer | ⏳ |
| 19 | Acceptance/decline requires appropriate secure authentication | ⏳ |
| 20 | Acceptance records timestamp and relevant audit information | ⏳ |
| 21 | Decline records timestamp and reason where collected | ⏳ |
| 22 | An accepted offer cannot silently be changed to declined | ⏳ |
| 23 | A declined offer cannot silently be changed to accepted | ⏳ |
| | **Lifecycle** | |
| 24 | Offers support explicit states | ⏳ |
| 25 | Offer expiry can be configured | ⏳ |
| 26 | Expired offers cannot be accepted | ⏳ |
| 27 | Authorized HR users can withdraw an offer before acceptance | ⏳ |
| 28 | Offer state transitions are validated server-side | ⏳ |
| 29 | Candidate hiring status is updated appropriately after final offer outcome | ⏳ |
| 30 | Existing decision records remain immutable | ⏳ |
| | **Safety / Governance** | |
| 31 | AI cannot create, approve, send, accept or decline offers on behalf of users | ⏳ |
| 32 | Offer actions are company-scoped | ⏳ |
| 33 | Compensation data is only available to authorized users | ⏳ |
| 34 | Offer records participate in existing retention/erasure mechanisms | ⏳ |
| 35 | Tests cover all major state transitions and authorization boundaries | ⏳ |

---

## PH4-A4 — Documents & Preboarding  ·  Wave 4

| # | Acceptance criterion | |
|---|---|---|
| | **Document Requirements** | |
| 1 | Authorized HR users can define document requirements for a requisition/preboarding process | ⏳ |
| 2 | Each requirement specifies the expected document type | ⏳ |
| 3 | Requirements can be marked mandatory or optional | ⏳ |
| 4 | Requirements can define expiry requirements where applicable | ⏳ |
| 5 | The system identifies which documents are outstanding | ⏳ |
| | **Candidate Upload** | |
| 6 | Candidates can securely upload required documents after offer acceptance | ⏳ |
| 7 | Uploads are associated with the correct candidate/enrolment | ⏳ |
| 8 | Candidate can see required, submitted, verified and rejected documents | ⏳ |
| 9 | Candidate can re-upload a rejected document | ⏳ |
| 10 | Candidate-facing document status is clearly communicated | ⏳ |
| 11 | Candidate cannot access another candidate's documents | ⏳ |
| | **HR Review** | |
| 12 | Authorized HR users can view submitted documents | ⏳ |
| 13 | HR can mark a document as verified | ⏳ |
| 14 | HR can reject a document | ⏳ |
| 15 | Rejection can include a reason | ⏳ |
| 16 | HR can request a replacement/re-upload | ⏳ |
| 17 | Verification actions record reviewer and timestamp | ⏳ |
| 18 | Document verification history is auditable | ⏳ |
| | **Preboarding** | |
| 19 | The system can determine when all mandatory documents are complete | ⏳ |
| 20 | A candidate cannot be marked preboarding-complete while mandatory documents remain unresolved | ⏳ |
| 21 | HR can see overall preboarding status | ⏳ |
| 22 | Preboarding status is associated with the accepted offer/candidate | ⏳ |
| | **Security / Compliance** | |
| 23 | Document access is permission-controlled | ⏳ |
| 24 | Documents participate in consent and retention rules | ⏳ |
| 25 | Document data participates in the existing erasure workflow | ⏳ |
| 26 | Document storage uses the existing secure object-storage/upload mechanism where possible | ⏳ |
| 27 | Document metadata is company-scoped | ⏳ |
| 28 | Unauthorized users cannot download/view protected documents | ⏳ |
| | **HRMS Handoff** | |
| 29 | Once preboarding requirements are complete, the system can prepare a signed export/webhook payload | ⏳ |
| 30 | The handoff contains only the required authorized information | ⏳ |
| 31 | Handoff activity is auditable | ⏳ |
| 32 | No vendor-specific HRMS integration is required for this phase | ⏳ |
| | **Tests** | |
| 33 | Tests cover upload, review, rejection, re-upload, verification, permissions, expiry and completion | ⏳ |

---

## PH4-D1 — Reusable Question Banks  ·  Wave 5

| # | Acceptance criterion | |
|---|---|---|
| 1 | Authorized users can create reusable questions | ⏳ |
| 2 | Questions can be organized into question banks | ⏳ |
| 3 | Questions support existing assessment question types | ⏳ |
| 4 | Questions can be associated with relevant competencies | ⏳ |
| 5 | Questions can have difficulty metadata | ⏳ |
| 6 | Questions can have appropriate review/approval status | ⏳ |
| 7 | Approved questions can be reused across multiple assessments | ⏳ |
| 8 | Reusing a question does not mutate previously published assessments | ⏳ |
| 9 | Published assessment questions remain immutable | ⏳ |
| 10 | Users can search/filter questions within authorized banks | ⏳ |
| 11 | Users can select questions when configuring an assessment | ⏳ |
| 12 | Duplicate/unwanted question selection is prevented where appropriate | ⏳ |
| 13 | Question bank access respects company/tenant permissions | ⏳ |
| 14 | Existing assessment questions continue to work | ⏳ |
| 15 | Tests cover creation, reuse, permissions and immutability | ⏳ |

---

## PH4-D2 — Candidate Accommodations  ·  Wave 5

| # | Acceptance criterion | |
|---|---|---|
| 1 | Authorized HR users can record an accommodation for a candidate | ⏳ |
| 2 | Accommodations are associated with the appropriate candidate/application | ⏳ |
| 3 | An accommodation can be associated with a specific assessment/interview where applicable | ⏳ |
| 4 | Authorized users can define the effective period or relevant assessment | ⏳ |
| 5 | The candidate's assessment experience reflects the approved accommodation | ⏳ |
| 6 | Additional time/deadline adjustments are applied correctly where configured | ⏳ |
| 7 | Accommodation information is not exposed unnecessarily to interviewers or other users | ⏳ |
| 8 | Accommodation does not change the underlying competency or evaluation criteria | ⏳ |
| 9 | Accommodation does not automatically increase or decrease candidate scores | ⏳ |
| 10 | Accommodation changes are auditable | ⏳ |
| 11 | Candidate data associated with accommodations follows existing consent, retention and erasure rules | ⏳ |
| 12 | Existing candidates without accommodations continue through the normal flow | ⏳ |
| 13 | Tests cover accommodation configuration, application and permission boundaries | ⏳ |

---

## PH4-D3 — Code Quality & Similarity Evidence  ·  Wave 5

| # | Acceptance criterion | |
|---|---|---|
| | **Code Quality** | |
| 1 | Coding submissions can be analyzed for supported quality signals | ⏳ |
| 2 | Quality signals can include measurable characteristics such as: | ⏳ |
| 3 | complexity | ⏳ |
| 4 | duplication | ⏳ |
| 5 | maintainability indicators | ⏳ |
| 6 | test coverage where available | ⏳ |
| 7 | code smells/static-analysis findings where supported | ⏳ |
| 8 | Quality results are associated with the specific candidate submission | ⏳ |
| 9 | Results are available to authorized reviewers | ⏳ |
| 10 | Quality signals are presented as evidence rather than an automatic decision | ⏳ |
| 11 | Analysis failures do not invalidate a candidate submission | ⏳ |
| | **Similarity / Integrity** | |
| 12 | The system can calculate similarity between code submissions where supported | ⏳ |
| 13 | Similarity analysis can identify potentially related submissions/reference material | ⏳ |
| 14 | Similarity results include enough context for a human reviewer to investigate | ⏳ |
| 15 | Similarity results are not treated as proof of misconduct by themselves | ⏳ |
| 16 | Reviewers can distinguish between a similarity signal and a confirmed integrity finding | ⏳ |
| 17 | Existing assessment integrity evidence remains available alongside code-analysis evidence | ⏳ |
| | **Decision Safety** | |
| 18 | Code quality cannot automatically reject a candidate | ⏳ |
| 19 | Code similarity cannot automatically reject a candidate | ⏳ |
| 20 | AI cannot directly change candidate lifecycle status based on these signals | ⏳ |
| 21 | Any resulting candidate decision remains human-authorized | ⏳ |
| 22 | Evidence and reviewer actions are auditable | ⏳ |
| | **Security** | |
| 23 | Analysis runs in an appropriately isolated environment | ⏳ |
| 24 | Candidate code is not unnecessarily exposed to unauthorized users | ⏳ |
| 25 | Company/tenant boundaries are enforced | ⏳ |
| 26 | Sensitive code-analysis data follows retention and erasure requirements | ⏳ |
| | **Compatibility** | |
| 27 | Existing coding assessments continue to work if analysis is unavailable | ⏳ |
| 28 | Existing sandbox/test execution remains functional | ⏳ |
| 29 | Analysis results do not alter the candidate's submitted source code | ⏳ |
| 30 | Tests cover quality analysis, similarity detection, permissions and no-auto-reject behavior | ⏳ |

---

## PH4-D4 — Job Simulations & Portfolio  ·  Wave 5

| # | Acceptance criterion | |
|---|---|---|
| | **Job Simulations** | |
| 1 | Authorized users can create a job simulation round | ⏳ |
| 2 | Simulation rounds can be associated with role competencies | ⏳ |
| 3 | A simulation can contain a structured candidate task/scenario | ⏳ |
| 4 | Candidates can access assigned simulations through the existing candidate experience | ⏳ |
| 5 | Candidates can submit simulation responses/work | ⏳ |
| 6 | Simulation submissions are persisted and associated with the candidate/enrolment | ⏳ |
| 7 | Simulation submissions have a clear lifecycle | ⏳ |
| 8 | Authorized reviewers can review submitted work | ⏳ |
| 9 | Reviewers can record structured evaluation/evidence | ⏳ |
| 10 | Simulation evaluation uses the existing competency/evaluation architecture where applicable | ⏳ |
| | **Portfolio** | |
| 11 | Authorized users can configure a portfolio submission round | ⏳ |
| 12 | Candidates can submit portfolio artifacts or approved external links | ⏳ |
| 13 | Portfolio submissions can support appropriate metadata such as title, description and artifact/link type | ⏳ |
| 14 | Authorized reviewers can view submitted portfolio evidence | ⏳ |
| 15 | Reviewers can record evaluation/evidence against configured criteria | ⏳ |
| 16 | Portfolio submissions are associated with the candidate/enrolment | ⏳ |
| 17 | Candidate submissions remain protected by appropriate permissions | ⏳ |
| | **Decision & Safety** | |
| 18 | Simulation and portfolio evidence appears in the candidate evidence/decision workspace | ⏳ |
| 19 | Simulation/portfolio results do not automatically reject candidates | ⏳ |
| 20 | AI cannot independently determine a candidate's final outcome from these submissions | ⏳ |
| 21 | Human decision authority remains unchanged | ⏳ |
| 22 | Evaluation criteria remain traceable to the configured workflow/round criteria | ⏳ |
| 23 | Published assessment configuration remains protected from unintended modification | ⏳ |
| | **Security / Compliance** | |
| 24 | Uploaded portfolio artifacts follow existing secure storage mechanisms | ⏳ |
| 25 | Candidate-submitted data follows consent, retention and erasure rules | ⏳ |
| 26 | Access is company/tenant scoped | ⏳ |
| 27 | Unauthorized candidates cannot access another candidate's submissions | ⏳ |
| 28 | Review and evaluation actions are auditable | ⏳ |
| | **Compatibility** | |
| 29 | Existing MCQ, coding and AI interview rounds continue to work | ⏳ |
| 30 | New simulation/portfolio rounds integrate with the existing workflow runner | ⏳ |
| 31 | Tests cover candidate submission, reviewer access, evaluation and decision integration | ⏳ |

---

## Decisions this phase rests on

| | Decision | Consequence you can see |
|---|---|---|
| D4-1 | A new `interviewer` role that sees only the interviews assigned to them | The **My interviews** console; super admins manage interviewers under **Team** |
| D4-2 | The super admin approves offers (A3) and workflow versions (O6) | Wave 2 and Wave 4 |
| D4-3 | Uploads are limited to PDF, JPEG and PNG, checked by content (not by extension); downloads only through signed links; no virus scanner yet | Wave 4 (A4) |
| D4-4 | Merge wave by wave; each wave's migrations are applied just before that wave deploys | This document is updated per wave |
