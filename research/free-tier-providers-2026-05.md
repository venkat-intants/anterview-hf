# Free-Tier AI Provider Audit — May 2026
**Purpose:** Identify the best zero-cost options for each component of the Intants voice-interview stack during the demo phase, with emphasis on natural Telugu + Hindi speech and sub-2s turn latency.
**As-of date:** 2026-05-29. Free tiers change frequently — re-verify before signing up.

---

## The Core Problem We Are Solving

Measured per-turn latency on the current demo stack:
- Sarvam TTS: ~15 s (free-tier throttle, not inherent model speed)
- Gemini LLM: ~6 s TTFT (15 RPM / 1,500 RPD free ceiling causing queuing)
- D-ID avatar: 10–16 s (render pipeline, not streaming)

Total: ~21–31 s per turn. Target: p95 < 2 s. The latency is almost entirely a free-tier throttle + architecture problem, not a fundamental model capability problem. The fix is provider-swapping + streaming architecture.

---

## Component 1: LLM (question generation, Indic script output)

### 1-A. Groq (RECOMMENDED #1)

| Dimension | Detail |
|---|---|
| Free allowance | Perpetual free tier — no credits, no expiry |
| Rate limits (free) | 30 RPM / 6,000 TPM / 1,000 RPD (most models); Llama 4 Maverick: 15 RPM / 3,000 TPM / 500 RPD |
| Credit card required | No |
| India / region | US-hosted; no data-residency guarantee — acceptable for demo phase |
| Trial expiry | None — perpetual |
| Latency / streaming | TTFT 85–110 ms on LPU hardware (Llama 3.x 8B/70B). Streaming supported. This is 4–7x faster first token than Gemini Flash free tier under load |
| Indic output quality | Llama 3.3 70B and Qwen3 32B both produce natural Hindi and Telugu native script. Not as fine-tuned as Sarvam-1 but fully adequate for interview Q&A |
| Key risk | 1,000 RPD hard ceiling is the binding constraint in demo. At ~6 turns/interview that is ~166 complete demo interviews per day — enough for early demos |

**Why #1 for demo:** The TTFT advantage (85 ms vs 420–450 ms for Gemini Flash on free tier) is decisive for voice. The 6 s Gemini latency you are seeing is almost certainly queue/429 throttling, not model speed. Groq eliminates that entirely.

### 1-B. Google Gemini Flash-Lite (RECOMMENDED #2 / fallback)

| Dimension | Detail |
|---|---|
| Free allowance | Perpetual free tier |
| Rate limits (free) | Flash: 15 RPM / 1M TPM / 1,500 RPD; Flash-Lite: 30 RPM / 1M TPM / 1,500 RPD |
| Credit card required | No |
| India / region | Global; no Mumbai residency |
| Trial expiry | None — perpetual |
| Latency / streaming | TTFT ~420–450 ms on free tier (no load). Under burst load on the free tier the queue can push this to 1–2 s or trigger 429. Streaming supported |
| Indic output quality | Excellent Hindi native script. Telugu output is good but benefits from explicit `te-IN` language instructions |
| Key risk | 15 RPM (Flash) is tight for multi-user demos; 30 RPM (Flash-Lite) is better. The 6 s you are seeing is likely queue saturation — you are hitting 15 RPM across shared demo traffic |

**Action:** Switch to `gemini-2.5-flash-lite-latest` immediately (doubles RPM to 30). Use Groq as primary with Gemini Flash-Lite as failover.

### 1-C. Cerebras (noteworthy but secondary)

| Dimension | Detail |
|---|---|
| Free allowance | 1,000,000 tokens/day perpetual — most generous raw volume |
| Rate limits (free) | 30 RPM / 60,000–100,000 TPM; 8,192-token context cap on free tier |
| Credit card required | No |
| Latency | Fastest raw throughput (2,600 t/s on Llama 4 Scout); TTFT comparable to Groq |
| Indic quality | Llama 3.3 70B and Qwen3 32B on Cerebras — similar quality to Groq. Not specifically fine-tuned for Indic |
| Key risk | 8,192-token context cap on free tier. Interview context (resume + JD + conversation history) will bump against this |

**Verdict:** Cerebras is the backup-to-the-backup. The 8K context cap is a real constraint for interview_core's enriched context. Use Groq first.

