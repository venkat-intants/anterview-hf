# FINAL STACK — AI ORCHESTRATOR DECISION (Lean v1.1)

Decisive, opinionated, RFP-grounded. No "options" — these are the picks.
Companion to HLD.md / LLD.md / CHANGES.md.

---

## THE BIG PICTURE — 1-LINE ARCHITECTURE

> **React PWA (client)** ⇄ **Cloudflare WAF + Kong API Gateway** ⇄ **4 Python/FastAPI services** ⇄ **{Bhashini STT, Claude Sonnet 4.6, Bhashini TTS, Ready Player Me viseme avatar}** + **Postgres/pgvector + Redis + S3** on **AWS Mumbai EKS** (single region, Multi-AZ) with **Keycloak SSO** into **APSSDC Naipunyam**.

---

## THE 4 SERVICES (lean from 8 in v1.0)


| Service              | Owns                                                                                  | Replicas (Phase-1) |
| -------------------- | ------------------------------------------------------------------------------------- | ------------------ |
| **interview_core**   | Auth (SSO+JWT+RBAC), WebSocket hub, LangGraph orchestrator, AI pipeline (STT/LLM/TTS) | 6 → 50 (HPA)       |
| **data_gateway**     | Naipunyam sync, jobs (real + virtual), NOS/NSQF KB                                    | 3 → 10             |
| **feedback_billing** | Scorecard generation, PDF render, billing meter, quarterly invoice                    | 3 → 10             |
| **admin_ops**        | Admin dashboards, reports, notifications (email + SMS)                                | 2 → 5              |


---

## FINAL COMPONENT MAP — WHAT CONNECTS TO WHAT

```
┌──────────────────────────────────────────────────────────────────────┐
│  CLIENT  —  React 18 + TypeScript PWA                                │
│  • WebRTC mic capture (Opus, 48 kHz)                                 │
│  • Silero VAD in WebAssembly  ← detects barge-in BEFORE server hop   │
│  • Ready Player Me avatar (GLB) + viseme driver (Rhubarb visemes)    │
│  • WebSocket to /interview/stream                                    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ WSS (audio frames + control msgs)
┌──────────────────────────────▼───────────────────────────────────────┐
│  EDGE  —  Cloudflare WAF + CDN  →  AWS ALB                           │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│  API GATEWAY  —  Kong OSS on EKS                                     │
│  • JWT validation (Keycloak issuer)  • Rate-limit  • WAF rules       │
└──┬──────────────────┬──────────────────┬────────────────────┬────────┘
   │                  │                  │                    │
┌──▼───────────┐  ┌───▼──────────┐  ┌────▼──────────┐   ┌─────▼────────┐
│ interview_   │  │ data_        │  │ feedback_     │   │ admin_       │
│ core         │  │ gateway      │  │ billing       │   │ ops          │
│              │  │              │  │               │   │              │
│ • Auth       │  │ • Naipunyam  │  │ • Scorer      │   │ • Dashboards │
│ • WebSocket  │  │   sync       │  │ • PDF render  │   │ • Reports    │
│ • LangGraph  │  │ • Jobs       │  │ • Billing     │   │ • Email/SMS  │
│ • AI pipe    │  │ • NOS KB     │  │   meter       │   │              │
│   (S/L/T)    │  │              │  │ • Invoicing   │   │              │
└──────────────┘  └──────────────┘  └───────────────┘   └──────────────┘
       │                 │                 │                  │
       └─────────────────┴────────┬────────┴──────────────────┘
                                  │
   ┌──────────────────────────────▼───────────────────────────┐
   │  AI PIPELINE (inside interview_core)                     │
   │                                                          │
   │  ┌──────────────┐   ┌────────────────┐   ┌────────────┐  │
   │  │ Bhashini STT │→  │ Claude Sonnet  │→  │ Bhashini   │  │
   │  │ ULCA (EN/HI/ │   │   4.6          │   │ TTS Indic  │  │
   │  │ TE streaming)│   │ + prompt cache │   │ (54 voices)│  │
   │  └──────────────┘   └────────┬───────┘   └────────────┘  │
   │  (fallback:        │         │            (fallback:     │
   │   AI4Bharat)       │         │             AI4Bharat)    │
   │                ┌───▼─────────▼─────────────┐             │
   │                │  Tool the LLM can call:   │             │
   │                │    • end_interview()      │             │
   │                └───────────────────────────┘             │
   └──────────────────────────────────────────────────────────┘
                                  │
   ┌──────────────────────────────▼───────────────────────────┐
   │  DATA TIER  (all India-resident)                         │
   │  • PostgreSQL 16 + pgvector  (users, sessions, scores,   │
   │    JD embeddings, NOS competency vectors)                │
   │  • Redis 7  (session state, prompt-cache hints,          │
   │    rate limits, billing event stream)                    │
   │  • S3 (AWS S3 Mumbai)  (audio, transcripts, PDF reports  │
   │    — SSE-KMS at rest)                                    │
   └──────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────┐
   │  PLATFORM                                                │
   │  • AWS EKS, Mumbai (ap-south-1)  — single region, Multi-AZ│
   │  • ArgoCD GitOps  • Helm charts                          │
   │  • Prometheus + Grafana + Loki + OpenTelemetry           │
   │  • HashiCorp Vault  (secrets, KMS)                       │
   │  • Cloudflare (WAF + CDN for static assets)              │
   └──────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────┐
   │  EXTERNAL                                                │
   │  • Naipunyam Portal  (SAML 2.0 IdP + REST APIs)          │
   │  • CERT-In empanelled auditor (annual VA/PT)             │
   └──────────────────────────────────────────────────────────┘
```

