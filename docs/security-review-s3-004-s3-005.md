# Security Review — S3-004 (WS auth hardening) + S3-005 (JWT replay / re-validation)

**Reviewer:** security-auditor
**Date:** 2026-05-27
**Scope:**
- `services/interview_core/app/routers/ws.py`
- `shared/auth/jwt.py`
- `services/interview_core/app/redis_client.py`
- `services/{interview_core,data_gateway}/app/{config,dependencies}.py`
- `shared/auth/local.py`
- `web/src/api/interview-ws.ts`
- `tests/integration/test_ws_auth.py`, `test_ws_protocol_v2.py`, `test_ws_jwt_revalidation.py`

Severity counts: CRITICAL=0  HIGH=0  MEDIUM=2  LOW=4

---

## 1. Original MEDIUM findings — verification

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 1 | JWT in `?token=` query string leaks to access logs | **PARTIALLY FIXED** | Browser path now uses `Sec-WebSocket-Protocol` (`web/src/api/interview-ws.ts:144`). Server still accepts `?token=` and logs a deprecation warning (`ws.py:294`). See MEDIUM-1 below. |
| 2 | No rate limit on WS messages | **FIXED** | `ws.py:608-632, 1146-1182` — per-connection deque + 3-strike close 1008. Tests at `test_ws_protocol_v2.py:964, 1007`. |
| 3 | Token expiry only at handshake | **FIXED** | `_revalidate_token_loop` at `ws.py:635-705`, every 300s, calls `verify_access_token` + jti recheck. Test `test_ws_jwt_revalidation.py:111`. |
| 4 | JWTs missing iss/aud/jti claims | **FIXED** | `shared/auth/jwt.py:35-107` — `require_iss`/`require_aud`/`require_jti` enforced via jose options, plus explicit empty-jti guard. Test `test_ws_auth.py:516` (missing iss → 4001). |

---

## 2. Decisions you flagged — verdicts

| # | Decision | Verdict | Reason |
|---|----------|---------|--------|
| 1 | Fail-open on Redis for jti blocklist | **YES (sprint-3 only)** | Acceptable for demo tier. Track as Sprint-4 HIGH item: in prod a Redis outage opens a replay window. Add circuit breaker + `app_env=="production"` guard to flip to fail-closed. |
| 2 | Hardcoded iss/aud defaults in `shared/auth/jwt.py` | **YES** | Defaults match real settings, so call-site compat is preserved. Acceptable. Add a unit test asserting `_DEFAULT_ISSUER == settings.jwt_issuer` so a future rename breaks loudly. |
| 3 | `?token=` still accepted as fallback | **YES (with caveat)** | Needed for websocat / TestClient. Production path no longer uses it. See MEDIUM-1 — must be off in prod. |
| 4 | Rate limit counts ALL frames (incl. `audio_chunk`) | **YES** | Correct. Anything else is bypassable via audio spam. Note: 60 msg/min is tight for real voice flows; see LOW-1. |
| 5 | Rate-limit state in-process (per-conn deque) | **YES (sprint-3 only)** | Documented in code. Sprint-4 must add Redis-backed per-user / per-IP counter — current scheme is trivially bypassed via reconnect. |

---

## 3. Findings

### [MEDIUM-1] `?token=` query-param fallback must be disabled in production
- **Location:** `services/interview_core/app/routers/ws.py:292-298`
- **Description:** The server still accepts JWT via query string. Even with a deprecation log line, ANY reverse proxy / WAF / CDN / nginx access log between the candidate browser and the service will capture the full URL — including a live 15-minute JWT — at INFO level.
- **Impact:** Anyone with read access to load-balancer / proxy logs (ops staff, log aggregator, compromised log shipper, future third-party log SaaS) can replay tokens for up to 15 minutes. Mitigated by the jti blocklist (replay is one-shot per session), but token can still be used to open a *new* session if attacker wins the race.
- **Remediation:** Gate the query-param path behind `settings.app_env != "production"`. In production, return `_WS_CLOSE_UNAUTHORIZED` if the only transport offered is `?token=`. Keep the test-client path conditional on env.
- **Reference:** OWASP A09 — Security Logging & Monitoring Failures (sensitive data in logs); CWE-598.

