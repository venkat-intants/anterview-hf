# Sarvam AI — Pricing, Rate Limits & Production Fit

**Date:** 2026-05-27
**Status:** Cleared for Sprint 3 with one model swap.
**Researcher:** main thread (market-researcher agent had no web access — direct WebFetch used)

---

## TL;DR

- **Voice fits the ₹12/session budget** at ~₹6–8/session if we pick Bulbul v2 TTS,
  ~₹10–12/session with Bulbul v3 TTS. Combined with ~₹1–2 Gemini cost, we stay
  inside the L1 envelope.
- **Saarika v2.5 (our currently configured STT model) is being deprecated** —
  switch to **Saaras v3 with `mode="transcribe"`** in Sprint 3, day 1. Same
  endpoint, same auth, same pricing tier.
- **Both STT and TTS have first-class WebSocket streaming APIs** — fits our
  p95 < 2s turn NFR. STT chunks must be WAV or raw PCM (16 kHz recommended,
  8 kHz supported for telephony).
- **No published per-connection concurrent limit on the Starter tier.** Have
  to test under load before any govt-tender demo with concurrent users.
  60 RPM is the only documented Starter-tier number.
- **Bulbul v3 streaming is the differentiator** — "low-latency streaming
  output mode, generated and played in near real time." Worth the price
  bump if voice quality is a sales lever. The Feb 2026 unlimited promo is
  over (today is May).

---

## 1. Pricing (today, from sarvam.ai/api-pricing)

| Service | Model | Cost (INR) | Per |
|---|---|---|---|
| STT | Saarika / Saaras (basic) | ₹30 | hour of audio |
| STT | + diarization | ₹45 | hour of audio |
| STT | + translation | ₹30 | hour of audio |
| STT | + translation + diarization | ₹45 | hour of audio |
| TTS | Bulbul v2 | ₹15 | 10,000 characters |
| TTS | Bulbul v3 | ₹30 | 10,000 characters |

- Universal **₹1,000 free credits at signup** (already burned on our existing key).
- Subscription tiers: **Starter** (PAYG, no minimum, 60 RPM) / **Pro** ₹10K (+₹1K bonus) / **Business** ₹50K (+₹7.5K bonus).
- No dedicated free dev tier. Demo runs on Starter pay-as-you-go.

---

## 2. Per-session cost model (10-min interview, our assumed shape)

| Cost line | Calculation | INR |
|---|---|---|
| STT candidate audio (~4 min) | 4 × ₹0.50 | ₹2.00 |
| TTS interviewer speech with **Bulbul v2** (~525 words ≈ 2,625 chars) | 2,625 × ₹15/10,000 | **₹3.94** |
| TTS interviewer speech with **Bulbul v3** (~525 words ≈ 2,625 chars) | 2,625 × ₹30/10,000 | **₹7.87** |
| Gemini (lite model, low context) | observed ~10K input tokens + ~1K output | **~₹1.00** |
| **Subtotal (Bulbul v2 path)** | | **~₹7/session** |
| **Subtotal (Bulbul v3 path)** | | **~₹11/session** |

CLAUDE.md budget ceiling is ₹12/session. **Bulbul v2 leaves ~₹5 headroom for
DB, storage, observability, and growth in turn count. Bulbul v3 leaves
~₹1 — too tight for production at scale.**

→ **Recommendation: Start Sprint 3 on Bulbul v2.** Add a v2/v3 swap config
flag so we can A/B on sales calls if quality matters.

---

## 3. Rate limits & quotas

| Tier | RPM | Concurrent | Source |
|---|---|---|---|
| Starter (PAYG) | 60 | Not published | sarvam.ai/api-pricing |
| Pro | Not published | Not published | — |
| Business | Not published | Not published | — |
| Streaming (WS) per-connection | Not published | Not published | docs.sarvam.ai |

- The `docs.sarvam.ai/api-reference-docs/getting-started/pricing` URL returns
  404 — rate-limit doc was either moved or removed. Worth a support ticket
  before we ship any concurrent-user demo.
- For the **APSSDC bid (20 lakh users)**: 60 RPM is a non-starter at scale.
  Need Pro or Business tier + likely a custom rate-limit conversation with
  Sarvam sales before submitting.

---

## 4. STT (Saarika / Saaras) — technical details

- **Model to use Sprint 3:** `saaras:v3` with `mode="transcribe"`.
  Saarika v2.5 is **being deprecated** — migrate now to avoid rework.
- **Languages:** Telugu (`te-IN`), Hindi (`hi-IN`), English (`en-IN`) all
  supported. Full list: Bengali, Tamil, Gujarati, Kannada, Malayalam, Marathi,
  Punjabi, Odia (11 Indian langs total). Good runway for the
  22-Indian-languages product goal.
- **Audio formats (streaming):** WAV or raw PCM only (`wav`, `pcm_s16le`,
  `pcm_l16`, `pcm_raw`). **MP3 / AAC / OGG are NOT supported for WebSocket
  streaming** — browser MediaRecorder defaults to webm/opus, so the frontend
  must encode/transcode to PCM 16-bit LE before sending. Add this as a
  Sprint 3 story.
