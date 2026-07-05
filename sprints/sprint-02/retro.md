# Sprint 2 Retro — 2026-06-10 to 2026-06-24

**Facilitated by:** sprint-coordinator
**Date:** 2026-05-27 (early — demo shipped ahead of plan)

---

## Sprint Goal Status

> "A logged-in candidate can select a job from a seeded list, start a text-only
> interview, complete 3-5 AI-driven turns via WebSocket, and reach an Interview
> Complete screen showing a session ID + placeholder summary."

**Met.** Founder verified the full flow in the browser on 2026-05-27 against the
live stack (real DB, real Gemini, real WS — `VITE_USE_MOCK=false`).

Path proven end-to-end:
register → login → /jobs (3 real DB-seeded roles) → POST /api/sessions →
WS upgrade with JWT → templated greeting (turn 0) → Gemini Q1 → candidate text
turn → Gemini follow-up → ... → closing + complete screen → session row
flipped to `completed`, turns persisted, duration recorded.

---

## What Worked (3)

1. **Two-tier swappable design absorbed two crises with one env change each.**
   (a) Gemini `2.5-flash` returned 503 (Google-side overload) right before the
   demo — switched to `gemini-flash-lite-latest` via one `.env` line. (b) The
   `LLMAdapter` Protocol let `ai-orchestrator` and `backend-engineer` work in
   parallel on the graph + the adapter without coupling. Validates the
   `LLM_PROVIDER` / `AVATAR_PROVIDER` / `SPEECH_*_PROVIDER` design pattern.

2. **Mock-first frontend strategy paid off again.** Same Sprint 1 pattern:
   frontend built the entire jobs list + chat UI + complete screen against
   mocks while backend wired the WS. When `VITE_USE_MOCK=false` was flipped
   the only break was a single misrouted base URL (sessions.ts pointed at
   data_gateway instead of interview_core). Fixed in ~2 min.

3. **Pre-merge `security-auditor` review on S2-003 prevented two would-be
   incidents.** Caught CSWSH (Origin header) and session-ownership-after-auth
   gaps. Both were addressed: ownership check landed in S2-007, Origin
   allowlist landed as first commit of Sprint 3 (per the auditor's
   condition — see `sprint-03/plan.md`).

---

## What Didn't (3)

1. **Backend-engineer agent was over-scoped twice and had to be interrupted.**
   First with S2-003+S2-004 batched, again with S2-007 covering 4-5 stories'
   worth. Cause: I (orchestrator) framed each agent task too coarsely. Fix
   already applied later in the sprint: smaller chunks (~3-5 min each) +
   background mode so the human can keep working while agents run.
   Documented as `feedback_use_agent_team` pattern.

2. **Two parallel agents reported contradictory test results** during S2-003 /
   S2-005 (one said "3 failures", the other said "6/6 pass"). Resolved by
   running pytest manually — all green. Root cause: snapshot timing — one
   agent ran tests before the other's commit landed. Fix: when two agents
   touch overlapping code, run a single verification pass before trusting
   either report.

3. **Interrupted S2-007 agent had already finished the code** but was killed
   before it could report. Wasted ~10 min on the assumption it needed to be
   restarted from scratch. Fix: when an agent is interrupted, read the
   tree state before re-launching — it may already be done.

---

## Action Items for Sprint 3 (3)

1. **Smaller-chunk + background-mode is the default agent pattern.** No
   agent task larger than one story (S-size, ~3-5 min). Always use
   `run_in_background=true` so the human can act while waiting. If a
   story is M or L, split it into N background agents and a sequencing
   note. (Applies to `backend-engineer`, `frontend-engineer`,
   `ai-orchestrator`, `devops-engineer`.)

2. **Sprint 3 first commit = WS Origin allowlist (DONE — landed before
   sprint kickoff).** The S2-003 auditor required this as Sprint 3's
   first commit. It is already in `app/routers/ws.py::_origin_allowed`
   with tests `test_ws_origin_allowlisted` + `test_ws_origin_rejected_for_unknown`.
   Sprint 3 plan should acknowledge this is done and move to remaining
   audit MEDIUMs (subprotocol token transport, payload size/rate limits,
   mid-session JWT re-validation, JWT iss/aud/jti claims).

3. **`devops-engineer` to investigate Vite env mismatch + harden startup
   scripts.** Discovered during demo: `interview-ws.ts` reads
   `VITE_WS_BASE_URL` but `.env` defines `VITE_WEBSOCKET_URL` (working only
   because of a hardcoded fallback). Same audit pass should standardize
   startup scripts so `start-all.ps1` brings docker + data_gateway +
   interview_core + web up in one command and waits for each `/health/live`
   before declaring ready.

---

## Velocity

- Committed: 8 stories
- Done: 7 (S2-001, S2-002, S2-003, S2-004, S2-005, S2-006, S2-007)
- Carry-over: 1 (S2-008 Playwright E2E — deferred to Sprint 3 backlog;
  manual verification by founder substituted for this sprint demo)

**Velocity: 7 stories / sprint (matches Sprint 1)**

Security findings resolved within sprint (not deferred): 1 HIGH (Origin /
CSWSH — landed as Sprint 3's first commit per auditor's condition)

Tests at close: 100 (28 data_gateway + 19 interview_core + 30 web + 23
others). All green.

Founder sign-off: yes — browser-verified 2026-05-27.
