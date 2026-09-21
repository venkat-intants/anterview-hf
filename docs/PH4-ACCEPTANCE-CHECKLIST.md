# PH4 — acceptance checklist against `docs/AntHire-Phase4.docx`

**Updated:** 2026-09-21 (first built 2026-09-17). **Checked against:** every acceptance criterion in the Phase 4
document, in the document's own order and wording — all 317 of them across 15 stories.

**Delivery (decision D4-4):** wave by wave. Each wave merges and deploys on its own, only
after CI, a code review and a security sign-off.

| Wave | Stories | State |
|---|---|---|
| 1 | A1 Human interview scorecards · A5 Interview kits · O4 Decision reason codes | **Live.** See the deployment line below. |
| 2 | O6 Workflow review & approval · O3 Branching workflows · O2 Workflow simulation & dry run · O1 Stage owners, SLAs & exceptions | **Live.** PR #29, merged 2026-09-20 as `e064968`. |
| 3 | A2 Interview scheduling & loops · O5 Panel workload & calibration | **Live.** PR #30, merged 2026-09-20 as `fe154de`. |
| 4 | A3 Offer lifecycle · A4 Documents & preboarding | **Live.** PR #31, merged 2026-09-20 as `fff4cbf`. |
| 5 | D1 Question banks · D2 Accommodations · D3 Code quality & similarity · D4 Job simulations & portfolio | **Built and signed off** — security signed off all four stories for production (D4 after three rounds of review), and the code review approved. It ships in the same pull request as this version of the document. |

**Deployment:** Waves 1–4 are **live** on the HF Space, which deploys `main` after CI passes; `space/entrypoint.sh` applies migrations (`alembic upgrade head`) on every boot. Wave 1 merged as `c3fb463` (2026-09-17); Waves 2–4 as `e064968`, `fe154de` and `fff4cbf` (2026-09-20). After each deploy the Space was checked: the new routes answer (401 without a session, not 404) and the web bundle carries the new screens.

**Key:** ✅ done and verified · ⚠️ done, with something you should know · ❌ not done · ⏳ not done yet (a later wave, or its screen is still being built)

**The bar for ✅** is that the person the criterion names can do it **in the product**, not
only through the API. Phase 3 was marked against a looser bar and had to be corrected.

**How each line was verified.** Tests live under `services/data_gateway/tests/` and `web/`.
- `unit`: `unit/test_ph4_wave1.py`, `test_ph4_wave1_hardening.py`, `test_ph4_wave2.py`, `test_ph4_wave3.py`, `test_ph4_wave4.py`, `test_ph4_d1_*`–`test_ph4_d4_*`. At the Wave 5 branch head, on a database built from nothing: 2596 in data_gateway, 178 in admin_ops and 685 in `shared/` (the command CI runs).
- `db`: `integration/test_ph4_scorecard_guarantees.py`, `test_ph4_wave2_guarantees.py`, `test_ph4_wave3_guarantees.py`, `test_ph4_wave4_guarantees.py`, `test_ph4_d{1,2,3,4}_guarantees.py`, `test_ph4_d4_wave5_fixes.py`. These run against a real migrated Postgres, and every refusal is checked for its *reason*, not just for failing.
- `smoke`: real Postgres through the real endpoints — `smoke_ph4_scorecards.py` (Wave 1, 109/109), `smoke_ph4_wave2.py` (57/57), `smoke_ph4_wave3.py` (82/82), `smoke_ph4_wave4.py` (108/108, with MinIO for the documents), `smoke_ph4_d1_question_banks.py` (41/41), `smoke_ph4_d2_accommodations.py` (36/36), `smoke_ph4_d3_code_evidence.py` (43/43), `smoke_ph4_d4_tasks.py` (60/60 with storage faked, 61/61 against a local MinIO). pytest never collects smoke scripts, so all 42 were run at the branch head, each on its own throwaway database: 41 pass. The one not run, `smoke_group_a_scorecard_retry`, needs a live LLM. Three had rotted since earlier waves and were fixed.
- `ui`: the web test suite, 1495 tests, and the screen named on the line.
- `e2e`: the Playwright browser suite against a local stack, 25 passed at Wave 4, including `interview-scorecard.spec.ts` (A1/A5), `workflow.spec.ts` (O6 review and approval), `interview-scheduling.spec.ts` (A2/O5), `offer-preboarding.spec.ts` (A3/A4, hire through to the HRMS handoff) and the decision journeys (O4). No Wave 5 story has a browser spec yet, so no Wave 5 line carries this tag.

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
| PH4-A3 Offer Lifecycle | 4 | 35 | 35 |  |
| PH4-A4 Documents & Preboarding | 4 | 33 | 33 |  |
| PH4-D1 Reusable Question Banks | 5 | 15 | 15 |  |
| PH4-D2 Candidate Accommodations | 5 | 13 | 13 |  |
| PH4-D3 Code Quality & Similarity Evidence | 5 | 30 | 29 | 1 ⚠️ |
| PH4-D4 Job Simulations & Portfolio | 5 | 31 | 30 | 1 ⚠️ |
| **Total** | | **317** | **308** | **9 ⚠️, 0 ❌, 0 ⏳** |

