# Security Review — S3-011 (DPDP Consent Gate)

Reviewer: security-auditor
Date: 2026-05-27
Scope: services/data_gateway/app/{routers/consent.py, schemas/consent.py, models.py (DpdpConsent), config.py}, tests/integration/test_consent_router.py; web/src/{components/ConsentModal.tsx, context/ConsentContext.tsx, pages/JobsList.tsx, api/consent.ts}; alembic 0001 migration; services/interview_core/app/routers/sessions.py.

Severity counts: CRITICAL=1  HIGH=2  MEDIUM=4  LOW=4

---

## Verification of the 6 stated PII / DPDP rules

| # | Rule | Verdict | Evidence |
|---|------|---------|----------|
| 1 | No raw IP stored, sha256+salt | PASS | `consent.py:56-63, 161-166`; integration test 7 (`test_evidence_ip_hash_is_sha256_hex_not_raw_ip`) re-computes the expected hash and rejects raw values |
| 2 | No PII in logs | PASS (with caveats) | Router logs only `user_id` + `consent_id`; `main.py:29-38` adds a `_redact_pii_processor` that drops `email/password/phone/full_name` defense-in-depth |
| 3 | JWT required on both routes | PASS | `Depends(get_current_user)` on both; `dependencies.py:49-83` raises 401 on missing/invalid; integration test 1 confirms |
| 4 | Purpose whitelist | PASS | `consent.py:42, 134-138`; integration test 4 confirms 400 on unknown purpose |
| 5 | Idempotent | PASS at app layer, RACE at DB layer | `consent.py:141-154` returns 200 with existing row; integration test 3 confirms single-row outcome under sequential calls. See [HIGH-1] for the concurrent-request gap. |
| 6 | `CONSENT_IP_SALT` required env | PASS | `config.py:59` — no default, Pydantic `Settings()` will raise `ValidationError` at import time if missing |

---

## Findings

### [CRITICAL] Consent gate is enforced only on the frontend
- Location: `services/interview_core/app/routers/sessions.py:84-148` (`create_session`) — and `services/interview_core/app/routers/ws.py` (handshake).
- Description: `POST /api/sessions` only validates JWT + job; it never checks `dpdp_consent_ledger`. The only thing blocking a session is React state in `web/src/pages/JobsList.tsx:70-77`. Any authenticated user with `curl` / Postman can `POST /api/sessions` and then open the WS — no consent, no log entry, no ledger row. The same is true of the WS upgrade itself.
- Impact: DPDP Act 2023 §6 (lawful processing requires consent) is violated for anyone who bypasses the UI. Voice recording, transcripts, scorecards all get processed under "I didn't consent". This is the exact regulator-letter scenario.
- Remediation (must ship before merging to main):
  1. In `interview_core/app/routers/sessions.py`, before `db.add(new_session)`, run `SELECT 1 FROM dpdp_consent_ledger WHERE user_id=:uid AND consent_type='interview_voice_recording' AND purpose='interview' AND granted=true AND revoked_at IS NULL LIMIT 1`. If no row, return `403 {"detail":"CONSENT_REQUIRED"}`.
  2. Mirror the check in `ws.py` between JWT verification and `websocket.accept()`. Close code: `4003` (already defined) with reason `"consent_required"`.
  3. Add an integration test: register user, do NOT call POST /consent, attempt `POST /api/sessions` → assert 403.
- Reference: OWASP A01 (Broken Access Control); DPDP Act 2023 §6 + §7.

### [HIGH-1] Idempotency check has a TOCTOU race; no DB-level uniqueness
- Location: `services/data_gateway/app/routers/consent.py:140-181`; migration `20260527_0001`.
- Description: The "find then insert" sequence is not transactional, and there is no partial unique index. Two concurrent `POST /consent` calls from the same browser (double-click, React double-render, retry) both read "no row", both insert, both commit. Result: two rows where the spec says one. Integration test 3 is sequential and does not exercise the race.
- Impact: Ledger duplication. DPDP audit / right-to-erasure queries become ambiguous ("which row is the active one?"). Idempotency contract silently breaks under load.
- Remediation: Add a partial unique index in a follow-up migration and let the DB enforce it; catch `IntegrityError` and return the existing row.
  ```sql
  CREATE UNIQUE INDEX uq_dpdp_active_consent ON dpdp_consent_ledger (user_id, consent_type, purpose)
    WHERE granted = true AND revoked_at IS NULL;
  ```
