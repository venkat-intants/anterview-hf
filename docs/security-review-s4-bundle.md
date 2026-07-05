# Security Review — S4-007 + S4-008 + S4-009 + S4-010 + S4-012 (Sprint 4 bundle)

**Reviewer:** security-auditor
**Date:** 2026-05-28
**Severity counts:** CRITICAL=0  HIGH=0  MEDIUM=2  LOW=3
**Previous reports:** `docs/security-review-s3-011.md`, `docs/security-review-s3-004-s3-005.md`

Re-verifies the five Sprint 4 follow-up stories closing prior findings. Does
not re-prove the underlying vulnerabilities — only checks that the fix is
genuine and that no new issues were introduced.

## 1. Original findings — verification

| Finding | Story | Fixed? | Evidence |
|---|---|---|---|
| S3-004 MEDIUM-1 — JWT in `?token=` URL → proxy access logs (15-min replay window) | S4-007 | YES | `services/interview_core/app/routers/ws.py:315-339` — `?token=` returns `(None, None)` when `settings.app_env == "production"`. `services/interview_core/app/config.py:14-23` — `_normalise_app_env` lowercases+strips so `APP_ENV=Production`/`PROD` cannot bypass. Test: `test_ws_query_token_rejected_in_production`. |
| S3-004 LOW-1 — 60 msg/min rate limit kills a real voice candidate after ~5 s of audio_chunk frames | S4-008 | YES | `ws.py:1218` skips counter when `msg_type == "audio_chunk"`; carve-out documented at `ws.py:48-56` + `ws.py:1211-1217`. Test `test_rate_limit_skips_audio_chunks` (`test_ws_protocol_v2.py:995`) sends 200 chunks and asserts no error. `test_rate_limit_61st_message_triggers_rate_limited` proves control-plane is still counted. |
| S3-011 HIGH-1 — TOCTOU race in POST /consent idempotency check (two active rows) | S4-009 | YES | Partial unique index `ix_dpdp_consent_active_unique` in `alembic/versions/20260528_0001_..._dpdp_consent_partial_unique_index.py`. Handler catches `IntegrityError` at `consent.py:252-281`, rolls back, re-queries, returns 200 with the winning row. Test `test_unique_index_present` proves migration applied. |
| S3-011 follow-up — DPDP §11 right-to-withdraw endpoint absent | S4-010 | YES | `DELETE /consent` at `consent.py:331-388`; JWT-scoped to caller (`get_current_user` → `current_user.user_id`). `consent_guard.has_active_consent` already filters `revoked_at IS NULL` (`consent_guard.py:78`). Tests cover 200, 404 (none), 404 (already revoked), and cross-service propagation via `/consent/status`. |
| S3-011 MEDIUM-1 — XFF leftmost-IP read allowed any client to spoof the audited IP hash | S4-012 | YES | `_extract_client_ip` at `consent.py:86-147` uses `trusted_proxy_count` config; `=0` ignores XFF entirely, `>0` indexes from end. Default value `0` in `config.py:84` is safe-by-default. Four unit tests cover spoof-blocked, hop-from-right, attacker-prepend, missing-XFF fallback. |

All five HIGH/MEDIUM/LOW items from the previous round are genuinely closed.

## 2. New issues introduced by these fixes

### [MEDIUM-1] Consent revocation not enforced on in-flight WS connections — S4-010

**Location:** `services/interview_core/app/routers/ws.py:680-737` (`_revalidate_token_loop`)

**Description:** The mid-session revalidation task re-verifies the JWT every
5 minutes but NEVER re-checks `has_active_consent`. A user who calls
`DELETE /consent` while their interview WS is open continues to be recorded
(audio_chunks buffered, transcripts produced, turns persisted) until the
JWT expires — up to 15 minutes after revocation.

**Impact:** DPDP §11 contract violation. The right-to-withdraw is documented
to take effect "immediately on next session-create / WS connect" (per the
review brief and `consent_guard.py:59-64` docstring), but in fact mid-session
processing continues for up to one JWT TTL after the user clicks revoke.
For a 10-minute interview the realistic exposure is the full remainder of
the session.

**Remediation:** Two acceptable fixes:
  1. Cheapest: have `_revalidate_token_loop` also call `has_active_consent`
     on each 5-minute tick (with a fresh DB session). On `False`, emit
     `{"type":"error","code":"CONSENT_REVOKED"}` and close 4003. Adds one
     indexed SELECT per 300 s per connection — negligible.
  2. Stronger: pubsub channel `consent:revoked:<user_id>` published by
     `DELETE /consent`; the WS handler subscribes for its user_id and
     closes on receipt. Sub-second propagation. Sprint 5 work.

