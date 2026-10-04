# Test coverage map

Two halves, built at different times and by different methods, so they are kept
apart rather than blended: **Phase 2** below (Groups A–E), then **Phase 3** at
the end of the file.

## Phase 2

Where each Phase 2 item (Groups A–E, and the defects fixed on 2026-09-16) is tested
today, and what is still missing. Built on 2026-09-16 by reading the unit test
functions, smoke check labels, web test names and Playwright specs, and matching
them to the commits that delivered each item; the key claims were then
spot-checked against the files. Where a test does not clearly prove an item the
cell says **partial?**.

**Keep it current.** When a spec, smoke or unit test closes a gap below, update
its row in the same change.

**Not green today.** Four smokes cited here fail on `main` for reasons that
predate this map, so their cells describe intent, not a passing check:
`smoke_group_a_scorecard_retry` (needs feedback_billing and storage running),
`smoke_group_b_api`, `smoke_group_b_ledger` and `smoke_group_b_requisitions`.

Phase 3 work (application source, JD versions, requisition approval, scheduled
publishing, reapplication, drafts) is mapped in **Phase 3** at the end of this
file, not in the Group A–E tables below.


Paths used below:
- **U** = `services/data_gateway/tests/unit/`
- **S** = `services/data_gateway/tests/integration/`
- **W** = `web/src/__tests__/`
- **E** = `web/e2e/`

What is in the browser suite today (`E`):

- `auth.spec.ts` — sign-in, role access.
- `opening.spec.ts` — create an opening, land on its dashboard, title required.
- `workflow.spec.ts` — a template draws the journey and its gates, publish is blocked with no questions, an interview-only workflow publishes and becomes read-only (with a server-side 409).
- `bulk-upload.spec.ts` — a batch of CVs filed into one opening, and the file that could not be read named on screen.
- `review-round.spec.ts` — the round a person judges: the checklist, a hold that is not a rejection, a release, then a pass.
- `company-board.spec.ts` — the company super admin's read-only hiring board.
- `same-person-two-openings.spec.ts` — one applicant, two applications, and a move on one leaving the other alone.
- `opening-dashboard.spec.ts` — an opening with a real held candidate in it.
- `exam-from-dashboard.spec.ts` — a candidate who never opens the invitation email signs in, starts the assessment from their own applications page, passes it, and the emailed link they never used is dead afterwards.
- `coding-round.spec.ts` — a candidate writes Python, it is executed on the sandbox against hidden tests, and the result advances them. Skips with instructions when no code runner is up.
- `interview-scheduling.spec.ts` (PH4-A2, PH4-O5) — the interviewer publishes availability; HR builds a loop the candidate books themselves, and a fixed time outside that availability is refused with the override offered, not taken; the candidate is emailed, sees only times inside the window, books one, and then has no control that moves or cancels it; the interviewer sees the booking; HR's panel shows the load.
- **The main journeys**, each one run end to end against the local stack:
  - `journey-hire.spec.ts` — a candidate applies from the public page, is scored, is shortlisted by a person, sits the exam from the emailed link, passes, and is hired with a reason. Hiring without a reason is refused.
  - `journey-held.spec.ts` — the same candidate fails instead. They are held, never rejected; the queue says why; a person can let them continue; and the rejection, when it comes, is a person’s with a reason on the ledger.
  - `journey-candidate-view.spec.ts` — that held candidate claims the account their confirmation email offered and reads their own status: "Under review", in words, with no score, percentage or threshold anywhere on the page.

The journeys need `AI_FAKE_MODE` and `TEST_HOOKS_ENABLED` on the local data_gateway (see README.md): the first makes scoring and question generation free and repeatable, the second runs the background passes on demand instead of on their timers.

---

## Group A: Reliability and notifications

| Item | What must hold | Unit | Smoke | Web unit | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|---|
| A1 | Failed or unfinished background work is retried: unscored applicants, embeddings, interviews with no scorecard, scorecards with no PDF | U `test_group_a_reliability.py`: `test_backoff_grows_and_is_capped`, `test_record_failure_parks_the_row_at_the_limit`, `test_only_interviews_the_worker_would_have_scored_are_retried`, `test_a_pdf_render_failure_is_retried…`, `test_an_unavailable_service_stops_the_pass_without_charging_anyone`; `services/feedback_billing/tests/test_scorecard_pdf_endpoint.py` | S `smoke_group_a_reconciliation.py` ("ats row retried once due", "embeddings written for every applicant…"); S `smoke_group_a_scorecard_retry.py` ("the interview whose scoring was lost now has a scorecard", "a scorecard whose PDF never landed now has one"). Needs a real LLM and MinIO | — | — | covered. The scorecard-retry smoke needs an LLM and storage, so it is probably not run routinely. |
| A2 | Candidates get reminders before exam and interview deadlines | U `test_group_a_reliability.py`: `test_reminder_windows_are_bands_not_nested_ranges`, `test_exam_reminder_dedupe_key_names_the_window…`, `test_rescheduling_rearms_both_reminder_windows`, `test_the_workflow_reminder_switch_is_honoured` | S `smoke_group_a_reminders.py` ("24h exam reminder sent…", "a deadline 40 min out gets the 1h touch only", "interview reminder sent", "a rescheduled interview is reminded about its new slot") | — | — | covered |
| A3 | The candidate lifecycle emails exist in EN, HI and TE, including the expiry warning and the no-show email | U `test_group_a_reliability.py`: `test_template_renders_in_every_day_one_language` (6 templates × en/hi/te), `test_template_is_actually_localised`, `test_the_no_show_email_states_facts_without_blame`, `test_the_expiry_warning_is_not_a_reminder`, `test_results_ready_carries_no_score` | S `smoke_group_a_reminders.py` ("a missed slot sends the no-show follow-up…", "lapsed link produces an expiry notice", "results email uses the invite language (te)", "every email has a subject") | — | — | covered at template and sweep level. Hindi and Telugu delivery is checked in a smoke only for results-ready. Adding an HI no-show or expiry case to the smoke is cheap. |
| A4 | HR is notified of events they are waiting for (exam submitted, interview completed, link lapsed, bulk upload finished) | U `test_runner_wiring.py`: `test_an_exam_submission_notifies_whoever_sent_the_link`, `…_is_atomic_and_announced_once`, `…_states_a_score_not_a_decision`; U `test_group_a_reliability.py`: `test_completion_notifies_hr_without_anyone_opening_a_page`, `test_batch_notification_fires_only_when_the_last_row_lands`, `test_hr_hears_about_a_lapse…` | S `smoke_group_a_reminders.py` ("HR owner notified about the lapsed link", "completion announced to HR once", "the batch is reported once"); S `smoke_group_e_bulk.py` ("the uploader is notified once…") | W `HRConsole.test.tsx` (feed renders notifications), partial | E `journey-hire.spec.ts` / E `journey-held.spec.ts`: the exam invitation reaches the candidate’s inbox and its link opens a live exam | **Gap (smoke):** the exam-submitted notice has no DB smoke. It is only source- or mocked-level in `test_runner_wiring`. **Gap (browser):** HR sees the notice in the bell after a candidate submits — the journeys prove the candidate-facing email, not the bell. |
| A5 | The HR console refreshes itself when data changes | — | — | W `LiveRefresh.test.tsx` ("refreshes exam results and everything downstream when an exam is submitted", "does not refresh again for the same notification…"); W `HRConsole.test.tsx` (bell and feed cache split) | — | **Gap (browser):** an open HR page (pipeline or decision queue) updates without a reload after a background event. Unit-level mapping is covered. |
| A6 | Scheduled jobs catch up after the container or Space is suspended | U `test_group_a_reliability.py`: `test_catchup_claims_only_a_window_that_was_really_missed`, `test_a_failed_run_is_retried_within_the_hour…`, `test_job_is_skipped_when_another_instance_holds_the_claim`, `test_the_ops_endpoint_flags_a_job_that_went_quiet` | S `smoke_group_a_scheduler.py` ("catch-up runs a window that was really missed", "two instances waking at once run the job once", "history keeps every run"); S `smoke_group_a_reconciliation.py` ("overdue job runs again") | — | — | covered. There is no web test for the platform-owner scheduled-jobs view (`GET /admin/scheduled-jobs`). That is optional web unit. |

