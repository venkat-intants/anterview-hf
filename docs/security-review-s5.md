# Security Review — S5 (post-hardening audit pass)

**Reviewer:** security-auditor
**Date:** 2026-08-06
**Severity counts:** CRITICAL=0  HIGH=2  MEDIUM=3  LOW=0
**Baseline:** `fa42edc` → `566b2ea` (23 commits, 117 files)
**Companion report:** `docs/code-review-s5.md`
**Previous reports:** `docs/security-review-s4-bundle.md`,
`docs/security-review-s3-011.md`, `docs/security-review-s3-004-s3-005.md`

---

> ## ⚠ SUPERSEDED — reconciled 2026-08-07
>
> **The `BLOCKED` verdict in §4 no longer applies. Both HIGH findings are fixed
> and verified at `352f366`.** This banner was added because the most senior
> security document in the repository was asserting a production block against
> work that had already shipped — and a stale block is read by a bid reviewer
> exactly as seriously as a live one.
>
> **Nothing below has been deleted.** The findings, evidence and reasoning are
> retained verbatim as RFP-traceability evidence: they record what was wrong,
> when it was found, and what the fix had to satisfy. Read the document as a
> historical record of the 2026-08-06 state, with the statuses in this table
> overriding it.
>
> | Finding | Status at `352f366` | Evidence at HEAD |
> |---|---|---|
> | **HIGH-1** — Naipunyam SSO `state` never validated (login CSRF, no PKCE, no privileged-account exclusion) | **FIXED** | One-shot Redis state with 600 s TTL written at `sso_naipunyam.py:251-261`, consumed get-then-delete before the token POST; binding cookie compared with `hmac.compare_digest` at `:396-398`; PKCE `code_challenge_method: "S256"` at `:276` (challenge built by `_pkce_challenge`, SHA-256 + url-safe base64, RFC 7636 §4.2); privileged-role rejection at `:493-509`, evaluated **before** the user upsert and before any token is issued |
> | **HIGH-2** — Piston sandbox runs `--privileged` with `--dns 8.8.8.8` | **FIXED** | `scripts/piston-up.ps1:120-138` — `--cap-drop=ALL` with a named allow-list, `--cgroupns=private`, `--pids-limit 512`, loopback-only bind, networking disabled. No `--privileged`, no explicit DNS |
> | **MEDIUM-1** — `trusted_proxy_count` unbounded and silently degrading | **FIXED (one direction)** | `config.py:283` `Field(default=0, ge=0, le=4)`; `client_ip_proxy_hop_underflow_total` counts the too-high case. The too-**low** case is still silent — carried forward as **DG-3** |
> | **MEDIUM-2** — rate limiting fails open on any Redis error | **FIXED** | `rate_limit_check_skipped_total` in `rate_limit.py:45-50`, and — since 2026-08-07 — consumed by `ops/alerts/rate_limit_fail_open.rules.yml`. The metric alone was **not** enough to close this; that gap was itself re-reported as **DG-6** |
> | **MEDIUM-3** — `/metrics` has no application-layer authentication | **FIXED** | `shared/metrics_auth.py` wired into all four services; bearer token required, fails **closed** (404) in production when `METRICS_TOKEN` is unset |
>
> **Current verdict lives in [`docs/code-review-2026-08-07.md`](code-review-2026-08-07.md)**,
> which supersedes this report and its companion `code-review-s5.md`. That review
> re-verified every item above by opening the file at HEAD rather than trusting
> this document.

---

Audits the surfaces the Sprint-5 hardening cycle did **not** reach. It does not
re-prove the vulnerabilities closed in that cycle — those are verified in §1 —
and it changes no application code. Findings are ranked for later remediation
phases.

Out of scope by owner direction: demo/seed account hygiene and credentials
already published in git history. These are setup concerns, not code-level
deploy risk, and are excluded from the verdict.

---

## 1. Prior findings — verification status

