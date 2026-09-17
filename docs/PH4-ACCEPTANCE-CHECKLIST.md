# PH4 — acceptance checklist against `docs/AntHire-Phase4.docx`

**Built:** 2026-09-17. **Checked against:** every acceptance criterion in the Phase 4
document, in the document's own order and wording — all 317 of them across 15 stories.

**Delivery (decision D4-4):** wave by wave. Each wave merges and deploys on its own, only
after CI, a code review and a security sign-off.

| Wave | Stories | State |
|---|---|---|
| 1 | A1 Human interview scorecards · A5 Interview kits · O4 Decision reason codes | **Built and verified.** See the deployment line below. |
| 2 | O6 Workflow review & approval · O3 Branching workflows · O2 Workflow simulation & dry run · O1 Stage owners, SLAs & exceptions | Not started |
| 3 | A2 Interview scheduling & loops · O5 Panel workload & calibration | Not started |
| 4 | A3 Offer lifecycle · A4 Documents & preboarding | Not started |
| 5 | D1 Question banks · D2 Accommodations · D3 Code quality & similarity · D4 Job simulations & portfolio | Not started |

**Deployment:** Wave 1 is not yet live. This line is updated when it is.

**Key:** ✅ done and verified · ⚠️ done, with something you should know · ❌ not done · ⏳ not started (a later wave)

**The bar for ✅** is that the person the criterion names can do it **in the product**, not
only through the API. Phase 3 was marked against a looser bar and had to be corrected.

**How each line was verified:**
- `unit`: `services/data_gateway/tests/unit/test_ph4_wave1.py` and `test_ph4_wave1_hardening.py`.
- `db`: `tests/integration/test_ph4_scorecard_guarantees.py`. It runs against a real migrated Postgres, and every refusal is checked for its *reason*, not just for failing.
- `smoke`: `tests/integration/smoke_ph4_scorecards.py`, 109/109 against real Postgres through the real endpoints.
- `ui`: the web test suite, 1065 tests, and the screen named on the line.

---

## Summary

| Story | Wave | Criteria | Done | Notes |
|---|---:|---:|---:|---|
| PH4-A1 Human Interview Scorecards | 1 | 15 | 15 |  |
| PH4-A5 Interview Kits | 1 | 15 | 14 | 1 ⚠️ |
| PH4-O4 Structured Decision Reason Codes | 1 | 13 | 13 |  |
| PH4-O6 Workflow Review & Approval | 2 | 14 | 0 | ⏳ not started |
| PH4-O3 Branching Workflows | 2 | 15 | 0 | ⏳ not started |
| PH4-O2 Workflow Simulation & Dry Run | 2 | 31 | 0 | ⏳ not started |
| PH4-O1 Stage Owners, SLAs & Exception Paths | 2 | 13 | 0 | ⏳ not started |
| PH4-A2 Interview Scheduling + Loops | 3 | 29 | 0 | ⏳ not started |
| PH4-O5 Panel Workload & Calibration | 3 | 15 | 0 | ⏳ not started |
| PH4-A3 Offer Lifecycle | 4 | 35 | 0 | ⏳ not started |
| PH4-A4 Documents & Preboarding | 4 | 33 | 0 | ⏳ not started |
| PH4-D1 Reusable Question Banks | 5 | 15 | 0 | ⏳ not started |
| PH4-D2 Candidate Accommodations | 5 | 13 | 0 | ⏳ not started |
| PH4-D3 Code Quality & Similarity Evidence | 5 | 30 | 0 | ⏳ not started |
| PH4-D4 Job Simulations & Portfolio | 5 | 31 | 0 | ⏳ not started |
| **Total** | | **317** | **42** | **1 ⚠️, 0 ❌, 274 ⏳** |

**Wave 1 alone:** 43 criteria — 42 ✅, 1 ⚠️, 0 ❌.

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

| # | Acceptance criterion | |
|---|---|---|
| 1 | Workflow versions can be submitted for review | ⏳ |
| 2 | Authorized reviewers can approve a workflow version | ⏳ |
| 3 | Authorized reviewers can reject/request changes | ⏳ |
| 4 | Only approved versions can be published/activated | ⏳ |
| 5 | Draft versions remain editable | ⏳ |
| 6 | Published/approved versions cannot be silently modified in place | ⏳ |
| 7 | Approval status is version-specific | ⏳ |
| 8 | Reviewer, timestamp and action are recorded | ⏳ |
| 9 | Approval/rejection actions are audited | ⏳ |
| 10 | Unauthorized users cannot approve their own workflow unless explicitly permitted by policy | ⏳ |
| 11 | Validation must pass before approval | ⏳ |
| 12 | Existing workflow validation and coverage checks remain enforced | ⏳ |
| 13 | A workflow cannot bypass approval through an alternate API/UI path | ⏳ |
| 14 | Tests cover approval, rejection, authorization and version isolation | ⏳ |