## Group B: Requisitions, ledger and applications

| Item | What must hold | Unit | Smoke | Web unit | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|---|
| B1 | An opening belongs to a company and has an owner, a target and a closing date | U `test_requisition_ownership.py`: `test_the_owner_rule_is_company_role_and_active`, `test_a_closing_date_in_the_past_is_refused`, `test_the_patch_checks_a_new_owner_before_writing` | S `smoke_group_b_ownership.py` ("an owner who does not exist is refused", "a closing date in the past is refused on create/…on update"); S `smoke_group_b_api.py` ("only this company", "target_hires stored") | W `OpeningDetails.test.tsx` ("offers the HR team as owners", "sends only what changed", "sends a closing date as the end of that day") | E `opening.spec.ts` "HR creates an opening…": sets title, level and target. **partial**: no owner, no closing date, no edit | **Gap (browser, small):** set owner and closing date on create, then edit them in "Opening details" and see them in the header. |
| B2 | Every candidate move is written to an append-only audit ledger | U `test_group_b_ledger.py`: `test_a_round_move_updates_the_round_and_records_where_from_and_to`, `test_the_ledger_refuses_edits_and_deletes`, `test_the_history_endpoint_names_only_people`; U `test_group_b_requisitions.py`: `test_transition_writes_status_and_ledger_together` | S `smoke_group_b_ledger.py` ("an entry cannot be edited", "an entry cannot be deleted while its application exists", "advancing between rounds is recorded…"); S `smoke_group_b_requisitions.py` ("ledger records the human actor") | W `StageHistory.test.ts`; W `CandidateDrawer.test.tsx` ("carries no action that would bypass the transition ledger") | — | covered for the invariant (smoke is the right level). **Gap (browser, low):** the candidate drawer's history shows "automatic" and person moves after real actions. |
| B3 | Existing data is backfilled into requisitions, and a review screen lets HR split, merge and confirm | U `test_group_b_review.py`: `test_split_moves_exactly_the_candidates_named`, `test_a_merge_moves_everyone_records_each_and_retires_the_source`, `test_confirm_is_its_own_audited_action`; U `test_group_b_requisitions.py` (`test_normalise_title`, merge tests) | S `smoke_group_b_requisitions.py` + `seed_pre_group_b.py` ("four spellings collapsed into one Python opening"); S `smoke_group_b_api.py` ("review lists the backfilled requisitions"); S `smoke_group_b_review.py`; S `smoke_group_b_split.py` | W `RequisitionReview.test.tsx` (split ticks, merge asks twice, confirm action) | — | covered below the browser. **Gap (browser, medium):** the review screen splits two candidates out, merges two openings (with the double confirm) and confirms an opening. This needs seeded backfill-shaped data. |
| B4 | One applicant record per person per company, with many enrolments (D-06) | U `test_group_b_identity.py`: `test_matching_is_by_company_and_ignores_case_and_spacing`, `test_the_runtime_index_is_the_migrations_index`, `test_a_cv_address_owned_by_someone_else_is_kept_aside`; U `test_group_b_requisitions.py`: `test_merge_*` | S `smoke_group_b_identity.py` ("a second Asha at the same company is refused", "a returning person gets a new application, not a new record", "the last blocking merge switches the rule on"); S `smoke_group_e_intake.py` ("no duplicate applicant", "same applicant record… now with two enrolments") | W `Applicants.test.tsx` "acts per opening for someone with several applications", partial | — | covered (DB invariant, smoke is correct). |
| B5 | Application-specific fields live on the enrolment, and every screen reads applications (D-06a) | U `test_group_b_enrolments_everywhere.py`: `test_an_application_is_scored_against_its_own_opening`, `test_a_returning_candidates_cv_gets_its_own_key`; U `test_group_b_ledger.py`: `test_a_named_application_is_guarded_on_its_own_status`; U `test_group_e_foundations.py`: `test_enrolling_pins_the_submitted_cv` | S `smoke_group_b_applications.py` ("the board has a row per application", "her Python exam shows on Python and not on Nurse", "a decision that does not name an application is refused"); S `smoke_group_b_enrolments_everywhere.py`; S `smoke_group_e_foundations.py` ("each application records the CV it was submitted with") | W `PipelineBoard.test.tsx` ("shows a person once per application…"); W `HRInterviews.test.tsx` ("offers a person once per opening…"); W `Applicants.test.tsx`; W `CandidateDrawer.test.tsx` ("shows that application") | E `same-person-two-openings.spec.ts`: one person applies to two openings, is listed once with an application each, and shortlisting for one leaves the other at "new" with no round started | covered. |