Pick (1) for Sprint 4 — it closes the legal gap with ~10 LOC.

**Reference:** DPDP Act 2023 §11(3) — "consent…can be withdrawn at any
time, with the same ease as it was given"; OWASP A01 Broken Access Control.

### [MEDIUM-2] `_normalise_app_env` validator lacks test coverage for capitalisation/whitespace variants

**Location:** `services/interview_core/app/config.py:14-23`; tests in
`services/interview_core/tests/integration/test_ws_auth.py:527-608`.

**Description:** The validator is correct in code (Pydantic v2 field validators
with `mode="before"` run on every input source — env var, `.env` file, init
kwarg — and Pydantic strips/lowercases via the validator before assignment).
However the test suite only exercises `monkeypatch.setattr(settings, "app_env", "production")` with the already-lowercase string. There is no test asserting
that `APP_ENV=Production`, `APP_ENV=PROD`, or `APP_ENV="  production  "`
all collapse to `"production"` and trigger the gate.

**Impact:** A future refactor (e.g. removing the validator, or moving it
behind `case_sensitive=True`) could silently re-open the S4-007 gate
without any test failing.

**Remediation:** Add a parametrised unit test in
`services/interview_core/tests/unit/test_config.py` (new file ok) that
constructs `Settings(app_env=raw)` for `raw in ["Production", "PROD",
"  production  ", "Production"]` and asserts `settings.app_env == "production"`.
~10 LOC. Not a deploy-blocker because the live code is correct; this is a
regression-prevention belt.

**Reference:** OWASP A04 Insecure Design — defence-in-depth for security
gates requires test coverage of all bypass-attempt shapes.

### [LOW-1] `IntegrityError` race-catch branch in POST /consent is not exercised by any test

**Location:** `services/data_gateway/app/routers/consent.py:252-281`.

**Description:** The S4-009 integration test `test_concurrent_posts_collapse_to_one_row`
uses `asyncio.gather(_post(), _post())` against an in-process ASGI app
(httpx `AsyncClient` + `ASGITransport`). This is single-event-loop
co-operative concurrency — the explicit pre-check (`_find_active_consent`)
likely wins both times in practice, so the `IntegrityError` catch at
line 252 may never actually be entered. The branch is logically correct
on code review (`db.rollback()` cleans session state; subsequent
`_find_active_consent` reads committed data) but has zero observed execution.

**Impact:** A regression that breaks the rollback/re-query path would not
be caught. The unique-index canary (`test_unique_index_present`) at least
proves the database-side guard exists, so a duplicate row is still
prevented — the worst case of an undetected regression is a 500 instead
of a 200 on a real race.

**Remediation:** Add a deterministic unit test that monkey-patches
`db.commit()` to raise `IntegrityError` on first call, then proves a
200 with the existing row's `consent_id` is returned. ~20 LOC. Backlog,
not deploy-blocker.

**Reference:** Test-coverage gap; no CVE.

### [LOW-2] Unbounded `_AudioChunkBuffer` per session — DoS surface

**Location:** `services/interview_core/app/routers/ws.py:1297-1300` + line 236.

**Description:** Already documented at `ws.py:148-150` as a known Sprint 4
backlog item. Worst-case: a hostile but JWT-valid client can send chunks
just under the 64 KB cap with monotonically increasing `seq` and never
send `turn_end`. The in-memory `_AudioChunkBuffer[session_id]` list grows
unbounded until the WS times out or the pod OOMs. With `_MAX_MESSAGE_BYTES=65536`
and (say) 10 000 chunks before turn_end, that is ~640 MB resident per
session, multiplied by concurrent sessions.

**Impact:** Single-pod memory exhaustion is realistic at modest fan-out.
This is NOT a P0 for the Sprint 4 merge because:
  - The threat requires a JWT-authenticated account (no anonymous attack).
  - The handler does not persist the buffer; it dies with the connection.
  - Documented in `ws.py:148-150` and `ws.py:153` already.

