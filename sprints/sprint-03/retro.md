# Sprint 3 Retro — 2026-05-27

## Outcome

Sprint 3 shipped fully on 2026-05-27 (founder-verified live). All 13 active stories implementation-complete in one compressed session. Both code-review verdicts APPROVED-WITH-FOLLOWUPS. Both security reviews (S3-011; S3-004/S3-005) APPROVED-WITH-FOLLOWUPS. 201 tests green: 100 interview_core + 35 data_gateway + 101 web + 1 Playwright E2E. Founder validated end-to-end voice flow in the browser including STT/LLM/TTS round-trip and DPDP consent gate.

---

## Story Results

| ID | Story | Result | Notes |
|---|---|---|---|
| S3-001 | Sarvam STT integration | DONE | |
| S3-002 | Sarvam TTS integration | DONE | |
| S3-003 | WS audio chunking + 64 KB cap | DONE | |
| S3-004 | Subprotocol token + rate limit | DONE | Bundled with S3-005 |
| S3-005 | Mid-session JWT re-validation + iss/aud/jti | DONE | Bundled with S3-004 |
| S3-006 | STT → LangGraph wiring | DONE | |
| S3-007 | TTS → LangGraph wiring | DONE | |
| S3-008 | p95 latency instrumentation | DONE | |
| S3-009 | Frontend mic capture | DONE | |
| S3-010 | Frontend TTS playback | DONE | |
| S3-011 | DPDP consent FE + BE + server gate | DONE | CRITICAL bypass caught and fixed live — see What Didn't below |
| S3-012 | EN/HI/TE prompts | DONE | Emergent rewrite to Hinglish/Tenglish after founder live test |
| S3-013 | Playwright E2E text smoke | DONE | Passes in 42 s |
| S3-014 | Env standardisation + start scripts | DONE | |

**Velocity: 13 stories committed / 13 shipped (100%).** Plus ~10 emergent items not in the plan: persona research, Hinglish/Tenglish register rewrite, 3 bug fixes (WS receive loop, silence detector, prompt-reload), code-reviewer pass with 10 must-fix patches.

---

## What Worked

**1. Parallel specialist-agent invocation pattern.**
S3-006 + S3-009 fired in parallel; S3-007 + S3-010 fired in parallel; backend and frontend code-reviewer passes ran in parallel. Wall-clock time roughly halved on Day 1 versus sequential. This validates the agent-roster division-of-labour and is the default pattern going forward.

**2. security-auditor caught a CRITICAL DPDP consent bypass.**
React modal gated the UX but the backend enforced nothing. The gap was invisible until `security-auditor` looked at `sessions.py` and `ws.py` directly. The server-side consent gate was added live and re-verified. Without this pass, a regulator could have extracted session data for non-consenting users — DPDP Act 2023 breach. Security review gates are now non-negotiable on any story touching auth or PII.

**3. Founder live-test loop improved the product mid-sprint.**
Three product defects caught during real founder testing, all fixed same session: (a) double-greeting + Q1-without-wait — restructured to single welcome + pause, (b) interviewer stuck on one competency — 4-competency rotation rule added, (c) pure shudh Hindi/Telugu sounded robotic — Hinglish/Tenglish code-mixed register adopted (aligns with memory note `feedback_modern_codemixed_hi_te.md`). Live testing is worth scheduling explicitly in every sprint.

---

## What Didn't

**1. `uvicorn --reload` silently missed new-file additions.**
Hit twice: adding `consent_guard.py` and `redis_client.py`. `--reload` picks up edits to existing files but not new module imports under a package. Cost ~30 min of confusion each time. Fix: stop using `--reload` for any story that introduces a new Python module. Hard-restart the process instead.

**2. Docker Desktop crashed twice mid-session with no trigger.**
Both times required restart + container recreation + re-run of failed tests. ~15 min lost per crash. Suspected cause: heavy parallel agent activity (backend-engineer agent + Playwright install simultaneously). No root-cause confirmed. Fix: document as known-flaky. If it recurs in Sprint 4, file a follow-up to add `restart: unless-stopped` policy to all Docker Compose services.

**3. code-reviewer agent mis-graded two findings.**
One documentation-clarity item was labelled CRITICAL (should have been LOW). One `Gemini` comment in a Python file was flagged as wrong, but it matches the live running config, not the doc — a doc-vs-code conflict the reviewer could not resolve without seeing `.env`. Required human triage. Fix: include the active `.env` values and running config in code-reviewer prompts so the agent can distinguish the code-of-truth from stale documentation.

---

## Metrics

| Metric | Value |
|---|---|
| Stories committed | 13 |
| Stories shipped | 13 |
| Velocity (stories/sprint) | 13 |
| Emergent work items | ~10 |
| Tests green (end of sprint) | 201 (100 interview_core / 35 data_gateway / 101 web / 1 E2E) |
| Security findings caught | 1 CRITICAL + 2 HIGH + 2 MEDIUM (all resolved or tracked) |
| Critical bypass fixed live | 1 (S3-011 DPDP server gate) |
| Playwright E2E runtime | 42 s |

---

## Action Items Carried into Sprint 4

| ID | Action | Owner | Tracking |
|---|---|---|---|
| A1 | Add CI workflow (.github/workflows) — pytest + vitest + Playwright as PR gate | `devops-engineer` | task #36 → S4-006 |
| A2 | Replace module-global LLM adapter with proper DI | `backend-engineer` | task #42 → S4-013 |
| A3 | 4 S3-011 DPDP follow-ups: proxy hop count (#30), DB unique index (#31), retention promise (#32), revoke endpoint (#33) | `backend-engineer` / `security-auditor` | → S4-009, S4-010, S4-011, S4-012 |
| A4 | 3 S3-004 WS security follow-ups: prod gate ?token= (#38), rate-limit tuning for voice (#39), ingress per-IP throttle (#40) | `backend-engineer` / `devops-engineer` | → S4-007, S4-008 |
| A5 | Switch to hard-restart pattern when stories add new Python modules — no `--reload` | All build agents | Process change — no ticket |
| A6 | PM call on "Google Gemini vs Anthropic Claude" wording in ConsentModal + docs — resolve doc-vs-code conflict | `product-manager` / `backend-engineer` | → S4-014 |
