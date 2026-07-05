# Intants AI Voice Interview Platform — Product Backlog

**RFP Ref:** ITC51-14022/9/2026-PROC-APTS
**Last updated:** 2026-05-29
**Maintained by:** sprint-coordinator
**Refined with:** product-manager

---

## Legend
- **Estimate:** S = <=1 day | M = 2-3 days | L = 4-5 days
- **Priority:** P0 = must-have (blocks demo/bid) | P1 = important | P2 = nice-to-have
- **Status:** Backlog | In Sprint | Done | Stale

---

## DONE — Sprint 1 (2026-05-27 → 2026-05-27+10)

| ID | Story | Priority | Estimate | Notes |
|---|---|---|---|---|
| S1-001 | `data_gateway` FastAPI app bootstrap | P0 | S | Done — port 8002, /health live |
| S1-002 | Postgres DDL — users/roles/user_roles/dpdp_consent_ledger via Alembic | P0 | S | Done — Alembic migrations clean |
| S1-003 | AuthProvider interface + LocalAuthProvider (bcrypt) | P0 | M | Done — pluggable, MockAuthProvider works in tests |
| S1-004 | Auth REST endpoints: register, login, refresh, logout | P0 | M | Done — JWT 15min/7d, Redis refresh tokens |
| S1-005 | React app bootstrap + auth UI (register, login, dashboard, logout) | P0 | M | Done — localhost:5173, real backend |
| S1-006 | Cross-service JWT validation in `interview_core` | P0 | S | Done — /api/me returns 200 with data_gateway JWT |
| S1-007 | Security review: auth flow + JWT spec | P0 | S | Done — 1 CRITICAL + 4 HIGH found and fixed |

**Sprint 1 velocity: 7/7 stories. Carry-over: 0.**

---

## DONE — Sprint 2

| ID | Story | Priority | Estimate | Notes |
|---|---|---|---|---|
| S2-001 | Full DB DDL via Alembic: sessions, turns, jobs, nos_competencies | P0 | M | Done |
| S2-002 | `data_gateway` job endpoints: GET /jobs, GET /jobs/{id} | P0 | S | Done |
| S2-003 | `interview_core` WebSocket endpoint with JWT auth guard | P0 | M | Done |
| S2-004 | LangGraph state machine: nodes, state schema, transitions, build.py | P0 | L | Done |
| S2-005 | Gemini LLM adapter + interviewer prompt + turn-loop integration | P0 | M | Done |
| S2-006 | Frontend: jobs list page + interview chat UI + complete screen | P0 | M | Done |
| S2-007 | Session + turn persistence in interview_core | P0 | S | Done |
| S2-008 | Playwright E2E: register → login → job → 3 turns → complete | P1 | S | Done |

**Sprint 2 velocity: 8/8 stories. Carry-over: 0.**

---

## DONE — Sprint 3

| ID | Story | Priority | Estimate | Notes |
|---|---|---|---|---|
| B-008 | Sarvam AI STT pipeline integration (stt_pipeline.py) | P0 | M | Done |
| B-010 | Sarvam AI TTS pipeline integration (tts_pipeline.py) | P0 | M | Done |
| B-011 | STT → LLM → TTS round-trip latency test (p95 < 2 s target) | P0 | S | Done |
| B-012 | D-ID avatar integration (Simli removed 2026-05-28) | P0 | L | Done — D-ID Talks Streams; Simli fully removed |
| B-014 | DPDP consent capture on registration (consent ledger write) | P0 | S | Done |
| B-025 | Rate limiting middleware (Redis token bucket) | P1 | S | Done |
| B-022 | Multilingual prompt templates: EN/HI/TE (LLD §7) | P0 | M | Done |

**Sprint 3 velocity: 7/7 stories. Carry-over: 0.**

---

## DONE — Sprint 4 (2026-05-28 → 2026-05-29)

