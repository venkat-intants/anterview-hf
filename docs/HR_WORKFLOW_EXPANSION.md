# Expansion to HR Workflow — ATS · Exam · Scheduling · Interview

> ⚠️ **Superseded in part (2026-06-25): the role model is now THREE tiers.**
> Where this doc says "super-admin creates companies + HR managers", that is now
> split: a new **`platform_owner`** (the Intants core, `support@intants.com`)
> creates/manages **companies** and creates **one `super_admin` per company**;
> each **`super_admin`** is **company-scoped** and creates **HR managers for its
> own company only**. So the hierarchy is
> `platform_owner → super_admin (per company) → hr_manager → candidate`.
> The rest of this doc (ATS, exam, scheduling, interview wiring) still applies.
>
> Status: **PROPOSED** (planning) · Author: Intants · Date: 2026-06-20
> Turns the AI interview engine into a full **HR hiring assistant**: a recruiter
> logs in, screens applicants by resume (ATS), runs an MCQ exam, shortlists by
> threshold, then schedules the AI interview — all from one console.

---

## 1. The target workflow

```
SUPER ADMIN (you — admin@intants.com)
   │  creates & manages companies + HR managers
   ▼
HR MANAGER (e.g. hr@gmail.com)                 ← created by super admin
   │
   1. Upload applicant resumes (single or bulk)
   2. Resumes auto-scored by ATS (LLM + JD match) → screening results
   3. Schedule an MCQ EXAM (HR authors questions + sets pass threshold)
   4. Applicants take the exam → auto-graded → scores to HR
   5. HR shortlists by exam threshold
   6. HR schedules the AI INTERVIEW for the shortlist
   7. Existing interview engine runs → scorecard → final results
```

Every step produces a record the HR can see in one **applicant pipeline** view:
`Resume score → Exam score → Interview score → Decision`.

---

## 2. What we reuse (already built)

| Capability | Reuse |
|---|---|
| Pluggable auth, JWT, `roles` + `user_roles` | Extend with new roles; add company dimension |
| Resume upload + PDF text extraction + versioning + S3 | As-is for ATS input |
| Gemini scorer (prompt + JSON + retry) in feedback_billing | Template for **resume ATS** + **exam essay** grading |
| APScheduler (already runs the DPDP retention cron) | Add exam/interview reminder + launch jobs |
| Admin analytics + audit log (admin_ops) | Extend for HR funnel + exam analytics |
| Interview engine (interview_core + LiveKit) | Reused unchanged as the final stage |
| `jobs` model (title, level, JD, company_name) | Backbone for "roles/postings" |
| Frontend `ProtectedRoute`/`AdminRoute`, AuthContext, per-domain API client | Pattern for HR/super-admin areas |

**Net-new:** role hierarchy + company tenancy, create-user endpoints, resume ATS
scoring, the entire exam portal, the email service (Resend — only config stubs
exist today), and future-dated scheduling.

---

## 3. Architecture decisions (recommended — confirm before build)

| # | Decision | Recommended | Why |
|---|---|---|---|
| D1 | **Tenancy** | Multi-company: super-admin creates Companies; each HR + their applicants/exams belong to one company; data is scoped by `company_id` | You said "each company" — this is the model to sell to many colleges/firms; isolates data per client |
| D2 | **Applicant identity** | HR uploads resumes → system auto-creates lightweight **candidate accounts** scoped to the company; applicants get an **emailed magic-link** to take the exam/interview (no self-registration) | Reuses the existing candidate-account + interview flow; HR-driven as you described |
| D3 | **Exam service** | Start as an **`assessment` module** inside a service (cheapest/fastest); split into its own microservice later if load needs it | Avoids a 5th always-on service (per-session cost cap ≤₹12); same code can be extracted later |
| D4 | **Email** | Implement **Resend** (in `shared/email/`), SMTP fallback from existing config | Tier-1 stack choice; needed for invites/reminders |
| D5 | **Exam grading** | MCQ = instant deterministic grading; optional short-answer = reuse Gemini scorer | MCQ needs no LLM (free, instant); LLM only if you add written questions |
| D6 | **Default password** | `12345678` (demo bootstrap) + force **change-on-first-login** + store only bcrypt hash | Security: a shared default is fine to bootstrap, not to leave; 8 chars satisfies the login rule |

Roles after this work: `super_admin` (you) → `hr_manager` → `candidate` (applicant).
`admin` (existing analytics admin) stays for platform ops.

---

## 4. Phased plan

### Phase 0 — Identity foundation: roles, companies, user management  *(~1.5–2 wk)*
**Goal:** you (super-admin) can create/manage companies and HR managers; HR can log in to an empty console.
- Add roles `super_admin`, `hr_manager`; promote `admin@intants.com` → `super_admin`.
- `companies` table; `users.company_id`; tenant-scoping helpers + RBAC guards (`super_admin`, `hr_manager`).
- Super-admin API: CRUD companies, CRUD HR managers (create with email+password, force-change-on-first-login).
- Frontend: **Super-Admin Console** (companies + HR managers) and an **HR Console shell** (nav + empty pages).
- Reuse: auth, roles, AdminRoute pattern, audit log. Net-new: company model, create-user endpoints, console UIs.