## Group C: Workflow engine

| Item | What must hold | Unit | Smoke | Web unit | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|---|
| C1 | Workflows are versioned; a published version cannot change (DB triggers); only one live version per opening | U `test_group_c_workflows.py`: `test_errors_block_publication`, partial (the app-level guard). No unit for the triggers, which is correct | S `smoke_group_c_immutability.py` (12 checks: raw SQL refused on settings, thresholds, criteria, added rounds, reopening, delete; "one opening cannot have two live workflows"); S `smoke_group_c_workflows.py` ("a published workflow cannot gain a round", "v1 archived rather than deleted"); S `smoke_group_c_api.py` ("adding a round to a published workflow -> 409", "clone is v2 and a draft") | W `WorkflowBuilder.test.tsx` ("explains that editing means a new version…", "clones rather than unlocking") | E `workflow.spec.ts` "an interview-only workflow publishes and becomes read-only" (UI read-only plus API PATCH 409) | covered. **Gap (browser, small):** "Edit as new version" creates v2 as a draft while v1 stays live. |
| C2 | A round has order, title, type, threshold, time limit, deadline and a next-round link | U `test_group_c_workflows.py`: `test_valid_linear_chain_passes`, `test_cycle_is_refused`, `test_unreachable_round_is_refused`, `test_scored_round_without_a_threshold_is_refused`; U `test_workflow_templates.py`: `test_timings_thresholds_and_settings_are_prefilled` | S `smoke_group_c_api.py` ("four rounds, in order", "the chain wired itself", "PUT order -> 200", "the chain healed around the gap", "threshold updated"); S `smoke_group_c_workflows.py` ("clone rewired the chain to its OWN rounds"); S `smoke_group_d_templates.py` ("…thresholds, time limits and deadlines filled in") | W `WorkflowBuilder.test.tsx` ("renders the rounds in order…", "sends the complete new order when a round moves") | — | covered for order, chain and threshold. **partial?:** `time_limit` and `deadline_days` are only checked through templates and `test_group_d_builder`; no smoke sets or edits them over the API directly. Smoke is the right level. |
| C3 | Each round has role-specific evaluation criteria, and question generation uses the round's criteria | U `test_group_c_round_generation.py`: `test_a_rounds_rubric_is_returned_with_its_provenance`, `test_an_exam_used_by_exactly_one_round_borrows_its_rubric`, `test_both_authoring_endpoints_send_the_rounds_rubric`; U `test_group_c_frozen_rubric.py`; `services/feedback_billing/tests/test_exam_generator.py` (round competencies reach the prompt) | S `smoke_group_c_api.py` ("criteria frozen with anchors"); S `smoke_group_c_workflows.py` ("no foreign key ties criteria to a live profile"); S `smoke_group_c_worker_rubric.py` ("the 10-turn plan probes only what the round promised"). **No smoke for exam/coding generation from round criteria** | W `WorkflowBuilder.test.tsx` ("copies the anchors and probes onto the round, not just the name") | E `review-round.spec.ts`: the review round’s own competencies are shown to the reviewer as the checklist, with their weights | **partial:** round-criteria exam generation is still unit-only (mocked LLM). The checklist a reviewer sees is covered. |
| C4 | Four round types, including human_review with a reviewer's verdict | U `test_group_c_workflows.py`: `test_exactly_four_round_kinds_ship`, `test_human_review_needs_no_threshold`; U `test_group_c_human_review.py`: `test_a_scored_round_cannot_be_passed_by_hand`, `test_failing_a_review_holds_rather_than_rejects`, `test_the_verdict_is_recorded_as_a_persons` | S `smoke_group_c_human_review.py` ("passing the review advances the candidate", "not passing holds… (D-05)"); S `smoke_group_c_workflows.py` ("only the four agreed round kinds are legal") | W `DecisionQueue.test.tsx`: **partial?** (`recordRoundReview` is mocked but no test name covers the review card, checklist or "Passes this round" / "Hold for a decision") | E `review-round.spec.ts`: the queue says "Waiting for review", "Hold for a decision" holds without writing any rejection, a person can release the hold, and "Passes this round" finishes the workflow — the live round result recording graded_by=human | covered. |
| C5 | A candidate who applies is enrolled into the published workflow automatically; the human shortlist gate still holds | U `test_group_c_runner.py`: `test_reapplying_is_a_noop`, `test_enrolling_without_a_published_workflow_still_keeps_the_candidate`; U `test_runner_wiring.py`: `test_shortlisting_an_enrolment_calls_the_runner`, `test_the_enrolment_gate_ignores_a_re_save`; U `test_group_e_foundations.py`: `test_every_intake_path_pins_the_cv` (source check) | S `smoke_group_c_runner.py` ("each enrolment pinned to the published workflow version", "nobody gets a round before the human shortlist gate"). It calls `enrol_applicant` directly. S `smoke_group_e_intake.py` checks "an enrolment was created" but **not** its `workflow_id` | — | E `journey-hire.spec.ts`: applying through the public form enrols the candidate into the published workflow, and the shortlist gate still holds — the decision queue is empty until a person shortlists | **partial?:** no smoke asserts that the public-apply or HR-upload endpoint attaches the published workflow version. **Gap (smoke):** add a `workflow_id`/version check to `smoke_group_e_intake`. Browser: covered by the hire journey. |
| C6 | An event-driven runner advances candidates on application, round submitted and interview scored | U `test_runner_wiring.py`: `test_submitting_a_round_calls_the_runner`, `test_the_exam_result_is_recorded_before_the_commit`; U `test_group_c_runner.py`: `test_a_scored_interview_is_recorded_as_its_rounds_result`, `test_only_workflow_issued_closed_invites_are_recorded`, `test_the_workflow_stage_runs_after_the_completion_stage` | S `smoke_group_c_runner.py` ("90% clears a 60% threshold and advances", "interview score advances to the human round", "replaying an old round event does not move the candidate"); S `smoke_group_c_interview_results.py` ("the next interview round gets a working invite") | — | E `journey-hire.spec.ts`: the candidate takes the exam from the emailed link, passes, and appears in HR’s decision queue without anyone moving them E `coding-round.spec.ts`: a coding round too — the candidate’s own Python is executed on the sandbox against hidden tests, and the pass advances them | covered at runner level and in the browser. |
| C7 | Candidates below a threshold are held, never rejected, and reach a final human decision queue | U `test_group_c_runner.py`: `test_hold_is_not_in_the_terminal_set`, `test_outcome_actions_contain_no_terminal_state`, `test_release_hold_has_no_automated_caller`; U `test_runner_wiring.py`: `test_the_runner_still_has_no_path_to_rejected` | S `smoke_group_c_runner.py` ("20% against a 60% threshold is held", "held and completed candidates appear in ONE queue", "the runner rejected nobody, at any point"); S `smoke_group_c_interview_results.py` ("…held, not rejected (D-05)") | W `DecisionQueue.test.tsx` ("shows held and finished candidates together", "says the hold was not a rejection…", "offers a release only where there is a hold") | E `journey-held.spec.ts`: a candidate who fails the round is held with a reason, sits in the queue marked held, is never rejected by the system, and the screen offers "Let them continue" | covered. The release path is offered and asserted as present; a browser test that actually releases a hold and watches the candidate resume is still missing. |
| C8 | AI interview rounds are scored in two layers: frozen round criteria decide, four canonical axes are stored separately | U `test_group_c_frozen_rubric.py` (`test_weights_are_renormalised…`, `test_criteria_composite_is_a_weighted_mean`); U `test_group_c_runner.py`: `test_a_frozen_rubric_breakdown_decides_instead_of_the_headline`, `test_criteria_are_used_only_when_they_cover_the_round_exactly` | S `smoke_group_c_runner.py` ("two-layer score stored: criteria AND axes"); S `smoke_group_c_interview_results.py` (commit: composite 5.0 advances on criteria 71%); S `smoke_group_c_worker_rubric.py` | W `CandidateDrawer.test.tsx` ("shows the criterion behind a score, and its evidence", "separates the frozen axes from the criteria that decided progression") | — | covered. **Gap (browser, low):** the drawer shows criteria and axes for a scored interview. This needs a seeded scorecard. |
| C9 | Each workflow has automation settings (auto_score_on_apply, shortlist_ats_threshold, reminders, auto-advance) | U `test_group_c_settings.py` (all 7); U `test_group_a_reliability.py`: `test_the_workflow_reminder_switch_is_honoured`; `shared/agents/tests/test_panel_and_watchers.py`: `test_candidates_over_the_shortlist_bar_are_offered_not_advanced` | S `smoke_group_c_settings.py` ("the opening that turned scoring off is not, and pays for nothing", "nobody was shortlisted by the score (D-05)"); S `smoke_group_c_runner.py` ("auto-advance off stops progression"); S `smoke_group_a_reminders.py` ("workflow with reminders OFF…"); S `smoke_group_c_api.py` ("PATCH settings -> 200", "an unknown setting is ignored") | W `WorkflowBuilder.test.tsx`: fixture carries settings, but **partial?** (no test name exercises toggling a setting) | — | **Gap (web unit):** toggling each setting sends `updateSettings` with the right key and value. **Gap (browser, low):** toggling on a draft persists after a reload. |