| ID | Story | Priority | Estimate | Notes |
|---|---|---|---|---|
| S4-001 | D-ID avatar (Simli removed) — FE component + BE adapter | P0 | M | Done — D-ID Talks Streams, lip-sync working |
| S4-002 | Language picker (EN/HI/TE) wired to WS connect | P0 | S | Done |
| S4-003 | 4-persona system (warm_screener, direct_technical, scenario_led, balanced_fit_first) — EN/HI/TE deltas, deterministic selection, LangGraph wired | P0 | M | Done |
| S4-004 | Sarvam streaming STT pipeline (SarvamStreamingSTT, stt_streaming_enabled, batch fallback, reconnect) | P0 | M | Done |
| S4-005 | Streaming TTS sentence-by-sentence (TTS_STREAMING_ENABLED, sentence splitter, per-sentence audio_response) | P1 | M | Done — delivered in Sprint 4, not deferred |
| S4-006 | CI workflow (GitHub Actions — backend pytest/ruff/mypy, frontend build/vitest, E2E cost-gated) | P1 | M | Done — CI gate live on all PRs |
| S4-007 | `?token=` query-string auth gated behind app_env != production | P0 | S | Done — security-auditor signed off |
| S4-008 | Per-session audio buffer cap (10 MB, BUFFER_OVERFLOW + close 1008) + rate-limit carve-out for audio_chunk | P0 | S | Done — security-auditor signed off |
| S4-009 | Partial unique index on dpdp_consent_ledger (WHERE revoked_at IS NULL) | P0 | S | Done — security-auditor signed off |
| S4-010 | DELETE /consent revocation endpoint (DPDP §11) | P0 | S | Done — security-auditor signed off |
| S4-011 | 90-day retention cron (APScheduler, dry-run default, purge_expired_sessions) | P0 | S | Done — security-auditor signed off |
| S4-012 | TRUSTED_PROXY_COUNT config + get_client_ip() utility | P0 | S | Done — security-auditor signed off |
| S4-013 | LLM adapter dependency injection (no module-level singletons) | P1 | S | Done |
| S4-014 | ConsentModal LLM wording reconciled (Google Gemini / Groq) | P1 | XS | Done |

**Sprint 4 velocity: 14/14 stories. Carry-over: 0.**

---

## ACTIVE SPRINT — Sprint 5 (2026-06-01 → 2026-06-13)
See `/sprints/sprint-05/plan.md` for full detail.

| ID | Story | Priority | Estimate | Assignee | Status |
|---|---|---|---|---|---|
| S5-001 | `feedback_billing` FastAPI app bootstrap (port 8003, /health, /health/deep) | P1 | S | `backend-engineer` | In Sprint |
| S5-002 | `admin_ops` FastAPI app bootstrap (port 8004, /health, admin JWT guard) | P1 | S | `backend-engineer` | In Sprint |
| S5-003 | Naipunyam SSO adapter (AUTH_PROVIDER=naipunyam) — SAML initiate + callback, upsert user, circuit breaker | P0 | L | `backend-engineer` | In Sprint |
| S5-004 | DPDP right-to-erasure endpoint (admin_ops POST /admin/users/{id}/dpdp/delete, erasure_requests table, cascade soft-delete, audit log) | P0 | M | `backend-engineer` | In Sprint |
| S5-005 | MinIO/S3 audio storage (turns.audio_s3_key populated after each turn) | P1 | S | `backend-engineer` | In Sprint |
| S5-006 | End-of-session scoring (feedback_billing scorer.py, Gemini scorer.j2, scorecards table, session.scored WS event) | P1 | M | `ai-orchestrator` + `backend-engineer` | In Sprint |
| S5-007 | Scorecard PDF generation (WeasyPrint, S3 upload, report_pdf_key, /scorecard/{id} FE page) | P1 | M | `backend-engineer` + `frontend-engineer` | In Sprint |
| S5-008 | Alembic migrations CI check (alembic check step in GitHub Actions CI) | P1 | S | `devops-engineer` | In Sprint |
| S5-009 | `data_gateway` /health/deep endpoint (Postgres + Redis + Naipunyam reachability) | P1 | S | `backend-engineer` | In Sprint |
| S5-010 | Sentry integration — sentry_sdk.init() across all 4 services, user_id + session_id tags, no PII in payloads | P1 | S | `devops-engineer` + `backend-engineer` | In Sprint |