| Finding | Origin | Status | Evidence |
|---|---|---|---|
| Google SSO login-CSRF (no browser-bound state, no PKCE) | S5 audit | **FIXED** | `sso_google.py:279-300` mints an httpOnly `oauth_state` cookie and stores only its SHA-256; `:370-393` compares with `hmac.compare_digest` before the token exchange; PKCE S256 pinned against RFC 7636 Appendix B. |
| `logout_all` defeatable (16-min epoch TTL vs 7-day refresh; refresh TOCTOU) | S5 audit | **FIXED** | `shared/auth/local.py:553-560` epoch TTL now covers the refresh lifetime; `:440-465` re-checks the epoch after minting against the **presented** token's `created_at`, then deletes and untracks the new key. Mutation-tested. |
| Assessment bypass — typed answers over `lk.chat` | S5 audit | **FIXED** | `interview_worker.py:1841` `room_options=RoomOptions(text_input=False)`; `rooms.py:239` `can_publish_data=False`. Regression test now asserts kwarg **presence** before value. |
| `javascript:`-scheme stored XSS in the HR console | S5 re-audit | **FIXED** | `auth.py` `_validate_link_scheme` rejects non-http(s) via `urlparse`; `ProfileView.tsx` `safeExternalUrl` refuses to emit a non-http(s) href, covering pre-existing rows. |
| Cross-tenant JD write (`super_admin`, and any co-tenant incl. guests) | S5 audit + re-audit | **FIXED** | `jd.py:65` `_PLATFORM_ADMIN_ROLES={"platform_owner"}`; `_JD_WRITER_ROLES` staff gate evaluated as Rule 0 before any co-tenancy rule. |
| `must_change_password` unenforced server-side | S5 audit | **FIXED** | `require_role_password_ok` composed into the three privileged chokepoints; dependency-tree walk confirms **72/72** privileged routes carry it. |
| Revocation-epoch check missing on `GET /api/scorecards` | S5 audit | **FIXED** | `feedback_billing/app/auth.py` is now the single dependency; `scorecard_list.py` and `scorecard.py` both import it. |
| Guest token usable across sessions | S5 audit | **FIXED** | `GuestBoundUserDep` applied to all three `{session_id}` routes. |
| **s4-bundle LOW-2** — unbounded `_AudioChunkBuffer` | s4-bundle | **OBSOLETE** | Cited location `app/routers/ws.py` no longer exists (WebSocket transport replaced by LiveKit). Surviving references are in `app/speech/`, which neither `app/main.py` nor `interview_worker.py` imports. Recommend closing. |
| s4-bundle LOW-3 / backlog #44 — proxy-count range validator | s4-bundle | **STILL OPEN** | `config.py:266` `trusted_proxy_count: int = 0`, no bounds. Carried as MEDIUM-1 below. |

---

## 2. Findings

```
=== Security Audit Report ===
Scope: services/data_gateway/**, services/interview_core/**,
       services/feedback_billing/**, services/admin_ops/**, shared/**,
       scripts/piston-up.ps1, docs/PISTON_SELFHOST.md,
       .github/workflows/**, both Caddyfiles
Severity counts: CRITICAL=0  HIGH=2  MEDIUM=3  LOW=0
```

### [HIGH-1] Naipunyam SSO callback performs no `state` validation — login CSRF / session fixation

**Location:** `services/data_gateway/app/routers/sso_naipunyam.py:17-19, 70-74, 227-412`

**Description:** `state` is generated at `:192`, embedded in the redirect, accepted
into `SsoCallbackBody`, and then **never read again**. The handler goes straight
from `body.code` to the token exchange. There is no Redis nonce, no
browser-binding cookie and no PKCE. The module docstring still records this as
deferred ("The stub currently skips server-side `state` verification"). It also
lacks the privileged-account exclusion that `sso_google.py:539-555` applies, so
an IdP assertion mapping onto a privileged row would yield a session at full
privilege. Separately, `:211` logs the raw nonce.

**Impact:** Identical to the Google finding closed in `da4c39a`: an attacker
completes the flow with their own IdP account, replays `code`+`state` into a
victim's browser via a top-level navigation, and the callback plants the
attacker's session cookies in the victim's browser. Everything the victim then
does — including uploading a CV — lands in an account the attacker controls.

