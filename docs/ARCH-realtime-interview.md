# Architecture — Real-Time Interview Engine (LiveKit + thin custom agent)

**Version:** 0.2 (revised after cto-architect review 2026-05-31 — verdict: GO-WITH-CHANGES)
**Status:** Design — no code yet
**Last updated:** 2026-05-31
**Owner:** Founder + AI agent team
**Branch context:** `feat/ui-redesign-v2`
**Supersedes:** the hand-rolled WebSocket turn loop (`routers/ws.py`, deleted 2026-05-31)
**Traces to:** HLD §6 (AI pipeline + latency budget), HLD §11 (NFR-03 concurrency), Final_stack (cost model), CLAUDE.md NFRs

> **v0.2 changes (cto-architect review):**
> 1. **Pipecat dropped** in favour of a thin custom LiveKit agent we fully own — default NO on
>    Pipecat unless a 2-day barge-in spike proves it's needed (we already own the hard pieces:
>    streaming Sarvam STT + sentence splitter). [was C2/S1]
> 2. **LangGraph demoted** from a running loop to a per-turn streaming function — the agent owns
>    turn cadence; the graph keeps only its policy (competency rotation, max-turns, persona). Avoids
>    two state machines fighting over "whose turn is it." [was C2]
> 3. **Latency budget rewritten** as a critical path (not a column sum), with a precise NFR
>    measurement point and SEPARATE demo (vendor video) vs bid (client-side RPM visemes) budgets. [was C1]
> 4. **LLM provider fixed** to Gemini-primary (demo) / Bedrock Mumbai (bid) — never "Groq default",
>    never GPT-4o; bid LLM must be Bedrock for DPDP residency. [was C3]

---

## 1. Purpose

Replace the deleted hand-rolled WebSocket turn loop with a **real-time, full-duplex
streaming interview engine** built on LiveKit (transport) + a **thin custom agent**
(orchestration), feeding a lip-synced avatar — while **reusing the brain + voice layers
we kept** (LangGraph *policy*, Sarvam STT/TTS adapters, LLM adapters, DB/auth/consent).