**Wave 1:** 43 criteria — 42 ✅, 1 ⚠️, 0 ❌.  
**Wave 2:** 73 criteria — 70 ✅, 3 ⚠️, 0 ❌.  
**Wave 3:** 44 criteria — 41 ✅, 3 ⚠️, 0 ❌.  
**Wave 4:** 68 criteria — 68 ✅, 0 ⚠️, 0 ❌.  
**Wave 5:** 89 criteria — 87 ✅, 2 ⚠️, 0 ❌.  

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
| 17 | Simulation produces a clear execution trace | ✅ | **Dry run → Show N scenarios**: each synthetic candidate's path round by round — outcome, where it went, how it ended (*reaches a decision* / *is held for you* / *waits for a person to move them on*, when rounds do not advance automatically / *could not finish*) — worded the same in the builder and on the super admin's review page; `ui` `smoke` |
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

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| | **Offer Creation** | | |
| 1 | An authorized HR user can create an offer for a candidate with a human-approved hiring decision | ✅ | HR: candidate drawer → **Offer → Create offer**, shown only once the application is *hired*; the server refuses any other status (409, *Record the decision to hire first*) and a candidate erased or being erased. HR managers only; `ui` `smoke` `e2e` |
| 2 | An offer is associated with the correct requisition and candidate/enrolment | ✅ | the offer takes its application, applicant and opening from the enrolment itself, never from the request; composite FKs `(enrolment_id, company_id)` and `(applicant_id, company_id)`; one offer in play per application (partial unique index). The document checklist resolves through the offer's opening; `db` `smoke` |
| 3 | An offer can use an existing offer template | ✅ | HR: **Offer templates** (create, edit, delete; names unique per company), then the drawer's **Template** picker, which fills the currency and the terms from it. A template carries employment type, currency, pay period, probation, notice, benefits, terms and validity — **never base salary**, so HR types the pay on every offer; `ui` `smoke` |
| 4 | Offer details can include compensation and relevant employment terms | ✅ | HR: **Offers → offer → Offer details** — job title, employment type, start date, location, base salary, currency, pay period, bonus, equity, benefits, terms, probation, notice, validity; refused in words and by CHECKs (salary above zero, three-letter currency, 1–60 days); `ui` `unit` `db` |
| 5 | Draft offers are editable by authorized users | ✅ | HR: **Offer details → Save changes**, only as a draft or once sent back; the fields are disabled otherwise and the `offers_lifecycle` trigger freezes the content from submission. **Reopen for editing** takes an approved, unsent offer back to draft, to be approved again; `ui` `smoke` `db` |
| 6 | Candidate compensation information is protected by appropriate permissions | ✅ | compensation is returned only by the HR routes (HR managers of the company), the super admin's approval routes, and to the candidate by link or portal. The company offer list carries none, no other module names `base_salary` (unit test), no email carries pay, and audit rows list changed field names, never values; `unit` `smoke` |
| | **Approval** | | |
| 7 | An offer can be submitted for approval | ✅ | HR: **Offer → Actions → Submit for approval** (draft or sent back); **Recall** takes it back, **Reopen** an approved one for editing. Every active super admin is notified, and the notice opens that offer on **Offer approvals**; `ui` `smoke` `e2e` |
| 8 | Authorized approvers can approve or reject the offer | ✅ | Super admin: **Offer approvals → offer → Approve** (optional note) or **Send back** (a note of at least 10 characters, in the UI and on the server). Only a super admin approves (D4-2); a sent-back offer is editable and can be resubmitted or withdrawn — there is no terminal reject; `ui` `smoke` `db` `e2e` |
| 9 | Approval actions record the approver and timestamp | ✅ | `decided_by_user_id` / `decided_at` on the row — the trigger refuses an approval with no approver, or by the offer's author or submitter — and an `offer_events` row per decision; both **History** cards read *Approved — <name> <time>*. A resubmission clears the row's pair; the history keeps every decision; `smoke` (*approval records who and when*) `db` |
| 10 | An unapproved offer cannot be sent to the candidate | ✅ | `send()` refuses anything not approved, in words; the trigger allows `sent` only from `approved`, and only with a link and a future deadline; **Send to candidate** appears only on an approved offer; `smoke` `db` `ui` |
| 11 | Approval/rejection actions are auditable | ✅ | `offer.approved` / `offer.rejected` audit rows (whether a note was given and its length, never its text) and append-only `offer_events`; a separation-of-duties refusal is a 403 shown as worded; `smoke` `db` `ui` |
| | **Candidate Delivery** | | |
| 12 | An approved offer can be sent securely to the candidate | ✅ | HR: **Offer → Send to candidate** — a fresh 256-bit link, stored only as a keyed hash, and a deadline of *valid days* from now; the email carries no pay and goes out in the candidate's language (English for an applicant with no account); `smoke` `unit` `e2e` |
| 13 | Candidate receives a secure offer link | ✅ | the email's link is `/offer#<token>`: the token rides in the URL fragment, never a path or query, so no request or access log carries it; the page reads it once and strips it from the address bar; the email says *This link is personal to you*; `smoke` `ui` `e2e` |
| 14 | Candidate can access the offer without exposing the offer to unauthorized users | ✅ | the page sends the token only as the `X-Offer-Token` header (both Caddyfiles delete it from access logs); every failure is the same 404; the public routes are rate-limited; a re-send, or the candidate's **Open offer** in the portal, retires the old link. Reading needs only the link — see below; `unit` `smoke` `ui` |
| 15 | Offer delivery uses the existing secure magic-link pattern where appropriate | ✅ | the same shape as `/exam` and `/interview-invite` — fragment token, header credential, hashed at rest — plus an emailed one-time code before anything changes state; `unit` `ui` `e2e` |
| 16 | Offer access and important actions are audit logged | ✅ | every change and every candidate action lands in `offer_events` and in the audit log with IP and user agent: created, updated, submitted, recalled, approved, sent back, reopened, sent, re-sent, viewed, code requested, accepted, declined, withdrawn, expired, preboarding completed, exported — and a member of staff opening an offer, its pay included, writes `offer.viewed_by_staff` naming the console they read it from. A candidate's own repeat views are recorded once an hour; `smoke` |
| | **Acceptance / Decline** | | |
| 17 | Candidate can accept an offer | ✅ | Candidate: offer link → **Accept offer → Send me a code** → the code and their typed full name → **Confirm acceptance**. A candidate with no account is given one and emailed a link to claim it; `ui` `smoke` `e2e` |
| 18 | Candidate can decline an offer | ✅ | Candidate: offer link → **Decline offer → Send me a code** → the code and an optional reason → **Confirm decline**. The decline request is not run end to end through the API — see criterion 35; `ui` `db` |
| 19 | Acceptance/decline requires appropriate secure authentication | ✅ | the link plus a six-digit code emailed when the candidate chooses: hashed, bound to the offer and to *accept* or *decline*, ten minutes, five tries per code, three codes per 15 minutes; 20 wrong codes over the offer's life lock it until HR re-sends. No sign-in is needed; `unit` `smoke` `ui` `db` |
| 20 | Acceptance records timestamp and relevant audit information | ✅ | `responded_at` and the name typed (`accepted_name`) — the trigger refuses an acceptance without either — plus `offer.accepted` with IP and user agent, and a DPDP consent entry for the documents that follow; `smoke` `db` |
| 21 | Decline records timestamp and reason where collected | ✅ | `responded_at` (required by the trigger) and the optional reason, up to 1000 characters; the audit row records only that a reason was given and its length. HR reads it on the offer page (*Decline reason: …*); `db` `ui` |
| 22 | An accepted offer cannot silently be changed to declined | ✅ | accepted, declined, expired and withdrawn are final at the database, and an answer (time, name, reason) cannot be rewritten; asking for a decline code on an accepted offer is refused (409); `db` (`test_an_answer_is_final`) `smoke` |
| 23 | A declined offer cannot silently be changed to accepted | ✅ | the same trigger (*offer … is declined and final*); the service answers *This offer is already declined*; the candidate's page shows a declined offer with no action; `db` (`test_a_declined_offer_cannot_be_accepted`) `ui` |
| | **Lifecycle** | | |
| 24 | Offers support explicit states | ✅ | nine states — draft, pending approval, approved, sent back (`rejected`), sent, accepted, declined, expired, withdrawn — a CHECK plus the transition table in `offers_lifecycle`; each is a tag and a filter on **Offers**; `db` `ui` |
| 25 | Offer expiry can be configured | ✅ | HR: **Offer details → Offer valid for (days)**, 1–60 (a template's value, or 7); the deadline is fixed when the offer is sent and cannot move afterwards, a re-send included; `ui` `unit` `db` |
| 26 | Expired offers cannot be accepted | ✅ | the trigger refuses an acceptance once `expires_at` has passed, by the database's clock; opening the link or asking for a code expires a past-due offer, and the reminder sweep expires the rest and tells HR; the candidate sees *This offer has expired*; `db` `ui` `unit` (sweep stage) |
| 27 | Authorized HR users can withdraw an offer before acceptance | ✅ | HR: **Offer → Withdraw offer** (optional reason, then a confirm) from draft, pending approval, approved, sent back or sent; the candidate is emailed if it had been sent; never after an answer (trigger). The confirm button reads *Delete* — see below; `ui` `smoke` `db` |
| 28 | Offer state transitions are validated server-side | ✅ | every step checks the state in the service and again in the `offers_lifecycle` trigger, whoever writes; a skipped step is refused naming both states; `db` (`test_no_step_can_be_skipped`) `smoke` |
| 29 | Candidate hiring status is updated appropriately after final offer outcome | ✅ | the outcome is written to `enrolments.offer_outcome` (accepted / declined / expired / withdrawn) and shown beside the decision on **Pipeline** (list and board). The decision itself stands — a hire is undone only by recording a rejection — but a hire whose offer was declined, expired or withdrawn stops counting towards the opening's target on the **Opening dashboard** and the company **Hiring board**, and a new offer clears the old outcome; `smoke` `ui` `e2e` |
| 30 | Existing decision records remain immutable | ✅ | offers never write a status or the stage ledger (AST test; the smoke counts the ledger before and after); a hire is now undone only by recording a rejection, in the service and by trigger; `unit` `smoke` `db` |
| | **Safety / Governance** | | |
| 31 | AI cannot create, approve, send, accept or decline offers on behalf of users | ✅ | no agent tool or model reaches `app.offers` or `app.preboarding` (unit test); every HR and super-admin route needs a person's session, and answering needs the candidate's emailed code; `unit` |
| 32 | Offer actions are company-scoped | ✅ | the company comes from the session on every HR and super-admin route; every query filters by it and the child tables carry composite FKs; another company's HR lists none of these offers; `smoke` `db` `unit` |
| 33 | Compensation data is only available to authorized users | ✅ | no compensation in the company list, the audit log, any email, or HR's notifications; only the approval notification shows the amount, to super admins; the HRMS payload goes only to HR managers; `unit` `smoke` |
| 34 | Offer records participate in existing retention/erasure mechanisms | ✅ | offers are kept with the application, as applications are (the retention job purges neither); on erasure an offer in play is withdrawn and its link killed, the typed name and every reason or note are redacted, and its codes, sessions and HRMS payloads are deleted (step 5f); `unit` (erasure inventory) `db` (an HRMS payload leaves only with erasure) |
| 35 | Tests cover all major state transitions and authorization boundaries | ✅ | `test_ph4_wave4.py`, `test_ph4_wave4_guarantees.py` (lifecycle, separation of duties, expiry, finality, frozen terms), `smoke_ph4_wave4.py` — 105 checks, including recall, reopen, a decline with its reason, the expiry sweep actually running, and every authorisation boundary — the `OfferDetail` / `OfferApprovals` / `OfferSection` / `OfferTemplates` / `Offers` / `PublicOffer` / `YourOffers` / `PipelineBoard` UI tests, and `offer-preboarding.spec.ts` end to end |

**Worth knowing.**

- **Reading an offer needs only the link.** Anyone the link is forwarded to can read the offer, pay included, until it is re-sent or retired; accepting or declining needs the emailed code. The link stops working 30 days after the offer ends, or 90 days after an acceptance whose preboarding never completes.
- **Opening an offer from the portal retires the emailed link.** **My applications → Your offers → Open offer** mints a fresh link; the one in the email then reads as invalid.
- **Twenty wrong codes lock the offer**, for answering and documents alike, until HR presses **Re-send**, which also retires the link and closes open sessions. HR is told once when it locks. The portal's fresh link does not unlock it.
- **A candidate with no account** (an applicant HR added) is given one at acceptance and emailed a link to claim it, in the language they accepted in; someone who already has an account is not.
- **Only a super admin approves** (D4-2). HR managers cannot approve each other's offers, and nobody approves an offer they wrote or submitted. A company with no active super admin can submit an offer but never get it approved, and nobody is told that.
- **A template is terms, not pay.** It carries employment type, currency, pay period, probation, notice, benefits, terms and validity; base salary is typed on every offer.
- **The name the candidate typed to accept is stored but not shown to HR**; the history names the candidate's account.
- **A hire is undone only by recording a rejection.** Moving a hired application back into the pipeline used to be possible; this wave refuses it in the service and by trigger. Reversing a hire after an acceptance closes the candidate's offer link.
- **The decision stands whatever the answer.** A declined, expired or withdrawn offer never rewrites the decision; it is recorded beside it, shown on the pipeline, and left out of the opening's hire count.

---

## PH4-A4 — Documents & Preboarding  ·  Wave 4

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| | **Document Requirements** | | |
| 1 | Authorized HR users can define document requirements for a requisition/preboarding process | ✅ | HR: opening dashboard → **Preboarding documents → Add requirement** — set per opening, by HR managers (hidden on the super admin's read-only view); `ui` `smoke` `e2e` |
| 2 | Each requirement specifies the expected document type | ✅ | **Kind**: identity, address, education, employment, tax, bank, photo, medical or other — refused in words otherwise, and by CHECK; `ui` `smoke` |
| 3 | Requirements can be marked mandatory or optional | ✅ | **Mandatory** at creation, **Make optional / Make mandatory** afterwards; only mandatory ones block completion; `ui` `smoke` `db` (the optional photo does not block) |
| 4 | Requirements can define expiry requirements where applicable | ✅ | **Needs an expiry date** at creation: the candidate must give a future expiry date to upload, an expired document cannot be verified (service and trigger), and completion needs every mandatory one in date. The flag cannot be changed from the screen afterwards — see below; `ui` `smoke` `db` |
| 5 | The system identifies which documents are outstanding | ✅ | each requirement's state on the offer — *outstanding*, submitted, verified, rejected, replacement requested, expired — and the list of unresolved mandatory names; **Offers** shows *Documents 1/2 · 1 awaiting review*; `smoke` `ui` |
| | **Candidate Upload** | | |
| 6 | Candidates can securely upload required documents after offer acceptance | ✅ | Candidate: offer link, once accepted → **Your documents → Get a code → Continue → Upload**. The link alone opens nothing: an emailed code opens an hour-long session held only in the page's memory, and the candidate is emailed on every upload. Closed once preboarding completes; `ui` `smoke` `e2e` |
| 7 | Uploads are associated with the correct candidate/enrolment | ✅ | the upload's offer and application come from the link, and its requirement must belong to that offer's opening (404 otherwise); composite FKs; one current document per requirement (unique index); `smoke` `db` |
| 8 | Candidate can see required, submitted, verified and rejected documents | ✅ | **Your documents**: each requirement, *Required* or *Optional*, and its state in words — *Not uploaded yet*, *Submitted — awaiting review*, *Verified*, *Rejected*, *Replacement requested*, *Expired* — in EN / HI / TE; `ui` `smoke` |
| 9 | Candidate can re-upload a rejected document | ✅ | **Upload a replacement** on a rejected document (or one HR asked to replace): a new version supersedes the old, which is kept; `ui` `smoke` `e2e` |
| 10 | Candidate-facing document status is clearly communicated | ✅ | states in words in three languages, HR's reason (*The hiring team said: …*), and emails on every upload and on every rejection or replacement request. An *Expired* document says its date has passed and takes a current one; an opening that asks for nothing says so; `ui` `smoke` `e2e` |
| 11 | Candidate cannot access another candidate's documents | ✅ | there is no candidate route by document id: the link names one offer, the session is bound to that offer, and an upload takes only that offer's requirements; a re-send retires the old link and closes its sessions; `unit` `smoke` |
| | **HR Review** | | |
| 12 | Authorized HR users can view submitted documents | ✅ | HR: **Offers → offer → Documents → Download** — a five-minute signed link that downloads rather than renders, every open recorded; the row shows version, file name and expiry. There is no in-app preview; `smoke` `ui` |
| 13 | HR can mark a document as verified | ✅ | HR: **Documents → Verify → Confirm**; the trigger requires a named reviewer and refuses an expired document; `ui` `smoke` `db` `e2e` |
| 14 | HR can reject a document | ✅ | HR: **Documents → Reject → Confirm**, with a reason; `ui` `smoke` `e2e` |
| 15 | Rejection can include a reason | ✅ | required: 5 to 1000 characters, in the UI and on the server; shown to the candidate and emailed in their language; the audit row keeps only its length; `ui` `smoke` |
| 16 | HR can request a replacement/re-upload | ✅ | HR: **Documents → Ask for a replacement** on a submitted or verified document, with a reason; the candidate is emailed, sees the reason, and uploads again — run end to end in `smoke_ph4_wave4.py`, alongside rejection in `offer-preboarding.spec.ts`; `ui` `smoke` `e2e` |
| 17 | Verification actions record reviewer and timestamp | ✅ | `reviewed_by_user_id` and `reviewed_at` on the row — the trigger refuses a review without both; HR reads *verified — <name> <time>* under **Document activity**; `smoke` (*verification records who and when*) `db` (`test_a_review_names_its_reviewer`) |
| 18 | Document verification history is auditable | ✅ | `document_events`, append-only (uploaded, verified, rejected, replacement requested, downloaded, deleted — who and when, never a reason's text), and `document.*` audit rows; every version is kept and frozen once superseded; **Document activity (N)** on the offer. The append-only test runs on `hrms_exports`, which shares the trigger; `smoke` `db` |
| | **Preboarding** | | |
| 19 | The system can determine when all mandatory documents are complete | ✅ | a mandatory requirement is unresolved until it has a current, verified, in-date document; **Offers** counts *verified / required*; the `offers_preboarding_complete` trigger makes the same count; `db` `smoke` |
| 20 | A candidate cannot be marked preboarding-complete while mandatory documents remain unresolved | ✅ | HR: **Offer → Preboarding → Mark preboarding complete** is refused naming the documents still missing — by the service, and by the trigger whoever writes; completion is recorded once, with who; `db` `smoke` `ui` `e2e` |
| 21 | HR can see overall preboarding status | ✅ | HR: **Offers → Status: In preboarding** (accepted, not yet complete), each with its document progress; the offer page's **Documents** and **Preboarding** cards (*Preboarding completed on …*). The list row does not mark a completion — see below; `ui` `smoke` |
| 22 | Preboarding status is associated with the accepted offer/candidate | ✅ | `preboarding_completed_at` / `_by` live on the offer row, allowed only on an accepted offer (CHECK); documents carry the offer and the application; `db` (`test_preboarding_follows_an_accepted_offer`) |
| | **Security / Compliance** | | |
| 23 | Document access is permission-controlled | ✅ | HR: HR managers of the company only (session gate, company-filtered queries); candidate: the link **and** a code-opened session, for listing and for uploading; super admins and interviewers have no document route; `unit` `smoke` |
| 24 | Documents participate in consent and retention rules | ✅ | consent: the accept step says what sharing documents means — what is asked for, that it is kept by sub-processors outside India, naming them (Singapore, United States — the copy said "may be outside India" until `44daaaa`), that it is deleted when it has served its purpose, and that consent can be withdrawn (EN/HI/TE) — then accepting writes a `preboarding_documents` / `onboarding` entry to `dpdp_consent_ledger`. Withdrawing it refuses any further upload — from the documents step itself (**Withdraw my consent**, POST /offer/documents/consent/withdraw), which most candidates here need because the account made at acceptance may never be claimed, or, signed in, through `DELETE /consent` — which until the Wave 5 PR matched only purpose `interview` and so never found this `onboarding` row; no screen calls that route, and it now matches each type's own purpose (tested). It is held per candidate, so it stops every offer they hold, the hiring team is told, and nothing re-grants it automatically. Retention: files and rows are purged 90 days after the offer ends unaccepted, after completion, or after an acceptance that never completes, and at the next run once the hire is reversed — proven in `smoke_ph4_wave4.py`, dry run and real. Note that `RETENTION_DRY_RUN` is true by default, so a deployment deletes nothing until it is switched off; `ui` `smoke` |
| 25 | Document data participates in the existing erasure workflow | ✅ | erasure collects every version's file key and lists each offer's storage prefix (so an orphaned file goes too) in step 1, deletes the files in step 8, and blanks the rows — key, file name, review note — in step 5f; the listing refuses when storage is not configured; `unit` (`test_new_tables_are_in_the_erasure_inventory_and_documents_leave_storage`; admin_ops `test_objects_under_an_offer_prefix_are_erased_even_when_no_row_names_them`) |
| 26 | Document storage uses the existing secure object-storage/upload mechanism where possible | ✅ | the existing uploads bucket through `s3_upload.upload_file`, under `preboarding/{company}/{offer}/{document}` (no person named), stored with the detected type and out only by a pre-signed link; PDF, JPEG and PNG only, checked by content; 10 MB. No virus scanner (AR-6); `smoke` (MinIO, SigV4, five minutes, `attachment`) `unit` |
| 27 | Document metadata is company-scoped | ✅ | `candidate_documents` carries `company_id`, with composite FKs to its offer, requirement and application; HR reads filter by the session's company; `db` `smoke` |
| 28 | Unauthorized users cannot download/view protected documents | ✅ | no public route serves a file (unit test) and the candidate has no download, by design (security review H1); HR gets a five-minute link with `Content-Disposition: attachment` and `no-store`, every open recorded; the link alone lists nothing (401); `unit` `smoke` |
| | **HRMS Handoff** | | |
| 29 | Once preboarding requirements are complete, the system can prepare a signed export/webhook payload | ✅ | HR: **Offer → Preboarding → Prepare HRMS export**, offered only once preboarding is complete (refused before): a JSON payload signed HMAC-SHA256 over its canonical form, with `key_id`, shown on the page with **Download JSON**. Nothing is sent to an HRMS — HR carries the file; `ui` `smoke` `e2e` `unit` |
| 30 | The handoff contains only the required authorized information | ✅ | the candidate's name and email; the company; job title, type, start date, location, base pay, currency, period, probation, notice and acceptance time; each verified document as facts (type, name, version, content type, SHA-256, expiry, verified at). No files, storage keys, scores, reasons, bonus, equity, benefits or terms; `unit` (`test_the_hrms_payload_is_minimal`) `smoke` |
| 31 | Handoff activity is auditable | ✅ | every export is kept, append-only, in `hrms_exports` (payload, signature, key id, who, when), with an `exported` offer event (*HRMS export prepared — <name>* in **History**) and `offer.hrms_exported` in the audit log; `db` `smoke` |
| 32 | No vendor-specific HRMS integration is required for this phase | ✅ | one generic, versioned schema (`anthire.preboarding.v1`); no vendor code; `unit` |
| | **Tests** | | |
| 33 | Tests cover upload, review, rejection, re-upload, verification, permissions, expiry and completion | ✅ | upload, rejection, re-upload, verification and completion run end to end in `smoke_ph4_wave4.py` (108 checks) and `offer-preboarding.spec.ts`; and with them the replacement request, consent withdrawn from the documents step and, once a fresh entry is written, given again, a verified document passing its expiry and being replaced, the retention purge dry and real, and another company's HR reaching neither the documents nor a download. Permissions in the unit gate tests and the smoke; the review rules in `test_ph4_wave4_guarantees.py`; the screens in the `OfferDetail` / `PublicOffer` / `DocumentRequirementsSection` UI tests |

**Worth knowing.**

- **Documents need a second factor.** After accepting, the candidate asks for a code, which opens an hour-long session held only in the page's memory; the emailed link alone lists nothing and uploads nothing.
- **There is deliberately no candidate download.** They can see each document's state and send a new version; the file itself is only served to HR, through a five-minute signed link.
- **No virus scanner.** Uploads are limited to PDF, JPEG and PNG by content, and a PDF that can run or carry something is refused — but nothing scans for malware (AR-6).
- **The files leave India in the demo tier** (Cloudflare R2, AR-1). The accept step says so before the candidate agrees.
- **Aadhaar guidance.** Any identity requirement asks for a masked copy, with the reason, in all three languages.
- **Completion is enforced by the database**, not only by the screen: every mandatory requirement needs a current, verified, unexpired document.
- **The HRMS handoff is a signed JSON payload** (HMAC-SHA256 with a key id) that HR downloads; there is no vendor integration, and nothing is pushed anywhere.
- **Retention deletes nothing until it is switched on.** `RETENTION_DRY_RUN` is true by default, so a deployment counts what it would remove and removes none of it until the flag is set to false.
- **Withdrawing consent is a button, not only a promise.** The documents step carries **Withdraw my consent**, behind the same credential as an upload, because the candidate may have no account to sign in with. It stops further uploads for every offer that candidate holds and tells the hiring team; it deletes nothing already sent, and nothing re-grants it — the candidate must agree again, which today only happens on a new acceptance.
- **Erasure covers the files, the rows, the codes, the sessions and the exports**, including an object left behind under the offer's prefix by a failed upload commit; it fails closed if object storage is not configured.

---

## PH4-D1 — Reusable Question Banks  ·  Wave 5

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Authorized users can create reusable questions | ✅ | HR: **Question banks → bank → New question** (`/hr/question-banks/:bankId`, `QuestionBankDetail.tsx`), gated by `HrCtxDep`; `POST /hr/question-banks/{bank_id}/questions` validates MCQ/coding shape by constructing the SAME `QuestionIn`/`CodingQuestionIn` Pydantic models `hr_exams.py`/`hr_coding.py` use for exam authoring, so a bank question and an exam question can never silently drift apart in what counts as well-formed; `ui` `unit` `smoke` |
| 2 | Questions can be organized into question banks | ✅ | `question_banks` (company-scoped, name unique per company case-insensitively); HR: **Question banks** list (`/hr/question-banks`) to create/rename/archive one; every `bank_questions` row FK'd to `bank_id`; `ui` `db` `smoke` |
| 3 | Questions support existing assessment question types | ✅ | `BankQuestionIn` reuses the existing MCQ/coding validators verbatim (see criterion 1) — mcq needs 2-6 options and a valid `correct_index`; coding needs ≥1 allowed language and ≥1 test case; `unit` |
| 4 | Questions can be associated with relevant competencies | ✅ | `competencies`/`competency_ids` (≤8, each `{id, name}`, id matches `^[a-z0-9_]{1,80}$`), edited via `CompetencyTagger.tsx`; `/hr/bank-questions/competencies` seeds from the role-engine's baseline plus the company's own `round_criteria`, so a tag uses the same words a round's rubric does; `ui` `unit` |
| 5 | Questions can have difficulty metadata | ✅ | `difficulty` (easy/medium/hard, CHECK-enforced) on every question, editable in `BankQuestionEditor.tsx`, filterable in the bank screen and in the picker; `ui` `db` |
| 6 | Questions can have appropriate review/approval status | ✅ | draft → in_review → approved → retired, enforced by the `bank_questions_lifecycle` trigger at the database (approval needs a NAMED reviewer who is neither author nor submitter — a 403 worded exactly like an offer's separation-of-duties refusal — and, since a security review found the gap, never anyone who edited the content: the service checks the append-only event log). HR: **Submit for review**, **Question reviews** queue (`/hr/question-banks/reviews`); super admin mirror at **/superadmin/question-reviews**, so a single-HR company is never blocked on a second approver; `ui` `db` `unit` `smoke` |
| 7 | Approved questions can be reused across multiple assessments | ✅ | HR: exam section → **Add from bank** puts an approved question into any exam as its own copy. The partial unique index `(exam_id, source_bank_root_id)` refuses the same question twice in ONE exam and allows it in others. The exam's own question list then shows **From bank · vN** (`BankProvenanceChip`, shared by `ExamEditor.tsx` and `CodingAuthoringSection.tsx`), fed by the `source_bank_*` fields on `QuestionOut`/`CodingQuestionOut`. Those fields were added in `d8d714c`: before that the API never sent them, so the chip could never render, and no test noticed. The D1 smoke now adds one approved question to a second exam and checks it is a separate copy with the same provenance; `ui` (`BankProvenanceChip`, `BankQuestionPicker`) `db` (schema) `smoke` |
| 8 | Reusing a question does not mutate previously published assessments | ✅ | copy-on-add: `add_to_section` inserts an INDEPENDENT `exam_questions`/`coding_questions` row carrying the bank question's content, not a reference; the `exam_round_content_frozen` trigger's UPDATE branch refuses ever repointing a copy's `source_bank_question_id/root_id/version`; the D1 smoke test approves a v2 of a bank question and asserts "the exam's existing copy is unchanged byte for byte"; `db` (`test_a_copied_question_keeps_the_provenance_it_was_made_with`) `smoke` |
| 9 | Published assessment questions remain immutable | ✅ | `exam_round_content_frozen`/`exam_rounds_frozen` triggers refuse INSERT/UPDATE/DELETE on `exam_sections`/`exam_questions`/`coding_questions`/grading fields once a round is `published` OR has an attempt; `app/exam_locks.py` (new this wave) turns that into a sentence BEFORE the write reaches the trigger, called from every writer in `hr_exams.py`/`hr_coding.py`/`hr_rounds.py` (AST-parametrised test). **Unpublish** (while untaken) or **Duplicate round** (`LockBanner.tsx` in `ExamEditor.tsx`) are the only ways forward; `ui` `db` (8 tests) `smoke` |
| 10 | Users can search/filter questions within authorized banks | ✅ | HR: **Question bank → Search prompt / kind / difficulty / language / competency / tag / status** (`QuestionBankDetail.tsx`'s `FilterBar`, backed by `svc.search()`); the same filters reappear in the picker; `ui` `db` |
| 11 | Users can select questions when configuring an assessment | ✅ | HR: exam section → **Add from bank** opens `BankQuestionPicker.tsx` (approved, same-kind questions across every bank in the company; anything already in THIS exam shown disabled with its reason) → **Add N questions** → `POST /hr/exams/{exam_id}/sections/{section_id}/bank-questions`; reachable from both `ExamEditor.tsx` (MCQ) and `CodingAuthoringSection.tsx` (coding); `ui` `smoke` |
| 12 | Duplicate/unwanted question selection is prevented where appropriate | ✅ | `picker_skip_reason()` — not approved, wrong kind, same lineage already in this exam, or an identical question by content hash — is the ONE function both the picker's row-disable logic and the server's own skip list call, so what the screen shows as pickable is never something the server would then skip anyway; DB-backstopped by `uq_{table}_exam_source_root`; `ui` `unit` `db` (`test_the_same_bank_lineage_cannot_land_in_one_exam_twice`) `smoke` ("adding it again is skipped as a duplicate") |
| 13 | Question bank access respects company/tenant permissions | ✅ | every route needs `HrCtxDep`/`SuperAdminCtxDep` (AST-verified, ≥25 routes), the company from the session, never the request; composite FKs everywhere. Smoke: "another company's HR gets 404 on the bank… and on the question… and search across banks finds nothing of another company's"; "an interviewer gets 403 on a bank route"; "a candidate gets 403 on a bank route"; `unit` (`test_every_route_sits_behind_its_audience_gate`) `db` `smoke` |
| 14 | Existing assessment questions continue to work | ✅ | every new column (`source_bank_*` on `exam_questions`/`coding_questions`) is nullable and additive; a plain, non-bank question insert never touches the new provenance checks (the T2 trigger's bank-specific `IF` block only runs when `source_bank_question_id IS NOT NULL`). The D1 smoke test runs a full pre-existing candidate path — assign, open round, start attempt, submit, pass — against an exam whose only question came in via the bank, unaffected by the new lock triggers. One real, deliberate behaviour change: a published-but-untaken round's questions used to be editable at the app layer (the migration's own docstring names this as the gap it closes) and now are not — HR must unpublish or duplicate first. That narrows an editing gap rather than breaking the candidate-facing flow; `db` (additive migration) `smoke` |
| 15 | Tests cover creation, reuse, permissions and immutability | ✅ | `test_ph4_d1_question_banks.py` (unit, ~30 tests: content-hash normalisation, the transition table pinned against the trigger's own SQL, the picker's skip logic, the audience-gate AST check, "no agent/LLM path ever imports this module"), `test_ph4_d1_guarantees.py` (db, ~35 tests: every lifecycle transition, both locks, cross-company FK refusals, the append-only event log), `smoke_ph4_d1_question_banks.py` (real API: author → submit → self-approve refused → a second HR approves → copy into an exam → duplicate skipped → publish → a real candidate takes and passes it → editing the published/taken round refused → a new version drafted, approved, retires v1 automatically, the exam's existing copy is untouched → cross-tenant and role boundaries), plus `BankQuestionEditor.test.tsx` / `BankQuestionPicker.test.tsx` / `QuestionBankDetail.test.tsx` / `QuestionBanks.test.tsx` / `QuestionReviewQueue.test.tsx` / `QuestionReviewsPages.test.tsx` |

**Worth knowing.**

- **The two known Wave-5 bugs are fixed and now tested.** Revising an approved/retired question
  used to have no "New version" control at all (`QuestionBankDetail.tsx` now has one, gated on
  `approved`/`retired` status); the editor used to keep the PREVIOUS question's draft state when a
  different question (or version) was selected, so "Save changes" could PATCH the wrong row —
  `BankQuestionEditor.tsx` now re-seeds strictly on `question?.id` changing, with a comment
  explaining why (fixed in `17acec9`).
- **Reuse across exams: a defect this pass found, now fixed and tested.** The "From bank · vN"
  chip on an exam's own question list could never render. `QuestionOut`/`CodingQuestionOut` did
  not send the provenance fields the frontend expected, and no test rendered the chip. Fixed in
  `d8d714c`, which adds the fields; the D1 smoke now puts one approved question into two different
  exams and checks each copy. The chip is now one component with its own test
  (`BankProvenanceChip`).
- **A published-but-untaken round's questions are now genuinely locked**, at the database as well
  as the app (new `exam_locks.py`), closing a real prior gap named in the migration's own docstring
  ("the app checked for attempts only, never for published"). Intentional and documented, but it is
  a behaviour change to an editing path that used to succeed.
- **Staff-console screens (`QuestionBankDetail.tsx`, `QuestionReviews.tsx`) are English-only by
  design** (CLAUDE.md — staff consoles are not translated); this is correct and not a defect.

---

## PH4-D2 — Candidate Accommodations  ·  Wave 5

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | Authorized HR users can record an accommodation for a candidate | ✅ | HR: candidate drawer → **Accommodations** → pick a scope → **Record accommodation** (`AccommodationsSection.tsx`, `POST /hr/applicants/{id}/accommodations`), `HrCtxDep`-gated; there is no super-admin, interviewer, candidate or agent route at all (module docstring, and `test_no_super_admin_interviewer_or_public_router_in_the_module`); `ui` `unit` `smoke` |
| 2 | Accommodations are associated with the appropriate candidate/application | ✅ | `applicant_id` always set (composite FK to `applicants(id, company_id)`); optional `enrolment_id` (composite FK to `enrolments`); a round- or exam-round-scoped row additionally REQUIRES an enrolment (CHECK constraints), so a round-scoped adjustment can never silently apply to a different application that happens to reuse the same round; `db` (`test_a_round_scoped_row_needs_an_enrolment`, `..._exam_round_...`) `unit` |
| 3 | An accommodation can be associated with a specific assessment/interview where applicable | ✅ | scoped to one workflow round (`round_id`) or one hand-assigned exam round (`exam_round_id`, mutually exclusive with `round_id` by CHECK); a deadline extension also lengthens an interview invite's own `expires_at` (`hr_interviews.py`, `workflow_runner.py` — both now carry `accommodation_id`). UI: **This application / One workflow round / One exam round** scope radios, disabled with a stated reason when there is no open application to scope them to; `ui` `db` `unit` |
| 4 | Authorized users can define the effective period or relevant assessment | ✅ | `effective_from`/`effective_until` (CHECK: until > from), editable via `localInputToIso`/`toLocalInputValue` in both the record and revise forms; a REVISE pre-fills the existing window rather than blanking it — a Wave 5 fix, since a blank window used to silently turn a time-boxed adjustment permanent; `ui` `db` |
| 5 | The candidate's assessment experience reflects the approved accommodation | ✅ | candidate: **/exam/start** returns SCALED round/section time limits (`accommodations.scaled`) and, when `relax_auto_submit` is set, `max_violations: null` (no auto-submit threshold); `PublicExam.tsx` shows a fact-only banner ("Your time for this round includes an adjustment.") in EN/HI/TE — never the percentage or the reason. Smoke: the candidate actually starts and submits inside the extended deadline; `ui` `unit` `smoke` |
| 6 | Additional time/deadline adjustments are applied correctly where configured | ✅ | `extra_seconds`/`scaled` (pure, unit-tested for rounding and the untimed-limit case) are frozen onto the attempt at `/start` (`exam_attempts_allowance_fixed` trigger — later HR edits cannot retroactively shrink or grow a clock already shown to the candidate). Smoke: "A's round + section time limits are scaled to 90s… A's attempt was frozen with +30s (50% of 60)… A is submitted (within the extended deadline)"; `unit` `db` `smoke` |
| 7 | Accommodation information is not exposed unnecessarily to interviewers or other users | ✅ | an assigned interviewer's OWN scorecard carries only `adjustments_note` (=`interviewer_note`), resolved by `_load_owned` scoped to THAT interviewer and THAT round — never `internal_note`, never the parameters, never `basis`. Smoke: "A's interviewer sees exactly the interviewer note… the payload never carries the internal note or the parameters… the interviewer for an unrelated candidate sees no note"; `ui` (`InterviewerScorecard.tsx`) `unit` `smoke` |
| 8 | Accommodation does not change the underlying competency or evaluation criteria | ✅ | no FK from `candidate_accommodations` to `round_criteria`; `round_id`/`exam_round_id` are used only to match SCOPE (which adjustment applies where), never to touch a round's own criteria rows; `AccommodationsSection.tsx` has no criteria-editing control at all; `db` (schema) `ui` |
| 9 | Accommodation does not automatically increase or decrease candidate scores | ✅ | `test_exam_grading_never_references_accommodations`/`test_coding_grader_never_references_accommodations` (AST-tested — the module name appears in neither grader's source, ever). Smoke proves it directly: "identical answers score identically" between the accommodated and the plain candidate; `unit` `smoke` |
| 10 | Accommodation changes are auditable | ✅ | `accommodation_events` (append-only, DB-refused UPDATE/DELETE) records recorded / revised / revoked / applied / redacted; every HR write also lands an `AuditLog` row with IP/user-agent. Smoke: "HR's history names who recorded the adjustment"; `db` `unit` `smoke` |
| 11 | Candidate data associated with accommodations follows existing consent, retention and erasure rules | ✅ | `_record_basis` writes a `dpdp_consent_ledger` row per state change, booked against the RECORDER (never falsely against the candidate — there is no candidate consent path here at all, and a prior version of this code wrongly booked consent against the candidate's own account; fixed in `1012488`). `purge()` redacts the four note fields 180 days after every one of the applicant's applications is decided, honouring `RETENTION_DRY_RUN`. Erasure step 5g revokes any still-active row and redacts all notes; `services/admin_ops/tests/test_erasure_step_order.py` (`test_accommodations_are_revoked_before_applicants_lose_their_user_id`) and `test_erasure_executor.py` both prove it. Smoke: "a retention dry run reports the candidate without redacting… the real run redacts it"; `db` `unit` `smoke` |
| 12 | Existing candidates without accommodations continue through the normal flow | ✅ | `pick()` returns `None` with no active rows; `extra_seconds`/`scaled` are 0/unchanged with no percentage; `test_deadline_with_zero_extra_seconds_matches_the_old_formula` proves the accommodated-with-nothing path computes byte-identically to the pre-D2 deadline formula. Smoke: "B sees no adjustment… B's time limits are the plain 60s… B is expired (past the plain deadline)"; `unit` `smoke` |
| 13 | Tests cover accommodation configuration, application and permission boundaries | ✅ | `test_ph4_d2_accommodations.py` (unit, ~40 tests: `pick()`'s scope-precedence rules, the grading-isolation AST tests, the HR-only audience gate, the email never carrying notes), `test_ph4_d2_guarantees.py` (db, ~38 tests against the real `candidate_accommodations_guard` trigger — arrival shape, frozen scope/parameters, redaction shape, revoke/supersede rules, cross-company refusals), `smoke_ph4_d2_accommodations.py` (real API: record → scaled exam start → frozen allowance → submit/expire → interviewer note scoping → revoke → retention dry/real → cross-company and super-admin denial), `AccommodationsSection.test.tsx` (ui, 20+ behavioural assertions incl. scope-disabling without an application and the three notes' audience warnings) |

**Worth knowing.**

- **No criterion here is anything but ✅** — this is the tightest of the three stories: every write
  path is guarded by a single DB trigger (`candidate_accommodations_guard`) that freezes scope,
  parameters, basis and dates after insert and only ever allows a revoke or a one-time supersede: no
  application-layer bypass is possible even in principle.
- **The consent-ledger fix (`1012488`) matters for DPDP correctness.** An earlier version of this
  feature booked a `granted=true` consent row against the CANDIDATE's own account for every
  HR-initiated accommodation — asserting a consent the candidate never gave, which the
  platform-owner's DPDP audit feed would have read as a real candidate consent grant. It is now
  booked against the RECORDER (the HR manager), with `granted=false` for a revoke, exactly mirroring
  the `bulk_ingest.record_hr_collected_basis` precedent.
- **A revoked-by-the-platform row correctly names no person.** `revoked_by_user_id` stays NULL when
  retention or erasure ends a still-active row (it is a real FK to `users`, and an earlier version's
  attempt to use a sentinel UUID failed an FK constraint in the smoke test — now fixed).
- **Extra time and a relaxed auto-submit threshold are frozen the moment a candidate's attempt
  starts** (`exam_attempts_allowance_fixed`), so a later HR revoke or revision cannot retroactively
  shrink a clock, or reinstate a threshold, the candidate has already been shown.

---

## PH4-D3 — Code Quality & Similarity Evidence  ·  Wave 5

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| | **Code Quality** | | |
| 1 | Coding submissions can be analyzed for supported quality signals | ✅ | HR: **Exams → exam → Results → attempt → Code evidence** (`ExamAttemptDetail.tsx` → `CodeEvidencePanel.tsx`) shows a `code_quality_reports` row per coding question; written by the nightly sweep (`analyse_pending`, wired into `app/reminders.py`) or on demand (`POST /hr/exams/{id}/attempts/{id}/code-analysis`, rate-limited per company); `ui` `unit` `smoke` |
| 2 | Quality signals can include measurable characteristics such as: | ✅ | heading item — the five characteristics below are all present on the same report and the same screen |
| 3 | complexity | ✅ | Python: real per-function McCabe cyclomatic complexity + nesting depth (`ast`-only, never executes the source); the other nine languages: a token-level decision-point-density approximation, explicitly labelled `kind='token-approximate'` and never presented as equal to the Python figure. Shown as "Avg. complexity"; `unit` `ui` |
| 4 | duplication | ✅ | `duplication_ratio` — a within-submission k-gram recurrence ratio, independent of the cross-submission similarity signal — shown as "Duplication"; `unit` `ui` |
| 5 | maintainability indicators | ✅ | the classic SEI maintainability index (0-100, clamped) from Halstead volume + complexity + LOC, computed identically for both the AST and the token path; shown as "Maintainability"; `unit` `ui` |
| 6 | test coverage where available | ⚠️ | honestly reported as unavailable for EVERY language — `coverage.available` is always `false`, with a stated reason ("Coverage requires instrumented execution… this analyser is pure static analysis and never runs candidate code."); the panel shows the sandbox's existing pass/fail TEST RESULTS instead, explicitly labelled results, never coverage. The criterion says "where available" and it is nowhere available, by design — a reader should know that no code-coverage figure exists anywhere in this feature, for any of the 10 languages, ever; `ui` (`'never shows a coverage figure — only the reason it is unavailable'`, `'shows the sandbox test results, labelled as results, not coverage'`) `unit` |
| 7 | code smells/static-analysis findings where supported | ✅ | Python: bare `except`, mutable default argument, unused import/local, `eval`/`exec`, `global`/`nonlocal`, long/deep-nested function. Other languages: empty catch, `var` (JS/TS), `any` (TS), `unsafe`/`.unwrap()` (Rust), `goto` (C/C++), magic numbers — rendered as a findings list with a severity per line; `unit` `ui` |
| 8 | Quality results are associated with the specific candidate submission | ✅ | `code_quality_reports` is keyed by `(attempt_id, coding_question_id, analyser_version)`, composite-FK'd to `exam_attempts(id, company_id)`; `db` `unit` |
| 9 | Results are available to authorized reviewers | ✅ | HR (only) reaches the full bundle via `ExamAttemptDetail.tsx`'s Code evidence tab, gated by `HrCtxDep` and the company's own attempt (`_owned_attempt`). NOTE: a SECOND read surface exists in the backend (`GET /hr/enrolments/{id}/code-evidence-summary`, and `row.code_evidence` computed into every decision-queue row by `hr_workflows.py`) but is wired to no screen at all — grepped `web/src/**` for both `code-evidence-summary` and `code_evidence`/`codeEvidence` outside `codeEvidence.ts`/`CodeEvidencePanel.tsx`/`CodeSimilarityCompare.tsx`/`ExamResults.tsx`, and `DecisionQueue.tsx` renders `row.scorecards` but never `row.code_evidence`. The primary reviewer surface (the attempt's own Code evidence tab) is real and complete, so this criterion still passes for it, but the summary-counts feature described in the backend's own comments is not reachable from any product screen; `ui` `unit` `smoke` |
| 10 | Quality signals are presented as evidence rather than an automatic decision | ✅ | the module's own docstring and an AST test (`test_code_evidence_never_calls_lifecycle_mutators`) prove `code_evidence.py` never writes `exam_attempts.status/passed/score_*` and contains no `UPDATE enrolments`; the screen labels findings as findings, with a severity, never a pass/fail verdict; `unit` `ui` |
| 11 | Analysis failures do not invalidate a candidate submission | ✅ | `exam_take.py` never imports `code_evidence` (`test_exam_take_never_imports_code_evidence`); a timeout or a pathological input is stored as `status='failed'` with only the exception's TYPE NAME, never the source, and the sweep moves to the next submission. Smoke: "the failed analysis still wrote a report… E's attempt is still submitted and graded despite the failure"; `unit` `smoke` |
| | **Similarity / Integrity** | | |
| 12 | The system can calculate similarity between code submissions where supported | ✅ | winnowing (MOSS-style, k=15/w=8) over normalised, boilerplate-subtracted token streams, for the same 10 languages the quality analyser supports; written by the same sweep, surfaced on the attempt's Code evidence tab and the exam-wide **Similarity** list (`ExamResults.tsx` → `listSimilaritySignals`); `unit` `db` `smoke` |
| 13 | Similarity analysis can identify potentially related submissions/reference material | ✅ | two signal kinds — `submission` (attempt vs. attempt) and `reference_solution` (attempt vs. the question's own reference solution). Smoke proves both fire: "exactly one submission-pair signal (A and B only)… at least one reference-solution signal"; `unit` `smoke` |
| 14 | Similarity results include enough context for a human reviewer to investigate | ✅ | HR: **Compare & record a finding** opens `CodeSimilarityCompare.tsx` — line-numbered excerpt blocks around each matched region (±3 lines, merged, capped 200 lines/side), a real gap row between separated blocks, containment/Jaccard figures. Fixed THIS wave (commit `c62756b`): rows used to be numbered 1..N against absolute `matched_regions`, so a match past roughly line 200 highlighted nothing and the gap between regions showed as a numbered line; the only prior test used a match on line 1 of a two-line file, the one case where the bug was invisible — now covered at line 40 and across two separated regions; `ui` `unit` |
| 15 | Similarity results are not treated as proof of misconduct by themselves | ✅ | every signal carries a fixed, server-written caption verbatim: "Automated, unreviewed — similar code is not evidence of misconduct on its own. Many candidates independently using the same tools or references can look alike." — rendered on-screen, not editable by anyone; `unit` `ui` |
| 16 | Reviewers can distinguish between a similarity signal and a confirmed integrity finding | ✅ | structurally two tables — `code_similarity_signals` (system, immutable, unreviewed) vs. `code_integrity_findings` (human, mandatory 20-2000 char rationale, one of `no_concern`/`follow_up`/`confirmed`, always against a NAMED `hr_manager`) — and two visually separate sections on the panel; "a signal is never a finding" is the screen's own load-bearing rule, UI-tested ("keeps similarity signals and integrity findings in separate, distinctly labelled sections", "labels the finding with who recorded it, not a similarity number"). Also fixed this wave (commit `44daaaa`): the compare dialog used to always put the pair's `low`-UUID attempt on the left under "This submission", regardless of which attempt HR was actually reviewing — whenever the reviewed attempt sorted second, HR judged the OTHER candidate's code as this one's, with the containment figures reversed; now the panes follow the attempt under review, with a new test for the side that was never tested (`'labels the HIGH side "This submission" when the attempt under review is attempt_high_id'`); `db` `unit` `ui` |
| 17 | Existing assessment integrity evidence remains available alongside code-analysis evidence | ✅ | `evidence_for_attempt` returns the PRE-EXISTING `integrity_score`/`proctoring_summary`/`exam_integrity_events` counts in the same bundle as the new quality/similarity data, rendered by `IntegritySummaryCard` on the same screen, right alongside the new evidence; `unit` `ui` |
| | **Decision Safety** | | |
| 18 | Code quality cannot automatically reject a candidate | ✅ | `code_evidence.py` never imports or calls a workflow/decision mutator; an AST test asserts none of `record_transition`/`record_final_decision`/`release_hold`/`_hold` appear anywhere in the module's source, and no `UPDATE enrolments`; `unit` |
| 19 | Code similarity cannot automatically reject a candidate | ✅ | the same module, the same test — similarity signals are written by the identical sweep and are equally inert; `unit` |
| 20 | AI cannot directly change candidate lifecycle status based on these signals | ✅ | no agent/LLM path touches any of the four tables: `test_no_agent_module_references_code_evidence` greps every file under `app/agents`/`shared/agents` for the table and module names; `test_no_module_here_imports_an_llm_client` AST-checks `code_evidence`/`code_quality`/`code_similarity`/`code_sandbox` for an LLM-client import; `unit` |
| 21 | Any resulting candidate decision remains human-authorized | ✅ | `record_finding` is the ONLY writer of a human judgement, always against a NAMED `hr_manager` (`recorded_by_user_id` NOT NULL, DB-enforced) with a mandatory 20-2000 char rationale; nothing here can itself become a hire/reject — `PanelVerdict.decision_authority` stays the literal `"human_only"`, structurally untouched by this feature; `db` `unit` |
| 22 | Evidence and reviewer actions are auditable | ✅ | `AuditLog` rows for `code_evidence.viewed` (every source/compare read, D3 #24) and `code_integrity.finding_recorded`. Smoke: "reading source wrote an audit row"; `db` `unit` `smoke` |
| | **Security** | | |
| 23 | Analysis runs in an appropriately isolated environment | ✅ | one `spawn`ed OS process PER analysis task, never a shared pool (a security review found and fixed the two ways a shared `ProcessPoolExecutor` broke that promise — one dead worker permanently disabling analysis for every tenant, and pool resets cancelling unrelated callers' work); a cleared environment (no inherited DB/Redis/API-key env vars — `test_the_child_starts_with_an_empty_environment`); per-task `RLIMIT_AS`/`RLIMIT_CPU` where the platform supports it; a hard timeout that kills the process (`test_a_timeout_kills_the_process_it_timed_out`, `test_one_analysis_timing_out_does_not_cancel_another`). The analysis itself never executes candidate source at all — `ast.parse`/Pygments tokenising only inspect syntax — so the process isolation is defence against a pathological INPUT (a deep expression, a lexer pathology), not against arbitrary code execution, which never happens; the child does have network access (stated plainly in the module docstring; the claim made is narrower than full sandboxing) — worth knowing, not a failure of this criterion; `unit` |
| 24 | Candidate code is not unnecessarily exposed to unauthorized users | ✅ | HR only (`hr_router`, no super-admin/interviewer/candidate/agent route — statically verified, ≥7 routes via `test_every_route_sits_behind_hr_ctx_dep` / `test_no_super_admin_interviewer_or_public_router_in_the_module`); reading a candidate's raw source or a compare view is audited EVERY call and never prefetched (`getCodeSource`/`getSimilarityCompare` fire only on an explicit "View source"/"Compare" click — UI-tested: "does not fetch source until HR clicks 'View source'"). Smoke: "an interviewer gets 403 on the evidence tab"; `unit` `ui` `smoke` |
| 25 | Company/tenant boundaries are enforced | ✅ | composite FKs from BOTH sides of a similarity pair to `exam_attempts(id, company_id)` make a cross-tenant pair UNREPRESENTABLE, not merely filtered (`test_a_cross_tenant_signal_pair_is_unrepresentable`, `test_a_cross_tenant_finding_attempt_is_refused`). Smoke: "another company's HR gets 404 on the evidence tab"; `db` `smoke` |
| 26 | Sensitive code-analysis data follows retention and erasure requirements | ✅ | `purge()` redacts source/program-output (never the score) and deletes reports/fingerprints/signals once retention elapses, honouring `RETENTION_DRY_RUN` (`test_new_tables_are_in_the_erasure_inventory`). Smoke: dry run changes nothing, the real run redacts — "reports were deleted… fingerprints were deleted… signals were deleted… the finding's rationale is redacted", and "the score survives redaction"; `unit` `smoke` |
| | **Compatibility** | | |
| 27 | Existing coding assessments continue to work if analysis is unavailable | ✅ | `analyse()` never raises for ordinary input (`status='unsupported'`/`'failed'` instead of an exception); `exam_take.py` imports none of this, so a sandbox outage cannot touch submission or grading. Smoke: "a disabled sweep writes nothing… E's attempt is still submitted and graded despite the failure"; `unit` `smoke` |
| 28 | Existing sandbox/test execution remains functional | ✅ | `test_execution_module_signature_is_unchanged` pins `app.execution.run_code`'s signature and the 10-language `SUPPORTED_LANGUAGES` set, untouched by this feature; the analyser is a SEPARATE, non-executing path (`code_sandbox.py`) from the existing Piston-backed execution sandbox candidates actually run against; `unit` |
| 29 | Analysis results do not alter the candidate's submitted source code | ✅ | both analysers only READ `source`/`starter_code` as strings (`ast.parse` / Pygments tokenise, never `exec`/`eval`/subprocess-run them); nothing in this module writes `exam_attempts.answers` except the one, differently-triggered retention redaction, which the `exam_attempts_submission_frozen` trigger only permits in the SAME statement as `code_redacted_at`, never as an ordinary write from analysis; `db` `unit` |
| 30 | Tests cover quality analysis, similarity detection, permissions and no-auto-reject behavior | ✅ | `test_ph4_d3_code_evidence.py` (unit, 78 tests: quality metrics, similarity scoring, redaction helpers, the audience-gate and no-agent/no-LLM AST checks, failure-isolation paths), `test_ph4_d3_sandbox.py` (unit, 7 tests: process isolation, timeout, empty environment), `test_ph4_d3_guarantees.py` (db, 61 tests against the real triggers: arrival/immutability/redaction shape for all four tables, cross-tenant unrepresentability, retention interaction with the submission freeze), `smoke_ph4_d3_code_evidence.py` (real API, 39 checks: full sweep → fingerprinting → signals for both kinds → evidence tab → source read audited → cross-company 404 → interviewer 403 → a confirmed finding recorded with no score/status change → an analysis failure still leaves the attempt submitted and graded → a disabled sweep writes nothing → retention dry run then real, with the score surviving redaction), `CodeEvidencePanel.test.tsx` (ui, 14 behavioural tests across 6 describe blocks, including the two Wave-5 regressions: the swapped compare side and the line-40/two-region highlighting) |

**Worth knowing.**

- **Test coverage does not exist, for any language, and the product says so.** `coverage.available`
  is hard-coded `false` everywhere, with a stated reason; the screen shows the sandbox's existing
  pass/fail test RESULTS instead, explicitly labelled results, never coverage. This is the honest
  reading of "test coverage where available" — it is nowhere available, by design, since this
  analyser is deliberately static-only and never executes candidate code.
- **The code-evidence counts reached no screen until `f455b79`, and then said too much.** The
  `/hr/enrolments/{id}/code-evidence-summary` endpoint and `row.code_evidence` on every
  decision-queue row were built and tested, but nothing rendered them. `f455b79` showed them. Its
  first wording called every signal "unreviewed" and every finding "recorded", so a candidate HR
  had reviewed and cleared still read as flagged on the decision screen (security review, D3 M3).
  They now say how many signals are awaiting review, and name each finding by its outcome ("1
  reviewed: no concern", "1 flagged for follow-up", "1 integrity concern confirmed"). Nothing
  shows when there is none. Tested by a db test on the counts and by `CodeEvidenceCounts` and
  `DecisionQueue`. The main reviewer surface, the attempt's own Code evidence tab, was complete
  throughout.
- **No HR screen receives a candidate's program output.** The evidence tab reduced each test to
  pass/fail in `c62756b`, but the same attempt page's score breakdown (`/breakdown`) still sent
  every test's stdout/stderr and the hidden cases, unaudited (security review, D3 M2). It is now
  reduced the same way, and the D3 smoke checks it over HTTP.
- **Two real, independently-found bugs were fixed in this wave's own review commit (`44daaaa`)**:
  the compare dialog used to always label the pair's UUID-`low` attempt "This submission" regardless
  of which attempt HR was actually reviewing (reversing the containment figures and misattributing
  code whenever the reviewed attempt sorted second — the only dialog where an integrity finding is
  recorded); and matched-region highlighting was numbered 1..N against absolute source line numbers,
  so a match past roughly line 200 lit up nothing. Both are now covered by dedicated regression
  tests, and both are the kind of defect this evidence pass is meant to surface, not merely record
  as already fixed.
- **The sandbox process has network access.** The isolation guarantee is about a fresh OS process,
  a cleared environment and hard resource limits — not a network-free jail — and the module
  docstring says so plainly. It is not a gap against the stated criterion (analysis never executes
  candidate code in the first place), but a reader should know the isolation is narrower than "no
  network."
- **Analysis is pure and static by construction** (stdlib + Pygments only, `ast.parse`/tokenising
  never execution) — the strongest form of "never expose candidate code to execution risk" available
  short of not analysing it at all.

---

## PH4-D4 — Job Simulations & Portfolio  ·  Wave 5

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| | **Job Simulations** | | |
| 1 | Authorized users can create a job simulation round | ✅ | HR: **Requisitions → opening → Workflow (`/hr/requisitions/:id/workflow`) → select a round → Round type: Job simulation**, then the **Task** section (`TaskEditor.tsx` inside `RoundInspector.tsx`) → **Save task**. `ck_workflow_rounds_kind` grew to six kinds; every `/hr/rounds/{id}/task*` route sits behind `HrCtxDep` (AST test) and writes only while the version is a draft. The D4 smoke drives the config write over HTTP: it saves a draft round's brief and items and reads them back, and a published round's save is refused (409); `ui` (`RoundInspectorTask`) `unit` `db` (`test_job_simulation_and_portfolio_are_legal_kinds`) `smoke` |
| 2 | Simulation rounds can be associated with role competencies | ✅ | HR: **Workflow builder → task round → Reviewer’s checklist** — the same `CriteriaPicker` a human_review round uses (`roundKinds.tsx` sets `supportsCriteria: true` for both task kinds), writing frozen `round_criteria`; `workflows.validate` refuses to publish a task round with no criterion. No test renders the checklist on a task round or triggers that publish refusal — the smoke seeds `round_criteria` by SQL, then proves a reviewer scores against them; `smoke` |
| 3 | A simulation can contain a structured candidate task/scenario | ✅ | `round_tasks`: a brief (≤20 000 chars, optional Hindi/Telugu translations served in the candidate's language), up to 20 items each with key, prompt, answer type (text / file / link), required flag and max characters, plus HR reference materials (PDF/JPEG/PNG) and the round's time limit and deadline — all edited in **TaskEditor**. `validate_config` is the single validator (15 unit tests); for a timed task the server withholds items and materials until the candidate starts (M4); `ui` (`RoundInspectorTask`, `PublicTask`) `unit` `db` (`test_timed_task_withholds_items_until_started`) `smoke` |
| 4 | Candidates can access assigned simulations through the existing candidate experience | ✅ | Two ways in. (a) The runner emails a magic link (`/task#<token>`, no login, EN/HI/TE) — the same pattern as exam and offer links. (b) A signed-in candidate: **My applications (`/applications`) → “Your task is ready” → Open task**, which mints a fresh link (`POST /users/me/tasks/{id}/link`) and lands on the same `/task` page. Caveats: (b) only works for a candidate whose application is linked to an activated account; opening from (b) invalidates the emailed link; the task page sits outside the signed-in shell; and the mint route's only backend test is the AST check that it needs `CurrentUserDep`; `ui` (`Applications` ×4, `PublicTask`) `unit` |
| 5 | Candidates can submit simulation responses/work | ✅ | Candidate: **task link → tick “I agree…” → Begin → answer each item (text autosaves on blur; a file item has its own upload/Replace/Remove) → Submit → Yes, submit**. Required items and minimum artifacts block Submit in the page and are refused again by `submit` (422); a second submit is 409; `ui` (`PublicTask`) `db` `smoke` |
| 6 | Simulation submissions are persisted and associated with the candidate/enrolment | ✅ | `task_submissions` carries `enrolment_id`, `applicant_id`, `round_id` and `company_id` through composite `(id, company_id)` FKs, with one live row per (enrolment, round) (partial unique index) and no hard delete; `task_responses` hangs off the submission; `db` (`test_one_live_submission_per_enrolment_round`, cross-company refusals, `test_submission_never_hard_deleted`) `smoke` |
| 7 | Simulation submissions have a clear lifecycle | ✅ | assigned → in_progress → submitted / expired / withdrawn, enforced by the `task_submissions_lifecycle` trigger (it must arrive `assigned`; submitting needs `submitted_at` + `closed_by`; terminal rows are frozen; a re-issue supersedes, then inserts) and mirrored in `ALLOWED_TRANSITIONS`. Shown to HR as status tags in **Candidate drawer → Job simulation / portfolio**, and to the candidate as begin / working / “Submitted”; `db` `unit` `ui` (`TaskSubmissionSection`, `PublicTask`) `smoke` |
| 8 | Authorized reviewers can review submitted work | ✅ | Once HR assigns them (**Candidate drawer → Human interview → Assign interviewers**), a reviewer opens **Interviewer console (`/interviewer`) → “Simulation review” → Submission tab** (`SubmissionPanel.tsx`): the round's brief, each answer under its item's own prompt, reference materials with a download, files as signed downloads, links through an interstitial. Content is served only for submitted work whose consent stands (M1, NEW-2). The screen was wired to the brief, prompts and materials in `498753b` (before that it showed item keys only); `ui` (`SubmissionPanel`) `db` (`test_reviewer_cannot_read_an_unsubmitted_draft`) `smoke` (brief and items, material download, file download) |
| 9 | Reviewers can record structured evaluation/evidence | ✅ | Reviewer: **Scorecard tab** — a 1–5 score or “not assessed” per round criterion, per-criterion evidence text and a summary. Submit needs every criterion scored or marked, with at least one scored; corrections supersede and never overwrite. This is the existing `interviewer_scorecards` machinery, unchanged; `ui` (`InterviewerScorecard`) `smoke` (a scorecard submitted against the simulation round's `round_criteria`) |
| 10 | Simulation evaluation uses the existing competency/evaluation architecture where applicable | ✅ | `SCORABLE_ROUND_KINDS = HUMAN_EVALUATED_KINDS` (`interviewer_scorecards.py:80`), so task rounds reuse the scorecards, the frozen `round_criteria` with anchors, the existing `post_round_review` → `record_result(graded_by='human')` verdict, the decision queue and `enrolment_awaits_human`. D2 accommodations scale the time limit and deadline (`due_and_limit`). No new scoring model, and no threshold; `unit` (`test_group_c_workflows`: task kinds ⊆ human-evaluated and disjoint from AI-graded/exam-backed) `db` `smoke` |
| | **Portfolio** | | |
| 11 | Authorized users can configure a portfolio submission round | ✅ | HR: **Workflow builder → round → Round type: Portfolio → Task** — brief, optional items, **Portfolio settings** (minimum/maximum artifacts 0–20, Accept files, Accept links, allowed link domains, defaulting to github/gitlab/behance/figma/…) → **Save task**. The editor blocks a file item when files are off, as `validate_config` does. The same HTTP write path is smoke-tested (criterion 1), with material upload, list, download and removal; `ui` (`RoundInspectorTask` ×2) `unit` `smoke` |
| 12 | Candidates can submit portfolio artifacts or approved external links | ✅ | Candidate: **task link → Begin → Your portfolio → Add a file / Add a link → Submit**. A link must be https with no userinfo, no IP address, port 443 only, and a dot-boundary match against the round's allow-list. Backslashes and control characters are refused, which closes the H1 bypass. Links are never fetched. Files pass the magic-byte check; the count is capped at `max_artifacts`. The free-form artifact form (`AddArtifactForm`) has no UI test, and no test uploads a free-form FILE artifact (only a file item); `unit` (`validate_link` ×14) `db` `smoke` (approved link accepted, `evilgithub.com` refused, a non-PDF refused) |
| 13 | Portfolio submissions can support appropriate metadata such as title, description and artifact/link type | ✅ | Each artifact stores a title (≤200), a description (≤2000) and a type (repository / design / document / video / website / other, checked in app and DB). Files also record the server's own sanitised name, content type, size and sha256. The candidate enters the first three under **Add a link → Type / Title (optional) / Description (optional)**, and the reviewer's Portfolio list shows them. No test ever submits a description; `ui` (`SubmissionPanel` renders title and type) `db` (`link_kind='repository'`) `smoke` (title) |
| 14 | Authorized reviewers can view submitted portfolio evidence | ✅ | Reviewer: **Submission tab → Portfolio** lists each artifact's title, description and type. A file opens through a presigned download from `reviewer_artifact_download`, which serves only submitted, consented work, only for the scorecard's own enrolment and round, and audits every download. A link opens only after an interstitial that shows the BROWSER-resolved host, is https-only and uses `rel=noopener noreferrer nofollow`. The smoke has the portfolio round's reviewer download the candidate's file, and the SIMULATION round's reviewer refused it (404, not their round); `ui` (`SubmissionPanel`: interstitial ×5, nothing shown before submit) `db` (M1 refusals) `smoke` |
| 15 | Reviewers can record evaluation/evidence against configured criteria | ✅ | The same **Scorecard tab** against the portfolio round's own frozen `round_criteria` — the kinds share `SCORABLE_ROUND_KINDS`. The smoke scores only the simulation round (the portfolio round is held without a scorecard), so the portfolio case rests on that shared path; `ui` (`InterviewerScorecard`) `unit` `smoke` (simulation) |
| 16 | Portfolio submissions are associated with the candidate/enrolment | ✅ | The same `task_submissions` row and FKs as criterion 6. A file answering an item carries its `item_key` and does not count toward `max_artifacts`. In the smoke, the runner issues the portfolio link for the same enrolment when HR passes the simulation; `db` `smoke` |
| 17 | Candidate submissions remain protected by appropriate permissions | ✅ | HR routes need `HrCtxDep` (hr_manager only), reviewer routes need `InterviewerCtxDep` plus ownership of the scorecard (someone else's is a 404, never a 500 — L1), and the candidate needs the link token (header only, HMAC at rest, rate-limited per token). No super_admin, platform_owner, admin or agent route reaches task content. Reviewers and HR get content and signed URLs only for submitted, consented work; a submitted link stops serving the candidate's own answers; `unit` (AST route gates) `db` (M1, `test_submitted_view_carries_no_content`) `smoke` `ui` (`SubmissionPanel`) |
| | **Decision & Safety** | | |
| 18 | Simulation and portfolio evidence appears in the candidate evidence/decision workspace | ✅ | HR: **Decision queue (`/hr/requisitions/:id/decisions`)** tags the row “Submission received” / “Submission in progress” while the candidate awaits review. **Scores, evidence & history** opens the drawer, whose **Job simulation / portfolio** section shows the brief, each item's prompt with the answer (text; a link through the same interstitial reviewers use; a file as an audited signed download), then free-form portfolio artifacts with title, description and type, beside the reviewers' scorecards and the round verdict. The live row is chosen by `is_current`, not by list position. Work whose consent was withdrawn is shown as “Consent withdrawn — content hidden”. The section was wired to the content in `498753b`; before that HR saw lifecycle only; `ui` (`TaskSubmissionSection`, `DecisionQueue` task tag ×3) `db` (`test_hr_reads_submitted_work_with_its_prompts_and_the_read_is_recorded`) `smoke` |
| 19 | Simulation/portfolio results do not automatically reject candidates | ✅ | Nothing turns a task outcome into a rejection. `close_due` only moves the SUBMISSION (expired, or submitted if consented work exists), and an expired task leaves the candidate waiting for a person. `post_round_review` holds on a fail and only refuses a PASS that has no submitted, consented work (`hr_workflows.py:930-944`). `job_tasks.py` contains no `UPDATE enrolments` or lifecycle mutator. The smoke checks that PASS refusal (409 before anything is submitted); `unit` (`test_job_tasks_never_calls_lifecycle_mutators`, `test_failing_a_review_holds_rather_than_rejects`) `smoke` (“held, never rejected”) `ui` (`TaskSubmissionSection`: “never offers a reject action”) |
| 20 | AI cannot independently determine a candidate's final outcome from these submissions | ✅ | No model ever sees or scores a submission. `job_tasks`/`guest_identity` import no LLM client, no module under `app/agents` or `shared/agents` names a task table, the workflow copilot stays pinned to the four older kinds, task rounds have no threshold, and `PanelVerdict.decision_authority` is the literal `"human_only"`; `unit` (`test_no_module_here_imports_an_llm_client`, `test_no_agent_module_references_job_tasks`, `test_copilot_round_kinds_stays_at_four`) |
| 21 | Human decision authority remains unchanged | ✅ | `record_final_decision` is still the one writer of hire/reject, and the dependency points one way: `final_decision.py` calls `job_tasks.close_for_decision` to withdraw open links, leaving submitted work alone. The round verdict is the existing named-HR **Decision queue → Passes this round / Hold for a decision**, recorded as `graded_by='human'` with the grader's id; `unit` `db` (`test_close_for_decision_*` ×2) `smoke` |
| 22 | Evaluation criteria remain traceable to the configured workflow/round criteria | ✅ | Scores are keyed by `(scorecard_id, competency_id)` with `round_id`. Ids are validated against the round's own frozen `round_criteria`, and submit lists any criterion left unscored. Each submission stores the `config_digest` of the task it was issued. The version's review fingerprint now covers `round_tasks`, so an approved version cannot drift from what was reviewed; `smoke` (scored with the round's competency id) `unit` (digest stability) |
| 23 | Published assessment configuration remains protected from unintended modification | ✅ | `round_tasks` and `round_task_materials` use the existing `workflow_children_immutable` trigger: frozen while the version is published, archived, in review or approved. `_config_lock_reason` gives HR a 409 sentence before the write reaches the trigger, and the editor goes read-only. A clone shares material objects, deleted only when no row names them; `ui` (`RoundInspectorTask`: read-only, no save) `db` (frozen in review, materials frozen when published, editable as draft) `smoke` (a published round's config save is 409) |
| | **Security / Compliance** | | |
| 24 | Uploaded portfolio artifacts follow existing secure storage mechanisms | ✅ | Files go through the same `app/document_storage` as preboarding documents: `check` (magic bytes, PDF/JPEG/PNG, 10 MB), `store`, presigned `signed_download`. Keys (`tasks/{company}/{submission}/{response}`) name no person, and downloads are limited to submitted, consented work. The D4 smoke runs against a real S3-compatible store with `SMOKE_REAL_STORAGE=1` (a local MinIO, nothing patched): 61/61, including a byte-for-byte read back through the signed URL HR receives. A replaced file's old object is removed only after the commit (NEW-6, fixed in `52b2385`). A material's file is removed when the material, or its round's task kind, is (`ba73e1a`). Known and accepted: the demo bucket is Cloudflare R2 in the US (AR-1), and uploads are not malware-scanned (AR-6); `unit` `db` `smoke` (faked and real storage) |
| 25 | Candidate-submitted data follows consent, retention and erasure rules | ⚠️ | Consent: ticking the box to **Begin** books one `dpdp_consent_ledger` row for THAT submission, written before `consented_at` is set. Nothing is saved or uploaded before it. The database enforces it both ways: `consented_at` can be set only by the start transition and only with an active ledger row for that submission, and revoking the row from anywhere clears it in the same transaction (`52b2385`, `c8e0a2b4d6f8`; db-tested). So an erasure REQUEST stops processing at once, not 30 days later. The grant records hashed request evidence and is audited. The candidate can **withdraw** on the task page while working or after submitting. Withdrawal hides the work from reviewers and HR, and stops it passing the round, but deletes nothing (NEW-1/NEW-2 fixed, db-tested). Retention is decision-aware: a held application is never purged; a decided one is purged after `TASK_SUBMISSION_RETENTION_DAYS` (180), only when `RETENTION_DRY_RUN=false`. What keeps this ⚠️: erasure step 5i/8 (redact responses, delete files) is proven only by inventory/version unit tests, never on real task rows; an application abandoned without a decision keeps its evidence until erasure; data sits outside India in the demo tier (AR-1); `db` `unit` `smoke` `ui` (`PublicTask` consent and withdrawal) |
| 26 | Access is company/tenant scoped | ✅ | The company always comes from the session (`HrCtxDep`/`InterviewerCtxDep`), never the request, and every HR/reviewer query filters on it. Composite `(id, company_id)` FKs refuse a cross-company round, applicant, enrolment or task config. The smoke: another company's HR gets 404 on the round's task and `[]` for the enrolment's submissions. Other-company download/reissue/withdraw are scoped the same way, but only by reading; `unit` `db` (×4) `smoke` |
| 27 | Unauthorized candidates cannot access another candidate's submissions | ✅ | `by_token` resolves exactly one submission from the HMAC of a 256-bit token (one 404 for every failure), and every candidate read or write uses that submission's id. A db test gives candidate A's valid token candidate B's artifact id: A's view shows nothing of B's, and removing it is a 404 with B's row intact. Consent withdrawal matches by submission id from the token, so one candidate cannot revoke another's. Reviewer reads are pinned to the enrolment and round of a scorecard they own; the smoke refuses the simulation round's reviewer the portfolio round's file (404). Re-issued, withdrawn, decided and erased links die (db); `db` (`test_one_candidates_token_cannot_reach_another_candidates_work`) `smoke` |
| 28 | Review and evaluation actions are auditable | ✅ | Scorecard assign/start/submit are audited by the existing machinery (`smoke_ph4_scorecards` asserts it), and the round verdict lands in `round_results` with the grader. HR re-issue, withdraw and download, reviewer download, and candidate submit or consent withdrawal each write an `AuditLog` row plus an append-only `task_event`. Reading a candidate's work writes a `submission_viewed` event against the reader, at most once an hour per reader: a reviewer's read, and HR's since `52b2385`. Giving consent at **Begin** is audited too (`task_submission.consent_given`). The smoke asserts the HR download's audit row and HR's read event; a db test asserts the read event, and that a draft read records nothing. A retention redaction writes a log line, not an audit row (it is not a review action); `db` `unit` `smoke` |
| | **Compatibility** | | |
| 29 | Existing MCQ, coding and AI interview rounds continue to work | ✅ | The full data_gateway, admin_ops and shared suites pass on a database built from nothing (numbers in the header). pytest never collects `smoke_*.py`, so all 42 smokes were run, each on its own throwaway copy of a migrated database, with every external credential blanked. 41 pass: 38 directly, the two Group B backfill smokes with the seeded setup their docstrings require, and the Wave 4 smoke against a local MinIO (108/108). Three had silently rotted since earlier waves and are fixed in `ea87ddf`: `smoke_group_c_api` still expected `portfolio` to be refused, and it, `smoke_group_c_runner` and `smoke_group_c_workflows` seeded questions into an already-published exam round, which D1's lock refuses. The one not run, `smoke_group_a_scorecard_retry`, needs a live LLM; `unit` `smoke` (the sweep) |
| 30 | New simulation/portfolio rounds integrate with the existing workflow runner | ✅ | `_assign_round` has an explicit `TASK_KINDS` branch → `job_tasks.issue` (link, email, D2 allowance). In the smoke, HR passes the simulation, the runner issues the portfolio link, HR holds, and the next round is a plain human_review. `enrolment_awaits_human` and the decision-queue join both cover task kinds. The publish rule for an unconfigured task round is untested; `unit` (`test_assign_round_has_an_explicit_task_branch`) `db` (`test_enrolment_awaits_human_true_for_task_round`) `smoke` |
| 31 | Tests cover candidate submission, reviewer access, evaluation and decision integration | ✅ | Candidate submission: `PublicTask` UI tests, the db tests in `test_ph4_d4_wave5_fixes.py` (36) and `test_ph4_d4_guarantees.py`, and the D4 smoke (60 checks faked, 61 on real storage). Reviewer access: db M1 tests, `SubmissionPanel`, and smoke downloads. Evaluation: a smoke scorecard against `round_criteria`. Decision integration: smoke pass → next round, hold → held, and the PASS refusal, plus db `close_for_decision` and the pipeline-board close-out (unit). No browser (Playwright) spec touches a task round; `unit` `db` `smoke` `ui` |

**Worth knowing.**

- **Three security reviews changed this story after it was built.**
  - The first found work reaching the hiring team without consent. The fix moved consent to
    **Begin**, with one ledger row per submission (`69435c0`).
  - The second found that a candidate who had withdrawn once could later work with no consent
    record, and that nothing could be withdrawn after submitting (`52b2385`).
  - The third found that an erasure request revoked the consent record but left the work
    readable for 30 days. The database now keeps the record and `consented_at` in step both ways
    (`c8e0a2b4d6f8`), which is also a revision that re-asserts every in-place edit to the D4
    migration, so a database that applied its first version is corrected too (checked).
- **The screens now show what the server sends.** The server had closed gaps 2–4, but the
  reviewer's Submission tab still showed item keys only, and HR's drawer showed lifecycle only.
  Both now show the brief, the prompts and the work (`498753b`).
- **Consent withdrawal hides; it does not delete.** Withdrawn work stays stored until retention
  or erasure removes it. An application that is never hired or rejected keeps its task evidence
  until erasure: retention waits for a decision by design, so it never purges held work. Purging
  happens only where `RETENTION_DRY_RUN=false`. DPDP §8(7) expects erasure once the purpose is
  served, so whether "hidden, then retained until decision + 180 days" is acceptable is a
  decision for the DPDP owner, not something this checklist can settle.
- **What no test exercises:**
  - erasure step 5i on real task rows;
  - a free-form portfolio FILE upload (a file ITEM is tested, and so are free-form links);
  - any Playwright browser journey through a task round.
- **Residency and scanning.** In the demo tier, uploaded files sit in Cloudflare R2 (US) and text
  in Neon (Singapore) — AR-1. No upload is malware-scanned — AR-6. Portfolio links are never
  fetched server-side — AR-7. The task page's consent notice names the storage locations.
- **Candidate access depends on email for anyone without an activated account.** The
  `/applications` "Open task" button helps only linked accounts, and it rotates the emailed link.
  Check email delivery before a demo: the demo Resend account delivers to a single address. The
  HI/TE candidate copy has not had a native-speaker review.

---

## Decisions this phase rests on

| | Decision | Consequence you can see |
|---|---|---|
| D4-1 | A new `interviewer` role that sees only the interviews assigned to them | The **My interviews** console; super admins manage interviewers under **Team** |
| D4-2 | The super admin approves offers (A3) and workflow versions (O6) | Wave 2 and Wave 4 |
| D4-3 | Uploads are limited to PDF, JPEG and PNG, checked by content (not by extension); downloads only through signed links; no virus scanner yet | Wave 4 (A4) |
| D4-4 | Merge wave by wave; each wave's migrations are applied just before that wave deploys | This document is updated per wave |
