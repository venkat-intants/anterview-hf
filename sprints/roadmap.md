# Intants AI Voice Interview Platform — Roadmap

**RFP Ref:** ITC51-14022/9/2026-PROC-APTS
**Last updated:** 2026-05-29

---

> ## ⚠ SUPERSEDED — historical planning document (banner added 2026-08-07)
>
> **Last substantively updated 2026-05-29.** Roughly ten weeks of shipped work
> are not reflected below: Sprints 5–7 are complete, the three-tier admin
> hierarchy (`platform_owner` → `super_admin` → `hr_manager`) landed 2026-06-25,
> the intelligence and agent packages landed 2026-08-01, and several vendor and
> stack decisions have changed. Sprint statuses such as "IN SPRINT" and
> "In progress" below are frozen at 2026-05-29 and should not be read as the
> current state.
>
> **Kept, not deleted.** The Sprint 1–5 plan of record is RFP-traceability
> evidence: it shows what was committed, when, and against which RFP clause.
>
> **For the current state, read [`CLAUDE.md`](../CLAUDE.md)** ("Current Phase"
> and the two-tier stack tables), which is maintained.
>
> **Vendor lines have been corrected inline rather than left to this banner**,
> because a stale *supplier* name in a bid-adjacent document is a factual error
> about a third party, not merely an out-of-date status. **D-ID was removed on
> 2026-05-31 and is not the avatar vendor.** The current demo-tier vendor is
> **Tavus via LiveKit** (`AVATAR_PROVIDER=tavus`), with **Simli** supported as
> the alternative (`AVATAR_PROVIDER=simli`) — so the old "Simli removed
> 2026-05-28 — do not re-introduce" constraint is also void. Corrected: the
> Phase 0 vendor line, the Phase 1 credentials line, the Sprint 10 row and the
> Hard Constraints block. D-ID mentions in the *completed-sprint* rows are left
> as written — they are an accurate record of what shipped at the time.

---

## Phase 0 — Agent Team Setup (COMPLETE)
**Duration:** ~1 week (pre-Sprint 1)

- 11-agent AI team configured in `.claude/agents/`
- Project structure scaffolded (4 services, web, infra, docs)
- `interview_core` FastAPI scaffold running with `/health/deep` (all 6 deps green)
- Local Docker stack: Postgres 16 + pgvector, Redis 7, MinIO, Mailpit
- Design docs finalized: HLD, LLD, Final_stack v1.1
- Avatar vendor: ~~D-ID Talks Streams (demo-only, CFO-approved 2026-05-28, sunset 2026-11-28); Simli removed~~
  **CORRECTED 2026-08-07 — D-ID was removed from the stack on 2026-05-31 and is
  not a supplier to this project.** The demo-tier avatar vendor is **Tavus via
  LiveKit** (`AVATAR_PROVIDER=tavus`, echo-mode persona), with **Simli** as the
  supported alternative (`AVATAR_PROVIDER=simli`). Both are demo-only: neither
  offers India data residency and both exceed the ₹12/session cap, so the
  Tier-2 production path remains the custom Three.js + Ready Player Me +
  Rhubarb-Lipsync avatar (`AVATAR_PROVIDER=custom`). See `CLAUDE.md`.

---

## Phase 1 — Foundation Build (Month 1–2)
**Target:** End-to-end auth, database, full voice interview loop, multilingual avatar experience.
**Demo-stack credentials active:** Gemini (LLM), Sarvam AI (STT/TTS), ~~D-ID~~
**Tavus via LiveKit** (avatar — corrected 2026-08-07; D-ID removed 2026-05-31),
OpenAI (embeddings).

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
| Sprint 10 | Custom avatar | Three.js + Ready Player Me replacing the hosted avatar vendor (**Tavus** — corrected 2026-08-07; this row said "D-ID", removed 2026-05-31); Rhubarb-Lipsync pipeline |
| Sprint 11 | Security + compliance | DPDP consent ledger hardening; penetration test + remediation; load test 20 lakh users capacity proof |
| Sprint 12 | Bid submission | Final RFP traceability matrix review; security-auditor sign-off; submission package |

---

## Hard Constraints Tracked

- Per-session variable cost <= Rs 12 (target Rs 10) — `cfo-cost-watcher` monitors each sprint
- Data residency: Mumbai region only (Phase 3 onwards; Phase 1-2 uses demo stack on Vercel/Railway/Neon)
- 22 Indian language support: EN/HI/TE Day-1 (Sprint 3 done); full 22 by Phase 3
- All phases gate on `security-auditor` sign-off before production deploy
- Naipunyam SSO (S5-003) — **shipping Sprint 5; APSSDC bid cannot be submitted without it**
- ~~D-ID avatar sunset: 2026-11-28 (hard gate; Three.js custom avatar must be live before then)~~
  **CORRECTED 2026-08-07:** D-ID was removed from the stack on 2026-05-31, so
  this sunset date is void. The constraint it encoded still holds under a new
  vendor: the hosted avatar (**Tavus via LiveKit**) has no India data residency
  and is over the ₹12/session cap, so the custom Three.js avatar must be live
  before any government bid or production deploy.
- ~~Simli removed entirely 2026-05-28 — do not re-introduce~~
  **CORRECTED 2026-08-07:** Simli is supported again as the alternative avatar
  provider (`AVATAR_PROVIDER=simli`). This "do not re-introduce" line is void.

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
