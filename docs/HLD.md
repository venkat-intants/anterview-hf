# HIGH-LEVEL DESIGN (HLD) — Lean v1.1

## AI-Based Multilingual Interview Platform — APSSDC

**Document:** HLD v1.1 (lean rewrite of v1.0)
**Project:** APSSDC Naipunyam Interview Platform (RFP ITC51-14022/9/2026-PROC-APTS)
**Owner:** Platform Engineering
**Status:** Draft for pre-bid alignment
**Change log:** see `CHANGES.md` for the explicit list of items cut from v1.0.

---

## 1. SCOPE & GOALS

**In scope:** Voice-first, multilingual (EN/HI/TE Day-1) AI mock-interview platform serving up to 20 lakh APSSDC users via Naipunyam SSO, with per-session billing and 99.5% uptime SLA over 5 years.

**Out of scope (v1):**
- Naipunyam Portal internals
- Placement workflows
- Classroom training
- Physical infrastructure procurement
- Counseling / career-guidance agent (RFP title plural; body never scopes it — pending pre-bid confirmation)
- Multi-tenant isolation (single tenant: APSSDC)
- Cross-region DR (single-region Multi-AZ meets 99.5%)

**Top NFRs:**

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | End-to-end conversational latency | < 2.0 s p95 |
| NFR-02 | Platform uptime | 99.5% monthly |
| NFR-03 | Concurrent interviews | 5,000 (Phase-1) → 50,000 (Phase-3) |
| NFR-04 | Data residency | India only (DPDP Act 2023) |
| NFR-05 | Encryption | TLS 1.3 in transit, AES-256 at rest |
| NFR-06 | Languages Day-1 | English, Hindi, Telugu |
| NFR-07 | Browser support | Chrome, Firefox, Safari, Edge (latest 2) |

---

## 2. SYSTEM CONTEXT DIAGRAM

```
                  ┌────────────────────────────┐
                  │      APSSDC CANDIDATE      │
                  │   (Student / Job-Seeker)   │
                  └─────────────┬──────────────┘
                                │  Web / PWA
                                ▼
   ┌──────────────────────────────────────────────────────┐
   │                                                      │
   │     AI-BASED MULTILINGUAL INTERVIEW PLATFORM         │
   │              (4 microservices)                       │
   │                                                      │
   └──┬──────────┬─────────────────┬───────────────────┬──┘
      │          │                 │                   │
      ▼          ▼                 ▼                   ▼
 ┌─────────┐ ┌──────────┐  ┌──────────────┐   ┌─────────────────┐
 │NAIPUNYAM│ │ BHASHINI │  │  ANTHROPIC   │   │  APSSDC ADMIN   │
 │ PORTAL  │ │  STT/TTS │  │  CLAUDE on   │   │  & REPORTING    │
 │(SSO+API)│ │ (Govt.AI)│  │  BEDROCK     │   │   DASHBOARD     │
 └─────────┘ └──────────┘  └──────────────┘   └─────────────────┘
```

**External actors:**
- **Candidate** — primary user, voice-first interaction
- **Naipunyam Portal** — identity provider + source of truth for user/job/training data
- **Bhashini ULCA** — Govt of India STT + TTS for Indic languages
- **Anthropic Claude on Bedrock (Mumbai)** — interviewer + scorer LLM
- **APSSDC Admin** — internal users consuming dashboards, reports, cohort analytics

---

## 3. LOGICAL ARCHITECTURE — 4-SERVICE LAYERED VIEW

> **Lean change from v1.0:** 8 microservices collapsed to **4 deployable services**. Same functional coverage, ~50% fewer Helm charts, ~50% fewer pipelines, simpler source-code handover (RFP Pg 23).

