# UI Port — Functional Coverage / Gap Report

> Compares the **live implemented app** (source of truth for behavior) against the
> **new `anterview-pages` design mockups** (source of truth for visuals), page by page.
> Generated 2026-06-24. The designs use **mock data + zero data layer**, so this report
> exists to guarantee **no working functionality is lost** when we apply the new look.

## The rule
**Logic is the source of truth; design is the skin.** For every page we port the *look*
onto the *behavior* — never the reverse. Three classifications:
- ✅ **Covered** — design has an equivalent slot → adopt the design treatment, keep the live handler.
- ❌ **Missing-preserve** — live has it, design omits it → re-create it in the design's visual language. **Never dropped.**
- ➕ **Design-only** — design adds something live lacks → keep as presentation *only if a real backend exists*; otherwise stub/defer, **never ship as fake**.

---

## Universal gaps (apply to ALL pages)
The design mockups contain **none** of these — every one must be re-created/preserved:
1. **react-query data + mutations** — every live page fetches/mutates via TanStack Query + `toast` + `invalidateQueries`. Designs are 100% local mock state.
2. **Loading skeletons, empty states, error states** (`role="alert"`) — designs render only the happy path.
3. **i18n** — every live string goes through `t()` (EN/HI/TE). Designs are hardcoded English. All `t()` keys must be reconnected.
4. **`data-testid`s** — tests depend on them (candidate + admin pages). Designs have none.
5. **Accessibility** — `aria-*`, roles, `aria-busy`, keyboard handlers. Preserve all.
6. **Single-shell wrapping** — design pages each wrap themselves in their own `<AppShell>` + `AuroraField`. The live router wraps once. **Strip the design pages' internal shell/background** so they don't double-nest.
7. **Score scale** — live scores are **0–10**; design `ScoreRing`/bars/`scoreColor` assume **0–100**. Must **convert**, not relabel.
8. **Consent / DPDP + magic-link security** — hash-fragment tokens (not path params), consent gates, video-capture consent. Preserve exactly.

---

## Port-direction & risk matrix

