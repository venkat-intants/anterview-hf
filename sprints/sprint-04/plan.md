# Sprint 4 Plan — 2026-05-28 to 2026-06-10

## Sprint Goal
A logged-in candidate can choose their interview language, see a lifelike 3D avatar speaking the questions, and experience varied interviewer personas across sessions — all within the p95 < 2 s NFR.

---

## Capacity

| Who | Role | Availability | Notes |
|---|---|---|---|
| `backend-engineer` | interview_core + data_gateway | 10 days | Carries S4-004, S4-005, S4-007 through S4-013 |
| `frontend-engineer` | React app (web/) | 10 days | Carries S4-001 FE component, S4-002, S4-003 FE side |
| `ai-orchestrator` | LangGraph + persona wiring | 3-4 days | S4-003 persona logic; S4-004/S4-005 pipeline changes |
| `devops-engineer` | CI + infra | 2-3 days | S4-006 CI workflow; ingress throttle follow-up |
| `security-auditor` | Review security follow-ups | 1-2 days (mid + end sprint) | Must sign off S4-007, S4-008, S4-009, S4-010, S4-011, S4-012 |
| `code-reviewer` | All PRs | As needed (same-day) | No PR merges without approval; include active .env context in prompt |
| Human engineer | Senior engineer | NOT YET ONBOARD | Hiring in progress — no capacity planned; risk escalates this sprint |
| Founder | Approval gate + HeyGen spike confirm | ~1 hr/day | Must confirm S4-001 HeyGen spike result by end of Day 1 |

AI agents work every day. No public holidays in this window.

---

## Committed Stories