**Currently dormant.** `_require_naipunyam_provider()` 404s unless
`AUTH_PROVIDER=naipunyam`, and the deployed value is `local`. It becomes live
the day those credentials arrive, with no code change — and this router is on
the APSSDC government-bid path.

**Remediation:** Port the `sso_google.py` pattern in full: random state in Redis
with a short TTL, an httpOnly `SameSite=Lax` binding cookie whose SHA-256 is
stored beside the state, `hmac.compare_digest` **before** the token exchange and
before any session mint, PKCE S256, and the privileged-role rejection. Delete
the docstring note and the `state=` log field.

**Reference:** CWE-352 (CSRF), CWE-384 (Session Fixation), OWASP A01/A07,
RFC 6749 §10.12, RFC 6819 §4.4.1.13

---

### [HIGH-2] Self-hosted Piston executes candidate code in a privileged container with outbound network

**Location:** `scripts/piston-up.ps1:71`; documented at `docs/PISTON_SELFHOST.md:26,31`

```powershell
docker run --privileged -d --restart unless-stopped -p 2000:2000 `
  -v piston_packages:/piston/packages --tmpfs $tmpfs --dns 8.8.8.8 …
```

**Description:** The coding-exercise runner executes attacker-authored source.
`--privileged` disables the container isolation that is the entire control, and
`--dns 8.8.8.8` is a deliberate outbound egress path. The script's own comment
(`:21`) justifies `--privileged` as "REQUIRED: Piston sandboxes each run with
isolate/nsjail" — nsjail needs specific capabilities, not blanket privilege, so
the justification is broader than the requirement.

**Impact:** Candidate-submitted code running with full container capability and
network reachability. Realistic consequences: container escape to the Docker
host, lateral movement to the loopback-bound services on the same host, and
outbound exfiltration of anything the process can read. Note this is the
**self-hosted** path; the deployed default is the hosted provider, which bounds
current exposure — but the script is committed, documented as the way to run
locally, and a developer host holds live cloud credentials.

**Residual risk to record even after the fix:** the public `emkc.org` Piston
fallback and the hosted JDoodle provider both execute candidate code on
third-party infrastructure we do not control and cannot isolate. That is an
accepted-risk decision, not something a container flag fixes, and it should be
stated explicitly in `docs/PISTON_SELFHOST.md`.

**Remediation:** Drop `--privileged` (grant only the capabilities nsjail needs,
e.g. `--cap-add=SYS_ADMIN` scoped, or run with `--security-opt seccomp=…`);
remove `--dns` and run the execution container on an internal network with no
egress; document the per-job network-namespace lockdown so a reviewer can verify
that executed code cannot reach the network.

**Reference:** CWE-250 (Execution with Unnecessary Privileges), CWE-693,
OWASP A05

---

### [MEDIUM-1] `trusted_proxy_count` has no bounds, and an out-of-range value degrades silently

**Location:** `services/data_gateway/app/config.py:266`

```python
trusted_proxy_count: int = 0
```

**Description:** This value is the divisor of the client-IP hop arithmetic in
`consent.py:_extract_client_ip` (`real_index = len(hops) - trusted_proxy_count`).
It is a bare `int` with no `Field(ge=…, le=…)`. Set too high, every request takes
the `real_index < 0` branch and resolves to the socket peer; set to 0, XFF is
ignored entirely. Either way every client collapses to a single identity, and
there is **no log line, metric or alarm** when it happens.

**Impact:** Per-IP rate limiting on `/auth/login`, `/auth/register`,
`/auth/forgot-password`, `/auth/reset-password` and the interview magic-link
redeem all key on this value, so a misconfiguration silently turns per-client
limits into one global bucket. Not remotely triggerable — it needs an operator
error — which is why this is MEDIUM.

**Remediation:** `Field(ge=0, le=4)`; add a one-time WARNING the first time the
`real_index < 0` branch is taken, so a wrong topology announces itself instead
of being discovered later. Cover both in a new `tests/unit/test_config.py`
alongside `_normalise_app_env`.

**Reference:** CWE-1284 (Improper Validation of Specified Quantity in Input),
OWASP A05

---

### [MEDIUM-2] Per-IP rate limiting fails open on any Redis error

**Location:** `services/data_gateway/app/rate_limit.py:40-42`

```python
except Exception as exc:  # noqa: BLE001 — Redis down / any error → fail open
    log.warning("rate_limit.skipped", bucket=bucket, error_type=type(exc).__name__)
    return
