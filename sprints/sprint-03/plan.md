# Sprint 3 Plan — 2026-06-25 to 2026-07-08

## Sprint Goal
A logged-in candidate can speak their answers and hear the AI interviewer's questions through speakers — completing a full 3-5 turn voice interview without typing.

---

## Pre-Sprint Status — S3-000 (DONE before kickoff)

| ID | What | Evidence |
|---|---|---|
| S3-000 | WS Origin allowlist | `services/interview_core/app/routers/ws.py::_origin_allowed` + tests `test_ws_origin_allowlisted` + `test_ws_origin_rejected_for_unknown` |

This was the `security-auditor` mandatory condition from S2-003. Confirmed landed. No action needed — listed for audit traceability only.

---

## Capacity

| Who | Role | Agent-days | Notes |
|---|---|---|---|
| `backend-engineer` | Sarvam STT pipeline, Sarvam TTS pipeline, WS audio chunking protocol, security audit MEDIUMs, DPDP consent ledger write | 5 | Bottleneck — capped at 5 agent-days; each invocation S-sized. Demo runs on Sarvam (Bhashini approval still pending — keep `SPEECH_*_PROVIDER` swap hot). |
| `ai-orchestrator` | Voice turn-loop integration, latency instrumentation, multilingual prompts | 3 | Owns STT→LangGraph→TTS wiring after pipeline stubs exist |
| `frontend-engineer` | Mic capture UI, audio playback, DPDP consent screen, E2E Playwright voice + consent | 3 | Can build mic/playback UI against mocked audio messages from Day 1 |
| `security-auditor` | S2-003 MEDIUM findings review (5 items), DPDP consent screen sign-off | 1 | Gate: no WS security stories merge without sign-off |
| `code-reviewer` | All PRs | As needed | Same-day turnaround; no merge without approval |
| `devops-engineer` | Env var standardisation + startup script hardening (retro action item 3) | 1 | On-call otherwise |
| Human engineer | Senior engineer | NOT YET ONBOARD | No capacity planned |
| Founder | Approval gate | ~1 hr/day | Final sign-off on sprint review 2026-07-08 |

**Total committed agent-days: 13** (backend 5 + ai-orchestrator 3 + frontend 3 + security 1 + devops 1). Within cap.

---

## Committed Stories

All stories are S-sized (~1 day or smaller). No M, L, or XL. If a story grew beyond S during Sprint 2, it is split here with a sequencing note.