### LLM Ranking Summary (free tier, Indic voice demo)

1. **Groq** — best TTFT, no card, perpetual, adequate Indic quality
2. **Gemini Flash-Lite** — better Indic quality, higher daily RPD, slightly higher TTFT
3. **Cerebras** — fastest throughput, 8K context cap is a problem

---

## Component 2: TTS — Telugu + Hindi (the critical pain point)

### 2-A. Google Cloud TTS Chirp 3 HD (RECOMMENDED #1)

| Dimension | Detail |
|---|---|
| Free allowance | 1,000,000 characters/month for Chirp 3 HD voices (always-free, perpetual). Plus $300 in new-account credits usable across GCP |
| Rate limits | Not publicly specified; Google Cloud scales horizontally — no documented per-minute throttle |
| Credit card required | Yes — GCP account requires a card but will not charge until you exceed $300 credit or the free tier |
| India / region | Global API; data routing not Mumbai-specific. GCP Mumbai region (asia-south1) exists for data storage |
| Trial expiry | $300 credit expires 90 days from signup. The 1M char/month is perpetual |
| Latency / streaming | Streaming synthesis supported (`streaming_synthesize`). Reported TTFA (time to first audio) ~200 ms in streaming mode vs ~800 ms batch |
| Telugu naturalness | te-IN voices confirmed in Chirp 3 HD. Chirp 3 HD uses a generative model with "emotional resonance" and "natural intonation." Best-in-class for te-IN among cloud providers |
| Hindi naturalness | hi-IN fully supported. Multiple speaker styles |

**Why #1:** 1M chars/month is enormous. At ~200 chars/response, that is 5,000 TTS calls/month free, enough for heavy demo usage. Chirp 3 HD streaming at 200 ms TTFA vs Sarvam's throttled 15 s is a 75x improvement. The card requirement is the only friction — low risk since $300 credit cushions actual charges.

**Estimate for demo:** A 10-min interview with ~20 TTS turns, ~150 chars each = 3,000 chars/interview. 1M chars free = ~333 free interviews/month before hitting paid tier.

### 2-B. Sarvam AI Bulbul v3 (CURRENT — keep as fallback)

| Dimension | Detail |
|---|---|
| Free allowance | Rs.1,000 signup credits (never expire, roll over). At Rs.30/10K chars = 333,333 chars = ~111 interviews worth |
| Rate limits (free/starter) | TTS REST: 60 req/min (30 req/min for Bulbul v3); WebSocket streaming: 30 concurrent |
| Credit card required | No |
| India / region | India-hosted — data residency advantage |
| Trial expiry | Credits do not expire |
| Latency | WebSocket streaming API available. Latency is low when not throttled. The 15 s you measured is the free rate limiter queuing — at 30 req/min that is one request every 2 s, with multiple concurrent turns queuing |
| Telugu naturalness | Best-in-class for te-IN among production APIs. Bulbul v3 achieves lowest CER on Indian domains including numerics, code-mixing, Romanized text. Built specifically for 11 Indian languages |
| Hindi naturalness | Excellent — purpose-built |

**Root cause of your 15 s latency:** You are hitting the 30 req/min Bulbul v3 ceiling. With concurrent sessions, requests queue. Fix: Switch to Google Cloud TTS Chirp 3 HD for primary (higher free capacity + streaming), keep Sarvam as India-data-residency fallback once you have production contracts.

### 2-C. ElevenLabs Multilingual v2 (worth testing for Hindi; weak on Telugu)

| Dimension | Detail |
|---|---|
| Free allowance | 10,000 chars/month (perpetual). At ~150 chars/response = ~66 TTS calls/month. Very limited |
| Rate limits | Not publicly documented for free tier |
| Credit card required | No |
| India / region | US-hosted |
| Trial expiry | Perpetual free tier |
| Latency | Turbo v2.5: ~75 ms TTFA. Multilingual v2: higher quality, higher latency |
| Telugu naturalness | Listed as supported (32 languages) but quality reviews indicate Indic/Southeast Asian languages are "noticeably lower" quality vs European languages. Not recommended for te-IN production |
| Hindi naturalness | Better — Hindi is a higher-resource language; quality is acceptable |

**Verdict:** 10K chars/month is too low for any demo volume. Telugu quality is uncertain. Not the right pick here.