### [MEDIUM-2] Pre-auth deque / state allocation — minor DoS surface
- **Location:** `ws.py:610` (deque allocated after auth ✓), but `ws.py:524-538` runs DB lookups + consent check BEFORE the rate limiter is armed.
- **Description:** Each unauthenticated handshake costs: 1 jose decode + 1 Redis `SET NX` + 1 DB JOIN query + 1 consent query. A flood of valid-shaped JWTs with random `sub` (no auth required to mint — attacker need only get one valid token once) can pin the DB pool.
- **Impact:** Unauthenticated CSWSH-allowed origins or a stolen-then-replayed token wave could exhaust the DB pool. Not a P0 because origin allowlist + jti replay protection drop most of the traffic before DB.
- **Remediation:** Sprint-4 — add per-IP connection-rate limit at the ingress (nginx `limit_conn_zone` / Kong rate-limit plugin) BEFORE the FastAPI handler. Document this as an infra-layer control, not a code one.
- **Reference:** OWASP A04 — Insecure Design.

### [LOW-1] Rate-limit budget may be too tight for real audio streams
- **Location:** `ws.py:187` — `_RATE_LIMIT_MESSAGES_PER_WINDOW = 60`.
- **Description:** Each candidate "turn" in v2 voice mode = N `audio_chunk` + 1 `turn_end`. With ~20 ms PCM frames a candidate speaking for 30 s sends ~1500 chunks — far above 60/min. Current design budget seems tuned for v1 text only.
- **Impact:** First real voice candidate trips RATE_LIMITED and gets closed on violation 3.
- **Remediation:** Either (a) raise the budget to 200/min with the same 3-strike rule, or (b) exclude `audio_chunk` from the counter and rely on per-message size cap + buffer cap (Sprint-4 audio buffer ceiling). Verify against Sarvam chunk-size expectations before shipping S3-006 audio path to candidates.

### [LOW-2] Revalidation task's `except Exception` swallows JWTClaimsError on iss/aud mutation
- **Location:** `ws.py:690-697`.
- **Description:** `JWTClaimsError` IS a `JWTError` subclass (verified in `jose/exceptions.py:21`), so the `except JWTError` correctly fires. The broader `except Exception` only catches Redis / network errors. Verdict: implementation is correct. **No action needed**, calling out because the structure looked suspicious initially.

### [LOW-3] Revalidation task may run a final tick AFTER normal session close, before cancel fires
- **Location:** `ws.py:644-705` (loop) and `ws.py:1471-1478` (cancel in finally).
- **Description:** Race window: if `_advance_after_candidate` returns True at the exact moment the task wakes from `asyncio.sleep`, the task can run one more `verify_access_token` + Redis `GET` before the cancel propagates. Token is still valid → no message sent → no harm. Worst case: one wasted Redis op per session close.
- **Impact:** Negligible. Document the no-op race.
- **Remediation:** None required. If desired, set `_session_closing = True` flag checked at top of loop body.

### [LOW-4] Subprotocol echo is the **client-supplied** JWT — verify Starlette accepts as-is
- **Location:** `ws.py:587` — `websocket.accept(subprotocol=subprotocol_echo)`.
- **Description:** A JWT character set is `[A-Za-z0-9_\-.=]` — RFC 7515 §3 is base64url + `.`. No CRLF, no whitespace. Starlette passes the value directly into the ASGI `websocket.accept` message without sanitisation (`starlette/websockets.py:109`). Header injection via subprotocol is therefore not possible with a well-formed JWT.
- **Impact:** None for valid JWTs. If `verify_access_token` ever moves AFTER `accept`, an attacker could supply a malformed subprotocol containing CR/LF before validation runs.
- **Remediation:** Add a defensive check in `_extract_token`: reject if the candidate token contains any char not in `[A-Za-z0-9_\-.=]` before returning. One-liner; future-proof.

---

## 4. JTI replay window — is it acceptable?

**Question:** A stolen JWT could only be replayed once per session-token-lifetime if jti is recorded at handshake. Acceptable?