- **Sample rate:** 16 kHz recommended; 8 kHz also supported (mobile telephony).
  Mismatch between connect handshake and audio body = silent quality loss.
- **Batch API chunk cap:** 30 seconds per request. Long audio must be split.
  Not relevant for our streaming path but worth noting if we ever batch.
- **Accuracy benchmarks (from Sarvam docs):**
  - All 11 langs: 4.96% CER / 18.32% WER
  - Hindi: 4.42% CER / 11.81% WER
- **Latency:** "results in milliseconds, not seconds" — no published p50/p95
  number. Test on Day 1 of Sprint 3 against our 2s budget.

---

## 5. TTS (Bulbul) — technical details

- **Bulbul v2 (recommended for Sprint 3):** ₹15/10K chars, batch API.
- **Bulbul v3 (upgrade path):**
  - 11 Indian languages, expanding to 22.
  - LLM-based prosody (natural emphasis, pauses, tone, pacing).
  - **Streaming output mode** — "audio generated and played back in near
    real time," critical for our turn loop.
  - Lowest word-skip / mispronunciation rate vs competitors per their human eval.
  - **Voice cloning** with "consent-based safeguards" — interesting for the
    naipunyam SSO scenario where a candidate could clone a teacher's voice.
    Probably out of scope but flag-worthy.
- **Languages:** EN/HI/TE all confirmed.
- **Streaming TTS doc URL returned 404** — same gap as the pricing doc.
  Test the streaming endpoint behavior empirically in Sprint 3 day 1.

---

## 6. Sketchy bits / unknowns

1. **Concurrent connection caps not published.** Cannot plan for the 20-lakh-
   user scenario without a number. Action: email `api@sarvam.ai` and ask
   before drafting any production tender response.
2. **Saarika v2.5 deprecation timeline not given.** Sarvam said "soon" —
   could be weeks or months. Migrate Sprint 3 to `saaras:v3` to avoid
   rework.
3. **DPDP / data-residency claims not visible on the pricing page.** Need
   to verify with Sarvam Legal before we put PII (candidate audio) through
   their pipeline. If they store audio for model training, that's a
   showstopper for the APSSDC bid. Tracked as a Sprint 3 blocker —
   `security-auditor` should review.
4. **No rate-limit doc.** The pricing doc URL we have for the API pricing
   page works, but `getting-started/pricing` 404s. Sarvam's docs are
   apparently in active reshuffle.
5. **Bulbul v3 voice cloning TOS:** they claim "consent-based safeguards"
   but we should review the cloning ToS before exposing the feature.

---

## 7. Bhashini comparison (when ULCA approval lands)

| Dimension | Sarvam (today) | Bhashini (when approved) |
|---|---|---|
| Cost | ₹0.50/min STT, ₹15-30/10K char TTS | Free (Govt of India) |
| DPDP claim | Unclear (action item) | Strong (Govt-owned) |
| Latency | "milliseconds" | Variable — community-run |
| Languages | 11 → 22 | 22 already |
| Streaming | Yes (WS) | Batch primarily |
| Rate limits | 60 RPM Starter | Not formally published |
| Reliability | Commercial SLA path | No SLA |

→ **Keep the `SPEECH_*_PROVIDER` env-swap design hot.** When Bhashini
approval lands, swap to Bhashini for free-tier cost (drops voice cost to
₹0/session → total ~₹1-2/session → huge L1 bid margin), but keep Sarvam
as the production fallback for the streaming + SLA requirement.

---

## 8. Actions for Sprint 3

| # | Action | Owner |
|---|---|---|
| 1 | Change `SARVAM_STT_MODEL=saarika:v2.5` → `saaras:v3` (with `mode=transcribe`) in `.env.example` and `.env` | backend-engineer (Day 1) |
| 2 | Change `SARVAM_TTS_MODEL=bulbul:v2` (already correct) — leave it | — |
| 3 | Frontend: encode mic capture to 16 kHz raw PCM (s16le) before WS send | frontend-engineer |
| 4 | Email `api@sarvam.ai` to confirm concurrent connection limit + DPDP data handling for `intants` account | founder |
| 5 | Add `LATENCY_BUDGET_MS=2000` instrumentation around STT + LLM + TTS calls | ai-orchestrator |
| 6 | Document fallback: if Sarvam latency p95 > 1s, swap LLM_PROVIDER to `gemini-flash-lite-latest` (already done) and consider OpenAI Whisper as STT backup | devops-engineer |

---

## Sources

- [Sarvam API Pricing](https://www.sarvam.ai/api-pricing) — retrieved 2026-05-27
- [Bulbul v3 announcement](https://www.sarvam.ai/blogs/bulbul-v3) — retrieved 2026-05-27
- [Saarika model docs](https://docs.sarvam.ai/api-reference-docs/getting-started/models/saarika) — retrieved 2026-05-27
- [Streaming STT API guide](https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/speech-to-text/streaming-api) — retrieved 2026-05-27
- [Speech-to-Text API Overview](https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/speech-to-text/overview) — retrieved 2026-05-27
- Bhashini comparison from internal `Final_stack.md` (cross-reference, not external)
