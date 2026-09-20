# Data-Flow and Sub-processor Transparency

> Last updated: 2026-08-19 (see Change Log)
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

This non-residency is recorded as an open, owned risk with a trigger for
revisiting — **[`ACCEPTED-RISKS.md` → AR-1](ACCEPTED-RISKS.md)**. The banner
above is the disclosure; AR-1 is the decision and who owns it.

---

## Sub-processor Table

| Sub-processor | Service provided | Data processed | Server location | Notes |
|---|---|---|---|---|
| **Neon** (managed Postgres) | Primary database | User accounts, session metadata, scorecards, DPDP consent ledger | **Singapore** (ap-southeast-1) | Moved from us-east-1 2026-06-xx for India latency; still outside India |
| **Upstash** | Serverless Redis cache | Session tokens, ephemeral rate-limit counters (no PII stored) | Global edge (nearest PoP) | Volatile only; TTL ≤ 1 hour |
| **Cloudflare R2** | Object storage | Voice audio recordings, uploaded resume PDFs | **United States** (Cloudflare default region) | SSE at rest; encrypted in transit |
| **Google Gemini** (gemini-2.5-flash) | LLM — role-profile derivation, scoring, exam generation, staff copilots | Job descriptions, interview transcript, candidate answers | **United States** (Google Cloud) | No training on submitted data per Google API ToS |
| **Groq** (`GROQ_MODEL`, currently openai/gpt-oss-120b) | LLM — the live interview turn loop | Interview transcript (candidate speech text), in real time, every session | **United States** (Groq Cloud) | **Unconditional, not optional.** The worker wires Groq directly and does NOT read `LLM_PROVIDER`; that setting routes only the non-realtime LLM paths. Every interview's speech text reaches the US via Groq. |
| **Sarvam AI** | Speech-to-text (STT) and text-to-speech (TTS) | Raw voice audio (STT) and transcript text (TTS) | **India** (Sarvam infrastructure) | Indian company; data-processing location confirmed as India |
| **Tavus** | Real-time avatar video | Avatar persona identifier only (no candidate biometric data) | **United States** | Demo-only; candidate's face is NOT sent to Tavus; only the TTS audio is relayed for lip-sync |
| **Simli** | Real-time avatar video | TTS audio stream for lip-sync | **United States** | Demo-only; same biometric caveat as Tavus |
| **LiveKit** | WebRTC real-time transport | Voice audio + video streams (in transit) | **United States** (LiveKit Cloud) | Streams are not persistently stored by LiveKit; audio is processed live by the worker |
| **Resend** | Transactional email | Candidate email address, recruiter email address, invite link | **United States** | Used for invite and notification emails only |
| **OpenAI** | Text embeddings (resume indexing) | Resume text | **United States** | `text-embedding-3-large`; used at resume-upload time, not during live interviews |
| **Vercel** | Frontend CDN | Browser static assets only (no PII in assets) | **Global edge** | Candidate PII never stored on Vercel; API calls go to the backend VM |
| **Oracle Cloud Free Tier** (backend VM) | Compute host for 6 Docker containers | All traffic in transit between services | **Region chosen by operator** (guide uses Frankfurt or Ashburn for availability; not India) | The free tier does not offer Mumbai region; operator must choose a supported region |
| **Sentry** | Error monitoring | Stack traces, request metadata (may include partial URLs) | **United States** | PII scrubbing configured; no full request bodies logged to Sentry |
| **JDoodle** | Code execution (coding exams) | Candidate code submissions **and any custom stdin the candidate typed** | ⚠️ **UNVERIFIED — do not claim a country** | Default provider for every deploy target. See the note below; risk accepted as [`ACCEPTED-RISKS.md#ar-3`](ACCEPTED-RISKS.md) |