| ID | Story | Assignee | Estimate | Acceptance Criteria |
|---|---|---|---|---|
| S4-001 | **[Updated 2026-05-28: avatar pivoted from Simli to D-ID; Simli code removed — see [[project_avatar_vendor_decision]].]** Originally: Simli Interactive Avatar integration — backend adapter at `services/interview_core/app/avatar/simli.py` issues Simli session token from `SIMLI_API_KEY` + `SIMLI_FACE_ID`; frontend React component consumes Simli's WebRTC stream and renders the lip-synced avatar driven by the existing TTS audio path. Config scaffolding (`avatar_provider`, `simli_*` settings, `/health` Simli check) already present from Sprint 1. | `frontend-engineer` + `backend-engineer` | M | Avatar visible on `/interview` page. Lip-syncs with the TTS audio currently flowing through `audio_response`. Falls back to audio-only if Simli unreachable (health check + circuit breaker). `AVATAR_PROVIDER=simli` (already the default). `SIMLI_API_KEY` from `.env`, never hardcoded. Remove leftover HeyGen placeholder env vars (`HEYGEN_API_KEY`, `VITE_HEYGEN_AVATAR_ID`) — see [[project-simli-avatar]] memory. |
| S4-002 | Language picker on session-creation page — candidate selects EN / HI / TE before clicking Start Interview | `frontend-engineer` | S | Dropdown visible on session-creation page. Selection stored and passed as `language` param on WS connect. Defaults to EN if unset. Vitest test covers state + param propagation. |
| S4-003 | 4-persona system — Warm Ramp / Direct Dive / Strategic Probe / Conversational Explorer — persona assigned per session, shapes question cadence and tone | `ai-orchestrator` + `frontend-engineer` | M | Persona selected randomly at session start (or by URL param for testing). Interviewer prompts reflect each persona's style per `docs/interview-persona-design-ai.md` and `docs/interview-style-variety-pm.md`. EN/HI/TE prompt variants all updated. Founder demo confirms each persona feels distinct. |
| S4-004 | Streaming STT pipeline — replace one-shot Sarvam HTTP upload with Sarvam saaras streaming WebSocket | `backend-engineer` + `ai-orchestrator` | M | STT streams partial transcripts to LangGraph as chunks arrive. End-to-end p95 turn latency measured; must not regress from Sprint 3 baseline. Mic capture loop updated if chunk-cadence requirements differ from one-shot path. Unit + integration tests pass. |
| S4-005 | Stream LLM tokens directly into Sarvam TTS sentence-by-sentence — no full-reply wait | `backend-engineer` + `ai-orchestrator` | M | First TTS audio chunk starts playing before LLM response is complete. Sentence boundary detection splits token stream correctly for EN, HI, TE. p95 turn latency target met. Deferred to later days if S4-004 slips. |
| S4-006 | CI workflow (.github/workflows) — pytest + vitest + Playwright run as PR gate | `devops-engineer` | M | `.github/workflows/ci.yml` triggers on PR to `main`. pytest (interview_core + data_gateway), vitest (web/), Playwright E2E all run. Failing tests block merge. Green badge visible on repo. Retro A1. |
| S4-007 | Gate `?token=` query-string auth path behind `app_env != "production"` | `backend-engineer` | S | `?token=` path returns 404 when `APP_ENV=production`. Allowed in `local` and `staging` only. `security-auditor` sign-off required. Retro A4 / task #38. |
| S4-008 | Rate-limit tuning for voice path — raise budget or exclude `audio_chunk` message type from per-message counter | `backend-engineer` | S | Voice sessions not rate-limited into failure under normal 10-min interview cadence. Threshold documented in config. `security-auditor` confirms attack surface not widened. Retro A4 / task #39. |
| S4-009 | Partial unique index on `dpdp_consent_ledger` (`user_id`, `purpose`) | `backend-engineer` | S | Alembic migration adds `CREATE UNIQUE INDEX ... WHERE revoked_at IS NULL`. Duplicate consent insert returns 409 not 500. Retro A3 / task #31. `security-auditor` sign-off. |
| S4-010 | `DELETE /consent` revocation endpoint — DPDP §11 right to withdraw | `backend-engineer` | S | `DELETE /consent/{purpose}` sets `revoked_at` timestamp. Subsequent session-start with revoked purpose returns 403. Unit + integration tests. `security-auditor` sign-off. Retro A3 / task #33. |
| S4-011 | 90-day retention policy — implement cron soft-delete OR update ConsentModal copy to remove the promise | `backend-engineer` / `product-manager` | S | Decision made by Day 3: if cron, Postgres cron job or APScheduler task deletes sessions older than 90 days; if copy-only, modal text updated and legal team notified. Either path closes task #32. Retro A3. |
| S4-012 | Trusted-proxy-count config + safe X-Forwarded-For extraction | `backend-engineer` | S | `TRUSTED_PROXY_COUNT` env var set; `request.client.host` replaced with correct forwarded-IP logic. Retro A3 / task #30. `security-auditor` sign-off. |
| S4-013 | Replace module-global LLM adapter slot with proper dependency injection | `backend-engineer` | S | No module-level `llm_adapter = ...` singletons. FastAPI `Depends()` or constructor injection used instead. Existing tests still pass. Retro A2 / task #42. |
| S4-014 | Reconcile "Google Gemini vs Anthropic Claude" wording in ConsentModal + CLAUDE.md | `backend-engineer` / `product-manager` | XS | Modal text matches the live LLM provider from `.env`. CLAUDE.md updated if needed. Retro A6. |

**Total committed: 14 stories | XS: 1, S: 7, M: 5, L: 0**

> Trim line: if agent-day capacity runs tight (13-day cap), defer S4-005 (streaming TTS is the most complex M) to Sprint 5 — Sarvam streaming STT alone (S4-004) gets p95 to ~p75 target. S4-011 can also shrink to XS if the decision is "soften copy only."

---

## Story Sequencing (Dependency Order)

```
Day 1:     S4-001 spike (HeyGen pricing + latency confirmed)    [frontend-engineer]
           S4-007, S4-012 (small security fixes, no deps)       [backend-engineer, parallel]
           S4-014 (XS wording fix)                             [backend-engineer, parallel]

Day 2:     S4-001 full implementation (BE adapter + FE component)  [frontend-engineer + backend-engineer]
           S4-002 language picker                                   [frontend-engineer, parallel]

Day 3-4:   S4-003 persona system                               [ai-orchestrator + frontend-engineer]
           S4-006 CI workflow                                   [devops-engineer, parallel]
           S4-011 retention decision + implementation           [backend-engineer]

Day 5:     S4-004 streaming STT                                [backend-engineer + ai-orchestrator]
           S4-008, S4-009, S4-013 (independent S stories)      [backend-engineer, parallel]

Day 6-8:   S4-005 streaming TTS (if in scope)                  [backend-engineer + ai-orchestrator]
           S4-010 DELETE /consent endpoint                     [backend-engineer]
           security-auditor pass: S4-007/S4-008/S4-009/S4-010/S4-011/S4-012

Day 9-10:  Integration + full regression (201 tests baseline)
           code-reviewer final pass — all open PRs
           Founder avatar demo: pick language → avatar speaks → persona visible
           Sprint review 2026-06-10
```

