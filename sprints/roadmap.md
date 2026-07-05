# Intants AI Voice Interview Platform — Roadmap

**RFP Ref:** ITC51-14022/9/2026-PROC-APTS
**Last updated:** 2026-05-29

---

## Phase 0 — Agent Team Setup (COMPLETE)
**Duration:** ~1 week (pre-Sprint 1)

- 11-agent AI team configured in `.claude/agents/`
- Project structure scaffolded (4 services, web, infra, docs)
- `interview_core` FastAPI scaffold running with `/health/deep` (all 6 deps green)
- Local Docker stack: Postgres 16 + pgvector, Redis 7, MinIO, Mailpit
- Design docs finalized: HLD, LLD, Final_stack v1.1
- Avatar vendor: D-ID Talks Streams (demo-only, CFO-approved 2026-05-28, sunset 2026-11-28); Simli removed

---

## Phase 1 — Foundation Build (Month 1–2)
**Target:** End-to-end auth, database, full voice interview loop, multilingual avatar experience.
**Demo-stack credentials active:** Gemini (LLM), Sarvam AI (STT/TTS), D-ID (avatar), OpenAI (embeddings).

| Sprint | Goal | Key Deliverable | Status |
|---|---|---|---|
| Sprint 1 | Auth end-to-end | Register/login/JWT/dashboard; cross-service JWT; pluggable AuthProvider; 42 tests passing; 5 security findings fixed | DONE — 7/7 stories |
| Sprint 2 | Text-only interview turn loop | Job list UI; WebSocket + JWT auth; LangGraph state machine (5 nodes); Gemini integration; session/turn persistence; Playwright E2E | DONE — 8/8 stories |
| Sprint 3 | Voice pipeline + avatar | Sarvam STT/TTS integrated; p95 < 2 s target; D-ID avatar rendered; DPDP consent capture; multilingual prompts EN/HI/TE | DONE — 7/7 stories |
| Sprint 4 (2026-05-28 → 2026-05-29) | Voice quality + security hardening | Streaming STT + TTS sentence-by-sentence; 4-persona system; language picker; CI/CD pipeline; 6 security fixes; DPDP revocation + retention | DONE — 14/14 stories |
| Sprint 5 (2026-06-01 → 2026-06-13) | Scoring + Naipunyam SSO | `feedback_billing` live; Gemini scorer; PDF scorecard; S3 audio storage; `admin_ops` bootstrap; Naipunyam SSO (P0 bid gate); DPDP right-to-erasure; Sentry | IN SPRINT — 10 stories |

---

## Phase 2 — Demo-Ready Product (Month 3–4)
**Target:** Full 10-minute interview loop, scorecard PDF, demo-able to colleges and APSSDC.

| Sprint | Goal | Key Deliverable | Target Window |
|---|---|---|---|
| Sprint 6 | Interview quality + observability | Google OAuth (P2); OpenAI job-search embeddings; admin cohort stats; Docker Compose full stack; load test scaffold; scorecard translation | 2026-06-16 → 2026-06-27 |
| Sprint 7 | College demo hardening | 6-avatar DB config + voice mapping; billing event pipeline; cohort management UI; 100-concurrent load test; Playwright full E2E suite | 2026-06-30 → 2026-07-11 |

---

## Phase 3 — Production Hardening (Month 5–6, post-revenue / pre-govt-bid)
**Target:** Migrate to AWS Mumbai (Tier 2 stack); DPDP compliance hardened; 99.5% uptime SLA; L1 bid submission ready.

| Sprint | Goal | Key Deliverable |
|---|---|---|
| Sprint 8–9 | AWS Tier 2 migration | AWS Bedrock LLM swap; AWS RDS + ElastiCache; S3 Mumbai (SSE-KMS); Helm charts; ArgoCD pipeline |
| Sprint 10 | Custom avatar | Three.js + Ready Player Me replacing D-ID; Rhubarb-Lipsync pipeline |
| Sprint 11 | Security + compliance | DPDP consent ledger hardening; penetration test + remediation; load test 20 lakh users capacity proof |
| Sprint 12 | Bid submission | Final RFP traceability matrix review; security-auditor sign-off; submission package |

---

## Hard Constraints Tracked

- Per-session variable cost <= Rs 12 (target Rs 10) — `cfo-cost-watcher` monitors each sprint
- Data residency: Mumbai region only (Phase 3 onwards; Phase 1-2 uses demo stack on Vercel/Railway/Neon)
- 22 Indian language support: EN/HI/TE Day-1 (Sprint 3 done); full 22 by Phase 3
- All phases gate on `security-auditor` sign-off before production deploy
- Naipunyam SSO (S5-003) — **shipping Sprint 5; APSSDC bid cannot be submitted without it**
- D-ID avatar sunset: 2026-11-28 (hard gate; Three.js custom avatar must be live before then)
- Simli removed entirely 2026-05-28 — do not re-introduce

---

## Milestone Summary

| Milestone | Target Date | Status | Gate |
|---|---|---|---|
| Text-only interview demo | Sprint 2 end | DONE | Sprint 2 review sign-off |
| Voice + avatar demo | Sprint 3 end | DONE | Sprint 3 review sign-off |
| Voice quality + CI pipeline | Sprint 4 end | DONE 2026-05-29 | Sprint 4 review sign-off |
| Full scorecard demo | Sprint 5 end 2026-06-13 | In progress | Founder scorecard demo + Sprint 5 review |
| Naipunyam SSO ready | Sprint 5 end 2026-06-13 | In progress | security-auditor + Sprint 5 review |
| College pilot demo | Sprint 7 end ~2026-07-11 | Planned | Sprint 7 review sign-off |
| APSSDC bid submission | ~2026-09-30 (est.) | Planned | security-auditor + founder sign-off |

---

## Velocity Trend

| Sprint | Committed | Done | Notes |
|---|---|---|---|
| Sprint 1 | 7 | 7 | 100% |
| Sprint 2 | 8 | 8 | 100% |
| Sprint 3 | 7 | 7 | 100% |
| Sprint 4 | 14 | 14 | 100% — TTS streaming delivered early (not deferred) |
| Sprint 5 | 10 | TBD | P0 anchor: S5-003 + S5-004 |
