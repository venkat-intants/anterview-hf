# Full-Repository Code Review — August 2026 Cycle

**Reviewer:** `code-reviewer`
**Review date:** 2026-08-07 · **Head at review:** `551f776`
**Remediation date:** 2026-08-07 · **Status: all findings closed**
**Predecessor register:** [`code-review-full-repo.md`](code-review-full-repo.md) (2026-08-06, head `45e25ea`)

```
=== Code Review ===
Scope:   services/interview_core, services/data_gateway,
         services/feedback_billing, services/admin_ops,
         shared/, web/
Verdict: APPROVE
```

**MUST FIX:** none found.
**SHOULD FIX:** 8 found — **8 resolved.**
**CONSIDER:** 10 found — **10 resolved.**

**Tests:** ADEQUATE. 1,176 backend + 252 shared + 326 frontend tests pass.
Every finding that changed behaviour shipped with a regression test. The RT-4
gap that made this INSUFFICIENT at review time (no hard-crash coverage) is
closed by `tests/unit/test_worker_crash_recovery.py`.

**Style:** CONSISTENT. The two exceptions noted at review — deprecated
`@app.on_event` hooks and divergent metrics wiring — are both resolved.

**Hand-offs needed:** `security-auditor` **(Y)** — DPDP carry-overs only, see
[Hand-offs](#hand-offs). `cto-architect` **(Y)** — RT-1 decision taken and
recorded below; no further action pending.

---

## Remediation summary

Every finding in this register is closed. Nothing was deleted: the owner's
direction was to harden the existing architecture in place, so the resolution
for RT-1 is a recorded architectural decision rather than a removal.

| Area | Findings | Resolution |
|---|---|---|
| Real-time interview | RT-1 … RT-5 | Split documented as deliberate; island held to production standard (retry + fallback, cancellation cleanup); worker crash recovery added |
| Business logic | BL-2 | `job_title` / `experience_level` now scanned and framed |
| Shared / cross-service | SH-1 … SH-6 | Redis + `/metrics` auth extracted to `shared/`; pooling, CORS, TTL, lifespan unified |
| Frontend | FE-1 … FE-3 | `RoleRoute`, interview-scoped ErrorBoundary, `fetchBlobWithAuth` |
| Carried open from baseline | S-6, S-7, S-9, M-6 | All four closed |

### Gate results after remediation

| Service | ruff | mypy | pytest | coverage | floor |
|---|---|---|---|---|---|
| `data_gateway` | pass | pass | 398 passed | 60% | 55 |
| `interview_core` | pass | pass | 521 passed, 1 skipped | 76% | 71 |
| `feedback_billing` | pass | pass | 161 passed | 89% | 84 |
| `admin_ops` | pass | pass | 96 passed | 84% | 79 |
| `shared` | pass | pass (26 files) | 252 passed | — | — |
| `web` | pass (0 warnings) | pass | 326 passed (28 files) | — | build + audit pass |

Run with each service's `.env` hidden and the CI env block supplied, per
[`CONTRIBUTING.md`](../CONTRIBUTING.md) — green locally is not green in CI
otherwise.

### CI changes made during remediation

Three gaps in `.github/workflows/ci.yml` were found *by* the remediation and
fixed. They matter because each one meant a control that appeared to exist did
not:

1. **`shared/tests` was never run.** The `shared` job enumerates paths
   (`shared/intelligence shared/agents shared/auth`), so the new regression
   suites for `metrics_auth` and `redis_factory` — the `/metrics` security gate
   and the Redis hardening for all four services — existed, passed locally, and
   were invisible to CI. Added to the pytest invocation.
2. **The new shared modules were never type-checked.** Both are top-level
   modules, not packages, so the two directory arguments to mypy missed them.
   Named explicitly now.
3. **The coverage-floor table had already drifted from the enforced values**
   (documented 55/68/83/78 vs. enforced 50/62/78/72) — the exact failure the
   surrounding comment block was written to prevent. Table and `case` are back
   in step, with a note that they must change together, and the floors were
   ratcheted to the newly measured numbers minus a 5-point margin.

---

## How this document is graded

Adopted verbatim from [`code-review-full-repo.md`](code-review-full-repo.md).

| Grade | Meaning |
|---|---|
| **MUST FIX** | Blocks the deploy this finding applies to. Requires sign-off from the named reviewer. |
| **SHOULD FIX** | Real defect or real gap; ship-blocking only in aggregate. Scheduled, not negotiable. |
| **CONSIDER** | Judgment call. Doing nothing is a defensible answer if the reason is recorded. |

Severity classes: **HIGH** / **MEDIUM** / **LOW** / **Structural** /
**Documentation-drift**.

Checklist order follows [`.claude/agents/code-reviewer.md`](../.claude/agents/code-reviewer.md):
**Correctness → Tests → Performance → Style → Anti-Patterns → API Contracts.**

This register records **only findings confirmed by opening the cited file** —
at review time and again after remediation. No resolution below is recorded on
an agent's report alone.

### Which project rules are enforced, and which are aspirational

| Rule | Source | Status |
|---|---|---|
| Single linear Alembic head | `CONTRIBUTING.md:16` | **Enforced** — `migrations` job |
| Container-image CVE scan against `.trivyignore` | `.trivyignore` | **Enforced** — Trivy 0.73.0 |
| Per-service line-coverage floor | `docs/LLD.md:2481` | **Enforced** — ratcheted 2026-08-07 |
| Python dependency CVE gate | `Final_stack.md` | **Enforced** — `pip-audit` |
| Frontend advisory gate | — | **Enforced** — `npm run audit:ci` |
| `shared/` regression suites | — | **Enforced as of 2026-08-07** — previously partial |
| ruff, pinned 0.7.4 | `CLAUDE.md` | **Enforced**, only from inside the service dir |
| **mypy strict** | per-service `pyproject.toml` | **Aspirational — dead config.** See below. |
| ≥80% coverage on new code | `.claude/agents/code-reviewer.md:42` | **Aspirational** — the CI floor is per-service measured, not a flat 80% |
| EN/HI/TE for all AI prompts | `CLAUDE.md` | **Enforced for candidate-facing prompts**; staff copilots English-only by design |
| Agents hold no write tools | `shared/agents/schema.py` | **Enforced structurally** — `ToolEffect` has only `read`/`draft` |

**On mypy — do not raise strict-mode failures as findings in this repo.** CI
invokes mypy from the repo root, which loads the root `mypy.ini`; that file is
deliberately **non-strict** and its header argues the case. The
`[tool.mypy] strict = true` blocks in all four service `pyproject.toml` files
are **never loaded** — dead configuration. Per-service tightening is tracked
work, not a defect. A module failing `mypy --strict` is **out of scope**.

---

## Previous reports

This register builds on, and does not restate, the following:

| Report | Covers |
|---|---|
| [`code-review-full-repo.md`](code-review-full-repo.md) | The baseline full-repo register (H-1…H-2, M-1…M-6, S-1…S-9, D-1…D-4) |
| [`code-review-s5.md`](code-review-s5.md) | Sprint-5 code review |
| [`security-review-s5.md`](security-review-s5.md) | Sprint-5 security audit |
| [`security-review-s3-004-s3-005.md`](security-review-s3-004-s3-005.md) | S3-004 / S3-005 narrow audit |
| [`security-review-s3-011.md`](security-review-s3-011.md) | S3-011 narrow audit |
| [`security-review-s4-bundle.md`](security-review-s4-bundle.md) | Sprint-4 bundle audit |

---

## Prior findings verification

Re-checked against the file named in the **Confirming file** column.

### Closed baseline

| ID | Finding | Status | Confirming file |
|---|---|---|---|
| **H-1** | Naipunyam SSO callback never validates `state` | **FIXED** | `sso_naipunyam.py` — Redis one-shot state (`:359-372`), httpOnly binding cookie (`:388-404`), PKCE (`:247-260`), `state_prefix` only in logs (`:288`) |
| **H-2** | Piston sandbox runs candidate code privileged | **FIXED** | `piston-up.ps1` — `--privileged` gone; explicit `--cap-add` allowlist (`:122-125`), per-job netns (`:81-82`), loopback bind (`:138`) |
| **M-2** | `trusted_proxy_count` unbounded | **FIXED** | `data_gateway/app/config.py:282` — `Field(default=0, ge=0, le=4)` |
| **M-4** | ILIKE wildcards unescaped | **FIXED** | `hr_applicants.py` — `_like_literal()` + `ESCAPE` on both paths (`:644-645`, `:754-756`) |
| **S-2** | `_mint_service_jwt` hand-rolls claims | **FIXED** | `shared/auth/jwt.py` gained `ttl_seconds`; worker delegates, TTL held at 60s |
| **D-1** | Alembic single-head gate claimed, not implemented | **FIXED** | `ci.yml` — `migrations` job runs `alembic heads` |
| **D-2** | `.trivyignore` maintained, no Trivy step | **FIXED** | `ci.yml` — Trivy 0.73.0 with `--ignorefile` |
| **D-4** | Coverage documented, nothing measured | **FIXED** | `ci.yml` — `--cov` + `--fail-under` per service |

### Obsolete

| ID | Finding | Status | Reason |
|---|---|---|---|
| — | Hand-rolled WebSocket audit findings against `routers/ws.py` | **OBSOLETE** | File no longer exists; LiveKit replaced the hand-rolled transport. Findings against a retired module are void, not fixed. |

### Carried open at review — now closed

| ID | Finding | Review status | Resolution |
|---|---|---|---|
| **S-1** | JWT dependency in four copies | still open | **Accepted trade-off**, re-affirmed — see SH-1 |
| **S-5** | Five near-identical route guards | still open | **RESOLVED** — see FE-1 |
| **S-6** | Health handler drift | still open — **3 of 4 copies leaked** | **RESOLVED** — no service returns the exception message |
| **S-7** | Malformed nested JSDoc | still open | **RESOLVED** — stray `/**` removed |
| **S-8** | No interview-scoped ErrorBoundary | still open | **RESOLVED** — see FE-2 |
| **S-9** | Applicant email typed `str` | still open | **RESOLVED** — `EmailStr` at the boundary |
| **M-6** | `/metrics` unauthenticated | still open | **RESOLVED** — `shared/metrics_auth.py`, all four services |

### Verified resolved before this cycle

| ID | Finding | Evidence |
|---|---|---|
| **M-5** | Scoring prompts lacked injection framing | Centralised in `feedback_billing/app/untrusted_input.py`; see BL-1 |
| **M-1** | Erasure orphaned R2 objects and left `applicants.embedding` | Key collection precedes deletion (`:198-221`); `embedding = NULL` (`:345`) with a regression test |

---

## Findings Register

All 14 new findings, each with its resolution. **RESOLVED** means re-verified by
opening the file after the change.

---

### RT-1 — Real-time interview exists as two parallel implementations

| | |
|---|---|
| **File** | `interview_core/app/graph/{brain,build,nodes}.py`, `app/agent/{orchestrator,livekit_agent}.py` vs. `app/worker/interview_worker.py` |
| **Grade / Severity** | **SHOULD FIX** / **Structural** · Checklist: Anti-Patterns |
| **Status** | ✅ **RESOLVED — recorded architectural decision** |

`worker/interview_worker.py` is the shipped path; its import block contains
none of the island. Tracing every import showed the island is **larger than
first recorded** — `app/avatar/` and `app/speech/` are reachable only from
`livekit_agent.py`/`orchestrator.py`, not from the worker, which uses the
LiveKit plugins instead. Every deployment entrypoint (`dev-up.ps1:79`, both
compose files, `render.yaml:242`, `Dockerfile`, `space/supervisord.conf`) runs
the worker.

**Impact.** Two implementations of the core loop, one unreachable but fully
maintained — every change to interview behaviour has two plausible landing
sites and no signal which is correct.

**Resolution — owner decision, 2026-08-07: keep both, delete nothing.**
`avatar/base.py`'s `AvatarTransport` is the seam the Tier-2
`AVATAR_PROVIDER=custom` migration builds on, and `speech/` holds the Sarvam
work (B-038 native script, v3 params, streaming reconnect) the Tier-2
self-hosted path needs. Removing them would mean rebuilding that seam.

The finding is therefore closed as a **documented, deliberate split** rather
than a removal, on three conditions, all now met:
1. `docs/ARCH-realtime-interview.md` carries an implementation-status banner
   naming the shipped path, listing the island, recording why it is kept, and
   giving a "which one do I change?" table (RT-5).
2. The island is held to production standard — RT-2 and RT-3 fixed *in* it.
3. `graph/{prompts,personas,state}.py` are called out as **not** island: the
   worker imports them, so changes there hit production immediately.

---

### RT-2 — `LLMError` from `generate_stream` propagated unhandled

| | |
|---|---|
| **File** | `interview_core/app/graph/brain.py` |
| **Function** | `InterviewBrain._stream_and_commit()` |
| **Grade / Severity** | **SHOULD FIX** / **MEDIUM** · Checklist: Correctness |
| **Reference** | CWE-703; OWASP A04 |
| **Status** | ✅ **RESOLVED** |

No `try`/`except` guarded the streaming iteration. `_stream_and_commit` is the
single funnel for both `first_question` and `follow_up`, so any adapter failure
escaped both and unwound into `livekit_agent.py`, which had no handler either.

**Impact.** One transient LLM failure — a Gemini 429, which this project hits
routinely on the ~10 RPM free tier — terminated the interview mid-session with
no retry and no fallback.

**Resolution.** Four parts, each load-bearing:
- `_is_transient()` branches on `exc.status` against a transient-status set,
  and for statusless errors on the `"network:"` prefix the adapters use. An
  "empty response" or `MAX_TOKENS` error is the model's verdict on *this*
  prompt and will reproduce — correctly not retried.
- **Retry only while nothing has been yielded.** `emitted` is set *before* the
  `yield`, because control leaves the coroutine there and the flag must already
  be true when a downstream failure is handled. Retrying after a partial stream
  would make the candidate hear the first half of the question twice.
- Bounded exponential backoff between attempts.
- On exhaustion the turn still emits a complete utterance — whatever streamed,
  plus a canned fallback — so the transcript equals what the candidate heard.
  The fallback **invites the candidate to speak**: the agent hands control back
  after every interviewer turn, so a turn ending in a bare apology would stall
  the session permanently. Provided in EN/HI/TE in native script per B-038,
  with English fallback for unknown codes.
- Logging carries status and counters only — never prompt, history or
  `exc.body` (provider error bodies echo the request back).

---

### RT-3 — Adapter connection-close on cancellation unverified

| | |
|---|---|
| **File** | `interview_core/app/avatar/simli.py` |
| **Grade / Severity** | **CONSIDER** / **LOW** · Checklist: Correctness / Performance |
| **Reference** | CWE-772 |
| **Status** | ✅ **RESOLVED** |

The barge-in design in `orchestrator.py` was **already sound** and was left
alone: each turn is its own cancellable task, `CancelledError` is swallowed at
the right level, and no half-turn is committed. `speech/sarvam_tts.py:192` was
also already correct — `async with httpx.AsyncClient(...)` releases on
cancellation.

The gap was `simli.py`: `close()` and `_cleanup_http()` existed, but no
`finally` guaranteed cleanup when `render()` was cancelled mid-flight.

**Resolution.** `except asyncio.CancelledError` arms added at each of the three
handshake await points — **necessary because `except Exception` does not catch
`CancelledError`**, which inherits from `BaseException`. Each cleans up and
re-raises; swallowing it would break cooperative cancellation. A `_closed` flag
makes `render()` after `close()` fail loudly rather than silently re-open a
writer nothing will close.

---

### RT-4 — Worker kept interview state in memory only

| | |
|---|---|
| **File** | `interview_core/app/worker/interview_worker.py` |
| **Function** | `InterviewState` |
| **Grade / Severity** | **SHOULD FIX** / **MEDIUM** · Checklist: Correctness, Tests |
| **Reference** | CWE-772; NFR 99.5% uptime |
| **Status** | ✅ **RESOLVED** |

Answer count, transcript and the single-fire close guard lived entirely in
process memory. Graceful shutdown was handled; a **hard** crash (OOM kill,
SIGKILL, node loss) was not — the session row stayed `in_progress` forever with
no resumption, no reaper and no durable idempotency guard. The candidate could
not rejoin, the unflushed transcript was lost, the close path never ran so no
scorecard was produced, and analytics accumulated permanently-live rows.

**Resolution.** Redis-backed checkpointing, deliberately **without** an Alembic
migration (`data_gateway` owns migrations; the existing schema sufficed):
- `to_checkpoint()` / `restore_checkpoint()` on `InterviewState`, persisted
  under `interview:checkpoint:<session_id>` with a 2-hour TTL against a
  ~10-minute session.
- Checkpoint writes are **best-effort with a 2s timeout** — a Redis failure
  logs and continues, matching the existing transcript-flush convention. A
  durability mechanism that can stall the interview is worse than the gap.
- A durable idempotency key guards close/scoring, replacing reliance on the
  in-memory flag alone.
- A startup sweep finalises sessions left `in_progress` with a stale
  checkpoint, marking them `abandoned` via the existing status vocabulary.

**Tests.** `tests/unit/test_worker_crash_recovery.py` (new) covers checkpoint
write, restore, double-fire idempotency, stale-session reaping, and Redis
failure not propagating. This is what moved Tests from INSUFFICIENT to
ADEQUATE.

---

### RT-5 — `docs/ARCH-realtime-interview.md` described the unwired implementation

| | |
|---|---|
| **Grade / Severity** | **SHOULD FIX** / **Documentation-drift** |
| **Status** | ✅ **RESOLVED** |

The document specified `InterviewBrain.next_turn()` driven by an orchestrator —
a precise description of the island, not of the shipped worker — and listed
work on that path as outstanding. A reader wanting to change interview
behaviour was directed straight to the dead modules.

**Resolution.** An implementation-status banner added at the top; the
2026-05-31 design record preserved intact below it, since it remains the
rationale for decisions still in force. The banner names the shipped path with
the five entrypoints that prove it, lists the island, records why it is kept,
flags which `graph/` modules are shared with production, and corrects the stale
claims by name.

---

### BL-1 — Prompt-injection framing on the scoring path: **adequate**

| | |
|---|---|
| **File** | `feedback_billing/app/untrusted_input.py` |
| **Status** | ✅ verified-adequate at review — **no action**, recorded so it is not re-raised |

M-5 was closed properly rather than locally patched: one module owns the
framing and all three consumers import it. `frame_untrusted` emits both open
and close markers (without a close marker the model has no signal for where
untrusted content ends). Truncate-then-scan ordering is correct — what is
inspected is exactly what the model sees. Detection deliberately logs rather
than strips: auto-stripping would mangle legitimate resumes ("Managed a team
responsible for system instructions") and train candidates to obfuscate.

---

### BL-2 — `job_title` / `experience_level` reached the prompt unframed

| | |
|---|---|
| **File** | `feedback_billing/app/{resume_scorer,scorer,exam_generator}.py` |
| **Grade / Severity** | **CONSIDER** / **LOW** · Checklist: Correctness |
| **Reference** | OWASP LLM01 |
| **Status** | ✅ **RESOLVED** |

At all three call sites `job_title` was passed to `scan_untrusted` as a
`**log_context` **keyword**, not as an entry in the `sources` dict — so it was
never scanned — and was then substituted raw into the template. The
`job_title=` kwarg at the `scan_untrusted` call actively misled a reader into
thinking it was covered.

Genuinely lower risk: these fields are HR-controlled, and `job_title` is capped
at `max_length=300` at the API boundary.

**Resolution.** Both fields are now entries in the scanned `sources` dict, and
a new `frame_untrusted_inline()` frames them at the point of substitution. The
deliberate judgement, recorded in the module: the multi-line BEGIN/END
delimiters of `frame_untrusted` read badly inside a one-line aligned header
(`Job : {{job_title}}`) and would degrade prompt quality, so short inline
fields get a lighter neutralisation while still being scanned. These prompts
drive scoring; the fix must not blunt them.

---

### BL-3 / BL-4 — Erasure executor, PDF rendering, analytics export: **adequate**

| | |
|---|---|
| **Status** | ✅ verified-adequate at review — **no action** |

- **`erasure_executor.py`** — both predecessor defects genuinely closed: key
  collection precedes deletion; `embedding = NULL` at `:345` with a regression
  test. The three remaining exclusions (`email_events.to_email`,
  `dpdp_consent_ledger`, `auth_tokens`) are **documented with reasons** at
  `:94-101` — recorded decisions, not silent omissions. They remain
  `security-auditor` territory.
- **`pdf_render.py`** — `_esc()` escapes untrusted text before ReportLab's
  markup parser, so an `<img>` in a candidate field renders as literal text
  rather than fetching a remote URL.
- **`analytics.py`** — parameterised SQL; `_SORT_WHITELIST` as defence-in-depth
  behind the `pattern=` validator with a safe fallback; `_csv_safe()` applied to
  every free-text cell (CWE-1236); export streams over a row generator rather
  than materialising the result set.

---

### SH-1 — Four parallel JWT dependency wrappers

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **Structural** · Checklist: Anti-Patterns |
| **Status** | ✅ **RESOLVED — accepted trade-off, re-affirmed** |

Still four files, 566 lines. **Extraction remains not recommended.** Phase 4 of
the predecessor cycle attempted it and `cto-architect` stopped it: the wrappers
return `User`, `dict` and `str`; one UUID-validates `sub`, which service tokens
(`sub="interview_core"`) would fail; one underpins a guest dependency reading
`session_id` off the raw payload. Standardising `interview_core` on `User`
would have made that comparison `None != str(...)`, returned 200 for everyone,
and silently undone guest session binding with nothing appearing broken. A
**fifth** copy exists at `feedback_billing/app/routers/score.py:254`.

**What mattered is already extracted:** the crypto core (`shared/auth/jwt.py`)
and the epoch check — the one piece whose divergence caused a live bug. What
remains is per-service request plumbing with different return contracts. A
shared helper would need three optional parameters to serve four callers, which
`.claude/agents/code-reviewer.md:61` names as its own anti-pattern.

---

### SH-2 — Redis hardening existed in one service of four

| | |
|---|---|
| **Grade / Severity** | **SHOULD FIX** / **MEDIUM** · Checklist: Correctness / Performance |
| **Reference** | CWE-1088; OWASP A04 |
| **Status** | ✅ **RESOLVED** |

`data_gateway` was hardened against Upstash idle-connection drops
(`health_check_interval`, `socket_keepalive`, bounded socket timeouts,
`Retry(ExponentialBackoff)`, `retry_on_error`). The other three had none — and
`interview_core`'s docstring falsely claimed it "Mirrors
`data_gateway/app/redis_client.py` exactly", which is part of the bug.

**Impact.** Upstash closes idle connections; without `health_check_interval`
the client hands out a dead pooled connection and the error surfaces as an
unhandled 500 — intermittent, correlated with idle periods, and the hardest
class of bug to attribute. `interview_core` was the worst placement: least
continuously loaded, and it holds the real-time path.

**Resolution.** New `shared/redis_factory.py::build_redis_client()`. **All
four** services now delegate — including `data_gateway`, which was left as the
last hand-copy until the verification pass caught it, so the settings have
exactly one definition. Values are byte-for-byte identical to the original, so
this is a zero-behaviour-change consolidation for `data_gateway` and a genuine
fix for the other three. Public API (`init_redis`/`close_redis`/`get_redis`)
unchanged. The false docstring claim is corrected.

---

### SH-3 — `admin_ops` missed the pooler-vs-direct branch

| | |
|---|---|
| **File** | `admin_ops/app/database.py` |
| **Grade / Severity** | **SHOULD FIX** / **MEDIUM** · Checklist: Performance |
| **Status** | ✅ **RESOLVED** |

Three services branch three ways; `admin_ops` branched two, applying `NullPool`
to any SSL connection instead of testing `"-pooler" in host`.

**Impact.** Against a direct (non-pgBouncer) endpoint, `admin_ops` opened a TCP
connection **plus a full TLS handshake per request** where its siblings reused a
pooled connection — on the analytics dashboard, which issues several queries per
page load and is the console a customer demo drives interactively.
`pool_pre_ping=True` could not help: there was no pooled connection to ping.

**Resolution.** The three-way branch ported verbatim from `data_gateway`
(identical in `feedback_billing` and `interview_core`), including the
`urlsplit` host extraction — so this did not become a fourth variant.
`tests/test_database_pool.py` asserts the pool class straight off the engine
without opening a connection, and additionally that the direct-SSL pool is
actually *sized* (a "real pool" of size 0 would reuse nothing).

---

### SH-4 — `data_gateway` kept a local copy of `validate_cors_origins`

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **LOW** · Reference: CWE-1041 |
| **Status** | ✅ **RESOLVED** |

S-3's consolidation had landed for three of four services; `data_gateway` — the
service with the widest public surface — was the holdout. The shared
implementation was confirmed behaviourally identical before switching (same
comma split, same `*`/`null` rejection, same scheme requirement); it now
delegates as the other three do.

---

### SH-5 — `SERVICE_TOKEN_TTL_SECONDS` value duplicated

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **LOW** · Reference: CWE-1041 |
| **Status** | ✅ **RESOLVED** |

Flagged for confirmation as possible dead code. **It was not dead** — three
live callers in `data_gateway` (`exam_ai_client`, `embedding_client`,
`scoring_client`). The real finding was narrower: `interview_core`'s
`_mint_service_jwt` correctly delegated to `issue_access_token` after the S-2
fix but passed a **locally defined** `_SERVICE_JWT_TTL_SECONDS`.

This matters more than the value agreeing today: a service token's `sub` is a
service name, so `logout_all` cannot revoke it — **its lifetime is its only
containment**. Now `_SERVICE_JWT_TTL_SECONDS = SERVICE_TOKEN_TTL_SECONDS`, so
the number has one definition and the local name still resolves for callers.

---

### SH-6 — Deprecated lifecycle hooks and inconsistent metrics wiring

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **LOW** · Checklist: Style |
| **Status** | ✅ **RESOLVED** |

`interview_core/app/main.py` used `@app.on_event`, deprecated by Starlette and
due to break on a major bump. Migrated to an async `lifespan` context manager
with startup/shutdown ordering preserved. Metrics wiring is now consistent
across all four services — the deliberate difference in *content*
(`admin_ops` exposes a hand-picked business set rather than default process
metrics) is preserved, since that was a considered choice, not drift.

---

### FE-1 — Five route guards unconsolidated

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **LOW** · Checklist: Anti-Patterns |
| **Status** | ✅ **RESOLVED** |

155 lines across five files implementing one guard-plus-spinner.

**Resolution.** One parametrized `RoleRoute`; **all five files kept** as thin
named wrappers, so every call site is untouched. The consolidated predicate was
checked by hand against each original, including the fail-closed `user === null`
path (`undefined ?? false` → redirect) and the byte-identical spinner markup.
`ProtectedRoute` passes no `roles`, so it stays authentication-only.

`RoleRoute.test.tsx` pins the **exact** admitted set per guard — each of the
four role guards is tested to redirect *every* other hierarchy role, so the
`denied` lists are complete complements over
`{platform_owner, super_admin, hr_manager, admin, candidate}`. That is what
makes this "exactly the role set" rather than merely "a role set".

Recorded as intentional, not a regression: `AdminRoute` denies an account
holding *only* `platform_owner`, matching the pre-refactor code — platform
owners are granted `admin` at provisioning. `InterviewSessionRoute.tsx` is a
different shape and was left alone.

---

### FE-2 — No ErrorBoundary scoped to the live interview

| | |
|---|---|
| **Grade / Severity** | **SHOULD FIX** / **MEDIUM** · Reference: CWE-755 |
| **Status** | ✅ **RESOLVED** |

`ErrorBoundary` was mounted only at the app root. React unmounts from the
nearest boundary, so any render error inside the interview panel — a malformed
transcript item, an unexpected avatar state, a null participant track — took
down the whole app and dropped the candidate out of a live, timed, paid session.

The predecessor graded this CONSIDER, reasoning that "a broken interview panel
is not a recoverable state anyway." **This review upgraded it**, on evidence
from RT-4: a client crash does *not* end the server-side session, so unmounting
the client is what made the loss permanent.

**Resolution.** A dedicated `InterviewErrorBoundary` wraps `LiveKitInterview`,
with a fallback offering **rejoin** rather than a generic error card — the
LiveKit room and server-side session survive a client render crash, and with
RT-4's checkpointing the worker can now resume. The root boundary stays as the
outer net; `App.tsx` untouched. The `fallback` prop is purely additive, so
existing usage is unchanged. All four i18n keys verified present in EN, HI
**and** TE.

*Note: the file was `web/src/pages/Interview.tsx`, not
`web/src/features/interview/Interview.tsx` as the review recorded.*

---

### FE-3 — Two raw `fetch` calls bypassed refresh-on-401

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **LOW** · Checklist: Style / Correctness |
| **Status** | ✅ **RESOLVED** |

`admin.ts::adminGetRaw` and `exams.ts::downloadQuestionTemplate` bypassed the
shared client. Both bypasses were **justified** — each returns a binary/streamed
body the JSON wrapper would mishandle — but neither retried after a token
refresh, so an expiring token produced a failed download.

**Resolution.** `fetchBlobWithAuth` in `client.ts` carries the same
refresh-on-401 logic while returning the raw `Response`; both call sites route
through it, keeping their exported names and signatures. The refactor also
tightened `attemptRefresh`: the single-flight slot is now cleared from
`.finally()` on the *stored* promise, and the no-CSRF short-circuit returns
without claiming the slot — both pinned by tests.

`api/sso.ts` is deliberately unchanged: its bypass is documented and correct,
since the SSO callback runs before any token exists.

---

### S-7 — Malformed nested JSDoc

| | |
|---|---|
| **Grade / Severity** | **CONSIDER** / **LOW** · **Status** | ✅ **RESOLVED** |

`proctorLogic.ts` had a bare `/**` immediately followed by a complete
`/** … */`, so tooling attributed the comment to the wrong symbol. The stray
opener is removed; cosmetic, no behaviour change.

---

## Summary — Severity × Grade

All findings closed. Counts are as-found at review.

| Severity | MUST FIX | SHOULD FIX | CONSIDER | Total | Open |
|---|---|---|---|---|---|
| HIGH | 0 | 0 | 0 | **0** | 0 |
| MEDIUM | 0 | 5 — RT-2, RT-4, SH-2, SH-3, FE-2 | 0 | **5** | **0** |
| LOW | 0 | 0 | 6 — RT-3, BL-2, SH-4, SH-5, SH-6, FE-3 | **6** | **0** |
| Structural | 0 | 1 — RT-1 | 1 — SH-1 | **2** | **0** |
| Documentation-drift | 0 | 1 — RT-5 | 0 | **1** | **0** |
| **Total (new)** | **0** | **7** | **7** | **14** | **0** |
| Carried open from baseline | 0 | 1 — S-6 | 3 — S-7, S-9, M-6 | **4** | **0** |

**Where the risk had concentrated.** Five of the seven SHOULD FIX findings —
RT-1, RT-2, RT-4, RT-5, FE-2 — were the same subsystem, and causally linked:
RT-1 was why RT-2 sat unnoticed in unreachable code, RT-5 was why the split
persisted, and RT-4 and FE-2 compounded each other. They were fixed as a set,
in that order.

---

## Notes carried forward (non-blocking)

Surfaced during remediation. Neither is a defect; both are recorded so they are
not rediscovered as findings.

1. **`admin_ops/tests/test_database_pool.py`** — the `clean_engine` fixture
   calls `dispose_engine()`, which clears `_engine` but deliberately leaves
   `_session_factory` set (matching `data_gateway` byte-for-byte). Harmless
   today: every later test that reaches the DB patches the session factory or
   the health check, and `/metrics` touches no DB. A future test calling
   `get_session_factory()` unpatched after this file would attempt a real
   connect instead of getting `RuntimeError`. Left unchanged rather than break
   deliberate cross-service parity.
2. **`data_gateway/tests/unit/test_hr_applicants.py::test_upload_accepts_absent_blank_and_valid_emails`**
   — passes for a slightly weaker reason than its docstring claims: the 400 it
   asserts comes from `_extract_pdf_text` rejecting the junk PDF, before the
   stubbed DB session is used. The test is correct and does prove what it needs
   to (email validation was not the rejector), but the stub is not load-bearing.

---

## Hand-offs

**`security-auditor` — (Y).** Only the DPDP carry-overs remain, deferred by
owner direction pending database consolidation. They are **not** findings of
this review:

| Item | Why it is theirs |
|---|---|
| DPDP R2 orphaning path | Object-storage lifecycle work beyond the executor fixes; compliance scope |
| DPDP 90-day retention purge | No scheduled purge job; DPDP §8(7) retention limitation |
| Consent-ledger test coverage | Same deferral |
| `erasure_executor` exclusions — `email_events.to_email`, `dpdp_consent_ledger`, `auth_tokens` | Documented decisions needing a compliance ruling, not a code fix |

**M-6 is no longer a hand-off** — `/metrics` now authenticates in all four
services. One **operational** action remains: set `METRICS_TOKEN` in the HF
Space secrets and the Railway/Render environments. Until it is set,
`APP_ENV=production` makes `/metrics` return 404 — safe, but the endpoint is
unavailable to a scraper. `.env.example` documents this in all five templates.

**`cto-architect` — (Y), discharged.** RT-1 decided and recorded; SH-1
re-affirmed as an accepted trade-off.

---

## Verdict

**APPROVE.**

All 14 findings from this cycle and all 4 carried open from the baseline are
closed and re-verified by opening each file. No MUST FIX was found at any point.

The subsystem that blocked approval at review — the real-time interview — now
has a crash-recovery story on both sides of the wire (Redis checkpointing with
a reaper server-side, a rejoin-capable boundary client-side), real degradation
on LLM failure instead of a dropped session, and an unambiguous answer to
"where does interview behaviour live?" recorded in the architecture doc. The
parallel implementation still exists by deliberate decision, but it is now
documented, signposted, and held to the same standard as the shipped path.

Two things are worth stating plainly about *how* this closed:

**The remediation found three CI gaps the review missed.** `shared/tests` never
ran, the new shared modules were never type-checked, and the coverage-floor
table had already drifted from the enforced values. Each meant a control that
appeared to exist did not — the same class of finding as the predecessor
cycle's D-1/D-2, and a reminder that a gate is only as real as the line that
invokes it.

**Coverage rose in every service** (59→60, 72→76, 87→89, 82→84) because the
fixes shipped with tests rather than despite them. The floors were ratcheted to
match, so the gains are now protected rather than merely achieved.
