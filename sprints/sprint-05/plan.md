# Sprint 5 Plan — 2026-06-01 to 2026-06-13

## Sprint Goal
The platform can score a completed interview, render a PDF scorecard, and an admin can trigger DPDP erasure — while Google OAuth SSO is live for the demo and the Naipunyam adapter skeleton is code-complete for the APSSDC bid.

---

## Capacity

| Who | Role | Availability | Notes |
|---|---|---|---|
| `backend-engineer` | feedback_billing + admin_ops + data_gateway + interview_core | 10 days | Primary on S5-001 through S5-009 |
| `frontend-engineer` | React app (web/) | 4 days | Scorecard display page (S5-007 FE side); no major FE stories this sprint |
| `ai-orchestrator` | LangGraph scoring node + scorer prompt | 3-4 days | S5-006 scoring pipeline |
| `devops-engineer` | CI + infra | 2-3 days | S5-008 Alembic CI |
| `security-auditor` | Sign off all new endpoints | 2 days (Day 6-7 midpoint + Day 9-10 end) | Must sign off S5-003b (Google OAuth), S5-004, S5-009 before merge |
| `code-reviewer` | All PRs | As needed (same-day turnaround) | CI gate required per Sprint 4 DoD; no merge without approval |
| Human engineer | Senior engineer | NOT YET ONBOARD | Hiring still in progress; no capacity planned |
| Founder | Review gate | ~1 hr/day | Scorecard demo slot needed Day 9 (2026-06-11); Google OAuth client_id/secret needed by Day 3 (10-min Google Cloud setup) |

AI agents work every day. No public holidays in this window.

---

## Committed Stories