| ID | Story | Assignee | Estimate | Acceptance Criteria |
|---|---|---|---|---|
| S3-001 | Sarvam STT adapter: `stt_pipeline.py` — HTTP batch (non-streaming) | `backend-engineer` | S | `SarvamSTTAdapter` calls Sarvam `/speech-to-text` endpoint using model `saaras:v3` with `mode="transcribe"` (NOT `saarika:v2.5` — deprecating per research/sarvam-pricing-2026-05.md). Accepts WAV / PCM 16 kHz mono. Returns `{"transcript": str, "language": str, "confidence": float}`. Reads `SARVAM_API_KEY`, `SARVAM_STT_MODEL` from env (already in `.env`). Unit tests pass with a recorded 5-second WAV fixture (EN + TE). `code-reviewer` approved. No hardcoded secrets. |
| S3-002 | Sarvam TTS adapter: `tts_pipeline.py` — HTTP, returns WAV bytes | `backend-engineer` | S | `SarvamTTSAdapter` calls Sarvam `/text-to-speech` endpoint with model `bulbul:v2` (v2 not v3 — half the cost, fits ₹12/session budget per research). Reads `SARVAM_API_KEY`, `SARVAM_TTS_MODEL` from env. Unit tests: EN greeting text → non-empty WAV bytes returned. `code-reviewer` approved. |
| S3-003 | WS audio chunking protocol: define message types for audio frames | `backend-engineer` | S | New WS message types documented in `docs/ws-protocol-v2.md` and enforced in `ws.py`: `{"type":"audio_chunk","data":"<base64>","seq":int}` (client→server, mic audio), `{"type":"audio_response","data":"<base64>","format":"wav"}` (server→client, TTS output), `{"type":"transcript","text":str,"speaker":"candidate"}` (server→client, STT result). Payload size limit enforced: reject any single message >64 KB with `{"type":"error","code":"PAYLOAD_TOO_LARGE"}`. Unit test: oversized message rejected. `security-auditor` signs off before merge. |
| S3-004 | WS security MEDIUMs — subprotocol token transport + per-connection rate limit | `backend-engineer` | S | (a) JWT delivered via `Sec-WebSocket-Protocol` header (subprotocol) as primary; `?token=` query-param demoted to fallback with deprecation log. (b) Per-connection rate limit: max 60 messages/minute per connection; excess messages → `{"type":"error","code":"RATE_LIMITED"}` + connection closed after 3 violations. `security-auditor` signs off before merge. |
| S3-005 | WS security MEDIUMs — mid-session JWT re-validation + JWT iss/aud/jti claims | `backend-engineer` | S | (a) Re-validate JWT every 5 minutes during an active session; if expired, send `{"type":"error","code":"TOKEN_EXPIRED","message":"reconnect required"}` and close cleanly. (b) JWT validation checks `iss`, `aud`, `jti` claims; reject token missing any of the three with code 4001. Unit tests: expired mid-session token → clean close; missing iss → 4001; jti replay → 4001 (Redis-backed jti blocklist, TTL = token expiry). `security-auditor` signs off before merge. |
| S3-006 | AI orchestrator: wire STT output into LangGraph `await_answer` node | `ai-orchestrator` | S | `await_answer` node accepts `audio_chunk` messages from WS layer, accumulates base64 frames, flushes to `SarvamSTTAdapter` on silence signal (VAD stub: flush after `STT_SILENCE_TIMEOUT_MS` env var, default 1500 ms), feeds transcript text into state as `candidate_text`. Existing text-input path preserved as fallback (for backward compat + Playwright tests). Unit tests: mock STT returns "I am a developer" → state `candidate_text` = "I am a developer". |
| S3-007 | AI orchestrator: wire TTS into LangGraph `ask_question` + `follow_up` nodes | `ai-orchestrator` | S | After Gemini generates question text, `ask_question` and `follow_up` nodes call `SarvamTTSAdapter`, get WAV bytes, emit `{"type":"audio_response","data":"<base64>","format":"wav"}` over WS. Text turn still emitted as `{"type":"turn","speaker":"interviewer","text":...}` in parallel (for transcript display). Unit tests: mock TTS returns WAV bytes → WS message emitted with correct type. |
| S3-008 | AI orchestrator: p95 latency instrumentation — STT→LLM→TTS per-turn timing | `ai-orchestrator` | S | Structured log entry emitted per turn: `{"event":"turn_latency","session_id":str,"turn":int,"stt_ms":int,"llm_ms":int,"tts_ms":int,"total_ms":int}`. At session close, `PATCH /sessions/{id}` stores `p95_latency_ms` (max observed in that session as proxy until load test exists). Integration test: run 3-turn mock session, confirm log entries present with all 4 fields > 0. |
| S3-009 | Frontend: mic capture UI + audio streaming over WS | `frontend-engineer` | S | "Start Speaking" button requests mic permission (MediaDevices API). On permission grant: button changes to "Listening..." with animated pulse indicator. Audio captured at 16 kHz mono raw PCM (NOT webm/opus — Sarvam streaming rejects compressed formats per research/sarvam-pricing-2026-05.md). Chunked every 500 ms, sent as `{"type":"audio_chunk","data":"<base64>","seq":n}` over existing WS. On silence (2 s of no audio above threshold): sends `{"type":"turn_end"}` to signal STT flush. Browser mic permission denied → visible error banner "Microphone access required". Vitest tests for state management. Tested in Chrome + Firefox (mobile Safari: manual verification gate in review). |
| S3-010 | Frontend: TTS audio playback from WS `audio_response` messages | `frontend-engineer` | S | On receiving `{"type":"audio_response","data":"<base64>","format":"wav"}`: decode base64 → ArrayBuffer → Web Audio API AudioContext → play. Audio plays through speakers without click/pop artefact. If autoplay blocked by browser policy: queue audio and play on next user gesture with a visible "Tap to hear response" fallback. Vitest test: mock WS message → AudioContext.decodeAudioData called. Tested in Chrome + Firefox. |
| S3-011 | DPDP consent screen (frontend) + `dpdp_consent_ledger` write (backend) | `frontend-engineer` + `backend-engineer` | S (split: FE half-day, BE half-day) | Frontend: before first interview session (checked per user, not per session), show full-screen consent modal: purpose of data collection, languages processed, retention period (90 days), "I Agree" / "Decline" buttons. "Decline" → redirect to `/jobs` with banner "You must consent to use the interview feature." "I Agree" → `POST /consent` in `data_gateway`; on 200, proceed to `POST /sessions`. Backend: `POST /consent` (JWT-protected) writes row to `dpdp_consent_ledger` (`user_id`, `purpose=interview`, `version=1`, `consented_at`, `ip_hash` — hash only, not raw IP). Returns `{"consented": true}`. `security-auditor` reviews before merge. Unit tests: consent row written, duplicate consent returns 200 idempotently. |
| S3-012 | Multilingual prompts: EN / HI / TE system prompt variants | `ai-orchestrator` | S | Three system prompt templates in `prompts/interviewer_{en,hi,te}.jinja2`. Language selected at session start from `sessions.language` field. Gemini call uses correct template. Unit tests: EN session → EN template loaded; HI session → HI template loaded; TE session → TE template loaded. Prompts reviewed for naturalness by founder (gate: founder sign-off in sprint review). |
| S3-013 | Playwright E2E — text interview smoke test (carry-over S2-008) | `frontend-engineer` | S | `npx playwright test` (headless) runs against local stack. Test: register → login → /jobs → click "Start Interview" → send 3 text messages → see "Interview Complete" screen. Passes in under 60 seconds. Added to CI (PR fails if broken). Note: voice E2E (mic) uses mock audio injection (no real mic in CI). |
| S3-014 | Devops: env var standardisation + startup script hardening | `devops-engineer` | S | (a) Rename `VITE_WEBSOCKET_URL` → `VITE_WS_BASE_URL` everywhere (`.env`, `interview-ws.ts`, docs) so the hardcoded fallback in `interview-ws.ts` is removed. (b) `start-all.ps1` brings up Docker stack + `data_gateway` + `interview_core` + `web` in order, polls each `/health/live` endpoint before declaring ready (max 30 s per service, exit 1 on timeout). (c) Verify `.env.example` has Sarvam STT/TTS model env vars (`SARVAM_STT_MODEL=saaras:v3`, `SARVAM_TTS_MODEL=bulbul:v2`) — currently has saarika:v2.5 which is deprecating. Keep Bhashini block as-is (pending approval). Retro action item 3 — closes it. |

