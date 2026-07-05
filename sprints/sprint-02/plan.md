# Sprint 2 Plan — 2026-06-10 to 2026-06-24

## Sprint Goal
A logged-in candidate can select a job from a seeded list, start a text-only interview, complete 3-5 AI-driven turns via WebSocket, and reach an "Interview Complete" screen showing a session ID and placeholder summary — proving WebSocket infrastructure, LangGraph state machine, Gemini integration, and sessions/turns persistence end-to-end.

---

## Capacity

| Who | Role | Availability | Notes |
|---|---|---|---|
| `backend-engineer` | interview_core WS + session/turn APIs; data_gateway job endpoints; DB migrations | 10 days | BOTTLENECK — capped at 12 agent-days across all stories |
| `ai-orchestrator` | LangGraph state machine; interviewer system prompt; turn-loop prompt; Gemini integration | 8 days | Owns S2-004 and S2-005 outright; pairs with backend-engineer on WS turn loop |
| `frontend-engineer` | Jobs list UI; interview chat UI; complete screen | 8 days | Can start S2-006 in parallel once S2-002 API contract is written (not yet implemented) |
| `security-auditor` | WebSocket JWT auth review | 1 day | Reviews S2-003 before any WS code merges |
| `code-reviewer` | All PRs | As needed | Same-day turnaround; no merge without approval |
| `devops-engineer` | Stand by | On-call | Unblock Docker/env issues only |
| Human engineer | Senior engineer | NOT YET ONBOARD | No capacity planned |
| Founder | Approval gate | ~1 hr/day | Final sign-off on sprint review 2026-06-24 |

**`backend-engineer` load accounting:**
S2-001 (M=2-3d) + S2-002 (S=1d) + S2-003 (M=2-3d) + S2-007 (S=1d) + S2-008 (S=1d) = 7-9 agent-days. Within cap.

---

## Committed Stories

| ID | Story | Assignee | Estimate | Acceptance Criteria |
|---|---|---|---|---|
| S2-001 | Full DB DDL via Alembic: `sessions`, `turns`, `jobs`, `nos_competencies` tables | `backend-engineer` | M | `alembic upgrade head` applies all tables from LLD §4 to local Postgres (5433). `sessions`, `turns`, `jobs`, `nos_competencies` exist with correct columns, FKs, indexes. Rollback (`downgrade -1`) tested and clean. Seed script inserts >= 3 jobs (e.g., "Junior Java Developer", "Sales Associate", "Data Entry Operator"). |
| S2-002 | `data_gateway` job endpoints: `GET /jobs`, `GET /jobs/{id}` | `backend-engineer` | S | `GET /jobs` returns paginated list of active jobs (`is_active=true`) with `id`, `title`, `description`, `level`, `language`. `GET /jobs/{id}` returns single job or 404. JWT-protected (requires valid access token). Integration tests pass. Response shape documented (used as API contract for frontend). |
| S2-003 | `interview_core` WebSocket endpoint with JWT auth guard | `backend-engineer` | M | `ws://localhost:8001/ws/interview/{session_id}` accepts connections. Auth guard reads `Authorization: Bearer <token>` from WS handshake headers (or `?token=` query param as fallback). Invalid/missing JWT closes connection with code 4001. Valid JWT: connection accepted, `{"type":"connected","session_id":"..."}` message sent. `security-auditor` signs off before merge. Integration test covering auth-accept and auth-reject paths. |
| S2-004 | LangGraph state machine: nodes, state schema, transitions, `build.py` | `ai-orchestrator` | L | LangGraph graph compiled without error. Nodes: `greeting`, `ask_question`, `await_answer`, `follow_up`, `closing`. State schema: `session_id`, `job_id`, `language`, `turn_count`, `turns` (list), `phase` (greeting/in_progress/closing/done). `build.py` exports `compile_graph()`. Unit tests cover: graph compiles, state transitions follow expected sequence, graph terminates after 5 turns. No LLM calls in unit tests (mock the Gemini node). |
| S2-005 | Gemini LLM adapter + interviewer prompt + turn-loop integration | `ai-orchestrator` | M | `LLMAdapter` class wraps `google-generativeai` SDK (model=`gemini-2.5-flash`, `MAX_TOKENS` from env). Interviewer system prompt: professional HR interviewer persona, job-title-aware, EN/HI/TE language support (language selected per session). Turn-loop prompt generates next question or follow-up from prior turns. Integrated into LangGraph `ask_question` and `follow_up` nodes. Integration test: 3-turn conversation against live Gemini API succeeds and returns valid text. `ai-orchestrator` pre-validates model compat before Day 1 (retro action item). |
| S2-006 | Frontend: jobs list page + interview chat UI + complete screen | `frontend-engineer` | M | `/jobs` route: fetches `GET /jobs`, renders job cards with title, level, "Start Interview" button. `/interview/:sessionId` route: WebSocket connection to `interview_core`; renders chat transcript (interviewer bubble left, candidate bubble right); candidate types in text input and presses Send; AI response appears within 5 seconds; "Interview Complete" screen shows after final turn with session ID + placeholder "Thank you, your responses have been recorded." text. `/jobs` and `/interview` are JWT-protected routes (redirect to `/login` if unauthenticated). Vitest tests for chat state management. |
| S2-007 | Session + turn persistence: `POST /sessions`, `POST /sessions/{id}/turns` in `interview_core` | `backend-engineer` | S | `POST /sessions` (JWT-protected): creates session row (`user_id`, `job_id`, `language`, `status=in_progress`), returns `session_id`. `POST /sessions/{id}/turns` (internal — called by LangGraph node, not directly by frontend): writes `turn_number`, `speaker` (interviewer/candidate), `text_content`, `timestamp`. At end of session, `PATCH /sessions/{id}` sets `status=completed`. Integration tests confirm rows written correctly after a simulated 3-turn exchange. |
| S2-008 | Playwright E2E smoke test: register → login → job list → start interview → 3 turns → complete | `frontend-engineer` | S | `npx playwright test` (headless) runs against local stack. Test: register new user → login → lands on dashboard → navigate to /jobs → click "Start Interview" on first job → send 3 text messages → see "Interview Complete" screen. Test passes in under 60 seconds. Added to CI check (fails PR if broken). This is the retro action item B-028 from Sprint 1. |

