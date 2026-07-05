# Sprint 1 Plan — 2026-05-27 to 2026-06-10

## Sprint Goal
Stand up end-to-end auth — candidate can register, log in, see a dashboard, and log out via `data_gateway` (port 8002), with the JWT validated by `interview_core` (port 8001), using a pluggable `AuthProvider` interface that lets `naipunyam`/`google`/`keycloak` drop in without changing call sites.

---

## Capacity

| Who | Role | Availability | Notes |
|---|---|---|---|
| `backend-engineer` | data_gateway + interview_core backend | 10 days | Primary implementer for S1-001 through S1-006 |
| `frontend-engineer` | React app (web/) | 10 days | Picks up S1-005 once S1-004 API contract is stable |
| `security-auditor` | Auth + JWT review | 1-2 days (end of sprint) | Reviews S1-003, S1-004, S1-007 before merge gate |
| `code-reviewer` | All PRs | As needed (same-day turnaround expected) | No PR merges without approval |
| `devops-engineer` | Stand by | On-call | Unblock Docker/env issues only; no new infra this sprint |
| Human engineer | Senior engineer | NOT YET ONBOARD | Hiring in progress — no capacity planned |
| Founder | Approval gate | ~1 hr/day review | Final sign-off on sprint review Friday 2026-06-10 |

AI agents work every day. No public holidays in this window.

---

## Committed Stories

| ID | Story | Assignee | Estimate | Acceptance Criteria |
|---|---|---|---|---|
| S1-001 | Bootstrap `data_gateway` as a runnable FastAPI app on port 8002 with `/health` endpoint | `backend-engineer` | S | `GET /health` returns `{"status":"ok"}`. App starts with `uvicorn`. Docker Compose entry added. `pyproject.toml` + `ruff` + `mypy` configured. |
| S1-002 | Apply DB schema for auth tables only (users, roles, user_roles, dpdp_consent_ledger) via Alembic | `backend-engineer` | S | `alembic upgrade head` runs clean against local Postgres (port 5433). Tables exist with correct columns and indexes per LLD §4. Rollback (`downgrade -1`) tested. |
| S1-003 | Implement `AuthProvider` abstract interface + `LocalAuthProvider` (email + bcrypt password) | `backend-engineer` | M | `AUTH_PROVIDER=local` env var selects `LocalAuthProvider`. Interface has `register()`, `authenticate()`, `refresh()`, `logout()` methods. Switching to a stub `MockAuthProvider` in tests requires zero call-site changes. Unit tests pass (pytest). No secrets hardcoded. |
| S1-004 | Auth REST endpoints: `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout` | `backend-engineer` | M | Endpoints match LLD §3.1 JWT shape: `access_token` (15-min), `refresh_token` (7-day), `user_id`. Passwords stored as bcrypt hash. Refresh tokens stored in Redis (key: `refresh:{token_hash}`, TTL 7d). Logout invalidates refresh token. 400/401/409 error codes correct. Integration tests pass against local Postgres + Redis. |
| S1-005 | React app bootstrap + auth UI: register page, login page, protected dashboard ("Welcome, {name}"), logout | `frontend-engineer` | M | Vite + React 18 + TypeScript strict. `npm run dev` serves at `localhost:5173`. Register form calls `POST /auth/register`. Login form calls `POST /auth/login`, stores `access_token` in memory (NOT localStorage). Dashboard shows `"Welcome, {full_name}"` — fetches `/auth/me`. Logout clears token and redirects to `/login`. Route guard: unauthenticated `/dashboard` redirects to `/login`. Vitest unit tests for auth state. |
| S1-006 | JWT validation middleware in `interview_core` — cross-service auth proof | `backend-engineer` | S | `interview_core` reads `JWT_SECRET` from env (shared with `data_gateway`). New protected route `GET /api/me` returns `{"user_id": "...", "roles": [...]}`. Calling with a JWT issued by `data_gateway` returns 200. Calling without token returns 401. Integration test proves the cross-service flow. |
| S1-007 | Security review: auth flow, JWT spec, bcrypt config, token storage | `security-auditor` | S | Review covers: JWT algorithm (must be HS256 or RS256 — document choice), secret length >= 32 bytes, bcrypt cost factor >= 12, refresh token rotation on use, no PII in JWT payload beyond `user_id` + `roles`, token not stored in localStorage. Reviewer signs off in PR comment. Any P0 findings block merge. |

**Total committed: 7 stories | S: 3, M: 3, S+M+S = 3S + 3M + 1S = 4S + 3M**
**Estimate summary: 4 x S (<=1 day each) + 3 x M (2-3 days each) = ~13 agent-days against 10 available (backend-engineer carries 6 stories, frontend-engineer carries 1)**