```
┌──────────────────────────────────────────────────────────────────┐
│  L1 — PRESENTATION LAYER                                         │
│  • Candidate Web/PWA (React + WebRTC + Avatar)                   │
│  • Admin Console (React)                                         │
└──────────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────────┐
│  L2 — EDGE / API LAYER                                           │
│  • Cloudflare WAF + CDN  • Kong API Gateway  • WebSocket Hub     │
└──────────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────────┐
│  L3 — APPLICATION SERVICES (4)                                   │
│                                                                  │
│  ┌─────────────────────┐   ┌─────────────────────┐               │
│  │  interview_core     │   │   data_gateway      │               │
│  │                     │   │                     │               │
│  │  • Auth (SSO+JWT)   │   │  • Naipunyam sync   │               │
│  │  • WebSocket hub    │   │  • Jobs (real +     │               │
│  │  • LangGraph        │   │    virtual)         │               │
│  │    orchestrator     │   │  • NOS/NSQF KB      │               │
│  │  • AI pipeline      │   │                     │               │
│  │    (STT/LLM/TTS)    │   │                     │               │
│  └─────────────────────┘   └─────────────────────┘               │
│                                                                  │
│  ┌─────────────────────┐   ┌─────────────────────┐               │
│  │  feedback_billing   │   │     admin_ops       │               │
│  │                     │   │                     │               │
│  │  • Scorer           │   │  • Admin dashboards │               │
│  │  • PDF render       │   │  • Reports/exports  │               │
│  │  • Billing meter    │   │  • Email (SendGrid) │               │
│  │  • Invoice gen      │   │  • SMS (MSG91)      │               │
│  └─────────────────────┘   └─────────────────────┘               │
└──────────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────────┐
│  L4 — AI / ML LAYER (lives inside interview_core)                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │   STT    │  │   VAD    │  │   LLM    │  │      TTS         │  │
│  │ Adapter  │  │ Server-  │  │  Agent   │  │   Adapter        │  │
│  │(Bhashini)│  │   side   │  │(LangGraph│  │  (Bhashini)      │  │
│  │          │  │          │  │+ Claude) │  │                  │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────┐  ┌──────────────────┐  ┌──────────────────────┐    │
│  │  Rubric  │  │  NOS/NSQF KB +   │  │  JD Embedding +      │    │
│  │  Scorer  │  │  Question Bank   │  │  Job-Match Engine    │    │
│  └──────────┘  └──────────────────┘  └──────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────────┐
│  L5 — DATA LAYER                                                 │
│  • PostgreSQL 16 (RDS Multi-AZ) + pgvector                       │
│  • Redis 7 (ElastiCache) — sessions, cache, streams              │
│  • S3 (Mumbai) — audio, transcripts, PDF reports                 │
│  • Vault — secrets/KMS                                           │
└──────────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────────┐
│  L6 — PLATFORM / INFRA LAYER                                     │
│  • AWS EKS Mumbai (single region, Multi-AZ)                      │
│  • ArgoCD • Helm • Prometheus/Grafana/Loki                       │
│  • OpenTelemetry • GitHub Actions • HashiCorp Vault              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. COMPONENT CATALOG — 4 SERVICES

### 4.1 Frontend — Candidate Web/PWA

| Aspect | Detail |
|---|---|
| Tech | React 18 + TypeScript + Vite, Tailwind, Service Worker (PWA) |
| Audio | WebRTC mic capture (Opus @ 48 kHz), Web Audio API playback |
| Avatar | Ready Player Me GLB models via three.js + Rhubarb viseme driver |
| VAD | Silero v5 in WebAssembly (client-side barge-in detection) |
| Transport | WebSocket (Socket.IO) for control + audio frames |
| Responsive | Desktop, tablet, mobile breakpoints; offline shell |

### 4.2 Service: `interview_core`

> Combines old: orchestrator + auth + WebSocket hub.

| Aspect | Detail |
|---|---|
| Tech | Python 3.12 + FastAPI + LangGraph + Keycloak |
| Auth | SAML 2.0 (Naipunyam IdP), OIDC, OAuth 2.0; JWT 15-min, refresh 8-hr; RBAC roles: candidate, admin, auditor, cohort_manager |
| State Machine | INIT → INTRO → TECH_Q → BEHAV_Q → CAND_Q → CLOSE → SCORED |
| Concurrency | Async per-session; horizontally scaled (HPA on WS conn count) |
| Memory | Last 8 turns in-process + full session in Redis |
| AI Pipeline | STT (Bhashini → AI4Bharat fallback), LLM (Claude Sonnet 4.6 via Bedrock Mumbai), TTS (Bhashini → AI4Bharat fallback) |
| Tools (LLM-callable) | `end_interview` (single tool — `score_turn` removed in v1.1) |

### 4.3 Service: `data_gateway`

> Combines old: naipunyam-sync + jobs-context.

| Aspect | Detail |
|---|---|
| Tech | Python 3.12 + FastAPI + httpx (async) |
| Naipunyam sync | Just-in-time at session start; cache 60 min in Redis; soft-degrade to stale cache |
| Data fetched | Profile, resume, interested jobs, training history, assessments |
| Jobs | Real (from Naipunyam) + Virtual (user-created with AI-assisted JD) |
| Virtual JD generation | Claude Sonnet 4.6 with pre-generation safety filter + post-generation review |
| Storage | Postgres `jobs` table + pgvector embeddings (OpenAI `text-embedding-3-large`) |
| NOS KB | Curated JSON from skillindia.gov.in, indexed in pgvector |

### 4.4 Service: `feedback_billing`

> Combines old: feedback + billing.

| Aspect | Detail |
|---|---|
| Tech | Python 3.12 + FastAPI + WeasyPrint |
| Scoring | **End-of-session only** (rolling per-turn scoring cut in v1.1); Claude Sonnet 4.6 scorer with cached rubric |
| Scoring axes | Communication, Technical Knowledge, Problem-Solving, Confidence (0–10 each) |
| Output | JSON scorecard + Markdown notes + PDF report (Indic-font-aware via WeasyPrint) |
| Translation | Lazy regeneration in target language via Claude |
| Billing source-of-truth | Redis Streams (append-only) → Postgres + S3 Parquet hourly |
| Unit | Per 10-min session (RFP Form C-2) |
| Invoice | Quarterly PDF + Parquet; GST 18% applied |

### 4.5 Service: `admin_ops`

> Combines old: admin-api + notification.

| Aspect | Detail |
|---|---|
| Tech | Python 3.12 + FastAPI |
| Dashboards | Per-cohort, per-district, per-job-role, per-skill-gap |
| Exports | CSV, PDF (signed); scheduled email delivery |
| Real-time tiles | Concurrent sessions, error rate, p95 latency |
| Email | SendGrid (transactional) |
| SMS | MSG91 |
| DPDP | Right-to-erasure endpoint with 30-day SLA |

---

## 5. DATA ARCHITECTURE

### 5.1 Logical Entity-Relationship

```
┌──────────┐   1     N   ┌───────────────┐   N    1   ┌───────────┐
│   USER   │─────────────│   SESSION     │────────────│   JOB     │
│          │             │               │            │ (real or  │
│ (mirror  │             │ • lang        │            │  virtual) │
│  from    │             │ • avatar      │            │           │
│ Naipunyam│             │ • status      │            └─────┬─────┘
└─────┬────┘             │ • duration_s  │                  │
      │                  │ • cost_units  │                  │ N
      │ 1                └───┬──────┬────┘                  │
      │                      │      │                       ▼
      │                    1 │      │ 1               ┌────────────┐
      │                  ┌───▼──┐ ┌─▼─────────┐       │ NOS_ROLE   │
      │                  │TURNS │ │ SCORECARD │       │ + COMPETEN │
      │                  │      │ │           │       │            │
      │                  │•role │ │•scores{}  │       └────────────┘
      │                  │•text │ │•notes     │
      │                  │•audio│ │•lang      │
      │                  │ _url │ └───────────┘
      │                  └──────┘
      │ 1
      ▼
┌────────────────┐
│  USAGE_EVENT   │   (immutable, billing)
│ • session_id   │
│ • minutes      │
│ • timestamp    │
└────────────────┘
```

### 5.2 Key Tables (Postgres)

| Table | Key columns | Notes |
|---|---|---|
| `users` | `user_id PK`, `naipunyam_id UNIQUE`, `state`, `district` | Soft-deleted; consent flags |
| `sessions` | `session_id PK`, `user_id FK`, `job_id FK`, `lang`, `avatar_id`, `started_at`, `ended_at`, `status`, `duration_s` | Partitioned by month |
| `turns` | `turn_id PK`, `session_id FK`, `role`, `text`, `audio_s3_key`, `latency_ms`, `created_at` | Partitioned by month; **no `per_turn_signals` column** (cut v1.1) |
| `scorecards` | `scorecard_id PK`, `session_id FK UNIQUE`, `scores JSONB`, `summary`, `lang` | JSON for 4 axes |
| `jobs` | `job_id PK`, `source`, `title`, `jd_text`, `skills[]`, `embedding vector(1536)` | pgvector |
| `nos_competencies` | `nos_code PK`, `nsqf_level`, `competency_text`, `embedding vector(1536)` | Seeded from skillindia.gov.in |
| `usage_events` | `event_id PK`, `session_id FK`, `minutes`, `event_ts` | Append-only |
| `invoices` | `invoice_id PK`, `quarter UNIQUE`, ... | Quarterly |
| `audit_log` | `event_id PK`, `actor`, `action`, `resource`, `ts`, `details JSONB` | DPDP requirement; partitioned monthly |
| `dpdp_consent_ledger` | `consent_id PK`, `user_id FK`, `consent_version`, `granted`, `ts` | DPDP audit |
| `erasure_requests` | `request_id PK`, `user_id FK`, `status`, `scheduled_for` | Right-to-erasure |

### 5.3 Object Storage Layout (S3 Mumbai, SSE-KMS)

```
s3://apssdc-interview-prod/
├── audio/{year}/{month}/{session_id}/turn_{n}.opus
├── transcripts/{year}/{month}/{session_id}.json
├── reports/{year}/{month}/{session_id}.pdf
├── billing/{year}/{quarter}/usage_snapshot.parquet
└── consent/{user_id}/{ts}_consent.json
```

Lifecycle: hot 30 days → IA 90 days → Glacier 1 yr → delete after retention (per APSSDC policy / DPDP).

---

## 6. AI PIPELINE — DETAILED DESIGN

```
USER SPEAKS (browser)
   │
   ▼
[1] Silero VAD (WASM, client)
       speech_end detected → flush 5s Opus buffer
   │
   ▼ WSS audio frame
[2] STT Adapter ──────► Bhashini ULCA streaming
       partial @ 200ms                         (fallback: AI4Bharat)
       final   @ 500ms
   │
   ▼
[3] interview_core (LangGraph node: "process_user_turn")
       updates session_state, appends to history
   │
   ▼
[4] Claude Sonnet 4.6 (via Bedrock Mumbai)
       inputs:
         • system_prompt (cached: persona + interview policy + rubric)
         • job_context  (cached per session: NOS competencies + JD)
         • profile      (cached per session: skills, experience tier)
         • last 8 turns
       tools available: [end_interview]
       streaming output: tokens
   │  (first token ~400ms)
   ▼
[5] TTS Adapter ──────► Bhashini Indic TTS
       streaming Opus chunks; first audio @ 300ms
   │
   ▼ WSS audio frame
[6] Browser plays + drives viseme lip-sync on avatar
   │
   ▼ If user starts speaking before TTS done:
      Silero VAD client fires → "barge_in" → server cancels
      in-flight LLM + TTS → loop back to [1]
```

**Latency budget (p95 target < 2 s):**

| Stage | Budget |
|---|---|
| VAD detect end-of-speech | 100 ms |
| Network upload (audio) | 150 ms |
| STT final transcript | 500 ms |
| Orchestrator overhead | 100 ms |
| LLM first token (cache hit) | 400 ms |
| TTS first audio chunk | 300 ms |
| Network download (audio) | 150 ms |
| Client buffer + play | 100 ms |
| **Total** | **1,800 ms** |

---

## 7. KEY SEQUENCE FLOWS

### 7.1 Session Start (SSO + Context Hydration)

```
Candidate    Naipunyam    interview_core    data_gateway    Postgres   Redis
   │            │              │                  │             │         │
   ├─Login─────►│              │                  │             │         │
   │◄──SAML AuthN response─────┤                  │             │         │
   ├─POST /sso/callback────────┤                  │             │         │
   │            │              ├─validate         │             │         │
   │◄──JWT (15 min)────────────┤                  │             │         │
   ├─POST /sessions ─────────► │                  │             │         │
   │            │              ├─hydrate ────────►│             │         │
   │            │              │                  ├─fetch user──┼────────►│
   │            │              │                  │◄──cache hit?┼─miss────┤
   │            │              │                  ├─load────────►│         │
   │            ├◄─────────────┼──────────────────┤             │         │
   │            ├─profile,resume,jobs,training────►│             │         │
   │            │              │                  ├─persist─────►│         │
   │            │              │                  ├─cache 60min─┼────────►│
   │            │              │◄─────────────────┤             │         │
   │◄──session_id + WS URL─────┤                  │             │         │
```

### 7.2 Interview Turn Loop (Steady State)

```
Candidate    Browser     WS Hub     interview_core    STT       LLM       TTS
   │           │           │             │             │         │         │
   ├─speaks───►│           │             │             │         │         │
   │           ├─VAD end──►│             │             │         │         │
   │           ├─audio────►│             │             │         │         │
   │           │           ├─frame──────►│             │         │         │
   │           │           │             ├─stream─────►│         │         │
   │           │           │             │◄──partial───┤         │         │
   │           │           │             │◄──final─────┤         │         │
   │           │           │             ├─prompt + tools────────►│         │
   │           │           │             │◄──token stream────────┤         │
   │           │           │             ├─text────────────────────────────►│
   │           │           │             │◄──audio chunks──────────────────┤
   │           │           │◄────────────┤                                │
   │           │◄──audio───┤             │                                │
   │◄──hears──┤            │             │                                │
   │           │           │ (loop)      │                                │
   │           │  IF user speaks during AI:                                │
   │           │  VAD → "barge_in" → cancel LLM+TTS → goto top            │
```

### 7.3 Session End → Scoring → Report

```
interview_core    feedback_billing    Postgres   S3            admin_ops
     │                  │                 │         │              │
     ├─end_interview tool fires           │         │              │
     ├─POST /scorecard {session_id, transcript}────►│              │
     │                  ├─call scorer──── │         │              │
     │                  ├─persist scorecard──────►  │              │
     │                  ├─render PDF (lang-specific)──────────►   │              │
     │                  ├─emit usage_event─────────►│              │
     │                  ├─trigger notification─────────────────────►│
     │                  │                 │         │              ├─email + SMS────►Candidate
```

---

## 8. INTEGRATION VIEW

```
┌─────────────────────────────────────────────────────────────────┐
│              4-SERVICE PLATFORM                                 │
└───┬────────┬────────────┬────────────┬───────────────────┬──────┘
    │        │            │            │                   │
    │ SAML   │ REST       │ WSS        │ HTTPS (Bedrock)   │ SMTP/SMS
    │ 2.0    │ (Bearer)   │            │                   │
    ▼        ▼            ▼            ▼                   ▼
┌────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────┐ ┌────────────┐
│Naipunya│ │Naipunyam│ │Bhashini │ │  Anthropic   │ │ SendGrid + │
│  IdP   │ │ Data API│ │STT+TTS  │ │  Claude on   │ │   MSG91    │
│        │ │         │ │  ULCA   │ │  Bedrock     │ │            │
└────────┘ └─────────┘ └─────────┘ └──────────────┘ └────────────┘
```

| Integration | Protocol | Auth | Failure mode |
|---|---|---|---|
| Naipunyam SSO | SAML 2.0 | Signed assertions, cert rotation 1 yr | Fall back to local login (admins only) |
| Naipunyam Data API | REST + Bearer | OAuth client credentials | Cached profile, soft-degrade |
| Bhashini STT/TTS | WebSocket | API key | Auto-failover to AI4Bharat self-hosted |
| Anthropic Bedrock | HTTPS | AWS IAM role | Circuit breaker → retry once → graceful end |
| SendGrid (email) | REST | API key | Queue and retry |
| MSG91 (SMS) | REST | API key | Queue and retry |

---

## 9. DEPLOYMENT VIEW (AWS Mumbai, ap-south-1, single region Multi-AZ)

```
                            ┌──────────────────┐
                            │  Cloudflare WAF  │
                            │     + CDN        │
                            └─────────┬────────┘
                                      │
                            ┌─────────▼────────┐
                            │   AWS ALB        │
                            │   (HTTPS+WSS)    │
                            └─────────┬────────┘
                                      │
   ┌──────────────────────────────────▼───────────────────────────┐
   │           AWS EKS — apssdc-interview-prod                    │
   │                                                              │
   │   ┌─── Node Group: app (c6i.2xlarge × 6, autoscale)──────┐  │
   │   │  • kong-gateway  • keycloak                          │  │
   │   │  • interview_core (6-50 pods, HPA)                   │  │
   │   │  • data_gateway  (3-10 pods)                         │  │
   │   │  • feedback_billing (3-10 pods)                      │  │
   │   │  • admin_ops (2-5 pods)                              │  │
   │   └───────────────────────────────────────────────────────┘  │
   │   ┌─── Node Group: ai (g5.2xlarge × 2, on-demand)────────┐  │
   │   │  • stt-fallback (AI4Bharat)  • tts-fallback          │  │
   │   └───────────────────────────────────────────────────────┘  │
   │   ┌─── Node Group: ops (t3.large × 2)────────────────────┐  │
   │   │  • prometheus  • grafana  • loki  • argocd  • vault  │  │
   │   └───────────────────────────────────────────────────────┘  │
   └──────────────────────────────────────────────────────────────┘
                                      │
   ┌──────────────────────────────────▼───────────────────────────┐
   │   Managed Data Plane                                         │
   │   • RDS PostgreSQL 16 Multi-AZ (db.r6g.xlarge)              │
   │   • ElastiCache Redis 7 cluster (cache.r6g.large × 3)       │
   │   • S3 (3 buckets: media, billing-WORM, backups)            │
   │   • KMS, Secrets Manager (mirrored to Vault)                │
   └──────────────────────────────────────────────────────────────┘
                                      │
   ┌──────────────────────────────────▼───────────────────────────┐
   │   External SaaS (within India)                               │
   │   • Anthropic Bedrock (ap-south-1)                           │
   │   • Bhashini ULCA endpoints                                  │
   └──────────────────────────────────────────────────────────────┘
```

**Environments:** `dev` → `staging` → `prod`, isolated VPCs, separate KMS keys, GitOps via ArgoCD.

> **Cut in v1.1:** DR pilot-light in ap-south-2 (Hyderabad) — single region with Multi-AZ meets 99.5%. Add only if APSSDC explicitly mandates DR.

---

## 10. SECURITY ARCHITECTURE

| Control | Implementation |
|---|---|
| Transport encryption | TLS 1.3 everywhere (ALB, ingress); pod-to-pod via K8s NetworkPolicies |
| At-rest encryption | AES-256 via KMS for RDS, S3, EBS volumes |
| Auth | SSO via Naipunyam (SAML 2.0), JWT 15-min, refresh 8-hr, MFA for admins |
| RBAC | Role matrix in Keycloak; enforced at API Gateway + service-level |
| Secret mgmt | HashiCorp Vault; no secrets in code or configmaps |
| Audit logging | All admin actions + data access to immutable bucket (S3 Object Lock) |
| Input validation | Pydantic schemas on every endpoint; LLM input sanitization |
| Content safety | Anthropic safety filters + custom denylist on Virtual Job JDs |
| Network | Private subnets for app/data; NAT GW; security groups least-privilege |
| Vulnerability mgmt | Trivy scan in CI, Snyk on dependencies, quarterly CERT-In VA/PT |
| DPDP compliance | Explicit consent at signup, data-deletion API (30-day SLA), retention caps |
| Backups | RDS PITR 35 days; S3 versioning enabled |

> **Cut in v1.1:**
> - Internal mTLS via Istio — K8s NetworkPolicies + ALB→pod TLS satisfy RFP §11 "E2E encryption".
> - Cross-region weekly snapshot — single region with Multi-AZ + PITR is sufficient.

---

## 11. SCALABILITY & PERFORMANCE

| Concern | Strategy |
|---|---|
| Concurrent sessions | Stateless interview_core pods + Redis session state → linear HPA on WS conn count |
| LLM throughput | Bedrock on-demand initially; provisioned throughput at >10k sessions/day; prompt caching for 90% input savings |
| STT/TTS throughput | Bhashini quota negotiated; AI4Bharat GPU fallback autoscales |
| DB scaling | Read replicas for reporting; partition `sessions`/`turns`/`audit_log` by month |
| Cache | Redis: profile cache (60 min), JD cache (1 day), prompt cache (Bedrock-managed 5 min) |
| CDN | Cloudflare for static assets, avatar GLBs, JS bundles |
| Cost control | Aggressive prompt cache, single end-of-session scoring (no rolling), off-peak archival to Glacier |

**Capacity numbers (Phase-2 target):**
- 50,000 concurrent interviews
- 200,000 sessions/day
- 6 M sessions/month
- Bandwidth: ~30 Gbps peak (audio in+out, Opus @ 24 kbps)
- DB writes: ~1,500 turn-inserts/sec peak

---

## 12. OBSERVABILITY

```
┌─────────────────────────────────────────────────────────────┐
│  Every service emits:                                       │
│   • Metrics → Prometheus (RED + USE + business KPIs)        │
│   • Logs    → Loki (structured JSON, trace-id correlated)   │
│   • Traces  → OpenTelemetry → Grafana Tempo                 │
└─────────────────────────────────────────────────────────────┘

Critical dashboards:
  • Latency: STT p95 / LLM TTFT p95 / TTS first-chunk p95 / E2E p95
  • Reliability: 5xx rate, WS drop rate, LLM/STT/TTS error rate, fallback rate
  • Business: concurrent sessions, sessions/day, completion %, language mix
  • Billing: minutes consumed (per day/quarter), revenue projection
  • Security: failed logins, RBAC denials, anomalous access patterns
  • Cost: $/session by component, Bedrock spend, Bhashini spend, infra

Alerting (PagerDuty):
- P1: uptime < 99.5% (rolling 30 min), E2E latency p95 > 3s, billing event loss
- P2: STT/TTS fallback rate > 10%, LLM error rate > 2%
- P3: cache hit rate < 80%, DB connections > 80%
```

---

## 13. FAILURE MODES & RECOVERY

| Failure | Detection | Mitigation |
|---|---|---|
| Bhashini STT down | Error rate > 5% over 1 min | Auto-failover to AI4Bharat (transparent) |
| Bhashini TTS down | Same | Same |
| Bedrock Claude timeout | Single-call timeout 5s | Retry once; on second fail → graceful "let's pause" + log |
| Naipunyam SSO down | Heartbeat probe | Block new logins; existing sessions continue (JWT valid 15 min) |
| Naipunyam data API down | Sync failure | Fall back to cached profile; if no cache → minimal profile flow |
| RDS failover | Multi-AZ auto-failover | App reconnects via RDS proxy; ~10s pause |
| Pod OOM | K8s restart | Stateless → next request lands on healthy pod |

> **Cut in v1.1:** Region-failure DR row removed. Region failure is rare and 99.5% SLA does not require cross-region. If APSSDC mandates, add DR as a Phase-2 addendum.

---

## 14. RFP → HLD TRACEABILITY MATRIX

| RFP Pg | RFP Requirement | HLD Section |
|---|---|---|
| 8 | 20L users, 10-min sessions | §1, §11 |
| 8 | 6 avatars (3M/3F) | §4.1 |
| 8 | EN/HI/TE Day-1 | §4.2 (STT/TTS in interview_core) |
| 9 | <2s response latency | §6 (budget table) |
| 9 | VAD + barge-in | §4.1, §6 |
| 9 | Adaptive by experience tier | §4.2 (LangGraph), §4.4 (scorer) |
| 9 | NSQF/NCVT alignment | §4.4 (data_gateway NOS KB), §5.2 |
| 10 | SSO SAML/OAuth/OIDC | §4.2, §7.1 |
| 10 | Naipunyam data sync | §4.3, §7.1 |
| 10 | Virtual Job creation | §4.3 |
| 10 | Scoring on 4 axes | §4.4, §5.2 (scorecards) |
| 11 | Cloud-native microservices | §3 (4 services), §9 |
| 11 | 99.5% uptime | §1 NFR-02, §11, §12 |
| 11 | LLM/STT/TTS | §4.2 |
| 11 | E2E encryption + RBAC | §10 |
| 11 | Responsive web | §4.1 |
| 12 | 15-day deployment | Roadmap (Phase-1) |
| 20 | Per-session quarterly billing | §4.4, §5.2 (usage_events) |
| 23 | Data ownership + source code handover | §10, deliverable |

---

## 15. OPEN HLD DECISIONS (need pre-bid answer)

| # | Decision needed | Why blocking |
|---|---|---|
| D1 | Concurrent vs total user count | Sizing of EKS, RDS, Bhashini quota |
| D2 | Hosting: bidder cloud vs APSSDC-provided (MeghRaj) | Network design, deployment topology |
| D3 | Naipunyam API spec + sandbox | §4.3 cannot be implemented blindly |
| D4 | NOS bank source — APSSDC provides or vendor curates | §4.4 question generation |
| D5 | Pricing unit: per-min vs per-10-min-session | §4.4 metering |
| D6 | Counseling agent in scope? | RFP title plural but body never scopes it. Out of v1 unless confirmed. |
| D7 | DR required? | Out of v1 unless confirmed. |

---

## NEXT ARTIFACTS

1. **LLD (already written)** — `LLD.md`
2. **CHANGES.md** — explicit list of items cut from v1.0 with rationale
3. **OpenAPI 3.1 spec files** — one per service, ready for codegen
4. **Sequence diagrams** — barge-in handling, error recovery, scoring (Mermaid)
5. **Capacity model spreadsheet** — knobs for concurrent users → infra cost
6. **Threat model** — STRIDE per external boundary
7. **Pre-bid query letter** — formalize the 7 open decisions above

---

## END OF DOCUMENT (HLD v1.1)
