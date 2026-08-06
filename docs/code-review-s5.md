# Code Review — S5 (post-hardening review pass)

**Reviewer:** code-reviewer
**Date:** 2026-08-06
**Baseline:** `fa42edc` → `566b2ea` (23 commits, 117 files, +7578/−905)
**Companion report:** `docs/security-review-s5.md`
**Previous reports:** `docs/security-review-s4-bundle.md`, `docs/security-review-s3-011.md`,
`docs/security-review-s3-004-s3-005.md`

This pass reviews the hardening work merged since the pre-audit baseline and the
surfaces it did not reach. It is a **review-only phase — no application code is
changed by this document.** Findings are ranked so later phases can pick them up
in severity order.

Every finding below was confirmed by opening the cited file. Where a previously
reported item no longer holds, that is stated explicitly rather than carried
forward.

---

```
=== Code Review ===
Scope: services/data_gateway/**, services/interview_core/**,
       services/feedback_billing/**, services/admin_ops/**, shared/**,
       web/src/**, .github/workflows/**, scripts/**, docs/PISTON_SELFHOST.md
Verdict: REQUEST CHANGES

MUST FIX:
- services/data_gateway/app/routers/sso_naipunyam.py:227-412: `state` is accepted
  into SsoCallbackBody and never compared against anything — no Redis nonce, no
  binding cookie, no PKCE. The module docstring still advertises this as deferred
  to "S5-003b". Same login-CSRF class that sso_google.py closed in da4c39a.
  → Port the sso_google.py pattern: Redis nonce, httpOnly binding cookie
    compared with hmac.compare_digest before the token exchange, PKCE S256, and
    the privileged-account exclusion. Delete the docstring note once done.
- scripts/piston-up.ps1:71: the self-hosted code-execution sandbox runs
  `docker run --privileged … --dns 8.8.8.8`, i.e. candidate-submitted code
  executes in a privileged container with deliberate outbound DNS.
  → Drop `--privileged` (or scope it to the minimum capability set), remove the
    explicit egress, and document the per-job network-namespace lockdown.
- services/interview_core/app/worker/interview_worker.py:869-890
  (`_mint_service_jwt`): hand-rolls the claims dict (iss/aud/iat/exp/jti) and
  encodes directly instead of calling the canonical minter. A second
  implementation of token minting is exactly the drift risk that produced the
  revocation-check bug in feedback_billing.
  → Replace with shared.auth.jwt.issue_access_token(); confirm the shared
    verifier still accepts the claim set.

SHOULD FIX:
- services/data_gateway/app/config.py:266: `trusted_proxy_count: int = 0` has no
  bounds. It is the divisor of the client-IP hop arithmetic; an out-of-range
  value silently collapses every client to one identity.
- services/data_gateway/tests/: no test_config.py exists. `_normalise_app_env`
  (the guard that stops APP_ENV=Production bypassing the production gates) and
  the proxy-count bound are both untested.
- .github/workflows/ci.yml: no `alembic heads` gate. Currently a single head
  (a1b2c3d4e5f7) — so this is preventive, not corrective.
- .github/workflows/ci.yml: no coverage collection or threshold, against a
  documented ≥80% convention.
- .github/workflows/ci.yml + .trivyignore: `.trivyignore` is 1526 bytes of
  carefully reasoned accepted-risk entries and **nothing consumes it** — no
  Trivy step exists. Python dependency CVEs *are* gated (pip-audit against
  scripts/pip-audit-ignore.txt, ci.yml:242-262), so the real gap is
  container-image scanning, not dependency scanning. Either wire Trivy for the
  images or delete the file; a maintained ignore list for a scanner that never
  runs reads as coverage that is not there.
- services/data_gateway/app/dependencies.py,
  services/interview_core/app/dependencies.py,
  services/feedback_billing/app/auth.py,
  services/admin_ops/app/admin_auth.py: the JWT auth dependency exists in four
  near-identical copies. The third copy already drifted once and shipped a
  missing revocation check on GET /api/scorecards. → hand off to cto-architect
  (see Hand-offs).

CONSIDER:
- web/src/components/{ProtectedRoute,AdminRoute,HRRoute,SuperAdminRoute,
  PlatformOwnerRoute}.tsx: 155 lines across five files implementing the same
  guard + spinner. Extract one RoleRoute/AuthLoading; re-confirm the frontend
  role sets still match backend semantics while consolidating.
- web/src/features/interview/proctorLogic.ts:173-174: malformed nested JSDoc —
  a bare `/**` immediately followed by a second `/** … */`, leaving the first
  block unterminated as documentation.
- web/src/features/interview/: no feature-scoped ErrorBoundary, so a render
  crash during a live interview unmounts the whole app rather than the panel.
- services/data_gateway/app/routers/hr_applicants.py:126,328,385: applicant
  email is `str | None`; EmailStr is available and used elsewhere in this
  codebase.
- services/data_gateway/app/routers/hr_applicants.py:725:
  `.ilike(f"%{job}%")` does not escape `%` or `_`, so a caller-supplied filter
  can widen its own match set.
- services/data_gateway/app/main.py:343-348: `/metrics` has no auth dependency.
  Edge-blocked today on both Caddyfiles; in-app it is open.
- services/admin_ops/app/erasure_executor.py:275-288: the applicants UPDATE
  nulls resume_text and resume_s3_key but not `embedding` — see the security
  report, DPDP section.

Tests: ADEQUATE for the merged hardening, INSUFFICIENT at the edges.
  1181 backend + 268 frontend tests pass. The security-relevant additions this
  cycle were mutation-tested (revert the fix → the test fails), which is the
  right bar. Gaps: no config test module; coverage is uninstrumented so the
  ≥80% convention is unenforced; two tests were found this cycle that passed
  with the control deleted (tenant isolation in fde86c9, can_publish_data in
  566b2ea) — that failure mode is worth a standing check, not just ad-hoc
  catches.

Style: CONSISTENT.
  Type hints, async I/O, ruff and mypy are clean across all five packages.
  Comment density and the "explain why, not what" convention hold in the new
  code. The one systematic exception is duplication-by-copy (auth dependencies,
  frontend guards, health-check handlers), which is a structural issue rather
  than a style one.

Hand-offs needed: security-auditor (Y), cto-architect (Y)
```