---

## EVERY PICK, WITH ONE-LINE JUSTIFICATION

### AI / ML — the core differentiators


| Component                         | Final Pick                                                                         | Why this, not alternatives                                                                                                                                                                                                                                                                    |
| --------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Interviewer LLM**               | **Claude Sonnet 4.6** (`claude-sonnet-4-6`) via Bedrock Mumbai                     | Best price/quality at conversational Indic + strong tool use + **prompt caching gives 90% input-cost reduction** on the static system prompt → critical for per-minute economics. Opus 4.7 too expensive at 2M-user scale; GPT/Gemini lack India-region residency guarantee Bedrock provides. |
| **STT (Indian languages)**        | **Bhashini ULCA** primary + **AI4Bharat IndicConformer** fallback                  | Bhashini is Govt-of-India backed — political and procurement bonus on a GoAP project. AI4Bharat fallback for resilience. Both handle Indian accents far better than Whisper or Google STT for HI/TE.                                                                                          |
| **TTS (multi-voice Indic)**       | **Bhashini Indic TTS** + **AI4Bharat IndicTTS-v2** fallback                        | 54 voice IDs (6 avatars × 9 languages) map cleanly. Same Govt-of-India rationale. Streaming chunked output keeps first-audio latency <300 ms.                                                                                                                                                 |
| **VAD (barge-in)**                | **Silero VAD v5 in WebAssembly** (client-side)                                     | Detect barge-in *before* the round-trip → enables the <2 s response target the RFP mandates. Server-side double-check with `webrtcvad`.                                                                                                                                                       |
| **Avatars**                       | **Ready Player Me** (free GLB) + **Rhubarb-Lipsync** viseme driver                 | D-ID / HeyGen are ₹5–15 per minute → kills unit economics at 2M users. RPM + visemes runs entirely client-side, cost = ₹0. 6 distinct personas via wardrobe + voice pairing.                                                                                                                  |
| **Orchestration / state machine** | **LangGraph** (Python)                                                             | Deterministic FSM: Intro → Tech → Behavioral → Q&A → Close. Tool-calling native. Replays for debugging. Beats LangChain (too loose) and hand-rolled (15-day timeline).                                                                                                                        |
| **Embeddings + vector store**     | `**text-embedding-3-large`** (OpenAI) stored in **pgvector**                       | India-region available, cheap, top-quality on multilingual. pgvector avoids a separate Qdrant/Weaviate cluster = one less thing to break in 15 days.                                                                                                                                          |
| **Scoring**                       | **Claude Sonnet 4.6 as judge**, cached rubric, **end-of-session only**             | Same model, separate call, structured JSON for 4 axes. **Rolling per-turn scoring cut in v1.1** — saves ~₹1/session and 5 LLM calls per session.                                                                                                                                              |
| **NOS/NSQF mapping**              | Curated JSON KB seeded from `skillindia.gov.in` NOS bank                           | No public API exists; ingest NOS corpus once, index in pgvector, LLM retrieves top-K per job role.                                                                                                                                                                                            |
| **Virtual Job JD generation**     | Claude Sonnet 4.6 + content-safety pre-filter (Anthropic safety + custom denylist) | Required by RFP Pg 10. Safety layer is non-negotiable for public-sector deployment.                                                                                                                                                                                                           |