> Note: `backend-engineer` is the bottleneck. S1-001 and S1-002 must land by Day 2 (2026-05-28) to unblock S1-003 and S1-004. S1-005 can start in parallel from Day 1 with a mock API. S1-006 and S1-007 are Day 8-9.

---

## Story Sequencing (Dependency Order)

```
Day 1-2:   S1-001 (data_gateway bootstrap)   [backend-engineer]
           S1-002 (DB schema — auth tables)   [backend-engineer, parallel with S1-001]
           S1-005 starts with mock API        [frontend-engineer]

Day 3-5:   S1-003 (AuthProvider interface + LocalAuthProvider)   [backend-engineer]

Day 4-7:   S1-004 (Auth REST endpoints)       [backend-engineer] — needs S1-003 done
           S1-005 continues, integrates real API once S1-004 is up

Day 8-9:   S1-006 (cross-service JWT)         [backend-engineer]
           S1-007 (security review)           [security-auditor]

Day 9-10:  code-reviewer final PR review + merge gate
           Founder demo: register → login → dashboard → logout → cross-service call
```

---

## Definition of Done (Sprint 1 specific)

All 7 stories must satisfy the project-wide DoD:
1. Code merged to `main` on a `feat/<name>` branch via PR
2. All tests passing (pytest for backend, Vitest for frontend)
3. `code-reviewer` approved in PR
4. `security-auditor` approved for S1-003, S1-004, S1-006, S1-007
5. No secrets in code (checked by `security-auditor`)
6. Demonstrated working: register → login → dashboard → logout at `localhost:5173`
7. Cross-service JWT flow demonstrated: `data_gateway` JWT accepted by `interview_core`
8. Listed in sprint review (2026-06-10)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `backend-engineer` bottleneck — 6 of 7 stories assigned to one agent | High | High | Sequence strictly: S1-001 and S1-002 in parallel on Day 1. Frontend starts with mock. Cut S1-006 to Sprint 2 if S1-004 slips past Day 7. |
| Postgres DDL conflicts with LLD schema (LLD users table has `naipunyam_id NOT NULL UNIQUE` but local auth has no Naipunyam) | Medium | Medium | Sprint 1 DDL only applies users/roles/user_roles/dpdp_consent_ledger. Relax `naipunyam_id` to nullable for `local` provider. Document as intentional divergence in migration comment. |
| React `access_token` in-memory storage causes UX friction (lost on page refresh) | Low | Low | Acceptable for Sprint 1. Add silent refresh via `refresh_token` cookie in Sprint 2 or as a quick follow-up. |
| JWT secret rotation — `interview_core` and `data_gateway` must share secret | Medium | High | Both services read `JWT_SECRET` from same `.env`. `devops-engineer` to confirm both containers mount the same env file in Docker Compose before S1-006 begins. |
| Human engineer not yet onboard | Certain | Low (Sprint 1) | Sprint 1 is entirely AI-agent driven. Risk escalates in Sprint 2-3 when architecture decisions need human judgment. Founder to accelerate hiring. |

---

## Dependencies

| Dependency | Owner | Status | Needed by |
|---|---|---|---|
| Local Docker stack (Postgres:5433, Redis:6379) running | `devops-engineer` | GREEN — confirmed all-green in Phase 0 | S1-002 (Day 1) |
| Shared `JWT_SECRET` env var in both `interview_core` and `data_gateway` `.env` files | `devops-engineer` | Action needed — confirm before S1-006 | S1-006 (Day 8) |
| `web/` directory empty — no Vite scaffold yet | `frontend-engineer` | Action needed Day 1 | S1-005 (Day 1) |
| `code-reviewer` availability for PR reviews | `code-reviewer` | Available — same-day turnaround expected | All PRs |
| Founder review time for sprint review demo | Founder | ~1 hr on 2026-06-10 afternoon | Sprint review |

---

## Out of Scope (Explicitly Not in Sprint 1)

- Naipunyam SSO / SAML (AUTH_PROVIDER=naipunyam) — interface prepared, impl deferred to Sprint 5+
- Google OAuth adapter — deferred to Sprint 2/3 or later
- Full Postgres DDL (sessions, turns, scorecards, etc.) — Sprint 2
- DPDP consent capture flow beyond table creation — Sprint 2 (B-014)
- Any AI pipeline (STT, LLM, TTS) — Sprint 3-4
- Avatar — Sprint 5+
- `feedback_billing` and `admin_ops` service bootstrap — Sprint 2