### 2-D. Azure Speech Neural TTS

| Dimension | Detail |
|---|---|
| Free allowance | 500,000 chars/month (Neural TTS, perpetual F0 tier). Plus $200 credit (30-day expiry) |
| Rate limits | F0 tier has concurrency and TPS limits (documented in Azure quotas) |
| Credit card required | Yes — Azure account requires card |
| India / region | Azure Central India / South India regions available |
| Trial expiry | $200 credit expires 30 days. F0 tier is perpetual |
| Latency | Real-time synthesis; streaming supported. ~200–400 ms typical TTFA |
| Telugu naturalness | te-IN Neural voices available (e.g., Shruti, Mohan). Quality is good — Microsoft has invested in South Asian neural voices. Second to Chirp 3 HD |
| Hindi naturalness | hi-IN: multiple neural voices, excellent quality |

**Verdict:** Strong backup. 500K chars/month free is half-again less than Google's 1M, and the $200 credit only lasts 30 days. Prefer Google Cloud TTS Chirp 3 HD as primary.

### 2-E. AI4Bharat IndicF5 / Bhashini (zero-cost, self-hosted path)

| Dimension | Detail |
|---|---|
| Free allowance | Models are open-source (MIT / CC-BY-4.0); Bhashini API is free for non-commercial use (approval required) |
| Rate limits | Bhashini: undocumented; approval-gated. Self-hosted IndicF5: no limits (your GPU) |
| Credit card required | No (Bhashini API), No (self-hosted) |
| India / region | Bhashini is India-government hosted. Self-hosted on Railway/Render gives you control |
| Trial expiry | Bhashini API: ongoing (subject to MeitY policy changes). Self-hosted: indefinite |
| Latency | Bhashini API: variable (shared govt infra, no SLA). Self-hosted IndicF5 on T4 GPU: ~300–500 ms |
| Telugu naturalness | Excellent — trained specifically on Indian language data including IITMadras Indic corpora. Near-human for te-IN per AI4Bharat benchmarks |
| Hindi naturalness | Excellent |

**Verdict:** Best long-term path for production (DPDP compliance, zero variable cost, no vendor lock-in). Not recommended as primary for immediate demo — Bhashini API approval takes time and has no SLA; self-hosted IndicF5 needs GPU infra you do not have yet. Flag for Tier-2 production stack.

### TTS Ranking Summary (free tier, Telugu + Hindi, demo)

1. **Google Cloud TTS Chirp 3 HD** — 1M chars/month free, best Indic quality among cloud APIs, 200 ms streaming TTFA (card required, low risk)
2. **Sarvam Bulbul v3** — best raw Telugu naturalness, no card, but 30 req/min cap needs architectural mitigation (WebSocket streaming, not REST)
3. **Azure Neural TTS** — 500K chars/month, strong te-IN, needs card
4. **AI4Bharat IndicF5** — best long-term but needs setup time

---

## Component 3: Talking-Head Avatar

**Constraint:** D-ID is the current demo avatar; sunset gate is 2026-11-28. You need an option with free streaming + external audio driving.

### 3-A. Tavus CVI (RECOMMENDED #1 for demo replacement)

| Dimension | Detail |
|---|---|
| Free allowance | 25 minutes of conversational video + 5 minutes of video generation + 5 minutes of lip-sync (perpetual free plan) |
| Rate limits | Free plan supports limited concurrent streams (exact number not published) |
| Credit card required | Not documented as required for free plan |
| India / region | US-hosted |
| Trial expiry | Free plan is perpetual (not a time-limited trial) |
| Latency | Real-time streaming; CVI (Conversational Video Interface) designed for < 1 s interaction |
| Lip-sync quality | Production-grade; powers real-time conversation |
| BYO TTS support | Yes — documented: "you can plug in any TTS — ElevenLabs, Cartesia, or your own." BYO LLM also supported |

**Why #1:** Tavus explicitly supports bringing your own TTS and LLM, which means you can drive the avatar with Google Cloud TTS Chirp 3 HD or Sarvam audio directly. 25 free minutes = ~2.5 complete demo interviews, enough for investor demos. Starter plan is $59/month for 100 minutes (~10 interviews).