> **Note on the JDoodle row.** This row previously read **"India (JDoodle
> infrastructure)"**. That claim has been withdrawn: we have no evidence for it.
> The API base is `https://api.jdoodle.com/v1` with no region selector, the
> client negotiates no region, and there is no DPA or contractual residency term
> on file. Because this document is linked from the in-app consent modal, an
> unevidenced residency statement here is worse than an honest "unverified" —
> it is the kind of claim a DPDP audit would ask us to substantiate. Restore a
> country only against a written statement from the vendor.
>
> The self-hosted **Piston** alternative is the path to a known execution
> location (see [`PISTON_SELFHOST.md`](PISTON_SELFHOST.md)); it is implemented
> and swappable by config, but is **not** the default today.

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
| Human interview scorecards (PH4-A1) — per-criterion 1–5 scores, written evidence, a summary, and any correction or withdrawal reason, recorded by **named company interviewers** (the `interviewer` role, or an HR manager on the panel) | Neon (Singapore) | Kept with the application as the company's hiring record | On erasure: open assignments withdrawn; evidence, summary and both reasons redacted (`[redacted]` / NULL); scores and who interviewed kept against the anonymised applicant (`erasure_executor` step 5f) |
| Interviewer private notes (PH4-A5) — an interviewer's own working notes on a candidate; never shown to HR or to the candidate | Neon (Singapore) | Until 90 days after the final decision, or 90 days after the interviewer's assignment was withdrawn — **only where `RETENTION_DRY_RUN=false`**. The nightly purge honours that flag, whose default is `true` (count, delete nothing); a deployment that has not turned it off keeps notes until erasure. The HF Space's value is unverified. | Deleted outright on erasure (step 5f) |
| Offers (PH4-A3) — for a hired application: job title, compensation (base pay, currency, period, bonus, equity), start date, location, benefits and terms; who approved it and when; when it was sent, viewed and answered; the name the candidate typed to accept, or their reason for declining | Neon (Singapore) | Kept with the application as the company's record of what was offered | On erasure an offer still in play is withdrawn and its link killed, and the typed name and every reason or note is redacted (`erasure_executor` step 5f); the audit log keeps actions, times and lengths only. One-time acceptance codes are stored hashed and deleted on erasure |
| Preboarding documents (PH4-A4) — the files a candidate uploads after accepting (identity, address, education and the like: PDF, JPEG or PNG only), with their hash, size, expiry date, and HR's review and reason | The same object store and bucket as CVs — in the demo tier Cloudflare R2 (US), so these documents leave India (AR-1) — for the files; Neon (Singapore) for the records | Deleted `PREBOARDING_DOCUMENT_RETENTION_DAYS` (90) after: the offer ends without an acceptance; preboarding completes (the HRMS holds them then); or the acceptance, if preboarding never completes. Deleted at the next nightly purge, with no 90-day wait, once the hire is reversed by a rejection before preboarding completes (after it completes, the 90-day clock applies). Only once `RETENTION_DRY_RUN=false` — while it is true (the default) nothing is deleted, only counted | Consent recorded at acceptance (`preboarding_documents` / `onboarding`), held per candidate rather than per offer: withdrawing it stops uploads for every offer that candidate holds, and it is never re-granted automatically. Opened only with the offer link AND an hour-long session unlocked by an emailed code; the candidate is emailed on every upload. On erasure the files are deleted from storage (step 8) and the records blanked (step 5f). Never virus-scanned — see ACCEPTED-RISKS AR-6 |
| HRMS handoff (PH4-A4) — a signed JSON payload: the candidate's name and email, the offer's terms and pay, and the list of verified document TYPES (no files, no scores, no reasons) | Neon (Singapore); leaves only when HR downloads it for their HRMS | Kept as the record of what was handed over | Deleted on erasure |
| Interview schedules (PH4-A2) — for each application HR schedules: the candidate's timezone (an IANA name, chosen by HR or taken from the candidate's browser when they pick a time), each interview's time, duration, round and place (a room or meeting link), who interviews, whether the candidate or HR chose the time, and any cancellation reason | Neon (Singapore) | Kept with the application as the company's scheduling record | On erasure every interview not yet held is cancelled, open loops are closed and any cancellation reason is replaced by `[redacted]` (`erasure_executor` step 5f); the audit log records times and ids, and only the length of a reason |
| Interviewer availability and limits (PH4-A2 / PH4-O5) — the windows an interviewer (company staff) is free, and the most sessions per day or week HR wants them to carry | Neon (Singapore) | Until removed by HR or the interviewer | Staff data, not candidate data; deleted with the staff account |
| Stage exceptions (PH4-O1) — HR's record that an application could not proceed normally (e.g. a reschedule request), with a free-text reason, an owner, who raised it and when, and how it was resolved | Neon (Singapore) | Kept with the application as the company's operational record | On erasure the reason is replaced by `[redacted]`, the resolution note cleared and an open exception closed (`erasure_executor` step 5f); the database keeps a redacted exception redacted, and the audit log records only that a reason was given and its length |
| Final-decision reason category (PH4-O4) — a company-defined code and label stored with each hire/reject | Neon (Singapore) | Kept with the stage ledger | The category itself holds no personal data; HR's free-text rationale alongside it is **not** redacted on erasure — see `ACCEPTED-RISKS.md` AR-5 |

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
| 2026-09-20 | **Offers, preboarding documents and the HRMS handoff disclosed (PH4 Wave 4).** Compensation, the candidate's answer, uploaded identity and other documents, and the signed payload HR hands to their HRMS are listed above with their retention and erasure handling. Documents go to the existing object store; the HRMS payload is prepared for HR to take, not sent anywhere by us. No new sub-processor. |
| 2026-09-19 | **Interview schedules disclosed (PH4 Wave 3).** HR can schedule human interviews and loops, and a candidate can pick a time from what is offered; the schedule, the candidate's timezone and any cancellation reason are listed above with their erasure handling, as are interviewers' availability and limits. Calendar files (.ics) are generated on request and not stored. Panel workload and calibration are computed when read and store nothing but an audit row saying who looked. Itinerary, change and reminder emails go through the existing outbox and sender. No new sub-processor. |
| 2026-09-18 | **Stage exceptions disclosed (PH4 Wave 2).** HR can record why an application cannot proceed normally; the reason is free text about a candidate, so it is listed above with its erasure handling. The rest of Wave 2 — workflow review and approval, branching, dry-run simulations (synthetic candidates only, never people) and stage owners/SLAs — stores company configuration, not personal data. No new sub-processor. |
| 2026-09-17 | **Human interview evidence disclosed (PH4 Wave 1).** Candidates are now assessed in human interview rounds by named company interviewers, who record structured scorecards and may keep private working notes. Both are stored in the existing Postgres; no new sub-processor. Interviewer assignment emails go through the existing Resend sub-processor and name the candidate to the interviewer, as other staff notifications already did. The rows above state what is kept, for how long, and what erasure does to it. The audit log's free-text decision rationale — which predates this change and is not redacted on erasure — is recorded as AR-5. |
| 2026-08-19 | **Groq cross-border processing disclosed as unconditional.** The Groq row previously read "active when `LLM_PROVIDER=groq`", which understated the transfer: the LiveKit worker wires Groq directly and never reads that setting, so every interview's candidate speech text is sent to the United States on every session, in real time. The row now says so, and names the model as `GROQ_MODEL` rather than a pinned id. The Gemini row was also corrected — it was credited as the "interview brain"; Gemini performs role-profile derivation, scoring, exam generation and the staff copilots, not the live turn loop. No new sub-processor was added: this is a correction of what was already happening. |
| 2026-08-07 | **JDoodle residency claim withdrawn** — the row said "India (JDoodle infrastructure)" with no evidence; now marked unverified (code-review finding AG-05). Candidate stdin added to the data-processed column. Cross-references added to the new `ACCEPTED-RISKS.md` register (AR-1 residency, AR-3 JDoodle). |
| 2026-07-01 | Initial document created; cross-border disclosure added to consent modal (fixes DPDP audit finding) |
| 2026-06-xx | Neon region moved from us-east-1 to ap-southeast-1 (Singapore) for lower India latency |