**Answer:** **YES, with one note.** The current design correctly prevents the most likely attack (token captured from proxy log, replayed). The remaining window:
- An attacker who captures the token can race the legitimate user to be the FIRST to open the WS connection — whoever wins gets the session, the loser is rejected as replay.
- The session-ownership check (`ws.py:559`) means even a stolen token only grants access to sessions owned by that user — attacker cannot pivot to other users' interviews.
- **Note:** The jti is recorded ONLY when WS connect succeeds. Stolen token used for HTTP `/api/*` calls (data_gateway) is NOT blocklisted — those endpoints are protected by token expiry alone (15-min TTL). If the same JWT is later used to open a WS, replay protection kicks in. Document this asymmetry.

---

## 5. New issues introduced

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| a | Subprotocol echo path safe? | safe | JWT charset can't inject CRLF; Starlette passes through. See LOW-4 for hardening. |
| b | Arbitrary subprotocol echoed? | yes — by design | Browser sends JWT as subprotocol, server echoes. JWT is verified before echo. Worst case: attacker supplies their OWN valid JWT and sees it echoed — they already have it. |
| c | Periodic revalidation race-free? | mostly | See LOW-3. |
| d | Pre-auth deque DoS? | no | Deque is allocated AFTER auth (`ws.py:610`). Pre-auth allocations are bounded (1 jose decode + 1 DB query + 1 Redis op per handshake). See MEDIUM-2 for infra-layer mitigation. |

---

## 6. DPDP / Residency

- **Consent ledger:** PASS — `has_active_consent(db, user_id)` checked server-side at `ws.py:538`, server-side gate (not just React modal).
- **Erasure endpoint:** N/A for this story — not modified.
- **Data residency:** PASS — Redis (jti store) is the same instance used elsewhere; deployment guarantees Mumbai region.
- **PII in logs:** PASS — raw token never logged. `user_id` and `jti` only. PII redaction processor in `main.py:25-34` strips email/phone/full_name.

---

## 7. Tests — do they prove the contract?

| Test | Contract | Verdict |
|------|----------|---------|
| `test_ws_subprotocol_token_accepted` | Sec-WebSocket-Protocol path works | **YES** — TestClient sends real subprotocol header. |
| `test_ws_query_param_still_accepted` | Deprecated fallback still works | **YES**. |
| `test_ws_missing_iss_rejected` | iss enforcement | **YES** — crafts token with no iss, expects 4001. |
| `test_ws_jti_replay_rejected` | jti blocklist | **PARTIAL** — mocks `redis.set` to return None. Does NOT prove the real Redis SET NX semantics. Acceptable for unit; spot-test against real Redis recommended. |
| `test_expired_mid_session_sends_token_expired` | Mid-session expiry triggers TOKEN_EXPIRED | **YES** — patches `verify_access_token` side_effect to fail on 2nd call. |
| `test_revalidation_cancelled_on_normal_close` | Task cancel doesn't leak | **WEAK** — only asserts `final_msg["type"]`. Doesn't actually assert no `PendingTask` warning. Add `pytest.warns(None)` check or capture asyncio warnings. |
| `test_rate_limit_61st_message_triggers_rate_limited` | 61st msg → RATE_LIMITED | **YES** — widens window to 1000s to avoid eviction. |
| `test_rate_limit_3rd_violation_closes_1008` | 3rd violation → close 1008 | **YES** — comment in test correctly documents the deque non-eviction in test conditions. |

---

## Verdict for production deploy

**APPROVED-WITH-FOLLOWUPS**

Sign-off contingent on:
1. **MEDIUM-1** (gate `?token=` query path behind `app_env != "production"`) — must land before any deploy that terminates TLS at a proxy that logs URLs. Track as Sprint-4 P0.
2. **LOW-1** (rate-limit budget) — verify against real Sarvam chunk cadence before exposing S3-006 voice to candidates. Track as Sprint-4 blocker before voice-path launch.
3. **MEDIUM-2** (ingress-layer per-IP rate limit) — add to DevOps Sprint-4 backlog as Helm/Kong config, not code.

The four S2-003 MEDIUM findings are genuinely closed for the demo tier. Code is well-structured, PII-clean, fail-modes are documented. Good work.