**Critical caveat:** 25 minutes is very limited. This is a "show-stopper demo" budget, not a pilot budget. You will need to upgrade quickly.

### 3-B. D-ID (CURRENT)

| Dimension | Detail |
|---|---|
| Free allowance | 14-day free trial, 20 credits. Each credit = up to 15 s of video. Total: ~300 s (~5 min) of video |
| Rate limits | Trial plan is feature-limited; streaming (Talks Streams API) requires API plan |
| Credit card required | Not required for 14-day trial |
| India / region | Azure-hosted (global) |
| Trial expiry | 14 days — time-limited, not perpetual |
| Latency | Talks Streams API: sub-200 ms video streaming (official spec); real user reports: 2–4 s between input and visible response in streaming mode. Your observed 10–16 s is likely the standard (non-streaming) Talks API |
| Lip-sync | 30 ms lip-to-speech sync (official). CTC-based phoneme alignment |
| BYO TTS | Yes — Talks Streams API accepts an audio buffer; you stream audio and get back video chunks |

**Root cause of your 10–16 s D-ID latency:** You are using the standard `POST /talks` endpoint, which renders a complete video file. The Talks Streams API (WebRTC-based) delivers video in chunks. You need to migrate to the streaming endpoint. This alone could drop avatar latency to < 1 s.

**Key issue:** The 14-day trial expires — it is not a perpetual free tier. Once expired, API pricing applies (~$0.05–0.10/s of video). At ~150 s/interview, that is $7.50–15 per interview in avatar costs alone, blowing the Rs.12/session budget.

### 3-C. Akool

| Dimension | Detail |
|---|---|
| Free allowance | Trial credits (exact amount not published). Basic plan free with watermark and 720p cap |
| Credit card required | Not required for basic plan |
| Latency | Real-time streaming avatar, diffusion-based lip-sync. 150+ language support |
| BYO TTS | Documented streaming avatar API |
| Key risk | Free tier is watermarked — unprofessional for client demos |

**Verdict:** Akool is a fallback option if both D-ID and Tavus have friction. The watermark on the free tier disqualifies it for demo sessions with clients.

### Avatar Ranking Summary

1. **Tavus CVI** — perpetual free plan, BYO TTS confirmed, real-time streaming. Best option for zero-cost demos. Very limited minutes — needs budget for pilot phase
2. **D-ID (current)** — migrate to Talks Streams API immediately to fix the 10–16 s latency. Trial expires in 14 days
3. **Akool** — backup only; watermark on free tier is a problem

---

## Component 4: STT (Speech-to-Text — Telugu + Hindi, streaming)

### 4-A. Deepgram Nova-3 (RECOMMENDED #1)

| Dimension | Detail |
|---|---|
| Free allowance | $200 credit (perpetual — "no expiration") |
| Rate limits (free) | Up to 50 concurrent REST / 150 concurrent WSS (WebSocket streaming) |
| Credit card required | No |
| India / region | US-hosted; no Mumbai endpoint |
| Trial expiry | $200 credit has no stated expiry |
| Latency | Sub-300 ms streaming transcription latency. Single multilingual model — no language detection overhead |
| Telugu support | Confirmed: Nova-3 added Telugu (language code: `te`) in its Asia-Pacific expansion. 34% WER reduction in March 2026 multilingual update |
| Hindi support | Full support with high accuracy |

**Value of $200:** At $0.0077/min (Nova-3 pay-as-you-go), $200 = ~25,974 minutes of streaming audio = ~433 hours of transcription. At 10 min/interview that is ~2,597 complete free interviews. This is the most generous STT free credit available.

### 4-B. Groq Whisper Large v3 Turbo (RECOMMENDED #2 — zero-card option)