| Page | Direction | Risk | Headline gap |
|---|---|---|---|
| **Candidate** |
| Dashboard | adopt-design+graft-data | Med | 4 query feeds + inline resume upload + isError/skeletons + isAdmin shortcut |
| JobsList | skin-onto-logic | High | DPDP consent gate + createSession + language persistence |
| StartInterview | adopt-design+graft-data | High | custom-job creation wizard + avatar API + consent (design is a device-check screen) |
| Interview | skin-onto-logic | High | LiveKit + MediaPipe proctoring + fullscreen + reconnect + consent |
| InterviewComplete | skin-onto-logic | Med | scorecard polling + early-exit + 90s timeout/retry |
| Scorecard | adopt-design+graft-data | Med | rationale accordions + PDF gate + 0–10 scale + query |
| History | adopt-design+graft-data | Med | pagination + responsive table/card + testids |
| Resume | skin-onto-logic | High | version CRUD + set-current/delete-confirm + upload progress |
| **HR** |
| HRConsole | adopt-design+graft-data | Low | real getMe greeting + 4 stage links (don't fake cost/stats) |
| Applicants | skin-onto-logic | High | bulk PDF upload + shortlist/reject/rescore + real ATS breakdown |
| Exams | adopt-design+graft-data | Med | createExam form + status taxonomy (drop dead "Copy link") |
| ExamEditor | skin-onto-logic | High | MCQ model + publish + **magic-link assignment** + attempt-lock |
| ExamResults | adopt-design+graft-data | Med | real attempts + in-progress logic (drop fake CSV/flagged) |
| HRInterviews | skin-onto-logic | High | invite form + magic-link mint/revoke + eligibility |
| HRPipeline | skin-onto-logic | High | persisted hire/reject + rationale + pagination (design Kanban doesn't persist) |
| HRAnalytics | adopt-design+graft-data | Med | real funnel + **both exports** (embedded + page); extra charts unbacked |
| **Admin / Super** |
| AdminOverview | adopt-design+graft-data | High | 6 KPIs + trends line + score-dist bar (design is a health board) |
| AdminInterviews | skin-onto-logic | Med | server filters/sort + CSV export + pagination + columns |
| AdminInterviewDetail | adopt-design+graft-data | High | 0–10 radar + rationale + integrity panel + disclaimer |
| AdminAnalytics | skin-onto-logic | Med | by-role + avg-axis-by-role + by-language pie + score-dist |
| AdminJobJd | adopt-design+graft-data | High | JD PDF upload+parse (design is a browse grid) |
| SuperAdminConsole | adopt-design+graft-data | High | createCompany + createHrManager mutations + hr_count |
| **Auth / Public / Shell** |
| Login | skin-onto-logic | Low | RHF+Zod + role-aware redirect + must_change_password + real Google |
| Register | skin-onto-logic | Low-Med | register mutation; don't adopt role tabs without backend |
| GoogleCallback | skin-onto-logic | Med | single-use OAuth exchange + StrictMode guard + error branch |
| ChangePassword | adopt-design+graft-data | Med | forced-bootstrap semantics + role redirect (no escape link) |
| PublicExam | skin-onto-logic | High | hash-token + intro/taking/result + timer/auto-submit |
| InterviewInvite | skin-onto-logic | Med | hash-token + redeem→guest-token→navigate + token-strip |
| NotFound | adopt-design+graft-data | Low | auth-aware home target |
| AppShell | skin-onto-logic | Med | keep live shell (roles, logout, **mobile nav**, transitions); strip design's internal shell |

---

## ➕ Design-only features with NO live backend (do NOT ship as real)
Present these as **"coming soon"/stub or omit** until a backend exists — never as working data:
- AdminOverview **microservice/AI-provider health board**
- AdminInterviews **Company** column; AdminInterviewDetail **transcript** card
- AdminJobJd **JD library / competencies / map-exam / new-role**
- SuperAdmin **plan/seats/status columns, feature-flags tab, DPDP audit-log tab**
- HRAnalytics **language donut / score-distribution / trend** charts
- ExamResults **CSV export + flagged/proctoring**; Exams **Copy-link**; HRInterviews **Watch**
- HRPipeline **drag-drop Kanban** (no stage-transition endpoint → would silently lose changes)
- Applicants drawer **fabricated `competencies()`** (use real `ats_breakdown`)
- Resume **extracted-skills**; Notifications **bell**; **Naipunyam SSO** button
- Register **role tabs** (role is server-assigned) + **consent param** (API doesn't capture yet)

---

## Decisions to confirm (everything else I'll handle as engineering)
1. **HRPipeline**: keep the live persisted **table + hire/reject** model and drop the non-persisting Kanban? (Recommended) — or build a stage-transition API for real drag-drop?
2. **Design-only unbacked features** (list above): default = **stub "coming soon" / omit**. Want any of them actually built (backend + wiring)?
3. **Register**: keep candidate-only (recommended) or wire role-selection + consent capture into the backend?
4. **Forced ChangePassword**: keep no-escape bootstrap semantics (recommended) — design's "Back to dashboard"/current-password are omitted in the forced case.

(Score-scale conversion 0–10, hash-token security, single-shell wrapping, keeping mobile nav — I'll just do these correctly; not asking.)

---

# Full per-page detail

_Each row: live feature → in design? → handling. Each page ends with a must-preserve checklist._

## Candidate (detail)

### Dashboard
**Direction:** `adopt-design+graft-data` — **Risk:** Med — design's readiness ring/nudges are richer, but every stat/list must be re-wired to the 4 live react-query feeds + inline resume upload.
- ❌ `getMe` (`['auth','me']`) → welcome name/email/role badges (design hardcodes "Aanya") — re-create
- ✅/wire `listSessions` (`['sessions',{perPage:3}]`) → interviews-taken count + recent list
- ⚠️ `listScorecards` → computed avg composite (design shows a readiness ring, no avg) — graft avg StatCard
- ❌ `getCurrentResume` → resume status + filename — re-create resume StatCard + inline upload
- ❌ inline `FileUploadZone` upload w/ progress + invalidates `auth/me`+`resume/current` — re-create
- ❌ per-stat `Skeleton` loading + welcome skeleton — re-create
- ❌ `isError` full-page alert + logout-recovery — re-create
- ❌ `isAdmin` → `/admin/jd` shortcut — re-create (role variant)
- ❌ `data-testid="recent-session-${id}"` + status `Badge` + `formatDuration`/`formatDate` — re-create
- ✅ quick-action buttons (`/start`, `/jobs`)
**Must-preserve:** 4 query keys (auth/me, sessions, scorecards, resume/current), avg-score calc, inline FileUploadZone + invalidation, `recent-session-${id}` testid, isError alert, isAdmin shortcut, all `dashboard.*` i18n, skeletons, empty state.

### JobsList
**Direction:** `skin-onto-logic` — **Risk:** High — live consent gate + session-create + DPDP is the page's core; design is a static grid.
- ❌ `getJobs` (`['jobs']`) query — keep
- ❌ `createSession` mutation → navigate `/interview/${id}` — keep
- ❌ DPDP consent gate (`useConsent`, `ConsentModal`, agree/decline, `recordConsent`, pending-job staging) — keep (S3-011)
- ❌ decline banner + start-error banner (`role="alert"`) — re-create
- ⚠️ `<select id="interview-language">` persisted to `localStorage('intants:interview-language')` — keep (tests use it)
- ⚠️ `<select id="level-filter">` client filter — keep; design search box is ➕
- ❌ loading skeletons, isError retry, both empty states — re-create
- ⚠️ `JobCard` per-item `isStarting` spinner + aria — adopt design card, wire start/isStarting
**Must-preserve:** `['jobs']` query, createSession+navigate, full ConsentModal gate, `interview-language` select+persistence, `level-filter`, skeletons, isError retry, empty states, per-card isStarting, all `jobs.*` i18n.

### StartInterview
**Direction:** `adopt-design+graft-data` — **Risk:** High — live is a custom-job creation wizard; design is a device-check for an existing job (functionally different).
- ❌ job-detail form (title required+validation, company, JD, level) — keep
- ❌ `createCustomJob`→`createSession` sequence + navigate — keep
- ⚠️ avatar picker from `getAvatars` (`['avatars']`), male/female radiogroup, `intants:interview-avatar` persistence, video onError fallback — adopt design cards, wire data+persistence+a11y
- ⚠️ language select persisted `intants:interview-language` — adopt SegTabs, keep persistence
- ❌ experience-level select, `getMe` resume banner, review summary, API error alert+toast — re-create
- ⚠️ ConsentModal gate (design uses a lighter checkbox) — keep modal gate
- ➕ device-check mic meter/WaveBars — optional presentation (no live wiring)
**Must-preserve:** title validation, createCustomJob→createSession+navigate, `['avatars']`+radiogroups+avatar persistence, language persistence, level select, getMe resume banner, ConsentModal, review summary, submit `aria-busy`, error alert+toast, all `startInterview.*` i18n.

### Interview
**Direction:** `skin-onto-logic` — **Risk:** High — real LiveKit + MediaPipe + fullscreen + DPDP video consent. Only restyle the HUD.
- ❌ `sessionId` guard; two-phase `InterviewIntro`→`LiveKitInterview`; intro video gate (lang `/intro/intro_${lang}.mp4`) — keep
- ⚠️ camera consent (`data-testid="camera-consent-checkbox"`, DPDP `video_capture`) — keep in intro
- ❌ fullscreen gate (`requestFullscreen`, `fullscreen-denied`, mid-interview re-entry `alertdialog`) — keep
- ❌ test-ids `interview-intro`/`intro-video`/`begin-button`/`skip-button` — keep
- ❌ LiveKit lifecycle (`useLiveKitInterview` connect-once, status machine, retry) — keep
- ⚠️ avatar video full-bleed + avatar-ready gate + 30s safety + overlays — restyle
- ⚠️ MediaPipe `useProctoring` (face/gaze/multi-face, tab/copy/paste/fullscreen events, batched POST) — keep, restyle HUD/PiP
- ❌ proctoring warning/calibration banners (`aria-live`) — re-create
- ⚠️ self-view PiP `localVideoRef` (mirrored) — keep real element
- ⚠️ mic/camera toggles (disabled until connected) — keep real toggles
- ⚠️ end → `disconnect()` → `/complete` w/ `endedEarly`; natural completion via room `disconnected` — keep
**Must-preserve:** Intro+LiveKit+useProctoring+useFullscreen+useLiveKitInterview, all test-ids, DPDP `video_capture`, fullscreen request+re-entry, real video refs, mic/camera toggles, end/natural-completion nav+endedEarly, aria-live banners, all `interview.*`/`interviewIntro.*` i18n.

### InterviewComplete
**Direction:** `skin-onto-logic` — **Risk:** Med — real scorecard polling + early-exit + timeout/retry vs design's fixed setTimeout steps.
- ❌ `sessionId` param + `location.state` (message, endedEarly) — keep
- ⚠️ `listSessions` poll (`refetchInterval` 3s) for `scorecard_id` — keep; design steps are ➕
- ✅ auto-redirect to `/scorecard/${id}` when ready
- ❌ early-exit branch (`data-testid="early-exit-card"`), 90s timeout + check-again, preparing skeleton `aria-live`, 3 nav CTAs — re-create
**Must-preserve:** complete-poll + 3s interval, redirect, endedEarly + `early-exit-card`, 90s timeout/retry, preparing skeleton, 3 CTAs, session-id label, all `interviewComplete.*` i18n.

### Scorecard
**Direction:** `adopt-design+graft-data` — **Risk:** Med — near-twin layout; wire query, rationale toggles, PDF.
- ❌ `getScorecard` (`['scorecard',id]`) + `:scorecardId` — keep
- ❌ loading skeleton (`role="status" aria-label="Loading scorecard"`), ErrorState + once-only toast — keep
- ✅ composite `ScoreRing` (+ sr-only overall) + band `Badge`
- ❌ 4 `ScoreBarRow` collapsible rationale (`aria-expanded`/`controls`) — re-create
- ✅ recharts radar over live `scores`; ✅ strengths/improvements lists; ❌ summary card — re-create
- ⚠️ PDF download (`report_pdf_url` gate) — wire real url
- ⚠️ score band: live 0–10 vs design 0–100 — reconcile scale
- ➕ transcript/share (no live API) — design-only
**Must-preserve:** scorecard query, loading `role="status"`/label, ErrorState+toast, ScoreRing+sr-only, **rationale collapsibles**, radar over live dims, conditional strengths/improvements/summary, conditional PDF, 0–10 scale, all `scorecard.*` i18n.

### History
**Direction:** `adopt-design+graft-data` — **Risk:** Med — graft paginated query + responsive table/card + per-row logic.
- ❌ `listSessions({page,perPage:10})` + pagination (prev/next, aria) — re-create
- ❌ isError toast, loading skeletons, empty state (`history-empty-state` + CTA) — re-create
- ⚠️ desktop `Table` rows (`session-row-${id}`: role/date/lang/status/duration/scorecard link) + mobile `SessionCard` (`session-card-${id}`) responsive split at `md` — adopt table, re-add testids/lang/duration + responsive cards
- ⚠️ `statusProps`/`formatDate`/`formatDuration` + conditional scorecard link — keep
- ➕ avg-score ring — presentation
**Must-preserve:** sessions query+pagination, `history-empty-state`, `session-row-${id}`+`session-card-${id}`, responsive split, status/format helpers, conditional scorecard link, skeleton+error, all `history.*` i18n.

### Resume
**Direction:** `skin-onto-logic` — **Risk:** High — full version-managed CRUD vs single-file mock.
- ❌ `listResumes` (`['resumes']`) + `getCurrentResume` — keep
- ⚠️ `FileUploadZone` (progress, PDF/size validation, retry, progressbar a11y) invalidates resumes/resume-current/auth-me — keep, restyle shell
- ❌ `setCurrentResume` mutation — keep
- ⚠️ `deleteResume` + confirm `Dialog` — keep (design trash has no confirm)
- ✅ current-resume card (filename/date/download) — adopt, wire
- ❌ version-history list (`resume-version-${id}`, is_current, download/set-current/delete) — re-create
- ❌ skeletons, `EmptyState` (`resume-empty-state`), `currentError` alert + toast, `formatDate`/`formatBytes` — keep
- ➕ extracted-skills (no live API) — design-only
**Must-preserve:** resumes+resume-current queries, FileUploadZone + 3-key invalidation, setCurrent/delete + confirm Dialog, `resume-version-${id}`+`resume-empty-state`, current card+download, skeletons, currentError alert+toast, formatters, all `resume.*` i18n.

## HR (detail)

### HRConsole
**Direction:** `adopt-design+graft-data` — **Risk:** Low.
- ❌ `getMe` greeting name — wire into design header
- ✅ links to all 4 stages (must keep applicants/exams/interviews/pipeline reachable)
- ➕ StatCards/activity/cost tiles are mock — don't show fabricated numbers as real
**Must-preserve:** real getMe greeting, all four route links, no fake cost/stat numbers.

### Applicants
**Direction:** `skin-onto-logic` — **Risk:** High — heaviest data+mutation page.
- ✅ `listApplicants` (`['hr','applicants']`) — wire into design table
- ❌ bulk PDF upload (multi-file, PDF-only, de-dupe, `MAX_BULK_FILES=25`, FormData target_job_title/level/jd) + progress + per-file failure list — re-create
- ❌ `bulkUploadApplicants`/`updateApplicantStatus`(shortlist/reject)/`rescoreApplicant` mutations + invalidation + toasts — re-create
- ◑ expandable detail: real `ats_summary`/`ats_breakdown`/strengths/concerns (design uses **fabricated** competencies) — replace with real
- ✅ score tone thresholds + rec badge; ✅ skeleton/empty/stagger (add — design lacks)
- ➕ search + SegTabs (stage) + detail drawer — adopt as presentation; reconcile `stage` vs live `status` enum
**Must-preserve:** bulk upload (25-cap/PDF/de-dupe/FormData), progress+failure list, shortlist/reject/rescore mutations+invalidation+toasts, real ats_breakdown/strengths/concerns, listApplicants, loading/empty, aria-labels.

### Exams
**Direction:** `adopt-design+graft-data` — **Risk:** Med.
- ✅ `listExams` — wire into cards
- ◑ `createExam` (title required, pass_threshold 60, time_limit minutes→sec, allow_retake) + navigate `/hr/exams/{id}` — re-create form
- ✅ ExamRow status/question_count/attempt_count/threshold — map taxonomy
- ✅/❌ loading + empty — preserve
- ➕ "Copy link" (dead in live) — drop or wire to assignment
**Must-preserve:** createExam (4 fields + validation), post-create navigate, listExams, status mapping, loading/empty, correct edit/results routes.

### ExamEditor
**Direction:** `skin-onto-logic` — **Risk:** High — full CRUD+publish+magic-link authoring vs thin mock w/ different question model.
- ❌ `getExam`+`listApplicants`+`listAssignments` queries — graft
- ❌ **MCQ model** (prompt + 2–6 options + `correct_index` + points) vs design's `{text,competency,seconds}` (incompatible) — re-create MCQ composer
- ❌ addQuestion/deleteQuestion mutations + validation — re-create
- ◑ publish/unpublish toggle (design buttons have no handlers) — re-create real mutation
- ❌ `thresholdMut` (pass_threshold onBlur) — re-create
- ❌ **attempt-lock** (`attempt_count>0` hides delete/composer + banner) — re-create (integrity)
- ❌ **assignment panel**: applicant checkboxes, `assignExam(ids)` → once-shown magic links, copy w/ clipboard fallback, `revokeAssignment`, assigned list — re-create (core)
- ➕ competency/seconds selects — drop (incompatible)
**Must-preserve:** MCQ model, addQuestion/deleteQuestion/updateExam, publish↔unpublish, pass_threshold save, attempt-lock + banner, full assignment flow (assign→once-shown links→copy→revoke+list), getExam/listApplicants/listAssignments, clipboard fallback.

### ExamResults
**Direction:** `adopt-design+graft-data` — **Risk:** Med.
- ❌ `getExam`+`listAttempts` — graft
- ◑ header summary (attempts/passed/threshold) → StatCards; **drop "Flagged"** (no field)
- ◑ row: score_percent tone + score_raw/max + attempt_no + submitted_at + in_progress/passed/failed — re-add (design lacks)
- ✅/❌ loading + empty — preserve
- ➕ CSV export + flagged + per-row scorecard — drop dead buttons (no live endpoint) or wire client-side CSV
**Must-preserve:** getExam+listAttempts, score_percent tone + raw/max + attempt_no + submitted_at, in-progress logic, threshold summary, loading/empty, correct back route; don't fake flagged/duration/CSV.

### HRInterviews
**Direction:** `skin-onto-logic` — **Risk:** High — invite-generation + magic-link + revoke vs read-only table.
- ❌ `listEligibleApplicants('any')`+`listInvites` — graft
- ❌ invite form (eligible-only select, en/hi/te, optional `scheduled_at`) — re-create (core)
- ❌ `createInvite` → once-shown magic-link + copy (clipboard fallback); `revokeInvite` (status `invited` only); eligibility gating — re-create
- ◑ InviteRow status taxonomy + real `composite_score`/scorecard link (not `/scorecard/demo`) — keep & restyle
- ✅/❌ loading + empty — preserve
- ➕ filter tabs + "Watch" (no handler) — presentation/omit
**Must-preserve:** invite form (eligible-only, en/hi/te, schedule), createInvite→once-shown link→copy, revokeInvite gated, eligibility rules, status taxonomy, real score+scorecard links, queries, loading/empty.

### HRPipeline
**Direction:** `skin-onto-logic` — **Risk:** High — paginated server funnel **table** w/ persisted decisions vs client Kanban w/ no persistence.
- ❌ `getPipeline({stage,limit:50,offset})` paginated — graft; **keep table model, not Kanban**
- ❌ pagination (PAGE_SIZE 50, prev/next, "x–y of n") — re-create
- ◑ stage filter tabs — reconcile design stages vs live `PipelineStage` enum (live wins)
- ❌ `setApplicantDecision(id,'hired'|'rejected',rationale)` + invalidate pipeline+analytics — re-create (design drag-drop never persists)
- ❌ canHire/canReject gating + rationale textarea (audit) + per-action loading — re-create
- ◑ per-stage scores (ATS/100, exam %, interview /10) + badges — re-create
- ❌ embedded `<HrAnalyticsPanel/>` — re-mount
- ➕ drag-drop Kanban — **drop** unless a stage-transition endpoint is built
**Must-preserve:** paginated getPipeline, prev/next, setApplicantDecision+invalidation, canHire/canReject, audit rationale, per-stage scores+badges, expandable detail + real scorecard links, embedded analytics, loading/empty. Don't replace persisted decisions with non-persisting Kanban.

### HRAnalytics
**Direction:** `adopt-design+graft-data` — **Risk:** Med.
- ❌ `getHrAnalytics` (`['hr','analytics']`) — graft
- ◑ 5 MetricTiles (applicants/+avg ATS, shortlisted %, exam pass rate, interviewed avg/10, hired %) — rebuild w/ rate()/fmtScore()
- ✅ funnel chart — feed real counts
- ❌ funnel empty + loading skeletons — re-create
- ◑ **both exports**: default embeddable panel (used by HRPipeline) + named `HRAnalyticsPage` — preserve both
- ➕ language donut / score-dist / trend charts — omit/placeholder (no API)
**Must-preserve:** getHrAnalytics, both exports, 5 funnel tiles + derivations, exact funnel/averages field names, funnel empty+loading; don't ship extra charts as real.

## Admin / Super Admin (detail)

### AdminOverview
**Direction:** `adopt-design+graft-data` — **Risk:** High — design is a health board, not the live KPI/trends page.
- ❌ `getOverview` (admin_ops :8004) → 6 KPIs (candidates, interviews, completed, completion_rate, avg_composite, avg_duration, today/7d/30d) — re-map design StatCards to all 6
- ❌ `getTrends` LineChart (30d), `getScoreDistribution` BarChart + 4 axis averages — re-create
- ❌ error/toast/skeletons, `data-testid="overview-tiles"` — keep
- ➕ microservice/AI-provider health — stub/omit (no endpoint)
**Must-preserve:** 6 KPI bindings, getOverview/getTrends/getScoreDistribution, trends line, score-dist bar + averages, completion_rate %, avg_duration fmt, error/skeleton, `overview-tiles` testid.

### AdminInterviews
**Direction:** `skin-onto-logic` — **Risk:** Med.
- ❌ `listInterviews` server-side (`['admin','interviews',filters]`, admin_ops) — bind
- ✅ debounced search `q` (400ms); ⚠️ status filter (design SegTabs All/Live/Completed/Flagged — re-map; "Flagged" has no live field)
- ❌ language filter, sort (created_at/composite, asc/desc), **CSV export**, pagination (PER_PAGE 20) — re-create
- ✅ row→`/admin/interviews/:id` (+ re-add keyboard a11y)
- ⚠️ columns: design drops Email/Language/Duration, adds Company (no live field) — re-add real, drop Company
- ❌ empty/error/skeleton + testids (`filter-search`,`export-csv-btn`,`interview-row-${id}`,`interviews-empty-state`) — keep
**Must-preserve:** listInterviews query, status/language/sort filters, debounced q, CSV export, pagination, row nav + keyboard, Email/Language/Duration cols, empty/error/loading, all testids.

### AdminInterviewDetail
**Direction:** `adopt-design+graft-data` — **Risk:** High — 0–10 vs 0–100, rationale, integrity panel.
- ❌ `getInterviewDetail` (admin_ops) — bind
- ✅ header candidate + status; ❌ session metadata grid — re-create
- ⚠️ composite score: design `ScoreRing` is **0–100** — re-create for 0–10
- ❌ radar chart; ❌ 4 axis bars w/ expandable rationale (`aria-expanded`/`controls`); ❌ strengths/improvements/summary; ❌ "no scorecard" branch — re-create
- ⚠️ integrity panel (integrity_score/100, by_type, flagged_seconds, event timeline, "not enabled" branch, AI-assist disclaimer) — re-map design's static flags to real data
- ⚠️ PDF export placeholder — keep disabled semantics
- ➕ transcript card — design-only (no endpoint)
**Must-preserve:** getInterviewDetail, 0–10 scale, radar, expandable rationale + a11y, strengths/improvements/summary, "no scorecard" branch, full integrity panel + disclaimer, metadata grid, skeleton/error/back; convert scale (don't relabel).

### AdminAnalytics
**Direction:** `skin-onto-logic` — **Risk:** Med.
- ❌ `getByRole`/`getByLanguage`/`getScoreDistribution` (admin_ops) — graft into design chart shells
- ❌ by-role count bar; ❌ **avg-axis-scores-by-role** grouped bar (unique live feature) — re-create
- ✅ by-language pie + per-lang avg legend — bind
- ❌ score-dist bar + 4 axis averages; loading/empty/error — re-create
- ➕ StatCards/AreaChart (mock) — only if mapped to real metric
**Must-preserve:** getByRole/getByLanguage/getScoreDistribution, by-role bar, **avg-axis-by-role bar**, language pie + avg legend, score-dist + averages, loading/empty/error, languageLabel.

### AdminJobJd
**Direction:** `adopt-design+graft-data` — **Risk:** High — live = JD PDF upload+parse; design = browse grid.
- ⚠️ `getJobs` (data_gateway) — re-bind grid
- ❌ job picker `Select`; **`FileUploadZone` + `uploadJd`** (PDF, 10MB, progress, char-count toast); accessToken gating; loading/error+Retry/empty; upload-zone remount on job change — re-create (upload entirely missing in design)
- ➕ competency chips / status / map-exam / new-role — presentation only (no endpoints)
**Must-preserve:** getJobs + accessToken gating, job Select, FileUploadZone + uploadJd (PDF/10MB/progress/toast), no-token/no-job rejection, loading/error+Retry/empty, remount on job change. Don't let the library UI replace the working upload.

### SuperAdminConsole
**Direction:** `adopt-design+graft-data` — **Risk:** High — read-only mock tables omit the create mutations that are the page's purpose.
- ⚠️ `listCompanies` (data_gateway `/admin/companies`) — re-bind
- ❌ **createCompany** mutation (form/toast/invalidate/auto-select) — re-create (design "Add tenant" is inert)
- ⚠️ company select drives HR panel + `hr_count` badge — keep interaction
- ❌ `listHrManagers(companyId)`; **createHrManager** (email/full_name/password=12345678, validation, dual invalidate); default-pw hint + `must_change_password` "pending/active" badge; HR loading/empty/"select a company" — re-create
- ➕ plan/seats/status columns, feature-flags tab, DPDP audit-log tab — stub/defer (no backend)
**Must-preserve:** listCompanies+listHrManagers, createCompany, createHrManager (default pw + validation + dual invalidate), select-drives-panel, real hr_count, must_change_password badge, default-pw hint, loading/empty states.

## Auth / Public / Shell (detail)

### Login — `skin-onto-logic`, Low
- ❌ RHF+Zod (email, password≥8) + FormMessage; login→getMe→setAuth; role-aware redirect (super_admin/hr/else); must_change_password→/change-password; isPending/aria-busy; toast errors; i18n — keep
- ⚠️ Google SSO via `googleLoginUrl()` (design button inert; adds inert Naipunyam) — graft real Google; Naipunyam disabled
- ➕ "Forgot password?" — careful (live change-password is forced bootstrap, not self-serve)
**Must-preserve:** RHF+Zod+FormMessage, login→getMe→setAuth, role-aware redirect, must_change_password guard, real googleLoginUrl(), isPending/aria-busy, toast, i18n.

### Register — `skin-onto-logic`, Low-Med
- ❌ RHF+Zod (full_name 2–100, email, password 8–128); registerUser→getMe→setAuth→/dashboard; isPending; toast; i18n — keep
- ⚠️ Google redirect (`signUpWithGoogle`) — graft; Naipunyam inert
- ➕ role SegTabs (Candidate/Hiring) — **don't adopt** without backend (role is server-assigned)
- ➕ DPDP consent checkbox — adopt as presentation (API has no consent param yet)
**Must-preserve:** register→getMe→setAuth, RHF+Zod (3 fields), Google redirect, isPending, toast, i18n; no role-based register routing without backend.

### GoogleCallback — `skin-onto-logic`, Med
- ❌ parse `?code`/`?state`; **`exchangedRef` StrictMode single-use guard** (token is get-then-delete); completeGoogleLogin→getMe→setAuth→/dashboard; `?error=` cancel; missing code/state; full error UI + retry + toast; i18n — keep
- ✅ spinner — restyle; ➕ AuroraField — add
**Must-preserve:** code/state parse, exchangedRef guard, completeGoogleLogin→getMe→setAuth, oauthError + missing-code branches, error UI + retry, i18n.

### ChangePassword — `adopt-design+graft-data`, Med
- ❌ forced bootstrap (shown when must_change_password, no shell) — keep semantics
- ⚠️ `changePassword(pw)` (new only) vs design's current+new+confirm — keep single-arg (or extend API deliberately)
- ❌ success → setAuth(clear flag) → `landingForRoles()` redirect — keep
- ✅ validation (min 8 + match) — adopt design strength meter, keep gate
- ➕ strength meter / "Back" link — adopt meter; **omit Back during forced reset** (no escape)
**Must-preserve:** must_change_password context, single-arg changePassword (or deliberate extension), setAuth clearing flag, landingForRoles redirect, min-8+match, isPending, i18n; no escape route in forced reset.

### PublicExam — `skin-onto-logic`, High
- ❌ token from URL **`#fragment`** + `X-Exam-Token` header (design reads path param — security regression) — keep hash
- ❌ `getPublicExam`; phase machine intro/taking/result; startExam (attempt+deadline); countdown + auto-submit; question radios + answeredCount; `submitExam` + `submittedRef` guard + retry; result screen (score/badge/expired); already-submitted gate; sticky taking-bar; i18n — keep/re-create
- ✅ invalid/expired/loading — restyle
- ➕ DPDP consent checkbox + fact grid — adopt intro chrome (bind to real exam fields)
**Must-preserve:** hash-fragment token (NOT path), getPublicExam, 3-phase machine, startExam, countdown+auto-submit, radios+answeredCount, submittedRef+retry, result screen, already-submitted gate, invalid/loading, i18n.

### InterviewInvite — `skin-onto-logic`, Med
- ❌ token from **`#fragment`** (design uses path) — keep
- ❌ `getInterviewInvite`; `redeemInterviewInvite(token,true)` → setAuth(guest) → store language → `history.replaceState` strip token → navigate `/interview/:sessionId`; localStorage language; already_completed gate; personalized `<Trans>` greeting; isPending/error; i18n — keep
- ✅ consent checkbox gate; ✅ scheduled_at — restyle/bind
- ➕ device-readiness checklist + avatar card — presentation
**Must-preserve:** hash token, getInterviewInvite, redeem→setAuth(guest)→language→token-strip→navigate, already_completed gate, Trans greeting, scheduled_at, invalid/loading/isPending/error, i18n.

### NotFound — `adopt-design+graft-data`, Low
- ❌ auth-aware home (`isAuthenticated ? '/dashboard' : '/'`) — graft; i18n; `aria-labelledby` — keep
- ➕ gradient 404 + AuroraField + dual CTA — adopt (make targets auth-aware)
**Must-preserve:** isAuthenticated-aware home link, i18n, aria-labelledby.

### Shell (AppShell + AuthLayout)
**Decision: KEEP the live `AppShell`; reskin it.** Design shell is presentational (hardcoded user, `role` prop, inert menus, no logout mutation, no must-change-password awareness, **no mobile nav**).
**Double-wrap fix:** when porting a design page, **strip its internal `<AppShell>` + `AuroraField`** — the live router provides the single shell + one aurora background. Auth/public pages stay outside the shell (Login/Register use AuthLayout/AuthSplit; rest are standalone).
- ⚠️ roles from `useAuth()` multi-role union (design takes a single `role` prop) — keep live
- ✅ candidate/admin/super-admin/HR nav — restyle (reconcile `/super-admin` vs `/superadmin`; keep HR Interviews link)
- ❌ real logout mutation (clear cache + clearAuth + /login) — keep (design "Sign out" is a Link)
- ⚠️ `LanguageSwitcher` real i18n — keep logic, restyle to design's Globe dropdown
- ⚠️ user menu Resume/History items — keep
- ❌ **mobile sheet nav** (design has none) — keep
- ✅ NavLink isActive + animated pill — restyle
- ❌ `INTERVIEW_LANGUAGE_KEY` seeding + per-route `AnimatePresence` transition — keep
- ➕ notifications bell / role badge — presentation/stub (no backend); Naipunyam SSO stays disabled
**Must-preserve:** roles from useAuth (multi-role), real logout mutation, mobile sheet nav, NavLink auto-active, real LanguageSwitcher, user-menu Resume+History, INTERVIEW_LANGUAGE_KEY, per-route transition, single-shell wrapping, i18n.