| ID | Story | Assignee | Estimate | Acceptance Criteria |
|---|---|---|---|---|
| S5-001 | `feedback_billing` FastAPI app bootstrap — `app/main.py`, `/health`, `/health/deep` (Postgres + Redis reachable), settings from `.env` via Pydantic BaseSettings, uvicorn on port 8003 | `backend-engineer` | S | Service starts on port 8003. `GET /health` returns `{"status":"ok"}`. `GET /health/deep` checks Postgres + Redis and reports each. pytest smoke test passes. No hardcoded secrets. |
| S5-002 | `admin_ops` FastAPI app bootstrap — `app/main.py`, `/health`, `/health/deep` (Postgres + Redis reachable), settings from `.env`, admin-role JWT guard on all `/admin/*` routes, uvicorn on port 8004 | `backend-engineer` | S | Service starts on port 8004. `/health/deep` checks Postgres + Redis. Admin-only JWT middleware rejects non-admin tokens with 403. pytest smoke test passes. |
| S5-003a | Naipunyam SSO adapter skeleton — `AUTH_PROVIDER=naipunyam` path in `data_gateway`. Implements the full adapter against a stub SAML fixture (no live IdP needed — no contact with APSSDC yet). `GET /auth/sso/initiate` (302 redirect) and `POST /auth/sso/callback` (SAML validation, upsert user via `naipunyam_id`, issue JWT). Circuit breaker: mock IdP unavailable → 503. Config: `NAIPUNYAM_SAML_METADATA_URL`, `NAIPUNYAM_SAML_ENTITY_ID`, `NAIPUNYAM_SAML_ACS_URL`, `NAIPUNYAM_SAML_CERT_PATH`, `NAIPUNYAM_API_BASE_URL`, `NAIPUNYAM_API_KEY`. Real IdP creds plugged in when APSSDC provides them (bid process). | `backend-engineer` | M | All code paths fully implemented. Tests pass with stub fixture. `AUTH_PROVIDER=naipunyam` activates SSO path; `AUTH_PROVIDER=local` still works. Invalid SAML returns 401. Skeleton demonstrates architectural readiness for bid evaluation — live creds not required. |
| S5-003b | Google OAuth adapter — `AUTH_PROVIDER=google` in `data_gateway`. Implements `GET /auth/sso/google/initiate` (302 → Google consent) and `GET /auth/sso/google/callback` (exchange code → access token → userinfo, upsert user, issue JWT). Config: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`. This is the working SSO path for the college demo. Requires a Google Cloud project (10-min setup — founder action by Day 3). | `backend-engineer` | S | `AUTH_PROVIDER=google` activates OAuth path. Login with a real Google account issues a valid Intants JWT. Callback with an invalid code returns 401. Frontend "Login with Google" button routes to initiate endpoint. Tests mock Google's token/userinfo endpoints. `security-auditor` sign-off required before merge. |
| S5-004 | DPDP right-to-erasure endpoint in `admin_ops` — `POST /admin/users/{user_id}/dpdp/delete` (LLD §3.3 contract). Writes to `erasure_requests` table (LLD DDL §4). Cascading soft-delete: sets `users.deleted_at`, soft-deletes sessions + turns for that user, queues audio purge from S3 (async task). Audit log entry written. Response: 202 with `{ "request_id": "...", "scheduled_completion": "<now+30d>" }` per DPDP Act 2023 §12. | `backend-engineer` | M | `POST /admin/users/{user_id}/dpdp/delete` returns 202 and inserts into `erasure_requests`. `users.deleted_at` set within the same transaction. Sessions and turns for that user soft-deleted. Audit log row written. Duplicate request for same user returns 409. Non-admin caller returns 403. Unit tests cover happy path + duplicate + non-admin. `security-auditor` sign-off required before merge. |
| S5-005 | MinIO/S3 audio storage — after each turn, `interview_core` uploads the raw audio buffer to S3 (bucket: `intants-interview-audio`) and stores the object key in `turns.audio_s3_key`. Config: `S3_ENDPOINT`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_USE_SSL` (already in `.env.example`). Local dev uses MinIO. | `backend-engineer` | S | Turn completes → `turns.audio_s3_key` is non-null in DB. MinIO local dev: object visible in MinIO console. Upload failure is logged (structlog) but does not fail the turn (non-blocking). pytest test mocks boto3/aioboto3 and asserts the key is persisted. |
| S5-006 | End-of-session scoring — `generate_scorecard` LangGraph node in `interview_core` calls `feedback_billing` via HTTP POST `/internal/score` at session end. `feedback_billing/scorer.py` assembles transcript, calls Gemini with `scorer.j2` prompt (LLD §7.5), parses JSON output, writes `scorecards` row. `session.scored` WS event sent to frontend. Temperature 0.2 for consistency. | `ai-orchestrator` + `backend-engineer` | M | Interview session completes → `scorecards` row created within 15 s. `session.scored` WS event delivered to the frontend with `scorecard_id`. Scorer output matches JSON schema from LLD §10 (scores dict + strengths + improvements + summary). EN/HI/TE transcripts all produce valid scores. Unit test uses a fixture transcript. Gemini call uses dependency injection (no module singletons per Sprint 4 DoD). |
| S5-007 | Scorecard PDF generation — `feedback_billing/pdf_render.py` (WeasyPrint). Triggered synchronously after `scorer.py` writes the DB row. Generates a bilingual PDF scorecard from `scorecard.html.j2` template (candidate name, job title, composite score, dimension breakdown, strengths/improvements, APSSDC branding). Uploads PDF to S3 bucket `intants-interview-scorecards`, sets `scorecards.report_pdf_key`. Frontend: new `/scorecard/{scorecard_id}` page displays composite score + dimension breakdown; "Download PDF" link points to pre-signed S3 URL. | `backend-engineer` + `frontend-engineer` | M | `scorecards.report_pdf_key` is populated after session end. PDF is a valid file (non-zero bytes) with candidate name visible. Pre-signed S3 URL valid for 30 days. Frontend `/scorecard/{id}` page renders composite score + dimensions. `PDF_RENDER_FAIL` error triggers background retry (3x). WeasyPrint pinned to version in `requirements.txt`. |
| S5-008 | Alembic migrations CI check — GitHub Actions step that runs `alembic check` (detects model/DB drift) on every PR to `main`. Fails the build if pending migrations exist without a corresponding Alembic revision file. | `devops-engineer` | S | `.github/workflows/ci.yml` includes an `alembic-check` job. PR with an unapplied model change causes CI to fail with a clear message. PR with migrations in sync passes. Documented in `CONTRIBUTING.md` (one sentence). |
| S5-009 | `data_gateway` `/health/deep` endpoint — checks Postgres connectivity (SELECT 1) and Redis ping. Returns per-dependency status dict and overall 200/503. | `backend-engineer` | S | `GET /health/deep` returns `{"postgres":"ok","redis":"ok"}` (200 if all ok, 503 if either down). pytest covers each failure scenario with mocks. `security-auditor` confirms endpoint is read-only and does not expose credentials. |

**Total committed: 10 stories | S: 5, M: 3, L: 0, XS: 0** *(S5-010 Sentry deferred to Sprint 6)*

> Trim line: S5-007 PDF can ship without the frontend page (just the S3 key) if `frontend-engineer` days are needed elsewhere. P0 anchor: S5-003b (Google OAuth) + S5-004 must land regardless.

---

## Story Sequencing (Dependency Order)

```
Day 1 (Mon 2026-06-02):
  S5-001  feedback_billing bootstrap          [backend-engineer]
  S5-002  admin_ops bootstrap                 [backend-engineer, parallel after S5-001 done]
  S5-009  data_gateway /health/deep           [backend-engineer, parallel — no deps]
  S5-008  Alembic CI check                    [devops-engineer, independent]

Day 2-3 (Tue-Wed 2026-06-03/04):
  S5-005  S3 audio storage                    [backend-engineer — needs S5-001 started, uses shared S3 config]
  S5-010  Sentry wiring                       [devops-engineer + backend-engineer, parallel]
  S5-003  Naipunyam SSO — begin implementation [backend-engineer; Founder must provide test IdP creds by Day 3]

Day 4-5 (Thu-Fri 2026-06-05/06):
  S5-003  Naipunyam SSO — complete + tests    [backend-engineer]
  S5-004  DPDP right-to-erasure               [backend-engineer — needs S5-002 admin_ops live]
  security-auditor first pass: S5-003, S5-004, S5-009 reviews queued

Day 6-7 (Mon-Tue 2026-06-09/10) — Week 2:
  S5-006  End-of-session scoring              [ai-orchestrator + backend-engineer — needs S5-001 feedback_billing live]
  security-auditor sign-off: S5-003, S5-004 (must complete by Day 7)

Day 8-9 (Wed-Thu 2026-06-11/12):
  S5-007  Scorecard PDF + frontend page       [backend-engineer + frontend-engineer — needs S5-006 scorer done]
  Final integration: full session → score → PDF flow verified end-to-end
  Founder scorecard demo: pick job → interview → view scorecard (Day 9)

Day 10 (Fri 2026-06-13):
  Full regression: pytest + vitest + Playwright CI green
  code-reviewer final pass — all open PRs
  Sprint review 2026-06-13
```