| Dimension | Detail |
|---|---|
| Free allowance | Perpetual free tier: 2,000 requests/day, 7,200 audio-seconds/hour |
| Rate limits | 20 RPM for Whisper (both v3 and v3-turbo). 7,200 audio-seconds/hour = 2 hours of audio per clock hour |
| Credit card required | No |
| India / region | US-hosted |
| Trial expiry | None — perpetual |
| Latency | ~80 ms transcription per request (LPU advantage). Not streaming in the WebSocket sense — you send a file/chunk and get text back fast |
| Telugu support | Whisper v3 supports 99 languages including Telugu. Community fine-tunes confirm good accuracy. Not as strong as Deepgram Nova-3 (Whisper v3 base model has higher WER on Indic vs Nova-3's specialized training) |
| Hindi support | Good. Whisper v3 has strong Hindi data in training |

**Note:** Groq Whisper is batch-per-chunk, not true continuous streaming. You send 2–3 s audio chunks and get near-instant transcription. This is fast enough for turn-based voice interviews.

### 4-C. Google Cloud STT Chirp 3 (worth noting alongside TTS)

| Dimension | Detail |
|---|---|
| Free allowance | 60 minutes/month (perpetual) + $300 new-account credits |
| Rate limits | Not publicly documented; scales with GCP infra |
| Credit card required | Yes (same GCP account) |
| Latency | Streaming (`StreamingRecognize`): ~200 ms |
| Telugu + Hindi | Chirp 3 ASR supports 100+ languages including te-IN and hi-IN |

**Verdict:** 60 min/month free is too low for any meaningful demo volume. Use the $300 credit if you open GCP for TTS anyway, but Deepgram or Groq Whisper are better primary STT options.

### 4-D. Sarvam Saaras v3 (current)

| Dimension | Detail |
|---|---|
| Free allowance | Rs.1,000 signup credits (shared with TTS). At Rs.30/hr = ~33 hours of STT free |
| Rate limits | REST: 60 req/min; WebSocket: 20 concurrent |
| Credit card required | No |
| India / region | India-hosted — residency advantage |
| Telugu + Hindi | Purpose-built for 11 Indian languages. Best-in-class WER for te-IN among commercial APIs |
| Latency | WebSocket real-time streaming supported |

**Note:** Sarvam credits are shared between STT and TTS. If you use Sarvam for both, the Rs.1,000 runs out faster. Consider using Sarvam for STT (where Indic quality is most critical) and Google Cloud TTS for TTS (where free capacity is larger).

### 4-E. Azure STT (F0 free tier)

| Dimension | Detail |
|---|---|
| Free allowance | 5 audio hours/month (perpetual F0 tier) |
| Credit card required | Yes |
| Telugu + Hindi | te-IN and hi-IN neural models available |
| Latency | Real-time streaming supported |

**Verdict:** 5 hours/month is very restrictive. Azure STT is not the right pick here unless you are already paying for Azure.

### STT Ranking Summary

1. **Deepgram Nova-3** — $200 no-expiry credit, sub-300 ms streaming, confirmed Telugu Nova-3 support, no card required
2. **Groq Whisper v3 Turbo** — perpetual free, 80 ms latency, good Indic (not streaming, but fast chunk processing), no card
3. **Sarvam Saaras v3** — best te-IN accuracy, no card, but shared credit pool with TTS

---

## Recommended Free Demo Stack (one pick per component)

| Component | Provider | Free Allowance | Card? | Latency |
|---|---|---|---|---|
| LLM | Groq (Llama 3.3 70B) | Perpetual: 30 RPM / 1K RPD | No | 85–110 ms TTFT |
| TTS | Google Cloud TTS Chirp 3 HD | 1M chars/month perpetual + $300 credit | Yes (no charge till limit) | ~200 ms TTFA streaming |
| STT | Deepgram Nova-3 | $200 no-expiry credit | No | Sub-300 ms streaming |
| Avatar | Tavus CVI | 25 min/month perpetual | No (unconfirmed) | < 1 s real-time |

**Groq as LLM fallback:** Gemini Flash-Lite (30 RPM, perpetual, no card)
**TTS as fallback:** Sarvam Bulbul v3 (Rs.1,000 credits, no card, best te-IN naturalness)
**STT as fallback:** Groq Whisper v3 Turbo (perpetual free, no card)

### Projected per-turn latency on the new stack

- STT (Deepgram Nova-3 streaming): ~200–300 ms
- LLM (Groq, 85–110 ms TTFT + ~300 ms generation for ~200 tokens): ~400–500 ms
- TTS (Google Chirp 3 HD streaming, 200 ms TTFA): ~200 ms
- Avatar (Tavus CVI real-time): < 500 ms
- **Estimated p50 per-turn: ~900 ms – 1.3 s. p95 target of < 2 s is achievable.**

---

## Long-Term Viability Assessment

| Provider | Viable Long-Term? | Note |
|---|---|---|
| Groq | Trial-only quality — must upgrade | Free tier RPD (1K/day) caps pilot scale; paid is $0.05–0.08/1M tokens — cheap |
| Google Cloud TTS Chirp 3 HD | Yes — production-viable | $30/1M chars after free tier. At 3K chars/interview = $0.09/interview for TTS |
| Deepgram Nova-3 | Yes — production-viable | $0.0077/min. At 10 min/interview = $0.077/interview for STT |
| Sarvam Bulbul v3 | Yes — preferred for prod India | Best te-IN, India data residency, Rs.30/10K chars. Should be Tier-2 primary |
| Tavus CVI | Upgrade required for pilot | $59/mo Starter = 100 min (~10 interviews). Not scalable; Tier-2 Three.js avatar is the right path |
| D-ID Talks Streams | Trial only — sunset gate confirmed | 2026-11-28 sunset. Fix streaming endpoint now, but do not extend dependence |
| AI4Bharat IndicF5 | Yes — best long-term TTS | Zero variable cost, best te-IN, needs GPU infra. Target Tier-2 production |

---

## Immediate Actions for the Team

1. **LLM — switch to Groq today.** Sign up at console.groq.com (no card). Use `llama-3.3-70b-versatile`. Set `LLM_PROVIDER=groq` in `.env`. Keep Gemini Flash-Lite as fallback. This alone cuts LLM latency from ~6 s to ~500 ms.

2. **TTS — open GCP account and enable Cloud TTS Chirp 3 HD.** A card is required but the $300 credit + 1M chars/month free means zero actual spend for the entire demo phase. Use the `te-IN-Chirp3-HD-*` voice family. Migrate TTS calls to streaming (`streaming_synthesize`). Target: TTFA ~200 ms.

3. **Avatar — fix D-ID first, then evaluate Tavus.** Migrate from `POST /talks` to the Talks Streams WebRTC API. This should drop D-ID latency from 10–16 s to < 1 s without any provider change. Then evaluate Tavus CVI for the BYO TTS advantage (drive avatar with Google TTS audio directly).

4. **STT — sign up for Deepgram (no card, $200 credit).** Use Nova-3 with `language=te` or `language=hi`. Enable WebSocket streaming endpoint. This replaces Sarvam STT and preserves the Rs.1,000 Sarvam credits exclusively for TTS.

5. **Sarvam — preserve Rs.1,000 credits for TTS fallback only.** Once Google TTS Chirp 3 HD is integrated, Sarvam becomes the te-IN quality backup when GCP has issues.

6. **Do not start Bhashini API approval yet** unless the APSSDC bid explicitly requires it. The approval timeline is unpredictable. Flag for Tier-2.

---

## Flags and Caveats

- **Card requirement:** Google Cloud TTS (Chirp 3 HD) and Azure Speech both require a credit card. Neither will charge during the free allowance / credit period, but the founder must register a card.
- **Data residency:** None of these providers host data in Mumbai during demo phase. This is acceptable per the two-tier strategy. Raise this explicitly in any APSSDC demo conversation — frame it as "demo environment; production will run on AWS Mumbai."
- **ElevenLabs Telugu:** Do not use for Telugu. Quality reviews consistently place it below Google and Sarvam for Indic languages, and 10K chars/month is insufficient volume.
- **HeyGen:** Removed free API tier in February 2026. Not a free-tier option.
- **Sarvam latency root cause:** The 15 s you measured is a 30 req/min queue, not inherent model latency. If you stay on Sarvam TTS, switch to the WebSocket streaming endpoint and reduce concurrent session count, or upgrade to a paid tier.
- **Gemini TTS (2.5 Flash):** Available via Google AI Studio free tier (preview). Supports Hindi (24 languages). Telugu support is confirmed in Gemini 3.1 Flash TTS (100+ languages) but less certain in 2.5 Flash. If you already have a GCP account for Cloud TTS Chirp 3 HD, test Gemini 2.5 Flash TTS in parallel — it may offer an all-in-one LLM+TTS path.

---

*Research by: market-researcher agent | Sources: official vendor pricing pages, Groq docs, Google Cloud docs, Deepgram docs, Tavus docs, AI4Bharat GitHub, third-party benchmark aggregators. All free-tier terms subject to change without notice.*
