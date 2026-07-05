# Data-Flow and Sub-processor Transparency

> Last updated: 2026-07-01
> This document is the authoritative record of every third-party sub-processor
> that handles candidate data and where that data flows geographically.
> It is referenced from the in-app consent modal (all three Day-1 languages).

---

## Current Deployment Status

**IMPORTANT — India-Residency Status:**

The current ("Tier-1 demo") deployment is **NOT India-resident**. Data
processed during a candidate interview passes through sub-processors located
in **Singapore** (database), **United States** (LLM, avatar, voice), and
**global edge** (CDN/cache). This deployment is explicitly labelled a
**demo/early-access tier** and is **not suitable for government-bid
(APSSDC/NSDC) or DPDP-strict production use without migration to Tier-2**.

Tier-2 (AWS Mumbai, India-resident, DPDP-compliant production) is the target
for government contracts and is the same codebase with environment-level
configuration changes. See `Final_stack.md` for the Tier-2 stack and
`docs/PROCUREMENT.md` for the migration checklist.

---

## Sub-processor Table

| Sub-processor | Service provided | Data processed | Server location | Notes |
|---|---|---|---|---|
| **Neon** (managed Postgres) | Primary database | User accounts, session metadata, scorecards, DPDP consent ledger | **Singapore** (ap-southeast-1) | Moved from us-east-1 2026-06-xx for India latency; still outside India |
| **Upstash** | Serverless Redis cache | Session tokens, ephemeral rate-limit counters (no PII stored) | Global edge (nearest PoP) | Volatile only; TTL ≤ 1 hour |
| **Cloudflare R2** | Object storage | Voice audio recordings, uploaded resume PDFs | **United States** (Cloudflare default region) | SSE at rest; encrypted in transit |
| **Google Gemini** (gemini-flash-lite-latest) | LLM — interview brain | Interview transcript (candidate speech text) | **United States** (Google Cloud) | No training on submitted data per Google API ToS |
| **Groq** (llama-3.3-70b-versatile) | LLM — fallback / interview worker | Interview transcript (candidate speech text) | **United States** (Groq Cloud) | Listed as alternative provider; active when `LLM_PROVIDER=groq` |
| **Sarvam AI** | Speech-to-text (STT) and text-to-speech (TTS) | Raw voice audio (STT) and transcript text (TTS) | **India** (Sarvam infrastructure) | Indian company; data-processing location confirmed as India |
| **Tavus** | Real-time avatar video | Avatar persona identifier only (no candidate biometric data) | **United States** | Demo-only; candidate's face is NOT sent to Tavus; only the TTS audio is relayed for lip-sync |
| **Simli** | Real-time avatar video | TTS audio stream for lip-sync | **United States** | Demo-only; same biometric caveat as Tavus |
| **LiveKit** | WebRTC real-time transport | Voice audio + video streams (in transit) | **United States** (LiveKit Cloud) | Streams are not persistently stored by LiveKit; audio is processed live by the worker |
| **Resend** | Transactional email | Candidate email address, recruiter email address, invite link | **United States** | Used for invite and notification emails only |
| **OpenAI** | Text embeddings (resume indexing) | Resume text | **United States** | `text-embedding-3-large`; used at resume-upload time, not during live interviews |
| **Vercel** | Frontend CDN | Browser static assets only (no PII in assets) | **Global edge** | Candidate PII never stored on Vercel; API calls go to the backend VM |
| **Oracle Cloud Free Tier** (backend VM) | Compute host for 6 Docker containers | All traffic in transit between services | **Region chosen by operator** (guide uses Frankfurt or Ashburn for availability; not India) | The free tier does not offer Mumbai region; operator must choose a supported region |
| **Sentry** | Error monitoring | Stack traces, request metadata (may include partial URLs) | **United States** | PII scrubbing configured; no full request bodies logged to Sentry |
| **JDoodle** | Code execution (coding exams) | Candidate code submissions | **India** (JDoodle infrastructure) | No interview audio or personal data; code only |

---

## Data Categories and Retention

| Data category | Where stored | Retention period | Erasure mechanism |
|---|---|---|---|
| Candidate profile (name, email, password hash) | Neon (Singapore) | Until account deletion request | `DELETE /api/v1/users/me` or support@intants.com |
| DPDP consent ledger entries | Neon (Singapore) | 7 years (legal obligation) | Anonymised on account deletion; audit record retained |
| Interview session metadata (job title, timestamps, scores) | Neon (Singapore) | 90 days post-session | Cascading delete from session row |
| Interview transcript (candidate speech text) | Neon (Singapore) | 90 days post-session | Cascading delete from turns table |
| Voice audio recording | Cloudflare R2 (US) | 90 days post-session | S3-compatible `DeleteObject`; automated lifecycle rule pending |
| Resume PDF | Cloudflare R2 (US) | Until candidate deletes or replaces | `DELETE /api/v1/resume/versions/{id}` |
| Resume extracted text | Neon (Singapore) | Until candidate deletes or replaces | Cleared when resume version is deleted |
| Session JWT (auth token) | Upstash Redis (edge) | 24 hours (TTL) | Automatic expiry; `POST /logout` flushes immediately |

---

## Candidate Rights (DPDP Act 2023)

Under the Digital Personal Data Protection Act 2023, candidates have:

- **Right of Access** — request a copy of your data: email support@intants.com
- **Right to Correction** — update your profile at any time via the dashboard
- **Right to Erasure** — request account + data deletion: email support@intants.com
  or use the in-dashboard delete option (when available)
- **Right to Withdraw Consent** — consent can be withdrawn at any time by
  emailing support@intants.com; withdrawal ends all active and future recording.
  Sessions already completed are retained for the stated retention period unless
  an erasure request is also submitted.
- **Right to Grievance Redressal** — complaints addressed within 30 days by the
  platform owner (support@intants.com)

---

## Path to India Residency (Tier-2)

| Current (Tier-1 demo) | Tier-2 (India-resident, pending) |
|---|---|
| Neon — Singapore | AWS RDS — Mumbai (ap-south-1) |
| Cloudflare R2 — US | AWS S3 — Mumbai (SSE-KMS) |
| Google Gemini / Groq — US | AWS Bedrock — Mumbai (claude-sonnet-4-6) |
| Upstash — global edge | AWS ElastiCache — Mumbai |
| Oracle Free Tier VM — non-India | AWS EKS — Mumbai (Multi-AZ) |
| LiveKit Cloud — US | LiveKit self-hosted — Mumbai EKS |
| Tavus/Simli avatar — US | Three.js + Ready Player Me — browser-side |

Migration is blocked on: AWS Bedrock approval (1–5 days), Bhashini ULCA API
approval, and commercial contract. The code is identical across both tiers;
only environment variables change.

---

## Change Log

| Date | Change |
|---|---|
| 2026-07-01 | Initial document created; cross-border disclosure added to consent modal (fixes DPDP audit finding) |
| 2026-06-xx | Neon region moved from us-east-1 to ap-southeast-1 (Singapore) for lower India latency |