---

## Findings by category

### Correctness

| Ref | Location | Finding |
|---|---|---|
| C-1 | `sso_naipunyam.py:227-412` | `state` never validated — login CSRF. Dormant (`AUTH_PROVIDER=local`) but one env flip from live, and it is the APSSDC bid path. |
| C-2 | `interview_worker.py:869-890` | Second JWT-minting implementation; claim set can drift from the verifier. |
| C-3 | `config.py:266` | Unbounded `trusted_proxy_count` feeds hop arithmetic with no range check. |
| C-4 | `hr_applicants.py:725` | Unescaped `%`/`_` in an ILIKE pattern. |
| C-5 | `erasure_executor.py:275-288` | `embedding` not cleared on erasure (detail in the security report). |

### Tests

| Ref | Location | Finding |
|---|---|---|
| T-1 | `services/data_gateway/tests/` | No `test_config.py`; `_normalise_app_env` and the proxy-count bound untested. |
| T-2 | `.github/workflows/ci.yml` | No coverage instrumentation → the ≥80% convention is documentation only. |
| T-3 | repo-wide | Two tests found this cycle passed with their control removed. Both fixed; the pattern deserves a lint or review checklist item. |

### Performance

| Ref | Location | Finding |
|---|---|---|
| P-1 | `dependencies.py` (`require_password_changed`) | One indexed read per privileged request. Accepted deliberately — scoped to privileged routers, not global — but it is a new per-request query and should be watched if `/hr/*` traffic grows. |
| P-2 | `hr_applicants.py` | Pagination added this cycle; `page` is bounded (`le=_MAX_PAGE`) so OFFSET cost stays capped. No action. |

### Style

No blocking findings. ruff (0.7.4, pinned) and mypy (1.20.2, pinned) clean across
`shared/` and all four services; `tsc -b` and `eslint --max-warnings 0` clean for
the frontend.

### Anti-patterns

