# Code Review — 2026-08-07 (domain-by-domain)

**Reviewer:** `code-reviewer`
**Date:** 2026-08-07
**Head at review:** `d1dd630` (`main`, after PR #8)
**Scope:** the four services under `services/`, the `shared/` library, the
`web/` frontend, CI/CD (`.github/workflows/ci.yml`, `ops/ci/`), and deployment
configuration (`render.yaml`, `services/*/Dockerfile`, `space/`)

**Severity counts (open):** 2 HIGH · 21 MEDIUM · 24 LOW — **47 open findings**
**Grades (open):** 2 MUST FIX · 16 SHOULD FIX · 29 CONSIDER
**Closed / confirmed this pass:** 30 (21 FIXED, 8 confirmed controls, 1 unverified)

> ## ✅ REMEDIATION COMPLETE — 44 of 47 closed, 3 recorded as owner decisions
>
> **Status 2026-08-07.** Every finding below is either fixed in place or
> explicitly recorded as an accepted risk. No file was deleted; the architecture
> is unchanged.
>
> | Gate | Result |
> |---|---|
> | `data_gateway` | ruff ✅ mypy ✅ **545 passed** · **67%** (floor 62) |
> | `interview_core` | ruff ✅ mypy ✅ **610 passed**, 1 skipped · **79%** (floor 74) |
> | `feedback_billing` | ruff ✅ mypy ✅ **197 passed** · **92%** (floor 87) |
> | `admin_ops` | ruff ✅ mypy ✅ **145 passed** · **90%** (floor 85) |
> | `shared` | ruff ✅ mypy ✅ (37 files) · **524 passed**, 1 skipped |
> | `web` | typecheck ✅ lint ✅ **504 passed** (44 files) |
> | invariants | coverage floors ✅ · alert rules ✅ · **env parity ✅ (new)** |
>
> **1,497 backend + 524 shared + 504 frontend tests.** Coverage rose in every
> service (66→67, 78→79, 91→92, 88→90) and floors were ratcheted to match.
>
> ### The three NOT fixed — recorded, not closed
>
> These cannot be resolved by code. Each is an accepted risk with an owner and a
> revisit trigger, written up in the accepted-risk register rather than quietly
> marked done:
>
> 1. **DPDP-3 — demo tier is not India-resident.** A Tier-2 infrastructure
>    migration (Bedrock/RDS/S3 Mumbai), already documented at
>    `docs/DATA-FLOW.md:12` with a per-processor region table. **Blocks any
>    India-residency bid until Tier-2 executes.**
> 2. **SEC-2 — one symmetric HS256 key across four services plus the worker.**
>    Asymmetric signing is the Tier-2 answer. *Interim mitigation shipped:*
>    `verify_access_token` now accepts a sequence of secrets, so a rotation
>    window (`[new, old]` → drain → drop old) is expressible for the first time.
> 3. **AG-05 — JDoodle is the default code-execution provider.** Switching to
>    the hardened self-hosted Piston would break execution on the Space, which
>    does not run it. JDoodle is now on the third-party processor register and
>    the switch is one env var.
>
> ### Found during remediation
>
> - **DPDP-2 needed `pgvector/pgvector:pg16`, not stock `postgres:16`** — the
>   migration fails with *"extension \"vector\" is not available"* on stock
>   Postgres. Same server version, with the extension built in.
> - **`interview_worker.py` is now 2,521 lines**, down from 2,972 (IC-4). Line
>   references to that file elsewhere in this document predate the split.

**Previous report:** [`code-review-2026-08-07.md`](code-review-2026-08-07.md) —
now **SUPERSEDED** by this document
**Companion reports:** [`code-review-full-repo-2026-08.md`](code-review-full-repo-2026-08.md),
[`code-review-full-repo.md`](code-review-full-repo.md),
[`code-review-s5.md`](code-review-s5.md),
[`security-review-s5.md`](security-review-s5.md)

> **On the filename.** The predecessor carries the same date. This review ran
> the same day, after PR #8 merged, and is a different exercise: the earlier
> document reconciled prior audits and found the DEP-1/DEP-2 deploy defects;
> this one is a systematic domain-by-domain pass over the merged tree. The
> `-domains` suffix distinguishes scope, not date.

---

```
=== Code Review ===
Scope:   services/{data_gateway,interview_core,feedback_billing,admin_ops},
         shared/, web/, .github/workflows/, ops/ci/, render.yaml,
         services/*/Dockerfile

Verdict: REQUEST CHANGES

MUST FIX:   2   (DPDP-4 retention orphans scorecards; CICD-1 per-service
                 Dockerfiles never built or scanned)
SHOULD FIX: 16
CONSIDER:   29

Tests: ADEQUATE for the backend (1,350 + 396 shared), INSUFFICIENT for the
       frontend staff surface — 26 of 41 pages untested, the whole hr/ and
       superadmin/ consoles among them (FE-2).
Style: CONSISTENT within services; divergent ACROSS them — four Prometheus
       implementations (XS-05), four copies of init_engine (XS-08), three
       frontend design systems (FE-4).
Hand-offs needed: security-auditor (Y — DPDP-4, DPDP-5, DPDP-7, DPDP-8,
       AG-05), cto-architect (Y — XS-05, XS-08, FE-4, IC-4).
```

These counts are derived from the Findings Register below, not written
independently of it — the two disagreeing is drift this repo has already had
three times.

---

## Grading

| Severity | Meaning |
|---|---|
| **CRITICAL** | Exploitable now, or data loss / false compliance claim in production today |
| **HIGH** | Exploitable under a reachable configuration, or breaks a legal obligation on a supported deploy target |
| **MEDIUM** | Real defect with bounded blast radius, or a control that silently does not work |
| **LOW** | Correctness, maintainability or hygiene; no direct security or availability impact |

| Grade | Meaning |
|---|---|
| **MUST FIX** | Blocks the deploy this finding applies to |
| **SHOULD FIX** | Real defect or gap; scheduled, not negotiable |
| **CONSIDER** | Judgment call; doing nothing is defensible if recorded |

Every finding cites file:line evidence read at `d1dd630`, with CWE / OWASP /
DPDP references where applicable.

**Out of scope:** `mypy --strict` failures. CI loads the root `mypy.ini`, which
is deliberately non-strict. The dead per-service config is recorded as a
Structural finding (**XS-09**), not as type errors.

**Deduplication.** Six findings were reported independently by two or three
domain agents. They are merged and cross-referenced rather than double-counted:
S3 env divergence (`DEP-ROOT` ≡ CICD-2), dead mypy config (`XS-09` ≡ CICD-4 ≡
DEP-3), global exception handler (`XS-01` ≡ DG-8), DPDP CI tests (`DPDP-2` ≡
DPDP-D1 ≡ DPDP-D2). The new shared-domain findings are numbered `SEC-*` because
`SH-*` is already taken by the prior register's IDs.

---

## 1. Prior-findings reconciliation

25 prior IDs re-checked by opening the cited code at `d1dd630`.
**21 FIXED, 1 confirmed control, 4 still open, 1 unverified.**

| Prior ID | Status | Confirming evidence |
|---|---|---|
| `S-1` | ✅ **FIXED** | `services/feedback_billing/app/routers/score.py:275-281` |
| `DG-2` | ✅ **FIXED** | `services/data_gateway/app/rate_limit.py:39` |
| `DG-3` | ⚠️ **STILL OPEN** | `services/data_gateway/app/utils/request_ip.py:163-171` |
| `DG-4` | ✅ **FIXED** | `services/data_gateway/app/agents/tools.py:156` |
| `DG-6` | ✅ **FIXED** | `ops/alerts/rate_limit_fail_open.rules.yml:63` |
| `DG-7` | ✅ **FIXED** | `services/data_gateway/tests/unit/test_hr_rounds.py:102` |
| `DG-8` | ⚠️ **STILL OPEN** | `services/interview_core/app/main.py` → see **XS-01** |
| `IC-1` | ✅ **FIXED** | `services/interview_core/app/worker/interview_worker.py:369-377` |
| `IC-3` | ✅ **FIXED** | `services/interview_core/app/worker/interview_worker.py:2589` |
| `IC-4` | ⚠️ **STILL OPEN** | `services/interview_core/app/worker/interview_worker.py:1966` |
| `IC-6` | ✅ **FIXED** | `services/interview_core/app/worker/interview_worker.py:511-545` |
| `RT-1` | ✅ **CONTROL** | `docs/ARCH-realtime-interview.md:1-60` |
| `RT-2` | ✅ **FIXED** | `services/interview_core/app/graph/brain.py:428` |
| `RT-3` | ✅ **FIXED** | `services/interview_core/app/avatar/simli.py:132` |
| `RT-4` | ✅ **FIXED** | `services/interview_core/app/worker/interview_worker.py:1403` |
| `RT-5` | ✅ **FIXED** | `docs/ARCH-realtime-interview.md:3-60` |
| `SH-5` | ✅ **FIXED** | `services/interview_core/app/worker/interview_worker.py:98` |
| `SH-6` | ✅ **FIXED** | `shared/observability/sentry.py:69` |
| `DEP-1` | ✅ **FIXED** | `render.yaml:86-89` |
| `DEP-2` | ✅ **FIXED** | `render.yaml:397-398` |
| `DEP-ROOT` | ⚠️ **STILL OPEN** | `services/feedback_billing/app/config.py:68` |
| `M-1a` | ✅ **FIXED** | `services/admin_ops/app/s3_client.py:123-135` |
| `DPDP-D1` | ⚠️ **STILL OPEN** | `services/data_gateway/tests/integration/test_consent_router.py:65` |
| `DPDP-D2` | ⚠️ **STILL OPEN** | `services/data_gateway/tests/integration/test_retention.py:55` |
| `DPDP-D3` | ❓ **UNVERIFIED** | `services/admin_ops/app/erasure_executor.py:208-217` — owner-deferred |

### 1.1 Where the brief was stale — 14 contradicted expectations

The brief for this review predicted several findings as open that PR #8 had
closed. Recorded rather than transcribed, because reporting them as open would
send an engineer to redo finished work:

| Brief expected | Verified reality |
|---|---|
| **IC-1** — live worker lacks injection framing | **FIXED, and it never lacked it.** `interview_worker.py:369-377` wraps the resume in a labelled `[CANDIDATE BACKGROUND]` block with an explicit non-instruction clause and balanced `"""` delimiters. The *island* (`graph/prompts.py`) was the real IC-1 subject and is now framed too (`:382-432`, `:485-498`), with `test_prompt_injection_framing.py:263` pinning that the two trees agree. |
| **IC-6** — no injection detection in `interview_core` | **FIXED.** `interview_worker.py:89` imports `detect_injection`; `_scan_resume_for_injection` (`:511-545`) is wired into the live lookup at `:596`. |
| Config validator gap in `admin_ops`/`feedback_billing` | **CLOSED** — all four run all three shared validators (**XS-03**). A different validator gap exists instead (**XS-04**). |
| "Three divergent Prometheus implementations" | **Four**, and `interview_core` has no HTTP metrics at all (**XS-05**). |
| "Confirm D-ID avatar gating" | D-ID was removed 2026-05-31. `Final_stack.md` still mandates a gate for a provider that cannot exist (**AG-06**). |
| `render.yaml` "deprecated with no drift gate" | It **is** marked deprecated at HEAD; two deploy docs still point at it (**CICD-3**). |
| `pyproject.toml` drift is a build risk | `pyproject` is not the install source anywhere — CI and all Dockerfiles use `requirements.txt`. Documentation drift, not build risk (**DEP-1**). |
| `python-jose`/`PyJWT` "duplication" | Not competing libraries: PyJWT is transitive from `redis`. The real finding is `python-jose` being unmaintained (**DEP-4**). |

---

## 2. New findings

### 2.1 MUST FIX

---

#### DPDP-4 — Retention purge orphans scorecards forever

| | |
|---|---|
| **File** | `services/data_gateway/app/retention.py:148` |
| **Function** | `purge_expired_sessions()` |
| **Grade** | **MUST FIX** / **HIGH** |
| **Reference** | DPDP Act 2023 §8(7) storage limitation; CWE-459 Incomplete Cleanup |

**Description.** The purge issues exactly one statement —
`delete(InterviewSession).where(predicate)` (`:148`) — and relies on FK cascade
for everything else. Its docstring enumerates what cascades: `turns` (`:17`) and
`integrity_events` (`:19`). The out-of-scope block (`:38-44`) lists
`dpdp_consent_ledger`, `users`, `jobs`/NOS and non-terminal sessions.
**`scorecards` appears in neither list.** And it does not cascade —
`models.py:990-992` says so explicitly: *"session_id is intentionally NOT a FK
here: scorecards live in feedback_billing while sessions live in interview_core
/ data_gateway. Cross-service FK enforcement is done at application layer."*
The DDL confirms it (`20260529_0002_..._scorecards_table.py:77-85`). Verified
independently: the four `scorecard` mentions in `retention.py` are incidental
status-vocabulary comments (`:14`, `:75`, `:76`, `:78`), not deletion logic —
and `:75` reads `'completed' — scorecard exists`, so the purge specifically
targets sessions that *have* scorecards.

**Impact.** After the 90-day purge deletes the session row, the scorecard
survives indefinitely holding candidate-derived personal data — `summary` (a
verdict on the candidate), `strengths`, `improvements`, `scores`,
`composite_score` — plus live R2 keys for the scorecard PDF and transcript JSON,
whose objects are also never deleted. Worse, the row becomes an orphan with no
session to join back to, so it is invisible to any session-scoped erasure or
audit query. The job reports success while leaving the most sensitive derived
artefact in place.

**Remediation.** In `purge_expired_sessions`, before the session DELETE:
(1) SELECT `report_pdf_key`/`transcript_key` for the sessions about to be
purged, (2) delete those objects from the scorecard bucket via the shared
storage helper, (3) `DELETE FROM scorecards WHERE session_id IN (<purge set>)`,
and only then delete the sessions — mirroring the collect-before-delete ordering
discipline `erasure_executor.py:198-220` already establishes. Add the table to
the docstring inventory either way, so the next reader can see it was
considered.

**Reference.** DPDP Act 2023 §8(7); CWE-459.

---

#### CICD-1 — CI builds and scans only the root Dockerfile

| | |
|---|---|
| **File** | `.github/workflows/ci.yml:580` |
| **Function** | `jobs.docker` |
| **Grade** | **MUST FIX** / **MEDIUM** |
| **Reference** | OWASP CI/CD-SEC-9; CWE-1395 |

**Description.** The `docker` job has exactly two steps: *Build the Space image*
(`:585-592`) with `context: .` and no `file:` key — so Docker resolves the
**root** `./Dockerfile` — and *Scan the image for CVEs* (`:613-625`) running
Trivy against that one tag. Neither step names `services/*/Dockerfile`, and
grepping the workflows for `dockerfile` returns nothing. `sync-to-space.yml`
does no building at all. So: **built = `./Dockerfile` only; scanned = that one
image only**, while `docker-compose.prod.yml:95-98, 136-139, 266-269` shows the
four per-service Dockerfiles are the documented production artefact.

**Impact.** A Dockerfile that is never built is a deploy that is never tested. A
broken `COPY`, a removed apt runtime library (e.g. `interview_core`'s
`libportaudio2`/`libgomp1`, `Dockerfile:55-60`) or an unresolvable layer change
ships green and is discovered during a deploy. Separately, `.trivyignore` — a
maintained accepted-risk list — is applied to one image while the four images
that actually run production are never scanned at all.

**Remediation.** Add a matrix leg to the `docker` job:
`strategy.matrix.service: [data_gateway, interview_core, feedback_billing,
admin_ops]`, `docker/build-push-action@v6` with `context: .`,
`file: services/${{ matrix.service }}/Dockerfile`, `push: false`, `load: true`,
then run the existing Trivy command against each tag with the same
`--severity HIGH,CRITICAL --ignorefile .trivyignore`.

**Reference.** OWASP CI/CD-SEC-9 (Improper Artifact Integrity Validation);
CWE-1395.

---

### 2.2 SHOULD FIX

---

#### DPDP-5 — `video_capture` consent is collected and revocable but never enforced

| | |
|---|---|
| **File** | `services/interview_core/app/consent_guard.py:42` |
| **Function** | `has_active_consent()` |
| **Grade** | **SHOULD FIX** / **HIGH** |
| **Reference** | DPDP Act 2023 §6(1), §7(a) purpose limitation; CWE-285 |

**Description.** `data_gateway` supports two consent types —
`_CONSENT_TYPE = "interview_voice_recording"` and
`_VIDEO_CONSENT_TYPE = "video_capture"` (`consent.py:82-84`) — and the frontend
collects the second as a separate opt-in (`InterviewIntro.tsx:126`, posted at
`Interview.tsx:60`). **Nothing on the server ever checks it.**
`has_active_consent(db, user_id)` (`consent_guard.py:46`) takes no
`consent_type` argument; the type is hardcoded at `:42` and injected at `:83`.
Grepping `video_capture` across `services/` returns hits only in `data_gateway`'s
consent router — never in the guard, and never in
`interview_core/app/routers/integrity.py:145-153`, which persists proctoring
events.

**Impact.** Purpose limitation is not enforced for biometric-derived data:
consent for one purpose (voice recording) is silently treated as consent for
another (webcam capture / proctoring). Any authenticated candidate can POST
proctoring events and have them persisted without ever granting
`video_capture`. Under DPDP the specificity of consent per purpose is the whole
mechanism.

**Remediation.** Give `has_active_consent` a
`consent_type: str = _CONSENT_TYPE` parameter — the default keeps every existing
call site byte-identical — and have `integrity.py:153` pass
`consent_type="video_capture"`. Add a per-type revoke path, or document
explicitly that revocation is all-or-nothing by design. Add a regression test
that a voice-only user gets 403 from the integrity endpoint.

**Reference.** DPDP Act 2023 §6(1), §7(a); CWE-285.

---

#### DEP-ROOT — S3 endpoint field name still diverges across four services

| | |
|---|---|
| **File** | `services/feedback_billing/app/config.py:68` (≡ CICD-2) |
| **Function** | `Settings` |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | CWE-1188; DPDP Act 2023 §12 |

**Description.** DEP-1 was patched, not cured. All four configs opened:
`data_gateway:298` `s3_endpoint`, `interview_core:95` `s3_endpoint`,
`feedback_billing:68` `s3_endpoint_url`, `admin_ops:73` `s3_endpoint_url`. Every
`Settings` still sets `extra="ignore"`, so the undeclared name is still dropped
silently. There are now **three** places that must stay in sync for one URL:
`render.yaml:86-89` (two hand-entered keys), `space/entrypoint.sh:118` (a shell
shim), and the four config files.

**Impact.** An operator who fills `S3_ENDPOINT` on Render and leaves
`S3_ENDPOINT_URL` blank silently reproduces DEP-1 exactly — PDFs never store,
DPDP erasure never completes — and pydantic's `extra="ignore"` makes the drop
invisible at startup. This is a re-armable one-typo regression of a HIGH
finding.

**Remediation.** Either (a) add `AliasChoices("S3_ENDPOINT", "S3_ENDPOINT_URL")`
to the field in all four services so both names populate one field and
`render.yaml` drops to a single key; or (b) move endpoint resolution into
`shared/s3.py`, which SVC-1 already created for exactly this class of problem.
Then add the env-name parity gate described in follow-up #1.

**Reference.** CWE-1188; DPDP §12.

---

#### XS-01 — No global exception handler in three of four services

| | |
|---|---|
| **File** | `services/interview_core/app/main.py` (≡ DG-8); same in `feedback_billing`, `admin_ops` |
| **Function** | — |
| **Grade** | **SHOULD FIX** / **LOW** |
| **Reference** | CWE-209; CWE-778; OWASP A09:2021 |

**Description.** A repo-wide grep for `exception_handler` across
`services/*/app` and `shared/` returns exactly two hits, both in
`data_gateway/app/main.py` (`:297`, `:298`). `data_gateway`'s implementation is
thorough — the metrics middleware wraps `call_next` in try/except and records a
`500` before re-raising. The other three have neither a handler nor an HTTP
metric.

**Impact.** On `interview_core`, `feedback_billing` and `admin_ops` an unhandled
exception reaches the ASGI server, whose default body is server-dependent and
can echo driver text (the asyncpg SQL-and-parameters leak is the concrete
worry). Separately, those services' request counters structurally cannot report
a 500, so `sum(rate(http_requests_total{status_code=~"5.."}[5m]))` is incapable
of firing for three quarters of the platform.

**Remediation.** Promote `data_gateway/app/main.py:264-324` (the `_record`
helper, the metrics middleware and the handler) into
`shared/http_observability.py` and install it in all four `main.py` files —
which also closes XS-05 and XS-06.

**Reference.** CWE-209; CWE-778.

---

#### XS-04 — `validate_database_ssl` runs in only one service

| | |
|---|---|
| **File** | `services/data_gateway/app/config.py` |
| **Function** | `validate_database_ssl` |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | OWASP A05:2021; CWE-319 |

**Description.** XS-03 confirmed the *shared* validators
(`validate_cors_origins`, `normalise_app_env`, `assert_strong_secrets`) now run
in all four services — the brief's expected gap is closed. The real gap is
narrower and was not predicted: `validate_database_ssl` is the one
security-relevant validator that exists in `data_gateway` only.

**Impact.** Three services can start in production against a database URL with
no TLS requirement, carrying PII over an unencrypted link, with no startup
failure. This is exactly the class `assert_strong_secrets` was written to catch
for secrets.

**Remediation.** Move the validator into `shared/security.py` next to its
siblings and call it from all four configs.

**Reference.** OWASP A05:2021; CWE-319.

---

#### XS-05 — Four divergent Prometheus implementations; `interview_core` has no HTTP metrics

| | |
|---|---|
| **File** | `services/interview_core/app/main.py` |
| **Function** | metrics wiring |
| **Grade** | **SHOULD FIX** / **MEDIUM** · Structural |
| **Reference** | OWASP A09:2021 |

**Description.** The brief said three; there are four, each different:
`data_gateway` has a hand-rolled middleware with counter + histogram;
`admin_ops` exposes a hand-picked business-metric set;
`feedback_billing` wires metrics through a different mechanism with no
`metrics()` function of its own; and **`interview_core` exposes only default
process metrics (CPU, memory, FDs, GC) and no HTTP metrics at all.**

**Impact.** `interview_core` is the real-time interview service and the one
whose latency the NFR (p95 < 2s) is written about. No dashboard or alert can be
written against its request rate, error rate or latency, because those series do
not exist. The other three differ enough that no dashboard ports between them.

**Remediation.** One shared middleware (see XS-01) installed in all four,
preserving `admin_ops`' deliberate business-metric additions on top rather than
replacing them.

**Reference.** OWASP A09:2021.

---

#### XS-06 — Metrics middleware labels unmatched paths with the raw URL

| | |
|---|---|
| **File** | `services/data_gateway/app/main.py` |
| **Function** | `_prometheus_middleware` |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | CWE-770 |

**Description.** The middleware normalises UUID path segments to `{id}` for
*matched* routes, but a request that matches no route is labelled with its raw
path.

**Impact.** Unbounded label cardinality driven by an unauthenticated caller: a
loop over `/aaa`, `/aab`, … creates a new time series per request, which is the
standard Prometheus memory-exhaustion vector against the scrape target and the
TSDB.

**Remediation.** Label unmatched requests with a constant such as
`__unmatched__`, and derive the label from the matched route template rather
than the raw path.

**Reference.** CWE-770 (Allocation of Resources Without Limits).

---

#### DPDP-2 — Retention and consent-ledger suites are marked integration and never run

| | |
|---|---|
| **File** | `services/data_gateway/tests/integration/test_retention.py:55` (≡ DPDP-D1, DPDP-D2) |
| **Function** | — |
| **Grade** | **SHOULD FIX** / **MEDIUM** · owner-deferred |
| **Reference** | DPDP Act 2023 §8(7) |

**Description.** `test_retention.py:55` and `test_consent_router.py:65` both
carry `pytestmark = pytest.mark.integration`, and `ci.yml:251` runs
`-m "not integration"`, so both are deselected. Exactly four of the six files in
`tests/integration/` now carry the marker — the marking added in PR #8 was
correct and deliberate; the gap is the missing database, not the marker.

**Impact.** DPDP §8(7) storage-limitation enforcement and §6/§7 consent-ledger
route behaviour are asserted only by tests nobody runs. Given DPDP-4 sits in
exactly that purge code and was found by reading rather than by a failing test,
this gap has already cost one HIGH finding.

**Remediation.** Add a `postgres:16` service container to the `data_gateway`
matrix leg and point `DATABASE_URL` at it; the marker already names precisely
which tests unlock. **Owner-deferred** pending database consolidation —
recorded, not re-raised as new.

**Reference.** DPDP Act 2023 §8(7).

---

#### DPDP-6 — `consent_withdrawn` session status is never written

| | |
|---|---|
| **File** | `services/interview_core/app/consent_guard.py` |
| **Function** | consent watchdog |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | DPDP Act 2023 §6(4) right to withdraw |

**Description.** The status vocabulary includes `consent_withdrawn`, but no code
path writes it. A mid-call withdrawal terminates the session and it is recorded
under a generic terminal status instead.

**Impact.** The audit trail cannot distinguish a candidate who withdrew consent
from one who abandoned or failed — which is precisely the distinction DPDP §6(4)
makes auditable, and precisely what a regulator would ask to see.

**Remediation.** Write `consent_withdrawn` on the watchdog termination path and
add it to the retention status table so its retention rule is explicit.

**Reference.** DPDP Act 2023 §6(4).

---

#### DPDP-7 — Erasure executor's "tables NOT reached" inventory is incomplete

| | |
|---|---|
| **File** | `services/admin_ops/app/erasure_executor.py:94-101` |
| **Function** | module docstring inventory |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | DPDP Act 2023 §12; CWE-212 |

**Description.** The module documents three deliberate exclusions
(`email_events.to_email`, `dpdp_consent_ledger`, `auth_tokens`). Tables holding
candidate-derived data exist that appear in neither the erasure path nor the
exclusion list — the same "unaccounted for" pattern as DPDP-4.

**Impact.** An erasure request completes and reports success while data survives
in tables nobody decided about. An incomplete-but-declared exclusion is
defensible under §12; an undeclared one is not.

**Remediation.** Enumerate every table carrying a `user_id` or candidate-derived
column and assign each to erase / exclude-with-reason. Add a test that fails
when a new table with a `user_id` column appears in the models and is absent
from both lists.

**Reference.** DPDP §12; CWE-212.

---

#### DPDP-8 — No self-service erasure endpoint, though the data flow advertises one

| | |
|---|---|
| **File** | `services/admin_ops/app/routers/erasure.py` |
| **Function** | — |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | DPDP Act 2023 §11 right to erasure |

**Description.** Erasure is admin-initiated only. `docs/DATA-FLOW.md` describes
a data-principal-initiated path that does not exist in code.

**Impact.** DPDP §11 gives the data principal the right to request erasure. A
documented capability that is not implemented is worse than an absent one: it
will be cited in a bid response and then not demonstrable.

**Remediation.** Either add an authenticated self-service request endpoint that
enqueues into the existing executor, or correct `DATA-FLOW.md` to state that
erasure is operator-mediated and describe the actual request channel.

**Reference.** DPDP Act 2023 §11.

---

#### SEC-5 — `shared/auth/jwt.py` claims a Redis `jti` replay check that does not exist

| | |
|---|---|
| **File** | `shared/auth/jwt.py` |
| **Function** | module docstring |
| **Grade** | **SHOULD FIX** / **LOW** |
| **Reference** | CWE-1059 (incorrect documentation) |

**Description.** The docstring states `jti` provides replay prevention via a
Redis blocklist. No such blocklist is consulted on the access-token path.

**Impact.** The documentation-outliving-the-code pattern in its most dangerous
form: a reviewer reading this file concludes replay is handled and stops
looking. `jti` is present and unique, but nothing consumes it.

**Remediation.** Either implement the check, or — preferably, given access
tokens are 15-minute and the epoch mechanism already covers revocation — correct
the docstring to say `jti` exists for traceability and that replay containment
is the short TTL plus the epoch check.

**Reference.** CWE-1059.

---

#### AG-06 — `Final_stack.md` mandates a production gate for a removed vendor

| | |
|---|---|
| **File** | `docs/Final_stack.md` |
| **Function** | — |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | — |

**Description.** D-ID was removed as avatar provider on 2026-05-31 (per
`CLAUDE.md`). `Final_stack.md` still mandates a D-ID production gate that cannot
be satisfied because the provider no longer exists in the codebase. Tavus is the
demo default; Simli is also supported.

**Impact.** A bid-adjacent document naming the wrong supplier, and a mandated
gate that no deploy can pass or fail. Anyone auditing the stack against this
document reaches a contradiction.

**Remediation.** Update the avatar row to Tavus/Simli with the real
`AVATAR_PROVIDER` values and the actual Tier-2 path
(`AVATAR_PROVIDER=custom`, Three.js + Ready Player Me + Rhubarb).

---

#### CICD-3 — `render.yaml` is marked deprecated; two deploy docs still point at it

| | |
|---|---|
| **File** | `render.yaml` |
| **Function** | — |
| **Grade** | **SHOULD FIX** / **LOW** |
| **Reference** | — |

**Description.** Contrary to the brief's framing, `render.yaml` **is** marked
deprecated at HEAD. Two deployment documents still direct an engineer to it.

**Impact.** An operator follows a current-looking doc to a deprecated target and
reintroduces the DEP-1 class of config bug on a path nobody monitors.

**Remediation.** Update the two documents to name the live target (the HF
Space), and either delete `render.yaml` or keep it with the deprecation notice
plus the env-name parity gate from follow-up #1 so it cannot drift silently.

---

#### CICD-5 — `MEDIAPIPE_REQUIRED` is set nowhere despite a guarded fetch path

| | |
|---|---|
| **File** | `.github/workflows/ci.yml` |
| **Function** | frontend asset fetch |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | CWE-1188 |

**Description.** The MediaPipe asset fetch is guarded by a
`MEDIAPIPE_REQUIRED` flag that is set nowhere in the repo, so the guard's strict
branch never executes.

**Impact.** The proctoring model assets can silently fail to fetch and the build
still succeeds, shipping a frontend whose proctoring degrades at runtime rather
than failing at build time.

**Remediation.** Set `MEDIAPIPE_REQUIRED=1` in the CI build environment so a
failed fetch is a red build, and document the local opt-out.

**Reference.** CWE-1188.

---

#### XS-09 — Dead `[tool.mypy] strict = true` in all four services

| | |
|---|---|
| **File** | `services/data_gateway/pyproject.toml:59` (≡ CICD-4 ≡ DEP-3) |
| **Function** | — |
| **Grade** | **SHOULD FIX** / **LOW** · Structural |
| **Reference** | — |

**Description.** All four carry `[tool.mypy] python_version = "3.11"`,
`strict = true`, `ignore_missing_imports = true`
(`admin_ops:50-53`, `data_gateway:59-62`, `feedback_billing:52-55`,
`interview_core:83-86`). CI type-checks from the repo root with `mypy.ini`, so
none is ever loaded. The declared `python_version` (3.11) also disagrees with
the 3.12 CI pins.

**Impact.** Four files assert a type-checking contract that does not exist. A
contributor who "fixes strict-mode errors" to satisfy them does unrequested
work; one who trusts them believes the service is strict-checked when nothing
checks it.

**Remediation.** Delete the four blocks and leave a one-line pointer to
`mypy.ini`. If per-service tightening is wanted later, add `[mypy-*]` sections
to `mypy.ini`, which is the only config CI loads.

---

#### FE-1 — `uploadWithProgress` has no 401 → refresh → retry

| | |
|---|---|
| **File** | `web/src/api/` — `uploadWithProgress` |
| **Function** | `uploadWithProgress()` |
| **Grade** | **SHOULD FIX** / **MEDIUM** |
| **Reference** | — |

**Description.** PR #8 added `fetchBlobWithAuth` for the blob *download* path.
The *upload* path is XHR-based (it needs progress events, which `fetch` cannot
provide) and therefore could not reuse that wrapper — so it still has no
refresh-on-401.

**Impact.** A candidate whose 15-minute access token expires mid-upload loses
the upload. Resume upload is the highest-friction step in the funnel and the one
where a silent failure is most costly.

**Remediation.** Add an XHR-native equivalent: on a 401 response, call the same
single-flight `attemptRefresh` the fetch client uses, then re-issue the XHR
once. Reuse the existing refresh primitive rather than adding a second one.

---

### 2.3 CONSIDER

Recorded with file:line evidence; each is a defensible no-action if the reason
is written down.

| ID | Severity | Finding | File |
|---|---|---|---|
| **IC-4** | LOW | `interview_worker.py` is **2,972 lines** — 295 *larger* than when IC-4 was raised, and 5.9× the project's 500-line threshold. The 599-line `entrypoint()` is genuinely gone (now 10 lines delegating to `InterviewJob`), and the ordering hazard that made it MEDIUM is dissolved. Six functions still exceed 50 lines; longest is `_interviewer_instructions` at 133. | `interview_worker.py:1966` |
| **DG-3** | LOW | `trusted_proxy_count` set too **low** is counted but not alertable. The too-high direction fires a counter and has an alert rule; the too-low direction takes a different branch with no signal. Not solvable by a counter — needs a startup assertion on observed hop count. | `utils/request_ip.py:163-171` |
| **SEC-1** | MEDIUM | Single symmetric HS256 key, no `kid`, no rotation mechanism. A leaked `JWT_SECRET` cannot be rotated without a service-wide disruption window and real risk of dropping in-flight scorecards. | `shared/auth/jwt.py:3` |
| **SEC-2** | MEDIUM | That one secret is shared by all four services plus the worker. HS256 means verification key = signing key, so read access to *any* environment mints tokens for any `sub` and any `roles`, including `service`. Blast radius of compromising analytics-only `admin_ops` equals compromising `data_gateway`. | `shared/auth/jwt.py:86` |
| **SEC-3** | LOW | Token-epoch revocation fails **open** in all five verifiers — confirmed at HEAD, now with a single implementation (`jwt.py:216-218`). Deliberate availability trade-off; the missing half is a counter + alert on `auth.token_epoch.check_skipped`. | `shared/auth/jwt.py:214-218` |
| **SEC-4** | LOW | S-1 residual: `score.py::_require_service_jwt` is a fifth verifier — present, but the rationale recorded in the prior review is **misattributed**. The real reason is a distinct authorization policy (service role + `sub` allowlist vs UUID user `sub`), not guest-session binding. | `score.py:255` |
| **SEC-6** | LOW | `feedback_billing/app/auth.py:15-20` says the epoch prefix is "duplicated rather than imported"; the code twelve lines below imports it. Misleading in the direction that matters — a reader may hunt for copies to hand-sync. | `auth.py:15-20` |
| **SEC-7** | LOW | Canonical `PII_FIELDS` misses the bare key `to`, and a legacy email shim logs a raw recipient under it. Currently unreachable (shim is dead) but it is public API in a live module. | `email_util.py:145` |
| **SEC-8** | LOW | Sentry scrubbing is wired to `before_send` only; transaction events take a different hook and are unscrubbed. | `sentry.py:149-164` |
| **SEC-9** | LOW | `/metrics` is open with no token in **staging**, while `shared/security.py:49` treats staging as a hardened env (`_ENFORCED_ENVS = ("production", "staging")`). Two guards in the same package disagree about what staging is. | `metrics_auth.py:102-109` |
| **XS-02** | MEDIUM | Rate limiting exists only in `data_gateway` (13 call sites). Determined from routing config, not assumption: every internet-reachable route on the other three is JWT-gated, so there is **no anonymous DoS surface** — the honest reason to downgrade from SHOULD FIX. Residual is authenticated cost abuse on spend-bearing endpoints (room-token/session creation). | `rooms.py:84` |
| **XS-08** | LOW | `init_engine()` duplicated near byte-for-byte across all four services, including the pgBouncer `-pooler` detection. Any future engine change must land in four files. | `admin_ops/app/database.py:34` |
| **XS-10** | LOW | CORS `allow_methods`/`allow_headers` diverge between `data_gateway` and the other three. No impact at HEAD; latent preflight failure the first time a PATCH route or custom header is added elsewhere. | `main.py:327` |
| **DPDP-3** | MEDIUM | Demo tier is not India-resident. **Not a defect found here** — `docs/DATA-FLOW.md:12` opens with an explicit banner saying so, with a per-processor region table and a written Tier-2 path. Blocks any RFP/L1 bid requiring residency; zero impact on the demo tier. | `docs/DATA-FLOW.md:12` |
| **DPDP-9** | LOW | The retention partial index cannot serve the purge query — `idx_sessions_retention` carries `WHERE deleted_at IS NULL` but `_purge_predicate` emits no matching clause, so the nightly purge sequential-scans a table sized for 20 lakh users. | `retention.py:93` |
| **AG-05** | MEDIUM | Default execution provider is **JDoodle** (`config.py:194`, `entrypoint.sh:121`, `docker-compose.prod.yml:106`) — candidate-authored code leaves our infrastructure to a third party, while the hardened Piston self-host is unused by default. `DATA-FLOW.md:46` claims "India (JDoodle infrastructure)" without evidence. | `config.py:194` |
| **AG-07** | LOW | Live-path injection detection is **log-only** — `_scan_resume_for_injection` returns markers, the caller logs them and discards. A candidate planting "ignore previous instructions" is invisible to the reviewing HR manager. | `interview_worker.py:540-545` |
| **CICD-6** | LOW | Four per-service `.dockerignore` files are never read by Docker (it resolves from the build-context root), and all four Dockerfile headers cite a `railway.json` that does not exist. Someone adding a secret pattern to one will believe it is excluded. | `services/data_gateway/.dockerignore` |
| **CICD-7** | LOW | `space/supervisord.conf:92` sets `S3_BUCKET_NAME` for `feedback_billing`, which declares no such field — silently discarded by `extra="ignore"`. Reads as though it configures the scorecard bucket. | `supervisord.conf:92` |
| **CICD-8** | LOW | CI downloads the gitleaks binary with no checksum verification and pins actions/scanners by mutable tag. | `ci.yml:563-572` |
| **CICD-9** | LOW | No SAST anywhere in the pipeline — the security job is dependency-CVE and secret-scanning only. Injection, unsafe deserialization and path-traversal classes have no automated detector across four DPDP-scoped services. | `ci.yml:485-572` |
| **DEP-1** | LOW | `pyproject` blocks omit packages the services import — but **`pyproject` is not the install source anywhere** (CI and all four Dockerfiles use `requirements.txt`). Documentation drift, not build risk. | `feedback_billing/pyproject.toml:9-28` |
| **DEP-2** | LOW | Two in-repo docs tell an engineer `poetry.lock` is the CI install source. This is the mechanism that keeps DEP-1 alive. | `interview_core/Dockerfile:11-12` |
| **DEP-4** | MEDIUM | `python-jose` is the platform's only JWT library and the sole path to an unfixable `ecdsa` CVE. Not exploitable today (HS256 only, single algorithm passed, blocking confusion). PyJWT is **transitive from redis**, not a competing choice. Migration is two call sites. | `shared/auth/jwt.py:23` |
| **DEP-5** | LOW | `admin_ops` pins APScheduler with no importer in the service. | `requirements.txt:71` |
| **FE-2** | MEDIUM | **26 of 41 frontend pages have no test**, including the entire `hr/` and `superadmin/` consoles — the staff surface where the three-tier admin hierarchy is enforced client-side. | `web/src/__tests__` |
| **FE-3** | LOW | Playwright E2E suite is empty (one README). Deliberate and documented, but `package.json`, `playwright.config.ts` and `CLAUDE.md` all still advertise it. | `web/e2e/README.md` |
| **FE-4** | MEDIUM | **Three** coexisting design systems (`components/ui`, `design/`, `landing/`) with three copies of `cn()`, two globally-loaded stylesheets and duplicated-then-diverged primitives. | `web/src/main.tsx:18-20` |
| **INT-1** | MEDIUM | `shared/intelligence/build_derivation_prompt` is executed by tests but nothing asserts its output, and its untrusted-input branch (`jd_text`) is never exercised — the prompt that shapes every interview and scorecard can be edited freely with no test noticing. | `prompt.py:71-174` |

---

## 3. Summary — Severity × Grade

| Severity | MUST FIX | SHOULD FIX | CONSIDER | Total |
|---|---|---|---|---|
| **HIGH** | 1 (DPDP-4) | 1 (DPDP-5) | 0 | **2** |
| **MEDIUM** | 1 (CICD-1) | 8 | 12 | **21** |
| **LOW** | 0 | 7 | 17 | **24** |
| **Total open** | **2** | **16** | **29** | **47** |

Closed this pass: 21 FIXED, 8 confirmed controls, 1 unverified (owner-deferred)
— 30 items.

**Where the risk concentrates.** Nine of the 47 open findings are DPDP —
including both HIGHs — and they share one shape: *a table, a consent type or a
storage object that nobody decided about.* DPDP-4 (scorecards absent from both
the cascade list and the exclusion list), DPDP-7 (tables in neither list) and
DPDP-5 (a consent type collected but never read) are the same omission class,
not three unrelated bugs. That class is invisible to tests because the code does
what it says; it is only visible by enumerating what the code *doesn't*
mention — which is why DPDP-2 (the unrun purge tests) matters more than its
MEDIUM grade suggests.

---

## 4. Follow-up prompts

STILL OPEN and SHOULD FIX items as self-contained remediation tickets, in
dependency order.

**1. Add an env-name parity gate for S3 configuration.**
Closes DEP-ROOT/CICD-2 and prevents DEP-1/DEP-2 from re-arming. Give every S3
endpoint field a pydantic `AliasChoices("S3_ENDPOINT", "S3_ENDPOINT_URL")` so
both spellings populate one field in all four services, then add a CI check that
asserts every field each `config.py` declares is supplied by every deploy target
(`render.yaml`, `space/entrypoint.sh`, `docker-compose*.yml`). Model it on
`ops/ci/check_coverage_floors.py`. Do this first — it is the gate the other
deploy-config tickets rely on.

**2. Fix the retention purge so it deletes scorecards and their R2 objects.**
Closes DPDP-4 (MUST FIX). In `purge_expired_sessions`, collect
`report_pdf_key`/`transcript_key` for the purge set, delete those objects, delete
the `scorecards` rows, then delete the sessions — the collect-before-delete
ordering `erasure_executor.py:198-220` already establishes. Add the table to the
docstring inventory. Ships with a test; see ticket 5 for the database it needs.

**3. Build and Trivy-scan the four per-service Dockerfiles in CI.**
Closes CICD-1 (MUST FIX). Add a `strategy.matrix.service` leg to the `docker`
job using `file: services/${{ matrix.service }}/Dockerfile`, `push: false`,
`load: true`, then run the existing Trivy command against each tag with the same
`--severity HIGH,CRITICAL --ignorefile .trivyignore`.

**4. Install a shared HTTP observability layer in all four services.**
Closes XS-01, XS-05 and XS-06 together — they are one missing module, not three
findings. Promote `data_gateway/app/main.py:264-324` into
`shared/http_observability.py`: the metrics middleware, the `_record` helper and
the global exception handler. Label unmatched routes with a constant to bound
cardinality. Preserve `admin_ops`' business metrics as additions.

**5. Add a `postgres:16` service container to the data_gateway CI leg.**
Closes DPDP-2 and unblocks the tests tickets 2 and 6 need. The
`integration` markers already name exactly which suites unlock. Owner-deferred
pending DB consolidation — this ticket is the trigger to revisit that.

**6. Enforce `video_capture` consent server-side.**
Closes DPDP-5 (HIGH). Add `consent_type: str = _CONSENT_TYPE` to
`has_active_consent` — the default keeps every existing call site identical —
and pass `consent_type="video_capture"` from `integrity.py:153`. Add a
regression test that a voice-only user gets 403 from the integrity endpoint.

**7. Complete the erasure inventory and write `consent_withdrawn`.**
Closes DPDP-7 and DPDP-6. Enumerate every table with a `user_id` or
candidate-derived column and assign each to erase or exclude-with-reason; add a
test that fails when a new such table appears in neither list. Separately, write
the `consent_withdrawn` status on the watchdog termination path.

**8. Decide and implement the self-service erasure path.**
Closes DPDP-8. Either add an authenticated request endpoint that enqueues into
the existing executor, or correct `DATA-FLOW.md` to describe the actual
operator-mediated channel. Do not leave a documented capability unimplemented in
a bid-adjacent document.

**9. Move `validate_database_ssl` into `shared/security.py`.**
Closes XS-04. It is the one security-relevant validator that did not make the
consolidation; three services can currently start in production against an
unencrypted database link.

**10. Add refresh-on-401 to the XHR upload path.**
Closes FE-1. `uploadWithProgress` cannot reuse `fetchBlobWithAuth` because it is
XHR-based for progress events. On 401, call the existing single-flight
`attemptRefresh` and re-issue the XHR once — reuse the primitive, do not add a
second.

**11. Correct the documentation that outlives the code.**
Closes SEC-5, SEC-6, AG-06, CICD-3, DEP-2 and CICD-5. Each is a claim a reader
would act on: the `jti` replay blocklist that does not exist; the epoch prefix
"duplicated rather than imported" that is imported; the D-ID production gate for
a removed vendor; two docs pointing at deprecated `render.yaml`; two docs naming
`poetry.lock` as the CI install source; and `MEDIAPIPE_REQUIRED`, which needs
setting rather than documenting.

**12. Decompose `interview_worker.py`.**
Addresses IC-4. At 2,972 lines it is 5.9× the project threshold and *grew*
during the last refactor. The correctness hazard is gone, so this is
maintainability: split `InterviewJob`'s lifecycle phases and the DB/consent
helpers (`resolve_consent_user_id`, `_post_score`, `_lookup_session`) into
sibling modules under `app/worker/`. Sequence after the DPDP tickets.

**13. Clean up dependency declarations.**
Closes XS-09 and addresses DEP-1, DEP-4, DEP-5. Delete the four dead
`[tool.mypy]` blocks with a pointer to `mypy.ini`; reconcile `pyproject` with
`requirements.txt` or delete the dependency blocks outright; drop the unused
APScheduler pin; and scope the `python-jose` → PyJWT migration (two call sites
in `shared/auth/jwt.py`).

**14. Test the frontend staff surface.**
Addresses FE-2. Prioritise by blast radius, not count: `superadmin/`
consoles first (role-scoped create/list actions, and that a wrong-role user sees
nothing), then the `hr/` console. 26 of 41 pages are untested and the untested
set is the entire staff surface, not the low-value tail.