- Reference: OWASP A04 (Insecure Design — concurrency); CWE-367.

### [HIGH-2] No revocation endpoint (open question #2)
- Location: missing.
- Description: DPDP §11 grants withdrawal "at any time" and §13(2) gives the Data Principal an enforceable right. Today there is no `POST /consent/revoke` and no UI to trigger one. A user who emails `support@intants.com` (per the modal) cannot self-serve.
- Impact: Compliance exposure if a regulator audit lands within the next ~30 days. Manual SQL is the only fix path — operationally fragile and not auditable.
- Remediation: Ship in the immediate next sprint (do not block S3-011 merge on this — gate the **enforcement** fix above is the blocker). New story should add `POST /consent/revoke` that sets `revoked_at = now()` and also ends any in-progress sessions for that user.
- Reference: DPDP Act 2023 §11, §13.

### [MEDIUM-1] `X-Forwarded-For` unconditionally trusted
- Location: `services/data_gateway/app/routers/consent.py:66-79` (open question #3).
- Description: The router takes the leftmost XFF value with no proxy-hop validation. Behind Railway / EKS ALB this is fine — they overwrite XFF. If the service is ever exposed directly (a misconfigured ingress, a developer running `uvicorn` on a public IP, a future direct-to-pod debug path), any client can spoof the IP and poison the ip_hash field with whatever they want. That defeats the evidentiary value of the hash.
- Impact: Forensic value of the consent ledger evaporates; DPDP audit-trail quality drops.
- Remediation:
  1. Add `trusted_proxy_count: int = 0` to `config.py`. When `> 0`, take XFF value at position `-trusted_proxy_count` (the value the trusted proxy itself recorded).
  2. When `0`, ignore XFF entirely and use `request.client.host`.
  3. Document the production setting (Railway = 1, EKS ALB = 1) in the README.
- Reference: OWASP A05 (Security Misconfiguration); CWE-348.

### [MEDIUM-2] No consent retention TTL (open question #1)
- Location: schema has no `expires_at`; no cleanup job.
- Description: DPDP §8(7) requires erasure once the purpose is fulfilled. Consent rows persist indefinitely today.
- Impact: After a couple of years we hold consents whose purpose was fulfilled (the interview ran and finished). Reasonable interpretation by a DPB officer = §8(7) breach.
- Remediation: Recommended as a follow-up story (not a blocker for S3-011): add `expires_at TIMESTAMPTZ` to schema, default `granted_at + INTERVAL '1 year'`; nightly job (or `pg_partman`) deletes expired+revoked rows after a 30-day grace window.
- Reference: DPDP Act 2023 §8(7).

### [MEDIUM-3] Static salt — no rotation plan (open question #4)
- Location: `config.py:59`; `consent.py:56-63`.
- Description: One static salt protects all ip_hash values forever. If it leaks (logs, backups, env dump), all historical hashes become attackable. There is also no per-row salt, so identical IPs produce identical hashes — limited correlation risk inside a single dataset.
- Impact: Low today (the hash is not exported), but contributes to "single shared secret = blast-radius problem" if it leaks.
- Remediation: Out of scope for S3-011 merge. Track as a follow-up: introduce per-row random salt stored alongside the hash (no rotation needed) OR adopt HMAC with a KMS-backed key in production.
- Reference: CWE-916.

### [MEDIUM-4] CORS allows `Authorization` header from `http://localhost:5173`
- Location: `services/data_gateway/app/main.py:90-96`; `config.py:63-87`.
- Description: Config-validated against wildcard with credentials — good. But it's worth flagging that the default `cors_allowed_origins` value embedded in `config.py` is the dev origin. Confirm production deploy overrides this; otherwise a hostile site could read the consent status of a logged-in user (the JWT lives in localStorage today, so this is somewhat moot, but worth tightening).
- Remediation: Add a deployment-time assertion: if `APP_ENV == "production"` and any origin starts with `http://localhost`, raise. One-liner in `config.py` validator.
- Reference: OWASP A05.

### [LOW-1] Pydantic `purpose` field has no `Literal` constraint
- Location: `services/data_gateway/app/schemas/consent.py:20`.
- Description: The whitelist is enforced in the router. Using `purpose: Literal["interview"]` would push the rejection up to the schema layer, return 422 with a structured field error, and make OpenAPI self-document the constraint.
- Remediation: Change to `Literal["interview"]`. Cosmetic, not functional.

### [LOW-2] Modal copy promises "90 days" retention, but no enforcement exists
- Location: `web/src/components/ConsentModal.tsx:117-120`.
- Description: The user is told voice/transcripts are deleted after 90 days. There is no cron, no S3 lifecycle rule, no audio retention column in the schema yet. We are making a regulator-binding promise we cannot prove.
- Impact: Risk of misrepresentation under DPDP §6(2). Until Sprint 4's retention job lands, this string is aspirational.
- Remediation: Either (a) ship a 90-day delete job in the same release window (preferred), or (b) soften the copy to "We delete recordings on completion of your use of the service, typically within 90 days" until enforcement exists. Track the gap in `docs/PROCUREMENT.md` retention column.

### [LOW-3] Focus is not restored to the trigger button after modal closes
- Location: `web/src/components/ConsentModal.tsx`.
- Description: A11y nit — when the modal unmounts (Decline or after I Agree), focus goes to `<body>` rather than back to the JobCard "Start Interview" button. Screen-reader users lose their place. Not a security issue, but the agent's brief mentioned a11y so calling it out.
- Remediation: In `ConsentModal`, capture `document.activeElement` on mount and restore on unmount.

### [LOW-4] Frontend treats GET /consent/status failure as "unknown" silently
- Location: `web/src/context/ConsentContext.tsx:42-50`.
- Description: The catch-all `setConsented(null)` on fetch error is fail-closed (good — user gets re-prompted), but the error is swallowed. No user-visible message, no Sentry breadcrumb (Sentry isn't wired yet anyway). If the backend is hard-down, the user will keep getting modal forever with no clue why.
- Remediation: Surface the error to the modal as the existing `error` prop, or at minimum `console.error` so devtools shows it.

---

## DPDP / Residency check

- Consent ledger: **PASS** — rows are written with hashed evidence, ORM matches migration schema. **One open: enforcement is frontend-only — see CRITICAL.**
- Erasure endpoint: **FAIL** — no `POST /consent/revoke`, no `DELETE /user/me` end-to-end. Modal points users at an email inbox. See HIGH-2.
- Data residency: **PASS for S3-011 surface** — no PII leaves the local DB. No third-party calls in the consent path.

---

## Answers to the 4 open questions

1. **Retention TTL** — Follow-up story. Not a merge blocker for S3-011 alone, but couple it with the "90 days" copy promise in Sprint 4 (see LOW-2). If neither lands in the next sprint, soften the modal copy.

2. **Revocation endpoint** — Follow-up story, **next sprint, not later**. Modal already advertises the right; we owe an implementation. Acceptable to ship S3-011 first because no recording happens yet (voice pipeline is being built in parallel).

3. **X-Forwarded-For trust** — Add the `trusted_proxy_count` setting now. It's a 15-line change and prevents a foot-gun the day someone runs `uvicorn` on a public port. Demo being behind Railway is the happy path, not a guarantee. See MEDIUM-1.

4. **Salt rotation** — Follow-up. Not blocking. Document the rotation procedure in `docs/PROCUREMENT.md` (re-hash is not possible without raw IPs, so rotation = accept that pre-rotation hashes lose strength). Acknowledge it; do not block on it.

---

## Other concerns flagged

- **Server-side bypass (CRITICAL above)** is the headline risk. Everything else can ship as follow-ups.
- **Race on idempotency (HIGH-1)** is a real bug under double-click; partial unique index closes it cleanly.
- **No rate limiting on `/consent`**: a user can spam `POST /consent` with bad purposes for log noise. The global `rate_limit_api_per_minute: 60` setting exists in config but I did not see it wired into the consent router. Apply the existing per-IP limiter when it lands (Sprint 3 backlog already has this).
- **Cookies / sessions**: N/A — JWT in localStorage, Bearer header only. No CSRF surface on these routes.

---

## Verdict

**BLOCKED** — server-side consent enforcement (CRITICAL) and the idempotency race (HIGH-1) must land before merge. MEDIUM-1 (XFF trust) and an integration test that proves `POST /api/sessions` returns 403 without consent are also required. Everything else is a follow-up.