---

## PH4-O3 — Branching Workflows  ·  Wave 2

| # | Acceptance criterion | |
|---|---|---|
| 1 | HR can configure conditional transitions between workflow rounds | ⏳ |
| 2 | Existing on_pass_next_round_id logic is reused where applicable | ⏳ |
| 3 | Workflow authors can visually see branches in the workflow builder | ⏳ |
| 4 | Branch conditions are clearly defined and validated | ⏳ |
| 5 | Every possible branch must lead to a valid next round or an allowed terminal human-decision state | ⏳ |
| 6 | Invalid destinations are rejected during workflow validation | ⏳ |
| 7 | Cyclic/invalid workflow paths are detected | ⏳ |
| 8 | Branching works correctly when the workflow is executed for a real candidate | ⏳ |
| 9 | held candidates can be routed to the appropriate human-review/decision path | ⏳ |
| 10 | No branch may create an automatic AI terminal rejection | ⏳ |
| 11 | O2 simulation can execute and validate all configured branches before publishing | ⏳ |
| 12 | Published workflow versions remain immutable | ⏳ |
| 13 | Branch configuration changes are audited | ⏳ |
| 14 | Existing non-branching workflows continue to work unchanged | ⏳ |
| 15 | Automated tests cover all supported branch paths and invalid configurations | ⏳ |

---

## PH4-O2 — Workflow Simulation & Dry Run  ·  Wave 2

| # | Acceptance criterion | |
|---|---|---|
| | **Simulation** | |
| 1 | Authorized users can run a simulation against a draft workflow | ⏳ |
| 2 | Simulation does not create a real candidate application | ⏳ |
| 3 | Simulation does not modify real candidate lifecycle state | ⏳ |
| 4 | Simulation does not create real interview invitations | ⏳ |
| 5 | Simulation does not send real candidate-facing emails | ⏳ |
| 6 | Simulation does not trigger real external side effects | ⏳ |
| 7 | Simulation uses representative/test candidate data | ⏳ |
| 8 | Simulation follows the configured workflow order and transition logic | ⏳ |
| | **Validation** | |
| 9 | Simulation identifies invalid workflow configuration | ⏳ |
| 10 | Simulation identifies missing required round configuration | ⏳ |
| 11 | Simulation identifies invalid transitions | ⏳ |
| 12 | Simulation identifies unreachable rounds | ⏳ |
| 13 | Simulation identifies invalid branching conditions where branching is configured | ⏳ |
| 14 | Simulation identifies missing evaluation criteria where required | ⏳ |
| 15 | Simulation identifies incompatible round configuration | ⏳ |
| 16 | Simulation identifies other execution-blocking errors before publication | ⏳ |
| | **Results** | |
| 17 | Simulation produces a clear execution trace | ⏳ |
| 18 | Each simulated round shows its simulated result/state | ⏳ |
| 19 | Errors and warnings are clearly distinguished | ⏳ |
| 20 | HR can identify where the workflow failed | ⏳ |
| 21 | HR can re-run the simulation after fixing configuration | ⏳ |
| 22 | Successful simulation indicates that the workflow can execute under the tested scenario, but does not automatically publish it | ⏳ |
| | **Safety** | |
| 23 | Simulation cannot modify production candidate records | ⏳ |
| 24 | Simulation cannot cause real candidate notifications | ⏳ |
| 25 | Simulation cannot create real offers/documents | ⏳ |
| 26 | Simulation cannot make real hiring decisions | ⏳ |
| 27 | Simulation cannot bypass existing workflow authorization | ⏳ |
| 28 | Simulation activity is auditable | ⏳ |
| | **Compatibility** | |
| 29 | Existing published workflows continue to operate normally | ⏳ |
| 30 | Draft workflows can still be edited after a simulation | ⏳ |
| 31 | Tests cover successful and failed simulation scenarios | ⏳ |

---

## PH4-O1 — Stage Owners, SLAs & Exception Paths  ·  Wave 2