**Total committed: 14 stories (including S3-000 pre-done). Active stories: 13. All S-sized.**

---

## Story Sequencing (Dependency Order)

```
Day 1:     S3-001 (Bhashini STT adapter)      [backend-engineer]
           S3-002 (Bhashini TTS adapter)      [backend-engineer — can run same day as S3-001]
           S3-014 (env/startup hardening)     [devops-engineer — no backend dependency]
           S3-012 (multilingual prompts)      [ai-orchestrator — no pipeline dependency]

Day 2:     S3-003 (WS audio protocol)         [backend-engineer — needs S3-001+S3-002 done first]
           S3-009 starts (mic UI, mock mode)  [frontend-engineer — builds against S3-003 protocol doc]
           S3-010 starts (TTS playback UI)    [frontend-engineer — same]

Day 3:     S3-004 (WS security: subprotocol  [backend-engineer]
             + rate limit)
           S3-005 (WS security: mid-session  [backend-engineer — security-auditor reviews S3-003+
             JWT + iss/aud/jti)               S3-004+S3-005 together before any merge]

           S3-006 (STT → LangGraph wiring)   [ai-orchestrator — needs S3-001 done]
           S3-007 (TTS → LangGraph wiring)   [ai-orchestrator — needs S3-002 done]

Day 4-5:   S3-008 (latency instrumentation)  [ai-orchestrator — needs S3-006+S3-007 done]
           S3-011 FE half (consent screen)   [frontend-engineer — no backend dependency to start]
           S3-011 BE half (POST /consent)    [backend-engineer — security-auditor gates merge]

Day 6-7:   S3-013 (Playwright E2E text)      [frontend-engineer — needs full stack wired]
           Integration pass: voice flow      [ai-orchestrator + backend-engineer]
             STT → LangGraph → TTS → WS
             end-to-end with real Bhashini

Day 8-10:  code-reviewer final PR sweeps
           Founder demo: speak → hear → complete screen
           Sprint review + retro (2026-07-08)
```