### Phase 1 — Applicants + Resume ATS screening  *(~2 wk)*
**Goal:** HR uploads resumes and sees ATS scores + a ranked shortlist.
- Email service (Resend) in `shared/email/`.
- HR uploads applicant resumes (single + **bulk**) → applicant (candidate) records scoped to company; reuse resume extraction/S3.
- **Resume ATS scoring**: new `resume_scores` table; LLM scorer (reuse `scorer.py` pattern) — fit vs JD, skills match, experience, red flags, 0–100 + rationale.
- HR screening dashboard: ranked applicants, ATS detail, manual shortlist toggle.
- Reuse: resume pipeline, Gemini scorer, S3. Net-new: bulk import, ATS scorer, screening UI, email.

### Phase 2 — MCQ Exam portal  *(~2–2.5 wk)*
**Goal:** HR authors an MCQ exam with a pass threshold; applicants take it; auto-graded; results to HR.
- Data model: `exams`, `exam_questions` (MCQ: stem, options, correct, marks), `exam_attempts`, `exam_answers`.
- HR exam authoring UI (create exam, add questions, set duration + **pass threshold**).
- Applicant exam delivery: invite link → timed exam runner → submit → **instant auto-grade**.
- Results to HR; auto-flag pass/fail vs threshold; shortlist by exam score.
- Reuse: assessment/session pattern, scheduler, email invites. Net-new: all exam tables + UIs + grading.

### Phase 3 — Scheduling + interview integration  *(~1.5–2 wk)*
**Goal:** HR schedules exams and interviews on dates; invites/reminders go out; shortlisted → interview.
- Add `scheduled_at` to assessments/sessions; APScheduler jobs for reminders + activation windows.
- HR scheduling UI (pick applicants + date/time) for exams and interviews.
- Wire **exam-passed shortlist → schedule AI interview** → existing interview engine opens for the applicant.
- Reuse: APScheduler, interview engine, email. Net-new: scheduled-at fields, scheduling UI, reminder jobs.

### Phase 4 — Unified pipeline, results & polish  *(~1.5–2 wk)*
**Goal:** one applicant pipeline view end-to-end; consolidated decision + analytics.
- Applicant pipeline board: Resume → Exam → Interview → **Decision** (advance/reject/hire) per applicant.
- Consolidated results + export (CSV/PDF); HR funnel analytics (applied → screened → exam → interview → offer).
- DPDP for new data (consent, retention, erasure cascade for applicants/exams), audit, role hardening.
- Reuse: admin analytics, DPDP retention/erasure, PDF. Net-new: pipeline board, decision model, funnel.

---

## 5. Timeline summary

| Phase | Scope | Est. effort |
|---|---|---|
| 0 | Roles, companies, user management | ~1.5–2 weeks |
| 1 | Applicants + Resume ATS | ~2 weeks |
| 2 | MCQ Exam portal | ~2–2.5 weeks |
| 3 | Scheduling + interview wiring | ~1.5–2 weeks |
| 4 | Unified pipeline + results + polish | ~1.5–2 weeks |
| **Total** | full HR assistant | **~8–10 weeks** |

Estimates assume focused AI-assisted development with you testing each phase.
Each phase ships independently usable — you get value before the whole thing is done.

---

## 6. New data model (additions only)

- `companies` (id, name, slug, created_by, created_at, …)
- `users.company_id` (nullable for super_admin/platform admin)
- `resume_scores` (id, resume_id, job_id, company_id, overall, skills/experience/fit sub-scores, rationale, created_at)
- `exams` (id, company_id, job_id, title, duration_min, pass_threshold, created_by, status)
- `exam_questions` (id, exam_id, stem, options JSONB, correct_index, marks, order)
- `exam_attempts` (id, exam_id, applicant_user_id, scheduled_at, started_at, submitted_at, score, passed)
- `exam_answers` (id, attempt_id, question_id, selected_index, is_correct)
- `sessions.scheduled_at` (interview scheduling)
- `applicant_pipeline` (or a status field linking resume→exam→interview→decision per applicant+job)

All in the single Postgres (now in **ap-southeast-1 Singapore** — fast). Schema via data_gateway Alembic migrations.

---

## 7. Cross-cutting

- **Security/RBAC:** every HR endpoint scoped to `company_id`; super-admin-only company/HR management; force-change default passwords; audit every create/role change.
- **DPDP:** applicants are PII — consent capture at upload, retention window, erasure cascade across resume/exam/interview data.
- **Cost:** MCQ grading is free (deterministic); resume ATS uses one Gemini call per resume — keep within the ≤₹12/candidate envelope; batch where possible.
- **Email deliverability:** Resend with a verified domain; invites + reminders + results.

---

## 8. Decisions — CONFIRMED (2026-06-20)

1. **Tenancy (D1): Multi-company.** ✅ Super-admin creates Companies; HRs, applicants, exams, jobs are scoped by `company_id`.
2. **Applicant access (D2): Email magic-link.** ✅ HR uploads resumes → applicant records; applicants get emailed secure links (no self-registration). Requires the Resend email service.
3. **Exam service (D3): Module inside a service.** ✅ Built as an `assessment` module (not a 5th always-on microservice); extractable later.

Phase 0 build order: add roles → companies table + `company_id` → make `admin@intants.com` super-admin → super-admin create-company/create-HR endpoints → super-admin & HR console UIs.
