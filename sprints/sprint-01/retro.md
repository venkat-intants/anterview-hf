# Sprint 1 Retro — 2026-05-27 to 2026-06-10

**Facilitated by:** sprint-coordinator
**Date:** 2026-06-10

---

## What Worked (3)

1. **`security-auditor` integration was high-ROI.** Five must-fix findings (1 CRITICAL + 4 HIGH) caught and fixed within the sprint — JWT_SECRET rotation, refresh TTL halved, timing oracle closed, PII scrubbed from logs, CORS hardened. Zero of these would have surfaced in a pure feature-velocity sprint. Pattern confirmed: run `security-auditor` on every sprint that touches auth, API boundaries, or PII handling.

2. **Parallel sequencing worked.** Frontend started with a mock API on Day 1; backend delivered real endpoints by Day 4-5. Zero days of frontend idle time waiting on backend. The explicit dependency order in `plan.md` prevented the usual "it's not ready yet" back-and-forth.

3. **Pluggable `AuthProvider` interface proved itself immediately.** Switching between `LocalAuthProvider` and a stub `MockAuthProvider` in tests required no call-site changes. The abstraction will absorb `naipunyam` and `google` adapters cleanly in Sprint 5+. Good upfront design decision validated.

---

## What Didn't (3)

1. **venv path assumption caused script failure.** `scripts/generate_jwt_secret.ps1` (and related scripts) hardcoded an in-project venv path. Poetry on this machine stores venvs in the global user cache, not in-project. Root cause: assumed venv location instead of detecting it via `poetry env info --path`. Fix: all future scripts that invoke a Poetry venv must call `poetry env info --path` dynamically and fail fast with a clear error if the env is not found.

2. **Gemini model selection cost 1+ days of debugging.** Tried 5 models before landing on `gemini-2.5-flash`. Blockers: thinking-token quirks on some models, quota limits on free tier for others, and `MAX_TOKENS` handling differing by model family. This was an avoidable research detour. Fix applied: model-selection gotchas documented in `.env` comments and `MAX_TOKENS` handling made explicit. Going forward: `ai-orchestrator` owns model compatibility validation before any new model is introduced.

3. **No E2E test existed — founder verification was manual.** The browser demo proved the flow worked, but there was no automated Playwright test to catch regressions. If auth breaks in Sprint 2 while plumbing WebSockets, we will not know until someone manually re-tests. Fix: B-028 (Playwright E2E smoke: register → login → dashboard) is promoted to Sprint 2 scope.

---

## Action Items for Sprint 2 (3)

1. **All scripts detect Poetry venv dynamically.** Any new script that needs to invoke a Poetry environment must use `$(poetry env info --path)` (or PowerShell equivalent) rather than hardcoding a path. `backend-engineer` to apply this pattern to any scripts written in Sprint 2. `code-reviewer` to flag hardcoded venv paths as a blocking review comment.

2. **`ai-orchestrator` validates model compatibility before sprint kick-off.** Before Sprint 2 Day 1, `ai-orchestrator` confirms that `gemini-2.5-flash` (or the model in `.env`) works for multi-turn conversational prompts, respects `MAX_TOKENS`, and does not exhibit thinking-token issues under the interview turn prompt design. Output: a one-paragraph "model OK" sign-off written into the Sprint 2 Day 1 daily note.

3. **Playwright E2E smoke test ships in Sprint 2.** Story B-028 is committed in Sprint 2 (not deferred again). Acceptance criteria: `npx playwright test` runs the full register → login → dashboard → logout flow against the local stack and passes in CI. This is the regression guard for auth while the rest of the stack is being wired up.

---

## Velocity

- Committed: 7 stories
- Done: 7
- Carry-over: 0

**Velocity: 7 stories / sprint**

Security findings resolved within sprint (not deferred): 5
Tests at close: 42 (21 data_gateway + 6 interview_core + 15 web)
Founder sign-off: yes — browser-verified 2026-06-10