---

## SPRINT 6 CANDIDATES — Interview Quality + Observability

| ID | Story | Priority | Estimate | Status |
|---|---|---|---|---|
| B-031 | Resume upload + parse — `POST /api/users/me/resume` in `data_gateway` uploads PDF to S3 (users/resumes/{user_id}.pdf), extracts text with pypdf, stores in `users.resume_text`. LangGraph interview graph passes extracted text into the system prompt so the AI can ask about the candidate's actual projects. Acceptance: resume text visible in interviewer prompt; unit + integration tests; pypdf added to deps. | P1 | M | Backlog |
| B-032 | JD document upload + scorer enrichment — `POST /api/jobs/{id}/jd-document` in `data_gateway` uploads PDF/DOCX, parses to `jobs.jd_text`. Scorer prompt extended with `jd_text` so evaluation is grounded in the actual role requirements. Acceptance: scorer prompt includes JD text when present; S3 key stored in `jobs.jd_s3_key`; tests. | P1 | S | Backlog |
| B-019 | Admin dashboard cohort stats endpoint + UI | P2 | M | Backlog |
| B-023 | OpenAI embeddings for job search (nos_competencies table) | P1 | M | Backlog |
| B-026 | Structured logging completeness audit (ensure all services) | P1 | S | Backlog — structlog in use; Sentry wired in S5-010; this is a completeness pass |
| B-027 | Docker Compose production-like compose file (all 4 services) | P1 | S | Backlog |
| B-029 | Load test scaffold: 100 concurrent sessions (Locust) | P2 | M | Backlog |
| C-001 | 6-avatar configuration in DB seed (LLD §4 INSERT) | P1 | S | Backlog |
| C-002 | Avatar persona + voice mapping per language | P1 | S | Backlog |
| C-004 | Scorecard translation (Sarvam translate API) | P2 | M | Backlog |

---

## SPRINT 7 CANDIDATES — Hardening + College Demo

| ID | Story | Priority | Estimate | Status |
|---|---|---|---|---|
| C-003 | Cohort management UI (admin) | P2 | L | Backlog |
| C-005 | Billing event pipeline (meter.py, flusher.py) | P1 | M | Backlog |
| B-003 | Alembic migrations CI check (moved to S5-008 — done in Sprint 5) | — | — | Moved to Sprint 5 |

---

## PHASE 3 BACKLOG — Production Hardening (post-revenue / pre-govt-bid)

| ID | Story | Priority | Estimate | Status |
|---|---|---|---|---|
| D-001 | AWS Bedrock LLM adapter (LLM_PROVIDER=bedrock) | P0 | M | Backlog |
| D-002 | AWS RDS Postgres migration (from local/Neon) | P0 | L | Backlog |
| D-003 | AWS ElastiCache Redis migration | P0 | M | Backlog |
| D-004 | Helm charts for all 4 services | P0 | L | Backlog |
| D-005 | Three.js + Ready Player Me custom avatar (AVATAR_PROVIDER=custom) | P1 | L | Backlog |
| D-006 | Rhubarb-Lipsync pipeline | P1 | L | Backlog |
| D-007 | Security penetration test + remediation | P0 | L | Backlog |
| D-008 | Load test: 20 lakh users capacity proof | P0 | L | Backlog |

---

## Backlog Health
- Total items: 43 (within 50-item limit)
- P0 items: 14 | P1: 19 | P2: 10
- Sprint 1 done: 7 stories | Sprint 2 done: 8 stories | Sprint 3 done: 7 stories | Sprint 4 done: 14 stories
- Cumulative velocity: 36 stories across 4 sprints (avg 9/sprint; Sprint 4 was 14 — capacity increased with stable agent team)
- Next refinement: 2026-06-09 (Sprint 5 Week 2 Monday) with product-manager — pull Sprint 6 candidates to ready state
- Stale items: none (all items touched within last 2 sprints)
- Note: B-003 (Alembic CI check) moved into Sprint 5 as S5-008; B-026 Sentry partially done (structlog live) — S5-010 completes it