**Sequencing notes (S-splitting):**
- B-008 (STT, originally M) → S3-001 (adapter only) + S3-006 (LangGraph wiring). Two separate background agent invocations.
- B-010 (TTS, originally M) → S3-002 (adapter only) + S3-007 (LangGraph wiring). Same pattern.
- S2-003 MEDIUM cleanup (originally one block) → S3-003 (protocol + payload limit) + S3-004 (subprotocol + rate limit) + S3-005 (mid-session JWT + claims). Three separate invocations.
- S3-011 (DPDP) split across `frontend-engineer` + `backend-engineer`, each half-day; merge only after both halves done and `security-auditor` reviewed.

---

## Definition of Done

A story is DONE only if all of the following are true:

1. Code merged to `main` on a `feat/<name>` branch via PR
2. All tests passing (pytest backend, Vitest frontend, Playwright E2E)
3. `code-reviewer` approved in PR
4. `security-auditor` approved for: S3-003, S3-004, S3-005, S3-011 (security-relevant stories)
5. No secrets hardcoded — all config via `BaseSettings` / env vars
6. No PII logged (structlog redaction processor active — inherited from Sprint 1)
7. p95 turn latency measured and reported in sprint review (S3-008 must be done before review)
8. Voice flow tested in Chrome + Firefox (manual). Mobile Safari: at minimum one manual verification pass before sprint review; document result in review.md
9. DPDP consent recorded in `dpdp_consent_ledger` for every demo session run during sprint review
10. Listed in sprint review (2026-07-08)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sarvam STT rate limit or downtime during integration | Medium | High | S3-001 isolates adapter behind `SPEECH_STT_PROVIDER` env var. Build `MockSTTAdapter` on Day 1 so S3-006 can proceed without live Sarvam. Sarvam Starter tier = 60 RPM (research) — fine for sprint demo, NOT for concurrent users. If Sarvam blocked at sprint review: demo with mock STT + real TTS (partial voice demo) and log incident. |
| Sarvam TTS latency pushes p95 turn > 2 s | Medium | High | S3-008 instruments per-leg timing Day 3+. Sarvam published as "milliseconds, not seconds" but no specific p50/p95 number. If TTS leg > 800 ms consistently: switch to `bulbul:v3` streaming mode (more expensive but lower latency) or serve cached greeting audio for demo. Flag to founder immediately. |
| Sarvam per-session cost overruns ₹12 budget | Low | High | Research confirmed ₹6-8/session with `bulbul:v2` + `saaras:v3` (fits). If we accidentally use `bulbul:v3` we hit ₹10-12/session (at the limit). `cfo-cost-watcher` reviews `.env` model selections at S3-002 PR merge. |
| Browser mic permission UX varies (Safari autoplay + permission policy) | High | Medium | S3-009 + S3-010 build explicit permission request flow + autoplay fallback ("Tap to hear"). Mobile Safari manual verification required before review. If Safari blocks autoplay at demo: pre-grant workaround documented for demo device. |
| WS audio chunking protocol design mismatch between FE and BE | Medium | Medium | S3-003 writes protocol doc Day 2 before FE starts building. FE builds against mock WS server (same mock pattern as Sprint 1+2). Integration pass scheduled Day 6. |
| Gemini / LLM 503 recurrence (same failure as Sprint 2 demo) | Low | High | `LLMAdapter` already swappable via env var (Sprint 2 lesson). `GEMINI_MODEL` env var points at `gemini-flash-lite-latest` as default — already known-stable from Sprint 2 switch. Claude Sonnet 4.6 via Anthropic API available as second fallback if Gemini down at demo. |
| Vercel cold-start breaks WS upgrades in demo environment | Low | Medium | `interview_core` runs on Railway (not Vercel) — not subject to Vercel WS limitations. Mitigated by architecture. If Railway cold-starts: add `/health/live` keep-warm ping in `start-all.ps1` (covered by S3-014). |
| `security-auditor` review of S3-003+S3-004+S3-005 is sequential bottleneck | Medium | Medium | Schedule `security-auditor` for Day 4-5 block review of all three WS security stories together. `backend-engineer` opens draft PRs for S3-003+S3-004+S3-005 on Day 3 so auditor can pre-read before final code lands. |