## Group D: Workflow builder UI

| Item | What must hold | Unit | Smoke | Web unit | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|---|
| D1 | The visual canvas shows the candidate journey, the shortlist gate, the hold path and who decides each round | — | — | W `workflowCanvasJourney.test.tsx` ("starts at the application, behind a human shortlist gate", "draws the hold path back to the decision queue", "says which rounds are automated and which a person decides") | E `workflow.spec.ts` "a template draws the journey with its human gates" (`shortlist-gate`, `hold-path`, "A person decides") | covered |
| D2 | The round panel works: changing type clears fields, human review has a reviewer checklist, the criteria picker works | U `test_group_d_builder.py`: `test_turning_a_test_into_a_human_review_clears_what_cannot_apply`, `test_nothing_is_invented_for_the_new_type`, `test_the_round_patch_accepts_a_type_change`; U `test_phase2_pipeline_defects.py`: `test_a_rounds_criteria_carry_the_ids_the_builder_keys_on` | S `smoke_group_d_round_type.py` ("the API accepts a type change", "…and clears the exam, threshold and time limit…", "…keeping its competencies, now the reviewer's checklist"); S `smoke_phase2_pipeline_defects.py` ("…and sending them back as the builder does is accepted") | W `workflowCanvasJourney.test.tsx` ("lets a human review carry a checklist…"); W `WorkflowBuilder.test.tsx` ("offers only published exams…"). **partial?:** no web test changes the type in the panel or ticks a criterion and checks the PATCH body | — | **Gap (web unit):** change the type in RoundInspector, then tick or untick a competency and assert the payload keeps ids. **Gap (browser):** change a round's type on a draft and see fields clear; tick criteria and see them persist after a reload. This is exactly the 2026-09-13 defect. |
| D3 | A live competency coverage check gives a verdict | U `test_group_c_workflows.py`: `test_unassessed_competency_warns`, `test_three_rounds_is_flagged_as_probably_accidental`, `test_warnings_do_not_block_publication`; U `test_group_d_builder.py`: `test_coverage_as_a_share_of_the_roles_weight` | S `smoke_group_d_round_type.py` ("validation reports the share of the role's weight…"); S `smoke_group_c_workflows.py` ("coverage warns about the unassessed competency") | W `WorkflowBuilder.test.tsx` ("names the competency nothing measures"); W `workflowCanvasJourney.test.tsx` ("words the weighted share…") | — | covered below the browser. **Gap (browser):** untick a competency and see the coverage verdict change live (strong, reasonable or thin). |
| D4 | Starter templates are built against the role profile | U `test_workflow_templates.py` (12 tests: `test_the_three_templates_have_the_specified_shapes`, `test_every_template_ends_at_a_human`, `test_the_recommendation_follows_the_occupational_family`) | S `smoke_group_d_templates.py` ("a software role is steered to Technical", "a nursing role is steered to Non-technical", "the full rubric was frozen…") | W `WorkflowBuilder.test.tsx` ("offers templates built for this role, marking the one that suits it", "creates a template as one draft on the server") | E `workflow.spec.ts` (all 3 tests pick a template; test 1 checks all three are offered) | covered. The browser check that the recommended template is marked is optional. |
| D5 | Draft, preview, validate, publish; publishing attaches waiting candidates | U `test_group_d_publish_waiting.py` (4 tests); U `test_group_c_workflows.py`: `test_errors_block_publication`; U `test_phase2_pipeline_defects.py`: `test_validate_checks_exam_round_readiness` | S `smoke_group_d_publish_waiting.py` ("the waiting applicants join the version just published", "the one a person already shortlisted starts the first round", "publishing shortlisted nobody itself"); S `smoke_group_c_api.py` ("publishing it -> 422 with the reasons") | W `workflowLifecycle.test.tsx` ("asks for a preview first, then publish once it validates", "says who starts and who waits, before it happens", "never shows candidates a score or threshold…"); W `WorkflowBuilder.test.tsx` ("asks before publishing…", "disables the header Publish on the same condition…") | E `workflow.spec.ts` "publishing is blocked while test rounds have no questions"; "an interview-only workflow publishes and becomes read-only" | **partial (browser):** the preview step ("Preview as a candidate") and the waiting-candidates wording in the publish confirmation are not exercised. **Gap (browser):** an opening with applicants and no live workflow shows the warning, and publishing reports how many joined and started. |