---

## Definition of Done (Sprint 4 specific)

All stories must satisfy the project-wide DoD plus the following Sprint 4 additions:

1. Code merged to `main` on a `feat/<name>` branch via PR
2. All tests passing: pytest (interview_core + data_gateway), Vitest (web/), Playwright E2E
3. **CI green on PR** — new requirement; S4-006 must land by Day 4 so all subsequent PRs use it
4. `code-reviewer` approved (include active `.env` context in prompt per retro A6)
5. `security-auditor` approved for S4-007, S4-008, S4-009, S4-010, S4-011, S4-012
6. Avatar visible and lip-synced on the `/interview` page (acceptance demo to founder)
7. All 3 languages (EN / HI / TE) selectable from the session-creation UI
8. p95 turn latency not regressed from Sprint 3 baseline (instrumentation from S3-008 is the measure)
9. Listed in sprint review (2026-06-10)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| D-ID API latency or pricing incompatible with ₹12/session cost cap | Low | High | D-ID is the configured demo provider (`AVATAR_PROVIDER=did`), conditionally CFO-approved demo-only (sunset 2026-11-28). Day 1 spike measures per-session cost + first-frame latency. If cost breaches the cap escalate to `cfo-cost-watcher`. Custom Three.js avatar (Tier-2) remains the fallback + production path. (Simli removed 2026-05-28.) |
| Sarvam saaras streaming WS has stricter chunk-cadence requirements than one-shot HTTP | Medium | Medium | Frontend mic-capture loop may need a rewrite. `backend-engineer` + `frontend-engineer` spike together on Day 5. If rewrite scope > 1 day, defer S4-005 and protect S4-001/S4-002/S4-003 delivery. |
| Human engineer not yet onboard — agent-only sprint has higher variance | Certain | Medium | Sprint 3 needed two emergency-fix rounds that required human-style judgement. Founder to provide >1 hr/day review availability as a compensating control. Flag immediately if any architectural decision needs human sign-off. |
| CI setup (S4-006) could block all PRs if misconfigured | Low | High | `devops-engineer` targets Day 3-4 for CI. PRs opened before CI is wired use the Sprint 3 manual review process. Once CI is green, it is required. Do not merge CI workflow until it passes on a test branch. |

---

## Dependencies

| Dependency | Owner | Status | Needed by |
|---|---|---|---|
| D-ID API credentials in `services/interview_core/.env` (`AVATAR_PROVIDER=did`) | Founder | D-ID demo-only, CFO-approved 2026-05-28; Simli removed 2026-05-28. | S4-001 (Day 1) |
| Sarvam saaras streaming WS endpoint + credentials | `backend-engineer` | Confirm against existing Sarvam account before Day 5 | S4-004 (Day 5) |
| `docs/interview-persona-design-ai.md` + `docs/interview-style-variety-pm.md` | `product-manager` | GREEN — both docs exist | S4-003 (Day 3) |
| GitHub Actions enabled on repo | `devops-engineer` | Confirm repo has Actions enabled before Day 3 | S4-006 (Day 3-4) |
| `security-auditor` mid-sprint pass (Day 6-8) | `security-auditor` | Schedule by Day 3 | S4-007 through S4-012 |
| Founder avatar demo slot | Founder | ~30 min on 2026-06-10 afternoon | Sprint review |

---

## Out of Scope (Explicitly Not in Sprint 4)

- Custom Three.js + Ready Player Me avatar (Tier-2) — deferred until post-revenue or pre-govt-bid
- AWS Bedrock LLM switch (`LLM_PROVIDER=bedrock`) — Bedrock approval pending; no action this sprint
- Ingress per-IP throttle (task #40) — requires production infra not yet deployed; track in Sprint 5
- Google OAuth / Naipunyam SSO adapter — Sprint 5+
- `feedback_billing` scoring + PDF scorecard — Sprint 5+
- `admin_ops` service bootstrap — Sprint 5+
- Any additional Indian language beyond EN / HI / TE — post-Phase 1