**Total committed: 8 stories | L: 1, M: 4, S: 3**
**Estimate: 1L (4-5d) + 4M (2-3d each, avg 2.5) + 3S (1d each) = ~17 agent-days total across backend-engineer + ai-orchestrator + frontend-engineer**

---

## Story Sequencing (Dependency Order)

```
Day 1-2:   S2-001 (DB DDL + seed)              [backend-engineer]
           S2-004 starts (LangGraph scaffold)   [ai-orchestrator] — no backend dependency
           S2-005 model compat validation       [ai-orchestrator] — retro action, Day 1

Day 2-3:   S2-002 (job endpoints)              [backend-engineer] — needs S2-001
           S2-006 starts with mock /jobs API    [frontend-engineer] — can start Day 1 from API contract doc

Day 3-5:   S2-003 (WebSocket + JWT guard)      [backend-engineer] — security-auditor review gates merge
           S2-004 completes                     [ai-orchestrator]

Day 4-7:   S2-005 (Gemini adapter + prompts)   [ai-orchestrator] — integrates into S2-004 graph
           S2-007 (session/turn persistence)    [backend-engineer] — needs S2-003 + S2-001

Day 6-8:   S2-006 continues, integrates real   [frontend-engineer] — needs S2-002 + S2-003 live
           WS + job API

Day 8-10:  S2-008 (Playwright E2E)             [frontend-engineer] — needs full stack wired
           code-reviewer final PR reviews + merge gate
           Founder demo: job list → interview → complete screen
```

---

## Definition of Done