This doc is the contract for the rebuild. It does NOT pick an avatar vendor
(that's a separate bake-off); the avatar is a **pluggable slot**.

**Why a thin custom agent, not Pipecat:** we already own the two hardest pieces Pipecat
would provide — `SarvamStreamingSTT` (partials, reconnect, finalize grace) and
`SentenceBuffer`, both tested with our hard-won fixes (B-038 native script, v3 params).
Pipecat's value is highest when you DON'T have these; we do. A second orchestration
framework on top of LangGraph also collides with it (§5) and adds a heavy dependency
tree + a 3-year maintenance tax on a govt contract where source-code handover (RFP Pg 23)
and auditability are scored. A ~200–400 line agent we fully control wins. We revisit
Pipecat ONLY if a 2-day barge-in spike proves hand-rolled cancellation is too hard.

---

## 2. What we KEEP vs REBUILD (scope boundary)

| Layer | Status | Files |
|---|---|---|
| Interview brain — **POLICY** | **KEEP** | `graph/` nodes (competency rotation, max-turns close, persona) — kept as a per-turn function, NOT the running loop |
| Interview brain — **LOOP CONTROL** | **DROP** | the compiled-graph loop, `await_candidate_input` pause, checkpointer apparatus — the agent owns turn cadence instead |
| Voice adapters | **KEEP** | `speech/` — Sarvam STT/TTS (incl. B-038 native-script fix, already-streaming `sarvam_stt_stream.py`), v3 params just locked |
| LLM adapters | **KEEP** | `llm/` (groq, gemini, anthropic, base) |
| Data / auth / consent | **KEEP** | `config`, `models`, `database`, `dependencies`, `consent_guard`, `redis`, `s3` |
| Session CRUD | **KEEP** | `routers/sessions.py` (D-ID coupling already stripped) |
| **Transport** | **REBUILD** | was `routers/ws.py` → LiveKit room + token endpoint |
| **Turn orchestration** | **REBUILD** | was the `ws.py` loop → thin custom LiveKit agent (owns cadence, VAD/barge-in, persist) |
| **Avatar** | **REBUILD (pluggable)** | was `avatar/did.py` + `avatar_*.py` → `AvatarTransport` interface; demo=vendor video, bid=client-side RPM visemes |

> **Streaming gap to close (cto found):** the kept graph nodes currently call the BLOCKING
> `generate()`, not `generate_stream()`, and `GroqAdapter.generate_stream` is a single-chunk
> shim (only `GeminiAdapter` truly streams). The per-turn `InterviewBrain.next_turn(...)` adapter
> (§5) MUST wrap `generate_stream()` for the overlap thesis to hold. This is a known build task,
> not a surprise.

**Design rule:** the brain/voice/data layers must NOT import anything LiveKit- or
Pipecat-specific. They stay transport-agnostic exactly as today, so a future
transport swap (or the Tier-2 self-hosted path) is a constructor change.

---

## 3. Target component diagram

ASGI mints LiveKit tokens ONLY. The media work runs in a **separate worker tier**
(one thin agent per active session) — never co-located with the FastAPI request
workers, or a CPU-bumpy media leg wrecks request p95 (we already saw event-loop
contention force a 10s STT connect timeout).

```
                         ┌──────────────────────────────────────────────┐
   Candidate browser     │   interview_core ASGI (request tier)          │
   (React PWA)           │   POST /api/rooms/{session_id}/token          │
   ┌──────────────┐      │    → after auth + ownership + DPDP consent,   │
   │ LiveKit JS   │◄────►│      mints short-TTL LiveKit JWT (1 room)     │
   │ client       │      └──────────────────────────────────────────────┘
   │  mic ──────► │
   │  ◄── avatar  │      ┌──────────────────────────────────────────────┐
   └──────┬───────┘      │   Interview agent (worker tier, 1/session)    │
          │ WebRTC       │   thin custom LiveKit agent — WE OWN IT        │
          ▼              │                                              │
   ┌──────────────┐      │   LiveKit audio-in                            │
   │ LiveKit      │◄────►│     → SarvamStreamingSTT (speech/)            │
   │ SFU / room   │      │     → LiveKit VAD / turn-final + barge-in     │
   │ (self-host   │      │     → InterviewBrain.next_turn() [graph policy]│
   │  Mumbai for  │      │        → LLM generate_stream (Gemini/Bedrock) │
   │  bid)        │      │     → SentenceBuffer (speech/)                │
   └──────┬───────┘      │     → SarvamTTSAdapter (speech/, v3 params)   │
          │              │     → AvatarTransport (PLUGGABLE)             │
          ▼              │     → LiveKit audio-out                       │
   ┌──────────────┐      │   persist turn-final → Postgres + Redis       │
   │ Avatar       │◄─────┘   (on barge-in: cancel, do NOT persist        │
   │ demo: vendor │          the interrupted interviewer turn)           │
   │ bid: browser │
   │ RPM visemes  │
   └──────────────┘
```

**Key insight (de-risk, verified 2026-05-31):** Sarvam ships official Pipecat AND
LiveKit TTS plugins, and the avatar vendors that matter (HeyGen/Bey) join a LiveKit
room as a participant — so "feed our Sarvam audio into the avatar" is a **supported
integration path**, not a hack. **But we keep our OWN `speech/` adapters, not the
Sarvam plugin** (the plugin would silently drop the B-038 native-script fix and our
per-moment v3 `pace`/`temperature` control). Keeping our adapters also removes Pipecat's
main remaining value — its STT/TTS plugins — which is another reason the thin agent wins.

---

## 4. Transport: LiveKit

- **Room model:** one LiveKit room per interview session, keyed by `session_id`.
  Participants: (1) the candidate browser, (2) the Pipecat agent worker, (3) the
  avatar worker (when a vendor is wired).
- **Auth:** browser never gets LiveKit credentials directly. It calls
  `POST /api/rooms/{session_id}/token` on interview_core, which — after the SAME
  gates the old WS enforced (JWT valid, session ownership, **DPDP consent present**) —
  mints a short-TTL LiveKit JWT scoped to that one room. Mirrors the close-code
  4003/4004 ownership logic from the deleted `ws.py`.
- **Self-host option:** LiveKit is open-source and self-hostable. This is the
  hook for Tier-2 India-residency (run the SFU in Mumbai) — unlike the avatar
  SaaS, the transport itself can be made compliant.

## 5. Orchestration: thin custom LiveKit agent

- **One agent instance per active session**, run in the worker tier (NOT the ASGI
  process). Replaces the `ws.py` turn loop. ~200–400 lines, fully auditable.
- **The agent owns turn cadence.** LiveKit gives us VAD + turn-final + barge-in
  primitives (the part worth buying); the agent wires them to our kept code.
- **LangGraph is demoted to a per-turn function.** We add a thin adapter:
  `InterviewBrain.next_turn(state, candidate_text) -> AsyncIterator[str]` that wraps
  the existing `ask_question` / `follow_up` node logic over `generate_stream()`. The
  graph's *policy* (competency rotation, max-turns close, persona) is kept; its
  *loop control* (`await_candidate_input`, conditional-edge loop, checkpointer) is
  dropped — the agent does that. This avoids two state machines fighting over
  "whose turn is it", which is exactly where barge-in breaks.
- **Per-turn flow** (on each candidate turn-final from LiveKit VAD):
  1. LiveKit audio-in → `SarvamStreamingSTT` (partials during speech, final on turn-end)
  2. turn-final text → `InterviewBrain.next_turn()` → graph picks next line as a STREAM
  3. LLM `generate_stream` — **Gemini primary (demo) / Bedrock (bid); never GPT-4o**
     (hard constraint #1 + DPDP consent modal discloses only "Gemini or Groq")
  4. `SentenceBuffer` accumulates tokens → emits complete sentences
  5. `SarvamTTSAdapter` per sentence (v3 params locked; **per-moment pace/temperature
     finally wired HERE** — the trigger the brain/voice work left ready)
  6. `AvatarTransport` (pluggable) — demo: push audio to vendor, get video;
     bid: emit viseme/timing to the browser for client-side RPM
  7. LiveKit audio(+video)-out → browser
  8. persist the committed turn → Postgres + Redis
- **Barge-in:** one cancellation token per turn, owned by the agent. On interrupt:
  (a) cancel TTS+avatar immediately (audible stop is the UX that matters),
  (b) cancel the LLM stream, (c) **do NOT persist the interrupted interviewer turn**
  (treat as never-said), (d) the candidate's interrupting utterance becomes the next
  turn-final. Clean precisely because the brain is a per-turn function, not a running loop.

## 6. Avatar: pluggable slot (NOT decided here)

- Define an `AvatarTransport` interface (mirrors how `speech/base.py` abstracts TTS).
  Concrete impls picked by the bake-off: D-ID-streams / HeyGen / Bey, or
  Tier-2 self-hosted Three.js+RPM.
- **Interface shape:** `AvatarTransport` must serve BOTH tiers without leaking the
  vendor assumption — demo = "join the LiveKit room as a video participant", bid =
  "emit viseme/timing frames to the browser for client-side RPM rendering". If the
  interface is shaped only for vendor-video, the bid path won't fit it.
- **Voice glued to avatar** ([[project_voice_per_avatar]]): each of the 6 avatars
  binds ONE fixed Sarvam voice for the whole session. Emotion varies via
  pace/temperature/text, never by swapping speaker.
- **Bid reality:** NO real-time avatar SaaS is bid-compliant (₹12/session cap +
  India residency). Demo can use a vendor; the APSSDC bid stays Tier-2 self-hosted.

---

## 7. Latency budget (must hold p95 < 2s — NFR-01)

**NFR measurement point (precise):** p95 turn latency = **candidate end-of-speech →
first audible interviewer audio**. We budget that critical path, NOT a sum of every
stage. STT partials run *during* the candidate's speech (off the clock); TTS fires
per-sentence while the LLM still streams later sentences (overlap). So the path that
counts is: turn-final detected → STT finalize → brain/LLM first sentence → TTS first
chunk → (avatar first frame) → audible.

Two budgets, because the avatar is a different shape in each tier:

**(a) BID path — client-side RPM visemes (the path that MUST hold <2s):**

| Critical-path stage | Budget (ms) | Notes |
|---|---|---|
| Turn-final → STT finalize | 250 | most of STT already done during speech |
| Brain + LLM first sentence | 550 | `generate_stream`; thinking-token aware (Gemini flash-lite/Bedrock) |
| TTS first chunk (Sarvam v3) | 350 | sentence-by-sentence |
| Avatar first frame | **~0 (server)** | visemes render client-side from the audio — no server video hop |
| Network + jitter (WebRTC) | 250 | audio out |
| **Critical path → first audible** | **~1400** | comfortable margin under 2000 |

**(b) DEMO path — vendor video avatar (best-effort, NOT the NFR gate):**

| Critical-path stage | Budget (ms) | Notes |
|---|---|---|
| (as above, through TTS first chunk) | 1150 | |
| Avatar first video frame | **500–1200** | **vendor-dependent — UNKNOWN until bake-off** |
| Network + jitter | 250 | |
| **Critical path → first audible** | **1900–2600** | may exceed 2000 — acceptable for DEMO only |

**Hard gate:** the bake-off must produce *measured* p95 first-frame per vendor
**before** any vendor is wired. The agent emits the HLD §12 latency metrics
(STT p95 / LLM TTFT / TTS first-chunk / E2E) from day one so we measure, not guess.
The bid NFR is judged on path (a); path (b)'s avatar number must never contaminate it.

---

## 8. Compliance & cost notes

- **DPDP — transport:** consent gate stays server-side at the token-mint endpoint
  (same gates the old WS enforced: JWT valid, session ownership, consent present).
  PII rules unchanged — never log transcript/audio. India residency: demo
  non-compliant (acceptable, demo-only); bid self-hosts LiveKit + avatar in Mumbai.
- **DPDP — LLM (the harder one):** candidate transcripts are PII. The **bid LLM
  must be Bedrock Mumbai** for residency (`LLM_PROVIDER=bedrock`, Final_stack
  anchor #1). Demo uses Gemini. Provider is env-swappable behind `llm/base.py` —
  no pipeline change. **Never "Groq default"; never GPT-4o.**
- **Cost:** Sarvam v3 ~2× v2 (demo, free credits). Avatar SaaS per-minute is the
  budget threat — `cfo-cost-watcher` must sign off any vendor before bid use.
  **`cfo-cost-watcher` ACTION:** re-derive the infra-amortized line — the Final_stack
  ₹1.50 infra figure was sized for the OLD WS architecture, not a per-session
  self-hosted LiveKit SFU media leg held open for the full 10-min session. Don't
  assume "self-hosted = free."

---

## 9. Resolved decisions (cto-architect review 2026-05-31)

1. **Pipecat vs thin agent →** Thin custom LiveKit agent. Default NO on Pipecat;
   revisit only if a 2-day barge-in spike proves hand-rolled cancellation too hard.
2. **Our adapters vs Sarvam plugin →** Ours. Non-negotiable; the plugin drops the
   B-038 native-script fix + our v3 `pace`/`temperature` control.
3. **Scaling →** One agent per *active session* in a **separate worker tier** (not
   ASGI). HPA on concurrent-session count, sized to **NFR-03 (5k → 50k concurrent**,
   not "20 lakh total"). Needs a graceful-drain story: a 10-min session must survive
   a rolling deploy. `cfo-cost-watcher` re-costs the LiveKit media infra line.
4. **LangGraph fit →** Does NOT fit as a running loop. Demoted to a per-turn
   streaming function (`InterviewBrain.next_turn`); the agent owns cadence +
   the candidate-input pause. (§5)
5. **Barge-in →** cancel TTS+avatar+LLM-stream; don't persist the interrupted
   interviewer turn; candidate's interruption is the next turn-final. (§5)

### Remaining build-time notes
- Close the streaming gap: graph nodes use blocking `generate()`; `GroqAdapter.generate_stream`
  is a single-chunk shim. `next_turn` must use `generate_stream()`; only Gemini truly streams today.
- `STTResult.confidence` is dropped on the streaming path. The bid will want
  low-confidence handling ("sorry, could you repeat?") for accented Indic speech —
  conscious omission for now, flagged for the bid.
- The `AvatarTransport` interface must accommodate BOTH "join a LiveKit room as a
  video participant" (demo vendor) AND "emit visemes/timing to the browser" (bid
  client-side RPM) — do not shape it only for the vendor-video case.

---

## 10. What this unblocks

Once approved: build the `AvatarTransport` interface + LiveKit token endpoint +
thin agent skeleton (vendor slot stubbed) with §12 latency metrics emitting from
day one, then run the bake-off against that real skeleton instead of a throwaway.
Brain (policy)/voice/data plug in unchanged.

**Verdict on record:** cto-architect — **GO-WITH-CHANGES** (all four changes make
the design smaller). LiveKit transport approved; Pipecat dropped; LangGraph demoted;
latency budget split demo/bid; LLM provider fixed to Gemini/Bedrock.