## Group E: HR surfaces and intake

| Item | What must hold | Unit | Smoke | Web unit | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|---|
| E1 | Each requisition has an HR dashboard | U `test_group_e_dashboard.py` (13 tests: attention rules, `test_manual_steps_are_the_human_gates_with_counts`, `test_stage_timing_follows_the_live_workflow`, `test_every_query_is_scoped…`) | S `smoke_group_e_dashboard.py` (21 checks: progress, funnel per workflow, held pool, needs attention, activity, cross-company) | W `RequisitionDashboard.test.tsx` (13 tests) | E `opening-dashboard.spec.ts`: an opening with a real held candidate shows the held card naming them and the round and bar they fell under, plus the funnel, the manual steps, the timing and the attention panel | covered. |
| E2 | The final decision queue requires a reason, uses one guarded writer, and is audited | U `test_group_e_decisions.py` (`test_what_is_refused`, `test_no_reason_no_decision`, `test_a_decision_is_recorded_against_the_person`, `test_the_generic_mover_hands_hire_and_reject_to_the_guarded_writer`); U `test_group_e_foundations.py`: `test_the_decision_queue_uses_the_shared_definition` | S `smoke_group_e_decisions.py` (26 checks: "no decision without a reason", "a hire is refused while they are still in an automated round", "the audit log records the same decision…") | W `DecisionQueue.test.tsx` ("will not record a hire or reject without a reason", "does not hire on the first click", "opens the evidence for this application…") | E `journey-hire.spec.ts`: hiring is refused until a reason is written, then recorded — the candidate leaves the queue and the stage ledger names the person and their reason. E `journey-held.spec.ts`: the same for a rejection | covered. |
| E3 | The company super admin has a roll-up hiring board with computed health | U `test_group_e_company_board.py` (12 tests, every health band; `test_the_board_routes_are_read_only_and_for_the_company_admin`) | S `smoke_group_e_company_board.py` (12 checks, one opening per band; cross-company 404; no write method) | W `HiringBoard.test.tsx` (5 tests); W `RequisitionDashboard.test.tsx` ("gives the company super admin the same numbers, with nothing to change (E3)") | E `company-board.spec.ts`: a super admin opens /superadmin/board, sees an unpublished opening banded as such, filters by band, and the page offers no Hire/Reject/Shortlist/Publish/Close/Delete control — only a link to read each dashboard | covered. E `auth.spec.ts` still covers the refusal for an HR manager. |
| E4 | Public apply: consent first, a duplicate reveals nothing, needs a published workflow, applicant chooses email language | U `test_group_e_public_apply.py` (6 tests: `test_an_opening_without_a_published_workflow_takes_no_applications`, `test_nothing_stored_is_echoed…`, `test_the_applicant_chooses_the_language_of_their_emails`); U `test_ph3_*` (drafts, publish gate) | S `smoke_group_e_intake.py` (56 checks: "applying without consent -> 422 / and nothing was stored", "…without echoing anything stored about them", no-workflow opening refused, "an application with emails in Telugu is accepted", "an unsupported language -> 422"); S `smoke_ph3_apply.py` | W `PublicApply.test.tsx` (consent gating, language, repeat application as reassurance); W `PublicApplySteps.test.tsx`; W `publicApplyDrop.test.ts` | E `journey-hire.spec.ts` / E `journey-held.spec.ts` / E `journey-candidate-view.spec.ts`: an unauthenticated candidate opens the public link, cannot submit before consent, uploads a CV and sees the confirmation | **Gap (browser):** a duplicate submission showing the neutral message, an applicant choosing Hindi and receiving Hindi email, and a link for an opening with no published workflow showing "not accepting applications". |
| E5 | Bulk resume ingestion runs in the background and shows progress | U `test_group_e_bulk_ingest.py` (15 tests: `test_the_request_stores_and_queues_but_reads_nothing`, `test_bad_files_are_refused_one_by_one…`, `test_a_storage_error_is_retried_then_given_up_on`, `test_the_loop_drains_only_while_work_is_moving`) | S `smoke_group_e_bulk.py` ("the upload is accepted straight away", "progress: all read, three being scored, three failed", "sixty files are accepted in one request… and drain across passes") | W `Applicants.test.tsx` ("will not upload without an opening", "follows the accepted batch as it is read, listing every failed file") | E `bulk-upload.spec.ts`: HR picks the opening, uploads four PDFs of which one is unreadable, and the panel reports "4 of 4 files read", 3 added, 1 failed, naming the file it could not read — and exactly three enrolments exist | covered. |
| E6 | Per-requisition watchers flag stalled rounds, candidates ready to shortlist, and funnel health | U `test_group_e_watchers.py` (8 tests: `test_round_stalls_use_the_rounds_deadline…`, `test_the_funnel_is_grouped_by_opening_not_title`, `test_attention_can_be_read_for_one_opening`); U `test_group_c_settings.py` (shortlist bar); `shared/agents/tests/test_panel_and_watchers.py` (`test_a_stalled_round_names_the_opening_and_the_round`, `test_a_lossy_funnel_links_to_its_opening`, `test_candidates_over_the_shortlist_bar_are_offered_not_advanced`) | S `smoke_group_e_watchers.py` (12 checks: "one stalled round is found, in the live opening only", "the alert names the opening and the round", "…leaving out the held, the human review, the closed…"); S `smoke_group_c_settings.py` ("candidates over the bar are surfaced for confirmation") | W `AttentionPanel.test.tsx` (generic rendering); W `RequisitionDashboard.test.tsx` (needs attention) | — | covered below the browser. **Gap (browser, low):** the per-opening attention panel shows a seeded stalled-round finding with a working link. This needs time-shifted seed data. |