**Remediation:** Add a session-wide cap (`_MAX_BUFFERED_AUDIO_BYTES = 5 * 1024 * 1024`
≈ 5 MB = ~5 min of 16 kHz mono PCM). On overrun emit
`{"type":"error","code":"BUFFER_FULL"}` and force a `turn_end` flush.
Track as Sprint 5 task (assign new task #48).

**Reference:** OWASP A04 Insecure Design — resource limits required; CWE-770.

### [LOW-3] No validator on `trusted_proxy_count` range

**Location:** `services/data_gateway/app/config.py:84`.

**Description:** Already captured in task #44. Worth restating: setting
`TRUSTED_PROXY_COUNT=-1` (typo or env-var-injection mishap) makes
`real_index = len(hops) - (-1) - 1 = len(hops)` which is an `IndexError`
on `hops[real_index]` at `consent.py:147` — handler crashes, returns 500.
Setting it absurdly high (e.g. 100) silently falls back to the direct
host on every request (correct behaviour, but masks the misconfig).

**Impact:** Operational footgun, not a security vector — an attacker
cannot influence this value. Task #44 already tracks it.

**Remediation:** `Field(ge=0, le=4)` on the field in `config.py` so a
bad env var is rejected at boot. ~1 line.

**Reference:** OWASP A05 Security Misconfiguration.

## 3. Test coverage adequate?

| Story | Verdict | Notes |
|---|---|---|
| S4-007 | PASS-WITH-GAP | `test_ws_query_token_rejected_in_production` + `test_ws_query_token_still_accepted_in_local` + `test_ws_subprotocol_token_works_in_production` correctly prove the env-gate. Missing: (a) capitalisation tests for `_normalise_app_env` (see MEDIUM-2), (b) regression that `?token=` + `Authorization: Bearer` in production falls through to the header. |
| S4-008 | PASS | `test_rate_limit_skips_audio_chunks` sends 200 chunks under a widened window and asserts no error reply — exactly the contract. `test_rate_limit_61st_message_triggers_rate_limited` proves non-audio_chunk types are still counted (uses `"type":"ping"`). |
| S4-009 | PASS-WITH-GAP | Index-present test is the strongest guard. The race test demonstrates idempotency but does not deterministically exercise the IntegrityError branch (see LOW-1). |
| S4-010 | PASS | 200/404/404-after-revoke/cross-service-propagation all covered. Cross-service test uses `GET /consent/status` as a proxy for the consent_guard predicate — acceptable rationale documented. Missing: explicit in-flight WS revocation test (see MEDIUM-1). |
| S4-012 | PASS | All four documented cases (no proxy + spoof, 1 proxy normal, 1 proxy + attacker prepend, missing XFF fallback) covered. Edge cases not tested: leading/trailing whitespace in XFF entries (handled by `.strip()` already, but no test), empty-string entries (filtered by `if h.strip()`). |

## 4. DPDP / Residency check

| Item | Result | Notes |
|---|---|---|
| Consent ledger entry on PII collection | PASS | POST /consent path hardened with partial unique index and IntegrityError catch — no duplicate active rows possible. |
| Erasure / right-to-withdraw | PARTIAL | DELETE endpoint correct and tested; **gap**: in-flight WS sessions continue recording for up to 15 min after revocation (MEDIUM-1 above). New session creation IS blocked immediately. |
| India data residency | PASS | No third-party SaaS introduced by these stories. Sentry DSN still empty by default. All DB/Redis remain Neon/Upstash (demo) or `ap-south-1` (prod). |
| PII in logs | PASS | Verified across all five changes: raw IP, raw UA, JWT, audio bytes, transcript text are never logged. Only hashes, lengths, jti, sub appear. `ws.py:144-147` documents the contract; consent.py logs only `user_id`/`consent_id`/`evidence`-NOT-included. |
| Audit log immutability | PASS | DPDP ledger is append-then-mutate-revoked_at-only; original `granted_at`/`evidence` preserved on revocation. The partial unique index does NOT prevent historical (revoked) rows from accumulating — design is correct. |

## Verdict for Sprint 4 merge

**APPROVED-WITH-FOLLOWUPS** — five Sprint 3 findings genuinely closed; no new
CRITICAL/HIGH introduced. MEDIUM-1 (in-flight consent revocation) MUST land in
Sprint 5 before any govt-bid deploy or external pilot.

### Action items (NEW, must-fix before next sign-off gate)

1. **MEDIUM-1 — Sprint 5 P0:** Add consent re-check to `_revalidate_token_loop` in `services/interview_core/app/routers/ws.py:680` so revocation kills live WS within 5 min (file new task; reference S4-010 follow-up).
2. **MEDIUM-2 — Sprint 5:** Add `tests/unit/test_config.py` covering `_normalise_app_env` with `"Production"`, `"PROD"`, `"  production  "` variants — regression guard for the S4-007 gate.
3. **LOW-1 + LOW-2 + LOW-3:** Fold into Sprint 5 backlog tasks #48 (audio-buffer cap), #49 (deterministic IntegrityError test), and existing #44 (proxy-count range validator).