---

## Definition of Done (Sprint 5 specific)

All stories must satisfy the project-wide DoD plus the following Sprint 5 additions:

1. Code merged to `main` on a `feat/<name>` branch via PR
2. All tests passing: pytest (all services), Vitest (web/), Playwright E2E — CI green required on every PR
3. `code-reviewer` approved (same-day turnaround SLA)
4. `security-auditor` approved for S5-003 (Naipunyam SSO), S5-004 (erasure), S5-009 (/health/deep)
5. No PII in Sentry payloads (verified by `security-auditor` for S5-010)
6. Scorecard demo to founder: end-to-end session → scorecard displayed on `/scorecard/{id}` page (Day 9)
7. Naipunyam SSO tested against real IdP sandbox (or stub fixture if sandbox not available by Day 3)
8. `turns.audio_s3_key` non-null in DB after a completed turn in local dev (S5-005 acceptance)
9. Listed in sprint review (2026-06-13)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Naipunyam IdP sandbox credentials not provided by Day 3 | Medium | High | S5-003 proceeds with SAML stub fixture; real-IdP smoke test added as a separate CI step once credentials arrive. Do not block merge on sandbox availability — gate it on fixture tests + security-auditor review. |
| WeasyPrint dependency conflicts with Python 3.12 + existing packages | Low | Medium | `backend-engineer` pins WeasyPrint version on Day 1 in a throwaway branch; confirms no conflict before committing to S5-007 scope. If conflicts appear, swap to `reportlab` (already evaluated in LLD). |
| Scoring latency: Gemini call at session end adds >5 s wall time | Medium | Low | Scoring is async (WS `session.scored` event sent after session close). 15-second SLA in acceptance criteria is generous. If Gemini cold-start exceeds 15 s, add `SCORING_FAIL` retry and notify candidate by email instead. |
| Human engineer still not onboard — agent-only sprint for 5th consecutive sprint | Certain | Medium | Founder provides >1 hr/day review. Flag immediately if any Naipunyam SAML implementation detail requires human architectural sign-off (SAML parsing libraries, certificate handling). |
| S5-003 + S5-004 security review bottleneck (both P0, both need sign-off same week) | Medium | High | `security-auditor` queued from Day 3. Both stories submitted for review simultaneously on Day 5. If sign-off slips past Day 7, S5-004 can merge conditionally with a TODO gate (`FEATURE_ERASURE=false` flag) — ungate after sign-off. |

---

## Dependencies

| Dependency | Owner | Status | Needed by |
|---|---|---|---|
| Naipunyam IdP sandbox URL + client_id + client_secret + SP cert | Founder | Not yet provided | S5-003 Day 3 (real test); Day 1 stub can proceed without it |
| `SENTRY_DSN` values for all 4 services in `.env` | Founder / DevOps | `.env` has `[GET NOW]` markers | S5-010 Day 2 |
| S3 credentials active in `.env` (MinIO for dev, R2 for demo) | Founder | MinIO default in `.env.example` — confirm R2 keys for demo | S5-005 Day 2 |
| `security-auditor` mid-sprint review window (Day 5-7) | `security-auditor` | Schedule by Day 2 | S5-003, S5-004, S5-009 |
| Founder scorecard demo slot | Founder | ~30 min on 2026-06-11 afternoon | Sprint review validation |
| CI passing on `main` (from Sprint 4 S4-006) | `devops-engineer` | GREEN — CI live | All PRs from Day 1 |

---

## Out of Scope (Explicitly Not in Sprint 5)

- Google OAuth adapter (B-021, P2) — defer to Sprint 6
- Admin dashboard cohort stats UI (B-019, P2) — defer to Sprint 6
- Load test scaffold (B-029, P2) — defer to Sprint 7
- OpenAI embeddings for job search (B-023) — defer to Sprint 6
- Billing event pipeline (C-005) — Phase 2, post-revenue
- 6-avatar DB seed (C-001) — Phase 2
- AWS Bedrock LLM swap — Bedrock approval still pending; no action this sprint
- Custom Three.js avatar (D-005) — Phase 3
- Any additional Indian language beyond EN/HI/TE — post-Phase 1