## Defects fixed 2026-09-16 (`96b51bf`, "fix(phase2): defects found by the whole-pipeline test")

| Defect | What must hold | Unit (`U test_phase2_pipeline_defects.py` unless noted) | Smoke | Web unit | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|---|
| Draft or empty exam round blocks publish | A workflow cannot go live with an exam round candidates cannot open | `test_a_draft_exam_round_is_a_blocking_error`, `test_an_empty_exam_round_is_a_blocking_error`, `test_validate_checks_exam_round_readiness`, `test_unpublishing_a_round_used_by_a_live_workflow_is_refused` | S `smoke_phase2_pipeline_defects.py` ("validation names the draft exam round as blocking", "publishing it is refused (422)", "unpublishing the round the live workflow uses is refused (409)") | W `WorkflowBuilder.test.tsx` ("offers only published exams, and only rounds that are published and have questions", "disables the header Publish…") | E `workflow.spec.ts` "publishing is blocked while test rounds have no questions". **partial**: covers rounds with no exam attached, not a draft exam attached | **Gap (browser, small):** attach a draft exam in the picker and see it disabled, with the link to the exam editor. |
| Runner mints no link for a non-live exam round | No broken link is emailed; the owner is told | `test_a_draft_exam_round_gets_no_link_and_the_owner_is_told` | S `smoke_phase2_pipeline_defects.py` ("no exam link is minted for a draft exam round", "…and none is emailed", "the workflow owner is told what to fix") | — | — | covered (background, smoke is correct). |
| Candidate emails use the candidate's language | Shortlist, decision and invite emails follow the applicant's chosen language | `test_the_exam_invite_is_sent_in_the_candidates_language`, `test_the_workflow_interview_invite_carries_the_candidates_language`, `test_candidate_language_reads_the_linked_account`, `test_candidate_emails_no_longer_hard_code_english` | S `smoke_phase2_pipeline_defects.py` ("the shortlist email goes out in Hindi", "the exam invite goes out in Hindi") | — | — | covered. The decision email (hire/reject) language is **partial?**: the source-level unit only, no smoke check. Add it to the smoke. |
| Public apply wakes the reconciler | A public application is scored within seconds, not about 8 minutes | `test_a_public_application_wakes_the_reconciler_after_it_is_saved` (mocked or source-level) | — | — | — | **partial:** no smoke asserts the wake. A unit test is probably enough; optionally a smoke that times `pending_enrichment` clearing after apply. |
| Application-received email text | Plain text has no HTML; HI and TE name the company in their own word order | `test_the_plain_text_part_carries_no_html`, `test_indian_language_copy_has_no_english_fragment`, `test_english_copy_still_names_the_company`, `test_without_a_company_no_dangling_words_remain` | — | — | — | covered (unit is the right level for a template). |
| Criteria id/name shape in `get_workflow` | Ticked competencies show as ticked, and saving keeps ids | `test_a_rounds_criteria_carry_the_ids_the_builder_keys_on` | S `smoke_phase2_pipeline_defects.py` ("a round's criteria…", "…and sending them back as the builder does is accepted") | **partial?**: no web test renders a fetched round and asserts the boxes are ticked | — | **Gap (web unit + browser):** open a saved round and see its criteria ticked; tick one more, reload, and all remain. This is the regression users actually hit. |
| Exam page "Round 1: Round 1"; hard-coded language fact | Clean round label | — | — | W `PublicExam.test.tsx` (4 tests) | — | covered |

---

## Browser e2e backlog

Behaviours a person sees or does that `web/e2e` does not yet cover, in priority order within each group.

Prerequisites now in place: `AI_FAKE_MODE` and the test hooks (see README).

### 1. Main journeys (end to end, across roles)
1. **Apply to decision (happy path):** HR creates an opening, applies a template and publishes (exam round with questions). A candidate applies through the public link with consent and appears on the pipeline. HR shortlists and the candidate is enrolled into round 1. The candidate opens the exam link, submits, and the result shows. HR sees the exam-submitted notice and the candidate advanced (or held). The candidate reaches the decision queue, HR hires with a reason, and the candidate shows as Decided. This covers C5, C6, A4, E4, E2 and D-05 in one run.
2. **Below threshold is held, not rejected:** the same journey with a failing exam score. The candidate appears in the decision queue as "held" with the reason, never "rejected". HR releases the hold, or rejects with a reason (C7, E2).
3. **Candidate-side view:** the candidate signs in and sees their application status in words ("Under review"), never a score or "failed".

### 2. Groups B, C and E
1. **E2 decision queue:** hire is blocked without a reason; the double-click confirmation; the decided candidate leaves the queue.
2. **E4 public apply:** consent gates submit; language choice; a repeat submission shows the neutral message; an opening with no published workflow shows "not accepting applications".
3. **C4 human_review:** a reviewer sees the checklist and uses "Passes this round" and "Hold for a decision".
4. **E5 bulk upload:** the opening picker is required; the progress panel counts up and names the failed file.
5. **E3 hiring board:** a super admin sees health chips, filters by band, and opens a read-only dashboard with no edit controls.
6. **E1 dashboard with data:** counts, held pool, needs attention, and links to the workflow and decision queue.
7. **B5 two applications:** one person applied to two openings appears once per application; a decision on one leaves the other untouched.
8. **B3 review screen:** split by ticked candidates, merge with double confirmation, confirm.
9. **B1 opening details:** owner and closing date on create, then edited, and the header reflects the change.
10. **C1 new version:** "Edit as new version" creates a v2 draft while v1 stays live.
11. **E6 attention (low):** a stalled-round finding on the opening's panel links through.
12. **C8 drawer (low):** criteria and evidence shown separately from the axes.