---

## API Contracts (written Day 1 — FE builds against these)

**WS audio message shapes (v2 additions):**
```json
// Client → Server: mic audio chunk (base64-encoded PCM/WAV, 500 ms slices)
{"type": "audio_chunk", "data": "<base64>", "seq": 1}

// Client → Server: end of candidate speech (triggers STT flush)
{"type": "turn_end"}

// Server → Client: STT transcript (for display in chat)
{"type": "transcript", "text": "I am a software developer.", "speaker": "candidate"}

// Server → Client: TTS audio response
{"type": "audio_response", "data": "<base64>", "format": "wav"}

// Server → Client: payload too large
{"type": "error", "code": "PAYLOAD_TOO_LARGE", "message": "Message exceeds 64 KB limit"}

// Server → Client: rate limited
{"type": "error", "code": "RATE_LIMITED", "message": "Too many messages; slow down"}

// Server → Client: mid-session token expired
{"type": "error", "code": "TOKEN_EXPIRED", "message": "reconnect required"}
```

All existing v1 message types (connected, turn, complete, error with other codes) remain unchanged.

**`POST /consent` (data_gateway):**
```json
// Request (JWT-protected)
{"purpose": "interview", "version": 1}

// Response 200
{"consented": true, "consented_at": "2026-06-25T10:00:00Z"}

// Response 200 (idempotent — already consented)
{"consented": true, "consented_at": "<original timestamp>"}
```

---

## Dependencies

| Dependency | Owner | Status | Needed by |
|---|---|---|---|
| Sarvam API key active (`SARVAM_API_KEY`) | Founder / DevOps | GREEN — verified Sprint 2 health check (api.sarvam.ai 200) | S3-001, S3-002 |
| Sarvam STT (saaras:v3) + TTS (bulbul:v2) endpoints | `ai-orchestrator` | Documented in research/sarvam-pricing-2026-05.md | S3-001, S3-002 |
| Bhashini ULCA approval | Founder | PENDING — keep `SPEECH_*_PROVIDER` swap design hot; do not block Sprint 3 on this | Sprint 4 / 5 (when approval lands) |
| Sarvam concurrent connection limit (not published) | Founder | Email `api@sarvam.ai` before any concurrent-user demo | Pre-APSSDC bid |
| Sarvam DPDP data handling (storage / training use) | `security-auditor` + Founder | Email Sarvam Legal before production traffic with PII | Before sprint review |
| `security-auditor` block review of WS security stories | `security-auditor` | Schedule Day 4-5 | S3-003, S3-004, S3-005 merge gate |
| Gemini API key + `gemini-flash-lite-latest` quota (inherited from Sprint 2) | `ai-orchestrator` | GREEN (Sprint 2 verified) | S3-006, S3-007 |
| Founder review for sprint review demo | Founder | ~1 hr on 2026-07-08 afternoon | Sprint review |
| Mobile Safari test device | Founder / engineer | Manual only | Definition of Done item 8 |

---

## Out of Scope (Explicitly Deferred to Sprint 4)

| Item | Rationale |
|---|---|
| Simli / HeyGen avatar rendering | Voice pipeline must be stable and p95 < 2 s before adding avatar complexity. Avatar is a visual enhancement; broken voice with pretty avatar is worse than no avatar. Sprint 4. |
| Three.js + Ready Player Me custom avatar | Phase 3 production feature. No scope in Phase 1 demo. |
| End-of-session scoring (Gemini scorer) | Sprint 4. |
| PDF scorecards (WeasyPrint) | Sprint 4. |
| `feedback_billing` service bootstrap | Sprint 4. |
| `admin_ops` service bootstrap | Sprint 4. |
| Naipunyam SSO | Sprint 5. |
| Silero VAD (WebAssembly, client-side barge-in) | Requires WASM build pipeline. S3-009 uses a simple silence-timeout stub (1500 ms). Full Silero VAD deferred to Sprint 4 when voice is proven stable. |
| WebRTC audio capture | S3-009 uses MediaDevices API (MediaRecorder). WebRTC upgrade deferred to Sprint 4 when lower latency is needed. |
| OpenAI embeddings / NOS KB search | Sprint 4+. |