```

**Description:** Any Redis failure disables rate limiting for the duration.
This is a deliberate and defensible availability trade-off — the same fail-open
choice the revocation-epoch check makes — but it is worth stating plainly
because the two controls fail open *together*: an Upstash outage simultaneously
removes brute-force protection on `/auth/login` and the immediate token-
revocation kill switch.

**Impact:** During a cache outage, credential-stuffing and magic-link brute
force are unthrottled. The window is bounded by the outage, and the compensating
controls (bcrypt cost 12, 256-bit opaque tokens, uniform 404s) remain — so this
is a hardening gap, not an open door.

**Remediation:** Keep fail-open for read paths. Consider failing **closed** on
the credential endpoints specifically, or degrading to a coarse in-process
limiter so the floor is never zero. At minimum, alert on
`rate_limit.skipped` — a silent security-control outage should page someone.
Explicitly re-affirm the trade-off in the module docstring so the next reviewer
knows it is chosen rather than inherited.

**Reference:** CWE-636 (Not Failing Securely), OWASP A04/A07

---

### [MEDIUM-3] `/metrics` has no application-layer authentication

**Location:** `services/data_gateway/app/main.py:343-348` (mirrored in `admin_ops`)

**Description:** The Prometheus scrape endpoint is registered with no auth
dependency. It is 403'd at both edges (`Caddyfile:46`, `space/Caddyfile:60`), so
it is not internet-reachable on either supported deployment — but there is no
in-app control, and `render.yaml` describes a deploy topology with **no proxy in
front of the services at all**, where each backend gets its own public hostname.

**Impact:** Per-endpoint request counts, status-code distribution and latency
histograms — business-sensitive volume and error telemetry. Not PII: UUID path
segments are normalised to `{id}` at `main.py:278-284` (which makes the root
`Caddyfile:40` comment about "user/session-id cardinality labels" stale).

**Remediation:** Gate on a shared-secret header or bind the metrics app to a
separate internal port, so the control does not depend solely on an edge config
that a second deploy path does not include. Separately: if `render.yaml` is
dead, delete it — a committed blueprint eventually gets applied.

**Reference:** CWE-497 (Exposure of System Data to an Unauthorized Sphere),
OWASP A05

---

## 3. DPDP Act 2023 / India data residency

> **Owner direction:** DPDP data-plumbing remediation is deferred pending
> database consolidation (see the deferred-work note). This table records
> current state for the audit trail; it is not a remediation request.

| Control | Status | Evidence |
|---|---|---|
| Consent ledger — recorded before processing | **PASS** | `dpdp_consent_ledger` written on the consent gate and atomically with SSO account creation (`sso_google.py:541`); `consent_guard.py` blocks an interview without a live consent row. |
| Consent ledger — automated test coverage | **FAIL** | The only tests need a live Postgres that CI does not provide, so the ledger and the `/auth/refresh` CSRF check have zero automated coverage. Deferred with the DB consolidation. |
| Erasure endpoint — reachable and authorised | **PASS** | `POST /users/{id}/dpdp/delete`, `AdminDep`-gated, routed to `admin_ops` on both edges. |
| Erasure — turns, resumes, scorecards, sessions | **PASS** | `erasure_executor.py:162,179,244,261` hard-delete; applicants anonymised at `:277`. |
| **Erasure — candidate-derived embeddings** | **FAIL** | See below. |
| Erasure — object storage (R2) | **FAIL** | Non-current resume PDFs and now scorecard PDFs remain in R2; the executor deletes no objects. Known, deferred. |
| Right to erasure — token revocation on erase | **PASS** | `erasure.py:304` `_revoke_all_tokens`; epoch TTL now outlives refresh tokens (`566b2ea`). |
| Retention — 90-day purge | **FAIL** | `retention.py` leaves scorecards and their PDFs; `scorecards` has no FK to `sessions`. Known, deferred. |
| Data residency — primary datastore | **PASS** | Neon `ap-southeast-1`. Tier-2 target is AWS Mumbai per `Final_stack.md`. |
| Data residency — LLM/speech processing | **CONDITIONAL** | Gemini and Sarvam process transcript and resume text outside India-only guarantees. Documented as a demo-tier trade-off; Tier-2 moves to Bedrock Mumbai + Bhashini. Not a new finding. |
| Data residency — avatar provider | **CONDITIONAL** | Tavus is explicitly recorded in `CLAUDE.md` as demo-only, no India residency. Unchanged. |
| PII in logs | **PASS** | `_redact_pii_processor` installed ahead of the renderer in all four services; magic-link tokens now stripped from Caddy access logs (`45ec2c2`); Sentry scrubs query strings, breadcrumbs and frame locals (`b04d257`). |

### Embedding-erasure gap (new this pass)

**Location:** `services/admin_ops/app/erasure_executor.py:275-288`

The applicants anonymisation sets `full_name`, `email`, `resume_text`,
`resume_s3_key` and `user_id` — but **not `embedding`**. The string "embedding"
appears **zero times** in the executor.

`applicants.embedding` is a `halfvec(3072)` written by
`hr_applicants._embed_applicant`, whose input is `applicant.resume_text` — so it
is a dense vector derived directly from the data subject's CV, and it survives
an erasure that nulls the text it came from. Embedding-inversion research shows
meaningful reconstruction of source text from such vectors, so treating them as
non-personal derived data is not defensible under DPDP §12 (right to erasure).
It also keeps the erased applicant semantically searchable via
`GET /hr/applicants?q=`, which is a user-visible failure of the erasure promise.

**Remediation (later phase):** add `embedding = NULL` to the same UPDATE, or
document the retention basis explicitly if the vector is genuinely
non-reversible for this model and dimension — and if so, record who assessed
that and on what evidence.

**Reference:** DPDP Act 2023 §12 (right to erasure), CWE-212 (Improper Removal
of Sensitive Information Before Storage or Transfer)

---

## 4. Verdict

> **⚠ This verdict is HISTORICAL (2026-08-06) and no longer applies.** Both
> HIGH findings were fixed at `352f366` and re-verified at HEAD on 2026-08-07 —
> see the reconciliation table at the top of this file. Repeated here because a
> reader arriving on a deep link to §4 would otherwise read a live production
> block. The block below is preserved, not corrected: it is the record of why
> the fixes were required.

```
Verdict for production deploy: BLOCKED — on HIGH-1 and HIGH-2.
```

**HIGH-1 (Naipunyam SSO)** does not block the *current* Space deploy, since the
provider is unreachable while `AUTH_PROVIDER=local`. It **must** block any
deploy that enables that provider, and because enabling it is an environment
change rather than a code change, the gate has to live in the code — not in a
release checklist. Fix it before the credentials arrive, not after.

**HIGH-2 (Piston)** blocks any deployment or developer workflow that uses the
self-hosted runner. The hosted provider is the current default, which bounds
today's exposure, but the script is committed and documented as the local path.

The three MEDIUMs do not block. MEDIUM-1 and MEDIUM-2 are hardening of controls
that already work; MEDIUM-3 is defence in depth behind an edge block that is
correct on both supported topologies.

**Everything closed in the Sprint-5 hardening cycle holds** under adversarial
re-reading (§1), including the two that did not hold at first pass and were
re-fixed: `require_password_changed` (defined but wired to nothing) and the
`can_publish_data` regression test (passed when the guard was deleted). The
three `CLAUDE.md` load-bearing invariants — agent tools read/draft-only,
`PanelVerdict` human-only, session-derived tenancy — are structurally intact.