### Application stack


| Layer                   | Final Pick                                                    | Why                                                                                                                                                               |
| ----------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend                | React 18 + TypeScript + Vite + Tailwind, **PWA**              | One codebase = web + mobile (RFP wants both). PWA satisfies "mobile app" without separate iOS/Android builds — critical for 15-day SLA. Confirm scope in pre-bid. |
| Real-time transport     | WebSocket (Socket.IO) + WebRTC for audio                      | WebRTC for low-latency audio capture; WS for control + text.                                                                                                      |
| API Gateway             | **Kong OSS** on EKS                                           | JWT, rate-limit, WAF in one. Free tier is enough.                                                                                                                 |
| Backend                 | **Python 3.12 + FastAPI** (all 4 services)                    | One language, one runtime, one dependency tree — simpler 5-yr ops and clean source-code handover (RFP Pg 23).                                                     |
| Auth / SSO              | **Keycloak 24**                                               | Supports SAML 2.0 + OAuth 2.0 + OIDC — all three protocols the RFP names (Pg 10). Single deploy.                                                                  |
| Primary DB              | **PostgreSQL 16** (AWS RDS Multi-AZ) + **pgvector** extension | Transactional + vector in one engine = simpler 5-yr ops story.                                                                                                    |
| Cache + queues          | **Redis 7** (ElastiCache) + Redis Streams                     | Session state, rate limits, billing event stream.                                                                                                                 |
| Object store            | **AWS S3 Mumbai** with SSE-KMS                                | Encrypted at rest (RFP requirement).                                                                                                                              |
| Container orchestration | **AWS EKS** (Mumbai) + Helm + **ArgoCD** GitOps               | Cloud-native microservices + horizontal autoscaling on STT/LLM/TTS pods.                                                                                          |
| Observability           | **Prometheus + Grafana + Loki + OpenTelemetry**               | 99.5% SLA must be *measured*. Per-leg latency tracing (STT→LLM→TTS) is mandatory for the <2s target.                                                              |
| Secrets                 | **HashiCorp Vault** + AWS KMS                                 | Source-code-handover clause (Pg 23) means secrets must NOT be hard-coded — Vault keeps them externalized.                                                         |
| Edge / CDN              | **Cloudflare** (free tier WAF + static asset CDN)             | Avatar GLBs, JS bundles served from edge.                                                                                                                         |
| CI/CD                   | **GitHub Actions** → ECR → ArgoCD                             | Standard, fast to set up, ISO 27001-friendly audit trail.                                                                                                         |
| Region                  | **AWS Mumbai (ap-south-1)**                                   | India data residency, DPDP Act 2023 compliance. Single region, Multi-AZ. If APSSDC mandates MeghRaj, swap EKS for MeghRaj's K8s — design is portable.             |


---

## CONNECTION FLOW — A SINGLE INTERVIEW TURN

```
[Candidate speaks 5 sec]
   │
   ▼ Silero VAD (client, WASM) detects speech-end → flush buffer
   │
   ▼ WebSocket → interview_core (FastAPI)
   │
   ▼ Bhashini STT streaming  (target: 500 ms to final transcript)
   │
   ▼ LangGraph state node "process_user_turn"
   │     → calls Claude Sonnet 4.6 with:
   │         system_prompt  (CACHED, 90% input cost saved)
   │         + interview_state
   │         + candidate_profile (from Naipunyam via data_gateway, cached in Redis)
   │         + job_context (NOS competencies from pgvector via data_gateway)
   │         + last 8 turns of transcript
   │         tools = [end_interview]            ← only tool (score_turn cut v1.1)
   │     (target: 400 ms first token)
   │
   ▼ Stream LLM tokens → Bhashini TTS streaming
   │     (target: 300 ms to first audio chunk)
   │
   ▼ WebSocket → Client buffers + plays
   │     → Viseme driver lip-syncs avatar in real time
   │
   ▼ If user starts speaking mid-response:
       Silero VAD fires → client sends "barge_in" → server cancels
       in-flight LLM + TTS → loops back to top
```