### 3. Groups A and D
1. **D2 round panel:** change a round's type on a draft and see the exam, threshold and time limit clear; tick criteria, reload, and they stay ticked (the 09-13 criteria-shape regression).
2. **D5 lifecycle:** "Preview as a candidate" shows each round in candidate wording; the no-live-workflow warning shows the waiting count; publishing reports joined and started counts.
3. **Draft exam in the picker:** a draft or empty exam is disabled, with the link to the exam editor.
4. **D3 coverage:** unticking a competency changes the verdict or warning live.
5. **A5 live refresh:** with the decision queue or pipeline open, a background event (candidate submits) appears without a manual reload.
6. **A4 notification bell:** the exam-submitted notice appears for the workflow owner.
7. **C9 settings (low):** toggling reminders or auto-score on a draft persists after a reload.

Background and DB items with no browser need (A1, A2, A3, A6, B2 and B4 invariants, C1 triggers, C8 scoring). Gaps there belong in smoke:
- the exam-submitted notice (A4)
- the enrolment `workflow_id` after public apply or HR upload (C5)
- the decision email's language
- exam generation from round criteria with a stubbed LLM (C3)
- a direct API set and edit of round `time_limit` and `deadline_days` (C2)

---

# Phase 3

Added 2026-10-04, when the browser suite caught up with the Phase 3 backend.
Built the other way round from the Phase 2 half above: by reading the specs and
the `test_ph3_*` files and matching them to item codes the specs already cite in
their headers, so every cell below names a file that exists.

**One deliberate difference in shape.** There is no *Web unit* column. Phase 2's
was built by surveying `web/src/__tests__/`; that survey has not been redone for
Phase 3, and a column of `—` would read as "none exists" rather than "not
looked at". Component-level cover for these screens may well exist — it is
simply not claimed here.

**Why Phase 3 needed browser tests at all.** The backend landed first and landed
well: 254 unit tests across seven `test_ph3_*` files. But several Phase 3
acceptance criteria are phrased as *"organizations can define…"* and
*"authorized users can override…"*, and for a while those were true of the API
and not of the product — no screen set the waiting period, no control called the
override. A unit test cannot tell the difference. Each spec below exists because
it asserts something structurally out of reach below the browser: two tabs at
once, a real wait for a scheduled job, or one rendered screen compared against
another.

What is in the browser suite for Phase 3 (`E`):

- `apply-source.spec.ts` — the same opening posted to several places with tagged links, and each application filed under where it came from.
- `requisition-approval.spec.ts` — an opening needs someone else's approval before the public can apply; HR cannot approve their own.
- `jd-versions.spec.ts` — an HR tab drafts new advert wording while a candidate tab still reads the old, and the history cannot be rewritten by going back to it.
- `scheduled-publishing.spec.ts` — a schedule is set and the spec *waits* for the opening to go live, then reads the careers board and clicks what is on it. The slowest spec in the suite, and the only proof the board never advertises something that 404s.
- `reapply-cooldown.spec.ts` — HR sets a waiting period, rejects someone, the candidate is refused, and HR then lets that one person through.
- `apply-indistinguishable.spec.ts` — the anonymous apply door renders the **same screen** in four different states.
- `save-and-resume.spec.ts` — an application left half-finished and picked up later.
- `application-confirmation.spec.ts` — what a parser read off a CV, shown for correction before it becomes an application.

| Item | What must hold | Unit | Integration / smoke | Browser e2e | Gap / recommended level |
|---|---|---|---|---|---|
| B0 | What the careers board lists and what the apply page accepts are the same set — the board never advertises an opening that 404s on click | U `test_ph3_publish_gate.py` (13) | S `smoke_ph3_apply.py` | E `scheduled-publishing.spec.ts`: reads the board, then clicks through to each advert on it | covered. This was once two hand-written copies of one predicate that had drifted, and only a browser can see the drift. |
| B1 | An application records where it came from; a tagged link is honoured but the server decides what channel it means | U `test_ph3_source_tracking.py` (22) | S `smoke_ph3_apply.py` | E `apply-source.spec.ts` | covered. |
| B2 | An opening cannot take public applications until someone other than its author approves it | U `test_ph3_requisition_approval.py` (32) | — | E `requisition-approval.spec.ts` | covered. The refusal is also enforced on the patch (`update_requisition` returns 409 for `public_apply_enabled` on an unapproved opening) rather than only at the publish gate. |
| B3 | Advert wording can be reworked without changing what candidates are currently reading, and replaced wording stays in the record | U `test_ph3_jd_versions.py` (31) | — | E `jd-versions.spec.ts`: two contexts — HR drafting, candidate reading — and a revert that publishes rather than edits | covered, and only observable with two tabs. |
| B4 | A per-opening waiting period after a rejection, and an authorized person can let one candidate through | U `test_ph3_reapplication.py` (50) | S `test_ph3_cooldown_indistinguishable.py` | E `reapply-cooldown.spec.ts` | covered end to end: the period is set on a screen and the override is clicked, which is what criteria 7–9 actually say. |
| B4a | An opening goes live by itself at a scheduled time, and the console never states a time without saying how precise it is | U `test_ph3_scheduled_publishing.py` (35) | S `smoke_group_d_publish_waiting.py` | E `scheduled-publishing.spec.ts` (sets a schedule and waits for it) | covered. The honesty sentence lives in the UI — the publisher is an interval loop, so "09:00" alone is a promise the architecture does not make. |
| B4b | The anonymous apply door answers **identically** whether the address has a live application, is inside the waiting period, is past it, has an override, or has never applied | U `test_ph3_reapplication.py` | S `test_ph3_cooldown_indistinguishable.py` (16): both doors, the stored reply, the work done, a storage outage, a read-only database, reply timing and the pad, and four crafted-input cases | E `apply-indistinguishable.spec.ts`: four states, one rendered screen, compared for equality | covered. See the gap note below on the fifth state. |
| B4c | An application can be left half-finished and resumed | U `test_ph3_drafts_and_confirmation.py` (71) | S `test_ph5_e3_draft_rediscovery_db.py` | E `save-and-resume.spec.ts` | covered. |
| B5 | What a parser read off a CV is shown for correction before it becomes an application | U `test_ph3_drafts_and_confirmation.py` | — | E `application-confirmation.spec.ts`; E `save-and-resume.spec.ts` | covered. |
| B6 | An application is pinned to the advert version that was live when it was made | U `test_ph3_jd_versions.py` | — | E `jd-versions.spec.ts` (the version-aware half) | **partial?** The browser proves the public page follows the published version. That an *application* stores the version it was made against is asserted in the unit file; no browser test reads it back off an application. Low value in the browser — recommend leaving it at unit. |