| # | Acceptance criterion | |
|---|---|---|
| 1 | Each applicable workflow stage can have an assigned owner/team | ⏳ |
| 2 | A stage can have an SLA duration | ⏳ |
| 3 | SLA countdown is based on the appropriate stage-entry timestamp | ⏳ |
| 4 | Overdue stages are clearly identified | ⏳ |
| 5 | HR can see stages approaching or exceeding their SLA | ⏳ |
| 6 | Exceptions can be explicitly recorded | ⏳ |
| 7 | Exception records include reason, owner and timestamp | ⏳ |
| 8 | Authorized users can resolve an exception | ⏳ |
| 9 | Exception handling does not silently alter candidate outcome | ⏳ |
| 10 | SLA/ownership information is visible in appropriate HR views | ⏳ |
| 11 | Existing workflows without SLA configuration continue to work | ⏳ |
| 12 | Stage ownership and exception actions are audited | ⏳ |
| 13 | Tests cover SLA calculation, overdue states, permissions and exception handling | ⏳ |

---

## PH4-A2 — Interview Scheduling + Loops  ·  Wave 3

| # | Acceptance criterion | |
|---|---|---|
| | **Individual Scheduling** | |
| 1 | Authorized HR users can configure interviewer availability | ⏳ |
| 2 | Interviewers can have available time windows | ⏳ |
| 3 | Candidates can select an available slot when self-scheduling is enabled | ⏳ |
| 4 | Candidate timezone is captured and displayed correctly | ⏳ |
| 5 | Scheduled times are stored in a timezone-safe format | ⏳ |
| 6 | A slot cannot be double-booked | ⏳ |
| 7 | Interviewer conflicts are prevented | ⏳ |
| 8 | HR can manually schedule an interview | ⏳ |
| | **Interview Loops** | |
| 9 | HR can create an interview loop for a candidate | ⏳ |
| 10 | A loop can contain multiple interview sessions | ⏳ |
| 11 | Each session can have its own interview round/type | ⏳ |
| 12 | Each session can have its own interviewer(s) | ⏳ |
| 13 | Each session can have its own duration | ⏳ |
| 14 | Multiple sessions can be scheduled for the same candidate on the same day | ⏳ |
| 15 | The system prevents overlapping sessions for the candidate | ⏳ |
| 16 | The system prevents interviewer conflicts across loop sessions | ⏳ |
| 17 | Configurable gaps/buffers can exist between sessions | ⏳ |
| 18 | The candidate receives the complete loop schedule as one coordinated itinerary | ⏳ |
| 19 | Each session maintains its own interview and scorecard relationship | ⏳ |
| 20 | The loop has an overall status | ⏳ |
| 21 | Individual sessions have their own status | ⏳ |
| 22 | HR can view the complete loop from the candidate/application view | ⏳ |
| | **Changes & Calendar** | |
| 23 | Candidate-initiated rescheduling is not supported | ⏳ |
| 24 | Authorized HR users can modify or cancel scheduled sessions/loops | ⏳ |
| 25 | Scheduling changes are audit logged | ⏳ |
| 26 | ICS calendar information can be generated for scheduled sessions | ⏳ |
| 27 | Existing single-interview scheduling continues to work | ⏳ |
| 28 | Existing invite.scheduled_at behavior remains compatible where applicable | ⏳ |
| 29 | Existing email/outbox infrastructure is reused for scheduling notifications | ⏳ |

---

## PH4-O5 — Panel Workload & Calibration  ·  Wave 3

| # | Acceptance criterion | |
|---|---|---|
| 1 | HR can view interviewer workload across upcoming interview sessions | ⏳ |
| 2 | Workload includes scheduled interviews and assigned candidates | ⏳ |
| 3 | Interview loops with multiple interviewers are represented correctly | ⏳ |
| 4 | HR can identify interviewer over-allocation or scheduling conflicts | ⏳ |
| 5 | Workload can be viewed by relevant time period | ⏳ |
| 6 | Interviewers only see candidates and information they are authorized to access | ⏳ |
| 7 | Scorecards continue to use the same frozen round_criteria | ⏳ |
| 8 | Calibration views can compare scoring patterns across interviewers without changing submitted scorecards | ⏳ |
| 9 | HR can identify meaningful scoring differences between interviewers | ⏳ |
| 10 | Submitted scorecards remain immutable | ⏳ |
| 11 | Calibration insights cannot automatically change candidate status or hiring decisions | ⏳ |
| 12 | No AI-generated calibration recommendation can become a hiring decision | ⏳ |
| 13 | Relevant workload/calibration actions are audited | ⏳ |
| 14 | Existing scheduling and scorecard functionality continues to work | ⏳ |
| 15 | Tests cover workload calculation, panel assignments, permissions, and calibration calculations | ⏳ |

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
