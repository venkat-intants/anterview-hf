# Intants AI Voice Interview Platform — Project Context

> This file is auto-loaded into every Claude Code session for this project.
> All sub-agents inherit this context. Keep it tight and current.

---

## What We're Building

A **voice-first AI interview platform**. A candidate logs in, picks a job role, and talks to a realistic 3D avatar interviewer in their preferred language (EN / HI / TE Day-1, expanding to 22 Indian languages). The avatar speaks with natural lip-sync, listens to the candidate, asks intelligent follow-ups, and produces a structured scorecard (PDF + on-screen) at the end of a ~10-minute session.

## Target Markets (in priority order)

1. **Private engineering colleges & skill universities** — fastest path to revenue
2. **Corporate L&D and recruitment teams** — mid-term recurring revenue
3. **Government skilling bodies** (APSSDC, NSDC, state SDCs) — long-term high-value contracts via L1 bidding

## Tech Stack — TWO-TIER STRATEGY (decided 2026-05-27)

**Tier 1 — Demo stack (current default, ships in 10–14 days):**
| Layer | Choice |
|---|---|
| LLM | Google Gemini (`gemini-flash-lite-latest`) — primary; Groq as the listed alternative provider for disclosure parity (`LLM_PROVIDER=gemini\|groq\|anthropic\|bedrock`) |
| Speech | Sarvam (live) + Bhashini ULCA (pending approval, env-swappable via `SPEECH_*_PROVIDER`) |
| Avatar | **Tavus via LiveKit** (`AVATAR_PROVIDER=tavus`, echo-mode persona; Simli also supported via `AVATAR_PROVIDER=simli`) — demo-only (no India residency, over the ₹12 cap). D-ID was removed 2026-05-31. |
| DB | Neon (managed Postgres + pgvector) |
| Cache | Upstash (serverless Redis) |
| Storage | Cloudflare R2 (S3-compatible) |
| Email | Resend |
| Hosting | Vercel (frontend) + Railway (backend) |
| Embeddings | OpenAI text-embedding-3-large |
| Errors | Sentry |

**Tier 2 — Production stack (migrate post-revenue or pre-govt-bid):**
| Layer | Choice |
|---|---|
| LLM | AWS Bedrock Mumbai (`LLM_PROVIDER=bedrock`) |
| Speech | Bhashini (unchanged) + AI4Bharat fallback |
| Avatar | Three.js + Ready Player Me + Rhubarb-Lipsync (`AVATAR_PROVIDER=custom`) |
| DB | AWS RDS Mumbai (Postgres 16 + pgvector + pg_partman) |
| Cache | AWS ElastiCache Mumbai (Redis 7) |
| Storage | AWS S3 Mumbai (SSE-KMS) |
| Email | AWS SES Mumbai |
| Hosting | AWS EKS Mumbai (Multi-AZ) |
| IaC | Helm + ArgoCD |
| Observability | Prometheus + Grafana + Loki + OpenTelemetry |

**Common across both tiers:**
- Frontend: React 18 + TypeScript + Vite (PWA)
- Backend: Python 3.12 + FastAPI + LangGraph
- Auth: Pluggable provider (`local` / `google` / `keycloak` / `naipunyam`) via `AUTH_PROVIDER`
- Containers: Docker

**Why two-tier:** AWS Bedrock approval takes 1-5 days, custom avatar build takes 2-3 weeks. Demo tier ships now without compromising the production path — same code, env-swappable. See `docs/PROCUREMENT.md` for sign-up checklist.

## Architecture — 4 Microservices

1. **`interview_core`** — LiveKit (real-time WebRTC) transport, LangGraph brain, voice pipeline, real-time turn loop
2. **`data_gateway`** — Auth (pluggable), user management, Naipunyam SSO bridge, job sync
3. **`feedback_billing`** — Scoring, scorecards, PDF generation, billing pipeline
4. **`admin_ops`** — Admin dashboard, analytics, ops APIs

## Non-Functional Requirements (RFP-derived)

- **p95 turn latency < 2 seconds**
- **99.5% uptime** (Multi-AZ design)
- Scale to **20 lakh users**
- 10-minute session length
- 6 avatars (3 male / 3 female)
- **DPDP Act 2023 compliance** (consent ledger, India residency, right to erasure)
- India data residency — Mumbai region only for production

## Design Documents (read these before any work)

- `HLD.md` — High-level design + RFP traceability matrix
- `LLD.md` — Low-level design (full DDL, prompts, code, error matrix)
- `Final_stack.md` — Tech stack & economic model (~₹10–12/session variable cost)
- `CHANGES.md` — Cuts made in v1.1 (and why) — historical context
- `reserch.md` — Original RFP analysis (20 ambiguities flagged)
- `.env` — Every credential/API key the system needs (with `[GET NOW]` markers)

## Current Phase

**Phase 1+** — The demo product is **built and running**. Four FastAPI microservices + a LiveKit
real-time worker + a React/Vite frontend, all runnable via `dev-up.ps1` against cloud demo infra
(Neon/Prisma Postgres + Upstash + R2). Working features: pluggable auth + SSO + consent, the
real-time avatar interview (Tavus/Simli over LiveKit, 10-question window), Sarvam EN/HI/TE voice,
6 avatars + picker, scoring + PDF scorecard, and the admin/analytics dashboard. Production (Tier-2
AWS Mumbai) is the same code, env-swappable.

**Admin hierarchy (three tiers, since 2026-06-25):**
`platform_owner` → `super_admin` (per company) → `hr_manager` → `candidate`.
- **`platform_owner`** — the Intants core ("super super admin"), `support@intants.com`. `company_id` NULL.
  Creates/manages companies and creates **one `super_admin` per company**; owns platform feature flags +
  DPDP audit; also holds `admin` (analytics). Console: `/platform`.
- **`super_admin`** — a single company's super admin, `company_id` SET. Created by the platform owner;
  creates **HR managers for its own company only** (server-scoped). Console: `/superadmin`.
- **`hr_manager`** — runs the ATS/exam/interview workflow for its company. Console: `/hr`.
- **`admin`** — platform analytics dashboard role (separate from the hierarchy). Console: `/admin/*`.

## Coding Conventions

- **Python:** PEP 8, type hints, async/await for all I/O, ruff + mypy strict
- **TypeScript:** strict mode, no `any`, eslint + prettier
- **Tests:** pytest (backend), Vitest + RTL (frontend), Playwright (E2E)
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`)
- **Branches:** `feat/<name>`, `fix/<name>`, `chore/<name>` — no direct commits to `main`
- **All code reviewed by `code-reviewer` agent before merge**

## Hard Constraints (do NOT violate)

1. **Never use a paid service not listed in `Final_stack.md`** without `cfo-cost-watcher` approval
2. **Never hardcode secrets** — read from `.env` via Pydantic `BaseSettings`
3. **Never store PII** without a `dpdp_consent_ledger` entry
4. **Never deploy to production** without `security-auditor` sign-off
5. **All AI prompts must support EN / HI / TE** (Day-1 languages)
6. **Never use `--no-verify` or `--no-gpg-sign`** to bypass hooks
7. **Per-session variable cost must stay ≤ ₹12** (target ₹10) for L1 bid viability

## The AI Agent Team (see `.claude/agents/`)

Strategic: `product-manager`, `cto-architect`, `cfo-cost-watcher`
Build: `backend-engineer`, `frontend-engineer`, `ai-orchestrator`, `devops-engineer`
Review: `security-auditor`, `code-reviewer`
Watchdog: `market-researcher`
Coordination: `sprint-coordinator`

Invoke agents proactively — they exist to be used.