| Ref | Location | Finding |
|---|---|---|
| A-1 | four services | JWT auth dependency copied ×4; one copy already drifted into a live bug. → cto-architect. |
| A-2 | `health.py` ×3 services | The same handler copied three times; the "return the exception type only" fix was applied to one of them, so they have now drifted. |
| A-3 | five frontend guards | Same guard + spinner copied five times. |
| A-4 | `interview_worker.py:_mint_service_jwt` | Token minting copied rather than imported. |

The common thread is copy-then-drift. Every high-severity issue found this cycle
in `feedback_billing` and `admin_ops` traced back to a copied block whose
siblings were later fixed and it was not.

### API contract

Changes already merged in earlier phases, recorded here so consumers can be
checked:

| Endpoint | Change | Commit |
|---|---|---|
| `POST /api/rooms/{session_id}/token` | now 409 on a completed/failed session or when a scorecard exists | `40df357` |
| `POST /internal/score` | `language` constrained to `en\|hi\|te` → 422 outside that set | `40df357` |
| `GET /api/scorecards/{id}` | pre-signed PDF URL TTL 30 days → 1 hour | `40df357` |
| `GET /admin/interviews/{id}/transcript` | now writes an `audit_log` row | `40df357` |
| `POST /jobs/{job_id}/jd-document` | now 403 for any non-staff role (was: any authenticated co-tenant) | `566b2ea` |
| `PATCH /auth/me/profile` | `linkedin_url` / `github_url` must be http(s) → 422 otherwise | `566b2ea` |
| all `/hr/*`, `/admin/*` | 403 while `must_change_password` is set | `a5b8f68` |

### Previously reported, now obsolete

**s4-bundle LOW-2 (unbounded `_AudioChunkBuffer`) — CLOSED, no action.**
The finding cited `services/interview_core/app/routers/ws.py:1297-1300`. That
file no longer exists; the WebSocket audio transport was replaced by LiveKit.
The only surviving `_AudioChunkBuffer`/`turn_end` references are in
`app/speech/sarvam_stt_stream.py`, which is imported by neither `app/main.py`
nor `app/worker/interview_worker.py` — it is off the production path. Adding a
cap here would harden code that does not run. Recommend closing LOW-2 as
obsolete and, separately, deleting or quarantining the dead `app/speech`,
`app/graph`, `app/agent` and `app/avatar` trees so future reviews do not audit
them as live.

---

## Hand-offs

### → `security-auditor` (Y)

1. **SSO** — `sso_naipunyam.py` state/PKCE/privileged-exclusion gap (C-1). Needs
   the same adversarial treatment `sso_google.py` received, including whether
   the backward-compat branch there can itself fail open.
2. **Sandbox** — `scripts/piston-up.ps1` + `docs/PISTON_SELFHOST.md`: privileged
   container plus deliberate egress for candidate-submitted code. Also assess
   the residual risk of the public `emkc.org` fallback and the hosted JDoodle
   provider, neither of which we control.
3. **JWT** — `_mint_service_jwt` duplicate minting, and whether the service
   plane should share `jwt_secret`, `iss` and `aud` with the user plane at all.

### → `cto-architect` (Y)

**Consolidate the four JWT auth dependencies into `shared/auth/`.**
Do NOT implement directly — this touches the boundary between four services and
their independent dependency pins.

Constraints the design has to respect, all verified:
- `shared/auth/local.py` pulls in bcrypt, redis, sqlalchemy and jose.
  `feedback_billing` deliberately does **not** ship bcrypt, which is why
  `feedback_billing/app/auth.py` re-declares `TOKEN_EPOCH_PREFIX` rather than
  importing it. Any shared dependency must be importable from a service that
  lacks bcrypt.
- Each service reads its own `Settings` for `jwt_secret`/`issuer`/`audience`;
  the shared dependency needs those injected, not imported.
- Redis clients differ per service (`app.redis_client` in each).
- The epoch check fails **open** by design in all four copies. That trade-off
  should be re-affirmed or changed deliberately during consolidation, not
  inherited by accident.

Evidence this is worth doing: `scorecard_list.py` was the fourth copy, drifted,
and shipped without the revocation-epoch check — so "log out all devices",
password reset, HR account deletion and DPDP erasure all failed to revoke access
to scorecard history until `40df357`.