### Phase 3 gaps worth naming

1. **The fifth state is not browser-reachable (B4b, accepted).**
   `apply-indistinguishable.spec.ts` covers four of the five states. "Rejected,
   and the waiting period has since elapsed" needs a rejection backdated past
   the window; a browser cannot do that, `/test-hooks` offers only `reconcile`
   and `reminders`, and `cooldown_days: 0` is not a substitute because the
   backend returns at `if not cooldown_days` before it reads the ledger — a
   different path. It stays covered by
   `test_ph3_cooldown_indistinguishable.py`, which backdates in SQL. Adding a
   backdating test hook would close it; that is a new test-only write path into
   the decision ledger, which is a larger decision than the gap is worth.
2. **`reapply-cooldown.spec.ts` asserts a denylist (B4b).** It checks the
   refused screen contains none of `reject`, `turned down`,
   `not able to consider`, `until 20`. A denylist only catches the phrasings
   somebody thought of. `apply-indistinguishable.spec.ts` now asserts the
   effect — the screens are equal — which is why the denylist is kept as a
   fast, readable signal rather than removed: it names the specific wordings
   that were once really there.
3. **Web unit not surveyed (all items).** See the note above. Redoing the
   Phase 2 survey for `web/src/__tests__/` against Phase 3 screens would make
   this table a complete map rather than a browser-and-backend one.

### The two specs that were not green, and what they turned out to be

A full run on 2026-10-04 was 32 passed, 2 failed, 1 skipped. The skip is
`coding-round`, which skips by design when no code runner is up. All eight
Phase 3 specs passed. Both failures were chased to a cause, and neither was a
Phase 3 defect — but one was a real bug and the other still is.

**`offer-preboarding` — fixed, and it was arithmetic, not product.** It waited
for the offer accept-code email and never saw it. The service log settled it in
one line: `{"template": "offer_code", "event": "email.enqueued"}` — the backend
had sent it. The mail worker sleeps `email_poll_interval_seconds` (**default
60**) between polls, while `waitForMail`'s default patience is **30 s**, so the
spec passed only when the worker's tick happened to land inside its window. It
has two such waits, which is why it could honestly be committed as "runs green
end to end" and then fail later. `EMAIL_POLL_INTERVAL_SECONDS=2` is now in the
documented run env and in the CI job; the spec passes and runs a minute faster.

**`review-round` — quarantined with `test.fixme`, cause narrowed, not closed.**
Everything up to and including the hold passes. After the release the card
offers the final Hire/Reject pair instead of "Passes this round". The controls
are gated on `row.awaiting_review`, which `workflow_runner.py` computes as
`review_round_id is not None and status != 'held'` — so the product's intent
agrees with the spec, and the observed behaviour means `review_round_id` is no
longer set when the card renders. A `page.reload()` first was tried and does not
help, so it is not a stale page. It may be a race: the poll returns the instant
the status leaves `held`, and a runner pass completing the round would clear
`review_round_id` underneath the page.

Two things about that one are worth keeping straight. **It is not this branch's
doing** — `workflow_runner.py`, `DecisionQueue.tsx` and the decision routers are
untouched by PH3-B4b, which changes 92 files and none of them here. And
**whether it also fails on `main` is not established**, because it was never run
against a `main` checkout; settle that before calling it a product defect. The
spec carries the same reasoning at its `test.fixme`, so it travels with the
code. C4's unit and smoke cover stays green; what is unguarded meanwhile is the
browser path for releasing a hold and then passing the round.

Neither failure was caused by the `locale: 'en-GB'` pin — both were re-run with
that line removed and failed identically. And rule out the three faults below
before reading any failure as a defect: each produced a message pointing
somewhere other than its cause, which is exactly how `offer-preboarding` spent
a run looking like a broken email template.

### Before reading any failure here as a defect

Three environment faults account for most of the time lost to this suite on
2026-10-04, and all three produced failures that read as product bugs.

1. **A stale database looks exactly like a missing column, because it is one.**
   Four Phase 3 specs failed with the draft door returning 500 —
   `column "resume_text" of relation "application_drafts" does not exist` — with
   the migration present in the branch the whole time. The dev database was at
   `2577ba99b7fe`; head was `f1b3d5a7c9e2`. Check `alembic current` against
   `alembic heads` before anything else, and run `alembic upgrade head` from
   `services/data_gateway` with `PYTHONPATH` set to the repo root.
2. **data_gateway needs `RATE_LIMIT_LOGIN_PER_MINUTE=1000`**, not only
   `AI_FAKE_MODE` and `TEST_HOOKS_ENABLED`. Without it small runs pass and a
   full run loses about six specs in **under a second each**, because the suite
   signs in far more than five times a minute from one IP. Several
   sub-second failures across unrelated specs is almost always the environment
   rather than any of them. The full table is in `README.md`; the fixtures also
   say so in the failure text.
3. **The apply door's submission budget is shared by the whole suite** — see
   the section below.

### A budget any new apply spec has to live inside

`POST /apply/{requisition_id}` and `POST /apply/draft/submit` share
`rate_limit("public_apply_submit", 6)` — a fixed 60-second window, keyed on the
client IP, counted across **both** doors. Everything in this suite runs from
localhost, so separate browser contexts do not buy separate budgets, and a
7th submission inside a minute gets "Too many requests. Please wait a minute and
try again." rendered into the form rather than a useful failure.

`apply-indistinguishable.spec.ts` was written with four candidates and seven
submissions and failed exactly there. It now walks one candidate through four
states in sequence, which fits. There is also
`rate_limit_window("public_apply_submit_hourly", 60, 3600)`, so the whole suite
has 60 public submissions an hour from one machine to share — worth knowing
before adding a spec that applies in a loop. Do not raise either limit for a
test: they are the product's defence on an anonymous, unauthenticated write
path, and a spec that needs more submissions than a real person could make is
asking the wrong question.