All Sprint 2 stories must satisfy:
1. Code merged to `main` on a `feat/<name>` branch via PR
2. All tests passing (pytest backend, Vitest frontend, Playwright E2E)
3. `code-reviewer` approved in PR
4. `security-auditor` approved for S2-003 (WebSocket auth)
5. No secrets hardcoded — all config via `BaseSettings` / env vars
6. No PII logged (structlog redaction processor active — inherited from Sprint 1)
7. Scripts that invoke Poetry venv use `poetry env info --path` (retro action item)
8. Demonstrated working: job list → start interview → 3 text turns → complete screen
9. Listed in sprint review (2026-06-24)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Gemini multi-turn conversational prompts hit quota or model quirks (same failure mode as Sprint 1) | Medium | High | `ai-orchestrator` validates on Day 1 (retro action). If blocked: fallback to a hardcoded question sequence for the demo (removes Gemini dependency from critical path). |
| LangGraph graph complexity slips S2-004 from L to XL | Medium | Medium | S2-004 scope is scaffold only — no scoring, no STT, no audio. If graph takes >5 days, cut `follow_up` node and ship linear 5-question sequence instead; restore in Sprint 3. |
| WebSocket JWT auth has edge cases across browsers (token expiry mid-session) | Low | Medium | Sprint 2 scope: only validate JWT at connection time. Mid-session token refresh deferred to Sprint 3. Document as known limitation. |
| `backend-engineer` is bottleneck across S2-001, S2-002, S2-003, S2-007 sequentially | High | Medium | S2-001 and S2-004 run in parallel Day 1. Frontend starts from API contract doc before backend implements. If S2-003 slips past Day 6, `ai-orchestrator` can stub WS layer for LangGraph integration tests. |
| `frontend-engineer` blocked waiting on real backend APIs | Medium | Low | Mitigated by mock-first approach (same as Sprint 1). API contract for `/jobs` and WS message shape documented in `plan.md` Day 1 so frontend can build against it immediately. |

---

## API Contracts (written Day 1 — frontend builds against these)

**`GET /jobs` response shape:**
```json
{
  "items": [
    {
      "id": "uuid",
      "title": "Junior Java Developer",
      "description": "...",
      "level": "entry",
      "language": "en",
      "is_active": true
    }
  ],
  "total": 3,
  "page": 1,
  "per_page": 20
}
```

**WebSocket message shapes:**
```json
// Server → Client: connection confirmed
{"type": "connected", "session_id": "uuid"}

// Server → Client: interviewer turn
{"type": "turn", "speaker": "interviewer", "text": "Tell me about yourself.", "turn_number": 1}

// Client → Server: candidate turn
{"type": "turn", "speaker": "candidate", "text": "I am a..."}

// Server → Client: session ended
{"type": "complete", "session_id": "uuid", "message": "Thank you, your responses have been recorded."}

// Server → Client: error
{"type": "error", "code": "SESSION_NOT_FOUND", "message": "..."}
```

---

## Dependencies

| Dependency | Owner | Status | Needed by |
|---|---|---|---|
| Local Postgres (5433) + Redis (6379) running | `devops-engineer` | GREEN — confirmed Sprint 1 | S2-001 (Day 1) |
| Gemini API key active + `gemini-2.5-flash` quota available | `ai-orchestrator` | Verify Day 1 | S2-005 (Day 3+) |
| LLD §4 DDL for sessions/turns/jobs/nos_competencies | Available in `LLD.md` | GREEN | S2-001 (Day 1) |
| `security-auditor` review of WS auth (S2-003) | `security-auditor` | Schedule Day 4-5 | S2-003 merge gate |
| Founder review for sprint review demo | Founder | ~1 hr on 2026-06-24 afternoon | Sprint review |

---

## Out of Scope (Explicitly Deferred)

- Voice STT (Sarvam) — Sprint 3
- Voice TTS (Sarvam) — Sprint 3
- Avatar rendering (Simli) — Sprint 3
- End-of-session scoring with rubric — Sprint 4 (placeholder summary only in Sprint 2)
- PDF scorecards (WeasyPrint) — Sprint 4
- `feedback_billing` service bootstrap — Sprint 4
- `admin_ops` service bootstrap — Sprint 4
- Naipunyam SSO — Sprint 5
- DPDP consent flow beyond table (table exists from Sprint 1) — Sprint 3
- Rate limiting middleware — Sprint 3
- p95 latency measurement — Sprint 3 (after voice pipeline exists)
