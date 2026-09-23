# PH5 — acceptance checklist against `docs/AntHire-Phase5.docx`

**Checked against:** every acceptance criterion in the Phase 5 document, in the document's own
order and wording — all 111 of them across 7 stories.

**Delivery (decision D5-4):** wave by wave. Each wave merges and deploys on its own, only after
CI, a code review and a security sign-off.

| Wave | Stories | State |
|---|---|---|
| 1 | C2 Governed Metric Layer · C1 Quality-of-Hire & Channel Analytics | Built on `feat/ph5-wave1` (HEAD `f12c47a`); code-review, security-pass and evidence-audit fixes are in the branch history; PR pending. |
| 2 | E4 Calibration & Outcome Learning · E5 First-Class Evidence Graph | Not started. |
| 3 | E1 Uniform Copilot Citations · E2 Document Corpus RAG | Not started. |
| 4 | E3 Talent Pools & Rediscovery | Not started. |

**Key:** ✅ done and verified · ⚠️ done, with something you should know · ❌ not done · ⏳ not started yet

**The bar for ✅** is that the person the criterion names can do it **in the product**, not only
through the API — the same bar as Phase 4.

**How each line was verified.**
- Wave 1: each line was checked against the code by a separate evidence pass (screen path, visible text, test bodies and the code claim), then re-checked twice as that pass's findings were fixed — at `3e23098`, and again at `f12c47a` for the console's stage counts. Every test named below was run on a fresh disposable local Postgres (`ph5_w1_ev`, `…_ev2`, `…_ev3`, pgvector, migrated to head `7416694620ed`; Redis local) — never the shared database.
- First pass (`267f735`) — backend 238 passed: `test_ph5_w1_metrics_definitions.py` 49, `test_ph5_w1_checkins.py` (unit) 29, `test_ph5_w1_metrics.py` (db) 37, `test_ph5_w1_checkins.py` (db) 42, `test_analytics_rollup.py` 14, `test_ph4_wave1.py` 67; plus `test_ph3_source_tracking.py` 38 and `ops/ci/tests/test_check_metric_lock.py` 12. Smoke 48/48. Web 169 passed in 10 files.
- Re-verification (`3e23098`) — backend 146 passed on a fresh database: `test_ph5_w1_metrics_definitions.py` 52 (the three new engine-lock tests included), `test_ph3_source_tracking.py` 39 (the new writer scan), `test_analytics_rollup.py` 14, `test_ph5_w1_metrics.py` 39 (the new decision-cohort and copilot-agreement tests) and the new `test_ph5_w1_source_lifecycle.py` 2; `ops/ci/tests/test_check_metric_lock.py` 12 again; smoke `smoke_ph5_wave1.py` 48/48. Web: 66 passed — `HRAnalyticsPage` 24, `HRAnalytics` (pipeline panel) 8, `MetricCell` 4, `HRPipeline` 15, `HRConsole` 15. The lock file and the registry were also compared directly: 44 entries, every hash matching, `registry_hash` `80d09921f7b3…`.
- Close-out (`f12c47a`, the console's four stage counts) — backend 110 passed on a fresh database: `test_analytics_rollup.py` 17, `test_ph5_w1_metrics.py` 41 (including `test_funnel_interview_completed_agrees_with_governed_interviewed` and `test_time_to_hire_days_is_the_median_not_the_mean`), `test_ph5_w1_metrics_definitions.py` 52; smoke 48/48; web 38 passed — `HRConsole` 15, `HRPipeline` 15, `HRAnalytics` 8.

**Decisions this phase rests on (made by the product owner, 2026-09-22):**

| | Decision |
|---|---|
| D5-1 | Talent-pool rediscovery is **opt-in only**: a candidate who agrees to be kept for future openings is rediscoverable for 12 months; nobody else is |
| D5-2 | Post-hire outcome is an **HR-recorded 90-day check-in** (still employed or left; performance below / meets / exceeds) — human-entered, never used to change a decision |
| D5-3 | The Copilot document library takes **PDF, DOCX, TXT/MD**, uploaded by HR managers and super admins, each tagged *all company staff* or *HR only* |
| D5-4 | Wave-by-wave delivery, each wave gated on CI, code review and security sign-off |

---

## Summary

| Story | Wave | Criteria | Done | Notes |
|---|---:|---:|---:|---|
| PH5-C2 Governed Metric Layer | 1 | 15 | 15 |  |
| PH5-C1 Quality-of-Hire & Channel Analytics | 1 | 15 | 15 |  |
| PH5-E4 Calibration & Outcome Learning | 2 | 18 | 0 | ⏳ not started |
| PH5-E5 First-Class Evidence Graph | 2 | 14 | 0 | ⏳ not started |
| PH5-E1 Uniform Copilot Citations | 3 | 12 | 0 | ⏳ not started |
| PH5-E2 Document Corpus RAG | 3 | 18 | 0 | ⏳ not started |
| PH5-E3 Talent Pools & Rediscovery | 4 | 19 | 0 | ⏳ not started |
| **Total** | | **111** | **30** | **0 ⚠️, 81 not yet ✅** |

---

## PH5-C2 — Governed Metric Layer  ·  Wave 1

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | A centralized metric-definition model exists | ✅ | One registry in `services/data_gateway/app/metrics/definitions.py`: 15 flags, 2 measures, 19 metrics, 2 dimensions and 1 rule, each `name@version`. HR sees it as the **Metric glossary** at the foot of **HR → Analytics** (`/hr/analytics`); `unit` (`test_the_real_registry_validates`), `smoke` (definitions: 200, registry hash matches the lock) |
| 2 | Each governed metric has: unique metric name, description, formula/definition, cohort basis, dimensions, version, effective date | ✅ | All seven are required fields of every `Metric` (the formula is generated from the spec) and of the `GET /hr/metrics/definitions` response. On **HR → Analytics** (`/hr/analytics`) the ⓘ beside each figure, or **How is this calculated?** in the glossary, opens a dialog with label, `name@version`, effective date, description, formula, cohort bases, dimensions and change note; `ui` (`HRAnalyticsPage`: "shows the metric@version, formula and change note"), `db` (`test_hr_manager_gets_200_on_all_three_routes`), `smoke` (definitions) |
| 3 | Core hiring metrics are defined centrally | ✅ | 19 metrics over 15 flags and 2 measures: seven counts (applications, screened, assessed, interviewed, selected, hires, rejections); six stage conversion rates including application → interview and interview → hire, plus offer acceptance; time to hire; check-in coverage; 90-day retention; 90-day performance mix; human interviewer score; `unit` (`test_every_registry_entry_matches_its_lock_entry`), `smoke` (all seven counts match an independently computed seed plan) |
| 4 | Metrics such as application-to-interview and interview-to-hire use the same definition everywhere | ✅ | Every consumer computes through the one `compute_funnel`: `/hr/analytics` (conversion, velocity), `/hr/analytics/funnel`, the opening dashboard, the company board, both company Copilot tools and the nightly funnel watcher. No rate is derived in the browser any more: the Hiring pipeline page's panel renders the governed `conversion.pct_*` from the same response (Shortlist / Sat exam / Interviewed / Hire rate, each a share of applications); `db` (`test_cross_consumer_consistency`: the application → interview rate agrees across every consumer that exposes it), `ui` (`HRAnalytics`: "renders the governed conversion rates as sent, never a client-computed one" and "shows the governed hire rate even when funnel counts would divide to over 100%") |
| 5 | Dashboard endpoints consume governed definitions rather than duplicating SQL logic | ✅ | Every figure that has a governed definition now comes from the metric layer: `/hr/analytics`'s conversion and velocity (the pipeline panel's rate strip) and its funnel's applications, exam taken, interviewed and hired — off the same `compute_funnel` call, so nothing is computed twice — plus the opening dashboard's and the company board's counts. The HR console's **Interviewed** tile and the Analytics page's **Interviewed** are now one metric. What stays on the `application_progress` view is what the registry does not define, each named with its reason in `HrFunnel`'s docstring: Applicants (people, not applications), Shortlisted and Rejected (where an application sits now, not a cumulative count), Exam passed (no governed pass metric) and Interview invited; work-queue fields (awaiting a decision, in play, pace) are ungoverned by the same rule; `db` (`test_funnel_interview_completed_agrees_with_governed_interviewed`, `test_cross_consumer_consistency`), `unit` (`test_funnels_governed_fields_are_the_conversion_counts_not_a_second_number`, `test_funnels_ungoverned_fields_stay_on_the_raw_query`), `smoke` |
| 6 | Copilot analytics uses the same governed definitions | ✅ | Both company-scoped Copilot tools read the metric layer. `get_funnel_analytics` (HR manager and super admin) returns governed figures, each carrying its `metric` and `version` plus the `registry_hash`; `get_company_overview` (super admin) now reports the same governed pipeline instead of the legacy `applicants.status` buckets and an AI-only interview join, and says in its payload that the counts are cumulative milestones. Neither is ever given a check-in metric; `db` (`test_get_company_overview_agrees_with_get_funnel_analytics`, `test_cross_consumer_consistency`), `unit` (`test_copilot_funnel_tool_excludes_checkin_metrics`) |
| 7 | Historical metric definitions remain identifiable after a definition changes | ✅ | Every published `name@version` stays in `published.lock.json` for good, with its hash, effective date and date added (a unit test requires the registry to keep every locked entry), and `GET /hr/metrics/definitions` lists every version with `current`. **HR → Analytics** (`/hr/analytics`) resolves each figure's definition by that figure's own `metric@version`, falling back to the version in force when something is opened by name alone, and the glossary lists current versions only; `unit` (`test_every_registry_entry_matches_its_lock_entry`), `ops/ci` (`test_check_metric_lock.py`), `ui` (`HRAnalyticsPage`: with two versions of one metric on file, the glossary and its dialog show the current one) |
| 8 | Metric versions cannot silently change historical results | ✅ | A published entry cannot be edited: CI's new step `ops/ci/check_metric_lock.py` fails any change or removal of a lock entry that existed at the merge base, and a unit test fails when a definition drifts from its lock hash. The lock covers the SQL that gives a flag its meaning as well as the flag itself — 44 entries, including `engine:app_facts_skeleton@1`, `engine:app_facts_filters@1` and one per cohort window — so editing the FROM/JOIN skeleton or a cohort predicate without a new version now fails too. The per-kind aggregation shape and the suppression branches are Python control flow rather than locked text, which both module docstrings say. The lock has a CODEOWNERS entry (a required review only if branch protection asks for code-owner review, which is unverified), and every result carries the `registry_hash` (`80d09921f7b3…`) it was computed under; `unit` (`test_every_registry_entry_matches_its_lock_entry`, `test_editing_the_app_facts_skeleton_without_a_new_version_is_caught`, `test_editing_a_cohort_predicate_without_a_new_version_is_caught`, `test_effective_from_running_backwards_raises`), `ops/ci` (`test_check_metric_lock.py`, 12 tests), `smoke` (the served hash matches the lock) |
| 9 | Cohort definitions are explicit—for example, application cohort vs decision cohort | ✅ | Three named bases on **HR → Analytics** (`/hr/analytics`) → **Cohort**, each with a plain-language line under the tabs: application (applied in the period), decision (first moved to hired or rejected in the period) and hire (first hired in the period, and the hire stands). Every funnel response echoes its basis and window; `db` (`test_application_cohort_filters_by_created_at`, `test_decision_cohort_counts_only_applications_decided_in_the_window`, `test_hire_cohort_filters_by_hired_at_not_created_at`), `smoke` (decision and hire cohorts each windowed to 60 days), `ui` (cohort switch refetches) |
| 10 | Company/tenant isolation is enforced | ✅ | The company comes only from the session, every query binds it, and a foreign opening id returns zero, never an error that reveals it exists; `db` (`test_company_b_sees_nothing_of_companys_as`, `test_a_foreign_requisition_id_yields_zero_not_an_error`), `smoke` (company B's opening id read by company A gives zero) |
| 11 | Authorized users can understand how a metric was calculated | ✅ | The ⓘ beside every figure on **HR → Analytics** (`/hr/analytics`) (and **How is this calculated?** in the glossary) shows formula, cohort bases, dimensions, `name@version`, change note and the registry hash, with a copy button. Clicking any underlined count, numerator or denominator opens a side panel listing the applications behind it; `ui` (`HRAnalyticsPage`: the dialog and the drill-down), `db` (`test_members_drilldown_returns_rows_and_writes_an_audit_row`), `smoke` (the drill-down's row count equals the governed hires) |
| 12 | Conflicting/invalid metric definitions are detected before publication | ✅ | The registry is validated when the service imports it, so a bad definition stops the service starting and fails CI at collection: duplicate keys, unknown or unversioned flags, unknown cohort / dimension / kind, a rate whose denominator is not a subset of its numerator, missing measures or buckets, versions whose effective dates tie or run backwards, and check-in metrics grouped by an unsafe dimension. CI also refuses lock edits; `unit` (17 `…_raises` tests in `test_ph5_w1_metrics_definitions.py`), `ops/ci` (`test_check_metric_lock.py`) |
| 13 | Existing dashboards continue functioning during migration | ✅ | `GET /hr/analytics` keeps its response shape throughout (it only adds `definitions` and `registry_hash`), so the HR console and the pipeline page's embedded panel keep working while four of their counts move to the metric layer — two of which change meaning, stated in the notes — and small companies still get their real figures; `ui` (`HRConsole`, `HRPipeline`, `HRAnalytics` panel tests), `unit` (`test_analytics_rollup.py`), `db` (`test_small_pipeline_cohorts_are_suppressed_but_stay_visible`) |
| 14 | Tests verify that different consumers return the same metric for the same cohort | ✅ | `db` (`test_cross_consumer_consistency`): one seeded company read through `/hr/analytics`, `/hr/analytics/funnel`, the opening dashboard, the company board, the Copilot funnel tool and the watcher's input. Applications agree across all six, hires across five (the watcher has none), interviewed across four, and the application → interview rate across the three that expose it. A seventh consumer has its own test (`test_get_company_overview_agrees_with_get_funnel_analytics`: applications, interviewed, hires and the registry hash). `smoke` repeats it through the HTTP routes (hires: `/hr/analytics` = funnel = board; applications: dashboards = funnel) |
| 15 | Metric calculation and definition changes are auditable | ✅ | Definition changes: git history, the append-only lock (CI-checked) and each version's change note. Calculations: every result carries its `metric` + `version` and the `registry_hash`, and the applications behind a figure can be listed, which writes an `analytics.members_viewed` audit row (metric, part, filters, total; no names); `db` (`test_members_drilldown_returns_rows_and_writes_an_audit_row`), `smoke` (the audit row names no candidate) |

**Notes.**
- **Small groups.** A rate, median or mean whose population is under 5 is marked *too few to compare*. Pipeline figures keep their value: a rate still shows its numerator and denominator, and a rate, a median and a mean each show the withheld figure on hover. Check-in coverage, retention and performance values are blanked below 5 — nothing on hover either — and for retention and performance the numerator is blanked too. When any group's retention or performance is blanked, it is blanked in every group, so it cannot be recovered by subtraction.
- **Live figures.** Definitions are frozen; figures are recalculated from the records each time, so they can move when a record does (an offer withdrawn after a hire).
- **Only the current version is calculated.** Every past version stays identifiable in the lock and in `GET /hr/metrics/definitions`, and a figure resolves its own version; the glossary lists the versions in force. Calculating an old version side by side would need `compute.py` extended, when the first version 2 is published.
- **Meaning changes** from the pre-PH5 roll-up, each stated in the metric's change note: *interviewed* now means an AI interview completed or a human scorecard submitted (not the ledger status, and no longer an AI scorecard alone — which is what the HR console's Interviewed tile used to count); *sat exam* no longer counts another application's exam, and now also counts an assessment recorded with no exam attempt behind it; *hired* counts only hires that stand; *time to hire* counts only hires that stand and starts at the application's `created_at` rather than the ledger's first move to `new`, which some applications never had. The console's Interviewed and Exam-taken counts, and the pipeline panel's bars, follow these definitions now, so both can read higher than before.


---

## PH5-C1 — Quality-of-Hire & Channel Analytics  ·  Wave 1

| # | Acceptance criterion | | Evidence |
|---|---|---|---|
| 1 | HR can view candidate volume by acquisition source/channel | ✅ | **HR → Analytics** (`/hr/analytics`) → **Group by: Source** → **Compare sources**: an Applications column, one row per source present in the window, after the All row; `ui` (`HRAnalyticsPage` group comparison), `db` (`test_unknown_source_is_its_own_group`), `smoke` (referral, job_board and unknown groups match the seed plan) |
| 2 | Sources captured through [PH3-B1] are retained throughout the candidate lifecycle | ✅ | An application's source is written once, when it is created, and only `enrol_applicant` can write the column at all — a scan of every production file under `services/` for a write to `enrolments` touching `source` allows exactly that one file. HR's own adds now record a chosen source instead of always *Internal* (**Applicants → Bulk upload resumes → Where did this candidate come from?**), and the source shows under **Details** on the candidate drawer; `unit` (`test_only_enrol_applicant_writes_enrolments_source`, `test_both_hr_write_paths_validate_their_source`), `db` (`test_a_bulk_uploads_source_survives_the_hand_off_and_later_stage_moves`: a real upload tagged *referral*, through the background ingest pass, still reads `referral` after the application is shortlisted and hired; `test_a_bulk_upload_with_no_channel_falls_back_to_internal`), `ui` (`Applicants`: sends the chosen source; `CandidateDrawer`), `smoke` (a bad source is refused) |
| 3 | HR can compare funnel conversion by source | ✅ | **HR → Analytics** (`/hr/analytics`) → Group by Source: every conversion rate per source, each with its own numerator and denominator from the server (never derived from neighbouring counts); `ui` (`HRAnalyticsPage`: a rate renders with its own numerator and denominator; group comparison) |
| 4 | Analytics can show progression such as: Applied, Screened, Assessment, Interview, Selected, Hired | ✅ | **HR → Analytics** (`/hr/analytics`) → **The funnel**: Applications → Screened → Assessed → Interviewed → Selected → Hires, with rejections alongside; `db` (flag tests: `test_screened_reads_the_ledger`, `test_assessed_via_round_result_with_no_exam`, `test_interviewed_via_human_scorecard_only`, `test_a_declined_offer_is_selected_but_not_accepted`, `test_a_reversed_hire_is_not_hired`), `smoke` (each stage matches the seed plan) |
| 5 | HR can compare hiring outcomes across sources | ✅ | **HR → Analytics** (`/hr/analytics`) with the **Decision** cohort grouped by source: share hired and offer acceptance per source; with the **Hire** cohort: hires, time to hire, check-in coverage, 90-day retention and performance per source (check-in outcomes follow the small-group rule in the notes); `ui` (`HRAnalyticsPage` group comparison), `db` (`test_a_visible_group_cannot_reveal_a_suppressed_ones_cell_by_subtraction`: hire cohort by source) |
| 6 | Human interview scorecard results can contribute to quality-of-hire analysis | ✅ | **HR → Analytics** (`/hr/analytics`) → **Quality of hire → Human interviewer scorecards**: the mean, across the cohort's hires, of each hire's current human scorecard score (not-assessed criteria excluded; AI scores are never used); `db` (`test_a_withdrawn_correction_leaves_the_original_as_current`, `test_time_to_hire_and_interviewer_score_stay_visible_when_suppressed`), `ui` (labelled as human-recorded) |
| 7 | Quality signals are clearly distinguished from final hiring decisions | ✅ | Quality of hire is its own card on **HR → Analytics** (`/hr/analytics`) with a standing banner: quality signals are recorded after hiring and never change an application or a decision. The 90-day check-in form on the candidate drawer says it is used only in aggregate and never changes the application or any decision; `ui` (`HRAnalyticsPage` banner, `CheckinSection` notice) |
| 8 | Analytics support appropriate cohort/time filtering | ✅ | **HR → Analytics** (`/hr/analytics`) → **Cohort**: Application / Decision / Hire tabs, From and To dates, Opening and Source filters. On the **Hire** tab the funnel card says it is measured over applications and points back to the application or decision cohort, rather than reporting no applications; `ui` (cohort switch refetches; the hire-cohort explanation), `db` (a cohort-window test per basis), `smoke` (decision and hire cohorts windowed) |
| 9 | Metrics use consistent definitions rather than endpoint-specific calculations | ✅ | Every governed metric is computed in one place and agrees everywhere it is read — the Analytics page, the HR console's stat strip, the pipeline panel, the opening dashboard, the company board, both company Copilot tools and the nightly watcher — and no rate is worked out in a browser (see PH5-C2 #4, #5 and #14); `db` (`test_cross_consumer_consistency`, `test_funnel_interview_completed_agrees_with_governed_interviewed`, `test_get_company_overview_agrees_with_get_funnel_analytics`) |
| 10 | Unauthorized users cannot access another company's candidate/source data | ✅ | Company from the session only; the funnel and the drill-down are HR-manager only; super admin, interviewer and candidate get 403 (super admin may read the definitions, which hold no candidate data); `db` (`test_company_b_sees_nothing_of_companys_as`, `test_super_admin_gets_403_on_funnel_and_members_but_200_on_definitions`, `test_interviewer_gets_403_everywhere`, `test_candidate_gets_403_everywhere`), check-ins `db` (`test_tenant_isolation_on_record`, `…_on_list`, `…_on_correct_is_not_found`), `smoke` (role gates; company B's HR gets 404 on company A's hire; a foreign opening id gives zero) |
| 11 | Missing source information is represented as Unknown/Untracked, not silently discarded | ✅ | `unknown` is a real value (the column default, never NULL), and an **Unknown / untracked** row appears on **HR → Analytics** (`/hr/analytics`) whenever any application in the window has it; `db` (`test_unknown_source_is_its_own_group`), `ui` (it renders as a normal row), `smoke` (the unknown group's label and count) |
| 12 | Historical applications remain analyzable even when source tracking was unavailable | ✅ | Applications from before source tracking carry `unknown` and are counted in every metric under Unknown / untracked. Exam and interview evidence recorded before applications were linked is credited by the same legacy rule the pipeline view uses (only when the person has exactly one application); `db` (`test_legacy_attribution_credits_the_only_application`, `test_legacy_attribution_does_not_bleed_across_two_applications`) |
| 13 | Analytics are based on auditable underlying records | ✅ | Click any underlined count, numerator or denominator on **HR → Analytics** (`/hr/analytics`): a side panel lists the applications behind it (capped at 200, with "Showing 200 of N"), each opening the candidate drawer. Reading it writes an `analytics.members_viewed` audit row. Check-in outcomes are aggregate-only, so their outcome cells are not listable; `db` (`test_members_drilldown_returns_rows_and_writes_an_audit_row`, `test_members_drilldown_refuses_a_checkin_outcome`), `ui` (drill-down opens the drawer), `smoke` (audit row written, no candidate named; outcome drill-downs refused) |
| 14 | No AI-generated metric or recommendation can modify candidate status | ✅ | The metric layer is read-only (its only write is the drill-down audit row), the check-in never touches the pipeline, and a Copilot tool can only read or draft (`ToolEffect`); `unit` (`test_metric_layer_is_read_only`, `test_hire_checkins_never_touches_the_pipeline`), `smoke` (no enrolment status changed and no ledger row was added across every read and check-in write) |
| 15 | Tests cover source attribution, funnel calculations, permissions, cohorts, and missing data | ✅ | `unit` `test_ph5_w1_metrics_definitions.py` (52), `test_ph5_w1_checkins.py` (29), `test_ph3_source_tracking.py` (39); `db` `test_ph5_w1_metrics.py` (39: flags, all three cohorts, sources including unknown, suppression, tenancy, roles, drill-down, consistency), `test_ph5_w1_checkins.py` (42), `test_ph5_w1_source_lifecycle.py` (2); `smoke` `smoke_ph5_wave1.py` (48); `ui` `HRAnalyticsPage.test.tsx` (24), `CheckinSection.test.tsx` (14), `MetricCell.test.tsx` (4) |

**Notes.**
- **90-day check-in (D5-2).** HR records it on the candidate drawer of a hired application (still employed, meets / below / exceeds; or left, voluntary / involuntary / unknown), no free text, from the start date to day 180 (*still employed* from day 80). **Analytics → Quality of hire → Check-ins due** lists who is due. Deleted 24 months after it was first recorded (only where `RETENTION_DRY_RUN=false`) and outright on erasure. It never reaches the Copilot or any model, and it is never used to change a decision.
- **HI/TE:** the candidate's offer-acceptance page now carries one sentence about the check-in; the Hindi and Telugu versions need native-speaker and legal review before production.


---

## PH5-E4 — Calibration & Outcome Learning  ·  Wave 2

| # | Acceptance criterion | |
|---|---|---|
| 1 | HR can view aggregated interviewer scoring patterns | ⏳ |
| 2 | Calibration analysis can compare interviewers using the same frozen evaluation criteria | ⏳ |
| 3 | Minimum sample thresholds prevent misleading comparisons based on very small datasets | ⏳ |
| 4 | The system can identify significant scoring variation between interviewers | ⏳ |
| 5 | Historical human scorecards remain immutable | ⏳ |
| 6 | Hiring outcomes can be associated with the relevant historical evaluation evidence | ⏳ |
| 7 | Outcome analysis can compare evaluation signals against later outcomes | ⏳ |
| 8 | Metrics use the governed metric definitions from C2 | ⏳ |
| 9 | Cohorts and time periods are explicitly defined | ⏳ |
| 10 | Calibration results are clearly presented as signals, not judgments about individual interviewers | ⏳ |
| 11 | Outcome-learning results do not automatically change candidate status | ⏳ |
| 12 | AI cannot automatically alter scorecards, decisions, or hiring outcomes | ⏳ |
| 13 | Authorized users can inspect the evidence underlying an insight | ⏳ |
| 14 | Company/tenant isolation is enforced | ⏳ |
| 15 | Candidate privacy, retention, and erasure rules are respected | ⏳ |
| 16 | Calibration and outcome-learning calculations are auditable | ⏳ |
| 17 | Existing scorecards and decision workflows continue to function unchanged | ⏳ |
| 18 | Tests cover aggregation, minimum sample sizes, permissions, cohort logic, and outcome linkage | ⏳ |

---

## PH5-E5 — First-Class Evidence Graph  ·  Wave 2

| # | Acceptance criterion | |
|---|---|---|
| 1 | Evidence relationships have a first-class representation | ⏳ |
| 2 | Evidence can be linked to its originating hiring stage | ⏳ |
| 3 | Evidence retains candidate/enrolment/company scope | ⏳ |
| 4 | Evidence can reference its source: application, assessment, interview, scorecard, decision | ⏳ |
| 5 | Evidence retains relevant timestamps | ⏳ |
| 6 | Evidence provenance is preserved | ⏳ |
| 7 | Authorized users can query evidence relationships | ⏳ |
| 8 | Tenant isolation is enforced | ⏳ |
| 9 | Existing permission/data-class controls apply | ⏳ |
| 10 | Evidence can be traced from decision → supporting evidence → originating event | ⏳ |
| 11 | Copilot/RAG can consume the graph without bypassing existing authorization | ⏳ |
| 12 | Evidence changes are auditable where required | ⏳ |
| 13 | Stale/deleted evidence follows existing retention/erasure rules | ⏳ |
| 14 | Tests cover cross-stage relationships and tenant isolation | ⏳ |

---

## PH5-E1 — Uniform Copilot Citations  ·  Wave 3

| # | Acceptance criterion | |
|---|---|---|
| 1 | Copilot answers provide citations for factual claims derived from retrieved hiring data | ⏳ |
| 2 | Citations identify the underlying evidence/source clearly enough for an authorized user to inspect it | ⏳ |
| 3 | Citations are displayed consistently across supported Copilot consoles | ⏳ |
| 4 | Retrieved evidence and model interpretation are visually distinguishable | ⏳ |
| 5 | Claims without supporting retrieved evidence are not presented as sourced facts | ⏳ |
| 6 | Citation generation respects the existing permission-aware retrieval layer | ⏳ |
| 7 | A user cannot receive a citation to data they are not authorized to access | ⏳ |
| 8 | Existing watcher citations continue to work | ⏳ |
| 9 | Citation metadata remains available for audit/traceability where appropriate | ⏳ |
| 10 | Copilot remains read/draft-only; citations do not introduce mutation capabilities | ⏳ |
| 11 | Existing Copilot functionality continues working when a source does not support a citation | ⏳ |
| 12 | Tests cover citation presence, source mapping, permissions, and unsupported-source behavior | ⏳ |

---

## PH5-E2 — Document Corpus RAG  ·  Wave 3

| # | Acceptance criterion | |
|---|---|---|
| 1 | Authorized HR users can upload or register supported documents into the governed corpus | ⏳ |
| 2 | Documents are associated with the correct company/tenant | ⏳ |
| 3 | Documents are classified using the existing data-classification/access model | ⏳ |
| 4 | Documents are parsed and indexed for retrieval | ⏳ |
| 5 | Documents can be searched semantically | ⏳ |
| 6 | Copilot can retrieve relevant document passages when answering questions | ⏳ |
| 7 | Retrieval respects user/company permissions | ⏳ |
| 8 | Unauthorized users cannot retrieve restricted documents or passages | ⏳ |
| 9 | Retrieved document evidence is attached to Copilot citations | ⏳ |
| 10 | Citations identify the source document and relevant evidence location | ⏳ |
| 11 | Document updates create a new retrievable version rather than silently corrupting historical references | ⏳ |
| 12 | Deleted/expired documents are removed from active retrieval | ⏳ |
| 13 | Document retention and erasure rules are enforced | ⏳ |
| 14 | Unsupported or malformed documents fail safely with a clear status | ⏳ |
| 15 | Copilot does not treat retrieved documents as instructions capable of changing system behavior | ⏳ |
| 16 | Copilot remains read/draft-only | ⏳ |
| 17 | Existing structured-record retrieval continues working | ⏳ |
| 18 | Tests cover ingestion, retrieval, permissions, citations, versioning, deletion, and tenant isolation | ⏳ |

---

## PH5-E3 — Talent Pools & Rediscovery  ·  Wave 4

| # | Acceptance criterion | |
|---|---|---|
| 1 | Authorized HR users can create and manage talent pools | ⏳ |
| 2 | Candidates can be added to a pool manually | ⏳ |
| 3 | Authorized users can remove candidates from a pool | ⏳ |
| 4 | Pools are company-scoped | ⏳ |
| 5 | Candidate permissions and access controls are enforced during rediscovery | ⏳ |
| 6 | HR can search the eligible talent universe semantically | ⏳ |
| 7 | Search can use relevant candidate evidence such as: competencies, skills, experience, previous assessment evidence, interview evidence | ⏳ |
| 8 | Rediscovery results show why a candidate matched | ⏳ |
| 9 | Retrieved candidate evidence remains subject to existing permission controls | ⏳ |
| 10 | Candidate consent/retention rules are checked before candidates are surfaced for rediscovery | ⏳ |
| 11 | Expired or no-longer-eligible candidate data is excluded from active rediscovery | ⏳ |
| 12 | Evidence has a freshness/staleness indicator where appropriate | ⏳ |
| 13 | Stale evidence is clearly distinguished from recent evidence | ⏳ |
| 14 | HR cannot treat a stale match as current candidate qualification without review | ⏳ |
| 15 | Candidate data is not exposed across companies | ⏳ |
| 16 | Pool membership and important pool actions are audited | ⏳ |
| 17 | Candidate erasure removes/excludes the candidate from relevant pool/search results | ⏳ |
| 18 | Existing semantic candidate search continues working | ⏳ |
| 19 | Tests cover permissions, consent/retention, pool membership, rediscovery, staleness, and erasure | ⏳ |

---