**Total budget:** ~1.5 s end-to-end on cache-hit, ~2 s cold. RFP target met.

---

## ECONOMIC SANITY CHECK (per 10-min session, v1.1 leaner)


| Cost line                                                                                            | Calculation                     | ₹               |
| ---------------------------------------------------------------------------------------------------- | ------------------------------- | --------------- |
| Bhashini STT                                                                                         | 5 min user speech × ₹0.40/min   | 2.00            |
| Claude Sonnet 4.6 input (cached)                                                                     | 20 turns × 5K cached @ $0.30/MT | 0.50            |
| Claude Sonnet 4.6 input (fresh)                                                                      | 20 turns × 1K fresh @ $3/MT     | 0.50            |
| Claude Sonnet 4.6 output                                                                             | 20 turns × 300 out @ $15/MT     | 3.00            |
| Claude scorer (single end-of-session call)                                                           | 6K in + 1K out                  | 0.50            |
| Bhashini TTS                                                                                         | 5 min AI speech × ₹0.50/min     | 2.50            |
| Infra amortized (EKS + RDS + S3 + bandwidth)                                                         | at 100k sessions/day            | 1.50            |
| **Total variable cost**                                                                              |                                 | **~₹10.50**     |
| ~~Rolling scorer calls (5 × per session)~~                                                           | ~~CUT in v1.1~~                 | ~~saved ₹1.00~~ |
| **Floor bid price** (3× margin for ops + audit + SLA + 5-yr support + corpus fund + transaction fee) |                                 | **~₹30–35**     |


→ At 20 lakh sessions, contract value lands roughly **₹6–7 Cr**. Aligns with the pre-qual single work order benchmark of ₹5 Cr+.

---

## THE 5 ANCHOR DECISIONS (don't relitigate)

1. **Bedrock Claude Sonnet 4.6**, not OpenAI/Gemini → India residency + prompt caching + tool use + multilingual quality.
2. **Bhashini for STT+TTS**, not Whisper/Google → Govt-of-India alignment + Indic accent quality + procurement optics.
3. **Ready Player Me viseme avatars**, not D-ID/HeyGen → client-side rendering kills 90% of avatar cost; lets per-session economics work at 2M scale.
4. **LangGraph state machine**, not free-form prompting → deterministic interviewer policy, replayable, auditable for a government SLA.
5. **AWS Mumbai EKS single region**, not multi-region/multi-cloud → 15-day deployment forbids cleverness; one region, one cloud, one K8s. DR added only if APSSDC mandates.

---

## WHAT WAS CUT FROM v1.0 (see CHANGES.md for full rationale)

1. ❌ DR pilot-light in ap-south-2 (Hyderabad)
2. ❌ Internal mTLS via Istio (Phase-2)
3. ❌ Multi-tenant RLS (for hypothetical future state SDC resale)
4. ❌ Litmus chaos testing
5. ❌ Rolling per-turn scoring (every 4 turns)
6. ❌ 8 microservices (collapsed to 4)
7. ❌ Counseling agent (out of v1; reinstate only if pre-bid confirms)
8. ❌ Embedding model deliberation (picked OpenAI text-embedding-3-large)

---

## TIER 1B — DEMO-ONLY OVERRIDES (added 2026-05-28)

These do **not** alter the anchor decisions above and **must never enter the APSSDC bid unit economics**. They are sales-demo conveniences only.

| Override | Provider | Per-session cost | Status |
| --- | --- | --- | --- |
| Avatar (demo) | **D-ID** real-time streaming (`AVATAR_PROVIDER=did`) | ~₹467 / 10-min (≈38× the ₹12 cap) | cfo-cost-watcher **CONDITIONALLY-APPROVED**, demo-only |

**Conditions (cfo-cost-watcher, 2026-05-28):**
1. Adapter **hard-refuses `did` when `APP_ENV=production`**. APSSDC / govt deploys always use `AVATAR_PROVIDER=custom` (Ready Player Me, per anchor decision #3).
2. Monthly spend cap **₹15,000** (alert at ₹12,000).
3. Registered here + in `.env` as non-compliant with bid economics.
4. **Sunset review 2026-11-28** — discontinue or renegotiate.

> Note: HeyGen (~₹85–170/session) is 2.7–5.5× cheaper for the identical demo purpose; D-ID chosen by founder directive despite this.

---

## END OF DOCUMENT (Final_stack v1.1)

