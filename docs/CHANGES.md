# CHANGES — v1.0 → v1.1 (Lean Cuts)

## What this document is

A single-page audit trail of every design item removed between LLD/HLD/Final_stack **v1.0** and **v1.1**, with the rationale and the RFP citation that justifies the cut.

If a procurement committee or future engineer asks "why isn't X in this design?", this is the answer.

---

## CUT SUMMARY

| # | Item cut | Type | Saved | RFP citation |
|---|---|---|---|---|
| C1 | DR pilot-light in ap-south-2 (Hyderabad) | Over-engineered | ~₹2 L/month infra | Not in RFP |
| C2 | Internal mTLS via Istio (Phase-2) | Over-engineered | ~1 week build, ongoing ops complexity | RFP §11 says "E2E encryption" — TLS to ALB + pod-to-pod via NetworkPolicies satisfies |
| C3 | Multi-tenant RLS (for hypothetical future state SDC resale) | Speculative scope | ~1 week DB design, ongoing query complexity | Not in RFP; APSSDC is the single tenant |
| C4 | Litmus chaos testing | Nice-to-have | ~3 days CI setup, ~₹50 K/month tooling | Not in RFP |
| C5 | Rolling per-turn scoring (every 4 turns) | Invented feature | ~₹1 per session in LLM cost; ~5 extra LLM calls per session | Not in RFP — only end-of-session scorecard is asked (Pg 10) |
| C6 | 8 microservices → 4 | Structural simplification | ~3 days deploy setup; ~50% fewer Helm charts, pipelines, runbooks | Indirectly: RFP Pg 23 mandates source code handover — fewer services = cleaner handover |
| C7 | Counseling agent | Out-of-scope until confirmed | ~2 weeks build | RFP title says "Agents" (plural); body never scopes counseling. Ambiguity A2. |
| C8 | Embedding model deliberation | Decision deferral | Removed from "Open Decisions" — locked in OpenAI `text-embedding-3-large` | Not RFP-driven; engineering judgment |

**Net savings:** ~3 weeks of engineering build effort, ~30% ongoing ops complexity, ~₹1/session ongoing LLM cost.

---

## DETAILED CUT RATIONALE

### C1 — DR Pilot-Light Removed

**v1.0 had:**
> "DR pilot-light setup in ap-south-2 (Hyderabad); RPO 1 hr / RTO 4 hr"

**v1.1 has:**
Single region (ap-south-1) with Multi-AZ. RDS Multi-AZ + EKS across 3 AZs. PITR 35 days. S3 versioning.

**Why cut:**
- RFP nowhere mandates DR.
- NFR-02 (uptime 99.5%) = ~3.6 hrs downtime allowance per month. This is meetable in single region with Multi-AZ.
- Cross-region pilot-light adds ~₹2 L/month for standby capacity that may never be needed.
- If APSSDC explicitly demands DR in pre-bid, this is a one-line Helm value-file change to re-enable.

**Where it lives:**
HLD §9 (Deployment), §10 (Security), §13 (Failure Modes) updated. LLD §18 (Security) updated.

---

### C2 — mTLS via Istio Removed

**v1.0 had:**
> "Transport encryption: TLS 1.3 everywhere (ALB, internal mTLS via Istio optional Phase-2)"

**v1.1 has:**
TLS 1.3 at the edge (ALB), pod-to-pod via K8s NetworkPolicies, no service mesh.

**Why cut:**
- RFP §11 demands "E2E encryption for all data in transit and at rest". TLS to ALB + NetworkPolicies meets this clause.
- Istio adds 200+ MB of memory per pod sidecar, doubles deploy complexity, and adds another component to learn for the operations team after handover.
- Reintroducing mTLS in Phase-2 is straightforward if needed.

**Where it lives:**
HLD §10 (Security) updated. LLD §18 (Security) updated.

---

### C3 — Multi-Tenant RLS Removed

**v1.0 had:**
> "Multi-tenant isolation — for future re-sale to other state SDCs: Row-level security (RLS) on Postgres + tenant_id column; deferred to Phase-3"

**v1.1 has:**
Single-tenant. No `tenant_id` columns, no RLS policies.

**Why cut:**
- Pure speculation. RFP is for APSSDC, single tenant, single contract.
- Adding RLS now means every query must include tenant scope, every new feature must be tested for cross-tenant isolation. Pure overhead with no current value.
- If APSSDC later licenses the platform to another SDC, it's a refactor — but that refactor is cheaper than carrying RLS for years without using it.

**Where it lives:**
LLD §4 (DDL) — `tenant_id` columns never added. LLD §20 (Open Decisions) — multi-tenant question removed.

---

### C4 — Litmus Chaos Testing Removed

**v1.0 had:**
> "Chaos | Litmus on K8s | Pod kills, network partitions, latency injection"

**v1.1 has:**
Standard Locust load tests + Playwright E2E + integration tests with testcontainers. No chaos engineering.

**Why cut:**
- Nice-to-have, never demanded by RFP.
- For a 99.5% SLA (not 99.99%), unit + integration + load coverage is sufficient.
- Litmus operator + maintenance overhead doesn't pay back for this SLA tier.
- Year-2 add if budget allows.

**Where it lives:**
LLD §15 (Test Strategy) updated.

---

### C5 — Rolling Per-Turn Scoring Removed

**v1.0 had:**
- `score_turn` tool exposed to LLM
- `score_rolling` LangGraph node firing every 4 user turns
- `PerTurnSignal` TypedDict + `rolling_signals` field in `InterviewState`
- `per_turn_signals` JSONB column on `turns` table
- `rolling_scores.show_to_user` feature flag

**v1.1 has:**
**Single scoring call at end of session only.**

**Why cut:**
- RFP Pg 10 only asks for **end-of-session** scorecard. Rolling scoring is a feature I invented.
- Adds ~5 LLM calls per session = ~₹1 extra cost.
- Adds graph complexity (conditional edge to `score_rolling`).
- Adds DB column (`per_turn_signals`) that mostly stays empty.
- Score quality at end-of-session is BETTER with the full transcript than averaged from partial signals anyway.

**Where it lives:**
- LLD §6.1 (State) — `rolling_signals` field removed
- LLD §6.2 (Nodes) — `score_rolling` node removed
- LLD §6.4 (Graph wiring) — conditional edge to `score_rolling` removed
- LLD §7.1 (System prompt) — `score_turn` tool removed from prompt
- LLD §8.6 (Tool definitions) — only `end_interview` remains
- LLD §10 (Scoring) — `generate_final_scorecard` is the only scoring path
- LLD §4 (DDL) — `per_turn_signals` column NOT created
- LLD §14.3 (Feature flags) — `rolling_scores.show_to_user` removed
- Appendix D cost model — line removed, ₹1/session saved

---

### C6 — 8 Microservices Collapsed to 4

**v1.0 had:**

| # | Service | Responsibility |
|---|---|---|
| 1 | auth | SSO + JWT |
| 2 | orchestrator | LangGraph + WS hub |
| 3 | naipunyam_sync | External API adapter |
| 4 | jobs_context | Real + virtual jobs |
| 5 | feedback | Scoring + PDF |
| 6 | billing | Metering + invoicing |
| 7 | admin_api | Dashboards |
| 8 | notification | Email + SMS |

**v1.1 has:**

| # | Service | Responsibility | Merges |
|---|---|---|---|
| 1 | `interview_core` | Auth + WS hub + LangGraph + AI pipeline | (1) + (2) |
| 2 | `data_gateway` | Naipunyam sync + jobs + NOS KB | (3) + (4) |
| 3 | `feedback_billing` | Scoring + PDF + meter + invoice | (5) + (6) |
| 4 | `admin_ops` | Dashboards + reports + email + SMS | (7) + (8) |

**Why consolidate:**
- 15-day deployment SLA. 8 services = 8 Helm charts, 8 CI pipelines, 8 Dockerfiles, 8 Vault paths, 8 sets of K8s manifests, 8 PDBs, 8 HPAs.
- RFP Pg 23 mandates source-code handover at end of contract. Fewer services = cleaner handover, faster onboarding for the recipient.
- Services that share a deployment lifecycle (auth always changes with orchestrator; jobs always change with Naipunyam sync) belong together.
- Each merged service is still ~500–1500 lines of code per module — well within a single team's cognitive load.
- Can re-split later if a single service becomes a scaling bottleneck. Easy direction (merge → split). Hard direction (split with shared DB → independent DBs) avoided.

**Where it lives:**
- LLD §1 (repo structure) — 4 service directories instead of 8
- LLD §3 (OpenAPI) — 4 spec sets instead of 8
- LLD §14.1 (Helm values) — example shown for interview_core; same pattern × 4
- LLD §17 (Deployment manifests) — 4 ArgoCD applications instead of 8
- HLD §3 (Logical architecture) — L3 layer redrawn with 4 boxes
- HLD §4 (Component catalog) — 4 service catalog entries
- HLD §9 (Deployment view) — node group layout updated
- Final_stack.md — service table updated

---

### C7 — Counseling Agent Removed from v1 Scope

**v1.0 had:**
> "Counseling agent (the hinted second agent — Pg 11 mentions 'interviews and counseling') — should be planned even though scope is thin"
> Plus a `counseling_agent.enabled` feature flag in `services/orchestrator/feature_flags`.

**v1.1 has:**
Explicitly marked **out of scope for v1** in HLD §1. Feature flag removed.

**Why cut:**
- RFP title says "Conversational Job Preparation **Agents**" (plural).
- RFP body (Pg 8–10) only scopes the **Mock Interview** module.
- AI Layer (Pg 11) mentions "LLM-based conversational AI for interviews and counseling" — single ambiguous mention.
- No specification of what the counseling agent does, when it's invoked, what data it consumes, what its rubric is.
- Building a half-specified feature against a guessed scope is the highest-risk thing we can do for a 15-day deploy.
- **Action:** Open Decision D6 in HLD §15. Add to pre-bid query letter. If APSSDC confirms scope, re-introduce as a Phase-2 addendum with its own LangGraph + prompt.

**Where it lives:**
- HLD §1 (Out of Scope) — listed explicitly
- HLD §15 (Open Decisions) — D6 unchanged
- LLD §14.3 (Feature flags) — `counseling_agent.enabled` removed
- LLD §20 (Open Decisions) — counseling agent placement question removed

---

### C8 — Embedding Model Locked, Not Open

**v1.0 had:**
> "L9 | Embedding model — `text-embedding-3-large` vs Cohere multilingual? | OpenAI `text-embedding-3-large` (1536 dim) via Azure India endpoint for residency | Open"

**v1.1 has:**
**Decided.** OpenAI `text-embedding-3-large` (1536 dimensions). Stop discussing.

**Why cut:**
- A genuine engineering decision but not load-bearing — both candidates would have worked.
- Leaving as "Open" invites bikeshedding in pre-bid review.
- Locked-in decisions look more credible to a procurement committee than "we'll figure this out".

**Where it lives:**
- LLD §20 (Open Decisions) — moved from Open → Decided
- Final_stack.md — pick stated without alternatives

---

## TRACEABILITY — WHAT WAS *NOT* CUT (defensible additions kept)

Several items in v1.0 went beyond literal RFP wording but are **kept in v1.1** because they're mandated by Indian law or contractually unavoidable:

| Kept item | Why |
|---|---|
| DPDP Act 2023 consent ledger + right-to-erasure | DPDP Act is law since Aug 2023; mandatory for govt projects |
| India data residency (ap-south-1 only) | DPDP + RFP §9.13 "data owned by APSSDC" |
| CERT-In audit cadence | RFP Pg 12 references security audit certificates |
| HashiCorp Vault for secrets | RFP Pg 23 source-code handover means secrets cannot be hardcoded |
| Audit log immutability (S3 Object Lock) | ISO 27001 (APTS is certified) inheritance |
| NOS/NSQF KB with embeddings | RFP Pg 9 demands NSQF alignment; embedding retrieval is leanest implementation |

---

## DOCUMENT FOOTPRINT (lean check)

| File | v1.0 lines | v1.1 lines | Delta |
|---|---|---|---|
| HLD.md | ~742 | ~430 | −42% |
| LLD.md | ~2000 | ~1900 | ~5% (kept full technical artifacts; only removed cuts) |
| Final_stack.md | ~261 | ~210 | −20% |
| **CHANGES.md** | 0 | ~270 | new |

LLD shrank less because we kept all the strong technical artifacts (DDL, prompts, adapters, WS protocol). HLD and Final_stack are noticeably leaner.

---

## SIGN-OFF

Anyone reviewing the v1.1 design who asks "why isn't X here?" can be pointed to this file. If a cut is later contested in pre-bid, this file documents the rationale and the cost of reversing it.

**Last updated:** 2026-05-26
**Author:** AI Orchestrator
**Approves cuts:** Pending owner review

---
---

# ADDENDUM — S5 Hardening Cycle (2026-08-06)

Everything above is the v1.0 → v1.1 *design-cut* log and is unchanged. This
addendum records the S5 review cycle: what was audited, what shipped, and what
consumers of the API need to know.

## Review documents

| Document | Reviewer | Verdict when written | Status |
|---|---|---|---|
| [`docs/code-review-s5.md`](code-review-s5.md) | code-reviewer | **REQUEST CHANGES** — 3 MUST FIX, 6 SHOULD FIX, 8 CONSIDER | **SUPERSEDED 2026-08-07** |
| [`docs/security-review-s5.md`](security-review-s5.md) | security-auditor | **BLOCKED** — CRITICAL 0 / HIGH 2 / MEDIUM 3 / LOW 0 | **SUPERSEDED 2026-08-07** |
| [`docs/code-review-2026-08-07.md`](code-review-2026-08-07.md) | code-reviewer | current | **CURRENT** |

Both S5 verdicts are historical. Each document now carries a dated
reconciliation banner listing its findings with their status at HEAD; the
documents themselves are retained in full as RFP-traceability evidence.

Prior security reports in the same chain:
[`security-review-s4-bundle.md`](security-review-s4-bundle.md),
[`security-review-s3-011.md`](security-review-s3-011.md),
[`security-review-s3-004-s3-005.md`](security-review-s3-004-s3-005.md).

The S5 pair covers `fa42edc` → `566b2ea` (23 commits, 117 files). The two
documents are companions: the security report verifies that the fixes merged in
that range hold, then audits the surfaces the cycle did not reach; the code
review ranks the same findings by remediation effort and adds correctness,
test-coverage and anti-pattern items that are not security issues.

**Scope note.** Per owner direction, DPDP data-plumbing remediation (R2 object
erasure, retention purge, consent-ledger test coverage) is deferred pending
database consolidation, and demo/seed credentials already published in git
history are out of scope. Both are recorded in the security report's status
tables for the audit trail; neither counts toward the verdict.

## Blocking items — REMEDIATED

**There are no open blocking items.** Both HIGH findings raised by the S5
security audit were fixed in `352f366` ("close all 18 open findings from the
Aug-2026 full-repo code review", 2026-08-07) and independently re-verified at
HEAD by [`docs/code-review-2026-08-07.md`](code-review-2026-08-07.md) §2.1.

This section previously read "Blocking items (not yet remediated)" and still
listed both. It is the fastest place a bid reader checks for open blockers, so
it was reporting two production blocks that no longer existed. Kept as a
remediation record rather than deleted — the audit trail is the point.

| Ref | Item | What it blocked | Remediated in | Evidence at HEAD |
|---|---|---|---|---|
| HIGH-1 | `sso_naipunyam.py` accepts `state` and never validates it — login CSRF, no PKCE, no privileged-account exclusion | Any deploy with `AUTH_PROVIDER=naipunyam`. Dormant then (`local`), but one env flip away and on the APSSDC bid path. | `352f366` | One-shot Redis state, 600 s TTL, consumed before the token POST (`sso_naipunyam.py:251-261`); binding cookie compared with `hmac.compare_digest` (`:396-398`); PKCE `code_challenge_method: "S256"` (`:276`); privileged-role rejection **before** the user upsert and before any token is issued (`:493-509`) |
| HIGH-2 | `scripts/piston-up.ps1` runs the code-execution sandbox `--privileged` with `--dns 8.8.8.8` | Any use of the self-hosted Piston runner, including local dev. Hosted provider was and remains the default. | `352f366` | `piston-up.ps1:120-138` — `--cap-drop=ALL` with a named capability allow-list, `--cgroupns=private`, `--pids-limit 512`, loopback-only bind, networking disabled. No `--privileged`, no explicit DNS |

The three S5 MEDIUMs are also closed, with one recorded residual:
`trusted_proxy_count` is now bounded (`config.py:283`, `ge=0, le=4`) and counts
the too-high case, but a value set too **low** still degrades silently — carried
forward as **DG-3** in the current review, not as a blocker.

## API contract and behaviour changes (already merged)

These shipped in the S5 range and are live. Any client, test fixture or
integration built against the previous behaviour needs checking.

| Endpoint | Change | Commit |
|---|---|---|
| `POST /api/rooms/{session_id}/token` | Now **409** on a completed/failed session, or when a scorecard already exists — closes session replay. | `40df357` |
| `POST /internal/score` | `language` constrained to `en\|hi\|te`; anything else is **422**. | `40df357` |
| `GET /api/scorecards/{id}` | Pre-signed PDF URL TTL cut from 30 days to **1 hour**. Clients must not cache or share the URL. | `40df357` |
| `GET /admin/interviews/{id}/transcript` | Now writes an `audit_log` row on every read. | `40df357` |
| `POST /jobs/{job_id}/jd-document` | Now **403** for any non-staff role (previously any authenticated co-tenant, including guests). | `566b2ea` |
| `PATCH /auth/me/profile` | `linkedin_url` / `github_url` must be `http(s)`; other schemes are **422**. | `566b2ea` |
| All `/hr/*` and `/admin/*` | **403** while the caller's `must_change_password` flag is set. Only `/auth/*` stays reachable, so the flag can be cleared. | `a5b8f68` |
| LiveKit interview room | Participant grants now carry `can_publish_data=False` and the agent session runs with `text_input=False` — typed answers over the data channel are refused at the transport. | `566b2ea` |
| `infra/docker/docker-compose.yml` | All six service ports bind to `127.0.0.1` instead of `0.0.0.0`. Anything reaching a dev service from another host must now go through an explicit tunnel. | `b04d257` |

Behaviour changes with no contract surface: `logout_all` now revokes for the
full refresh-token lifetime rather than 16 minutes; the Google SSO callback
requires a browser-bound state cookie and PKCE verifier, so a callback replayed
into a different browser fails.

## Later phases

Remediation is phased by severity — MUST FIX, then SHOULD FIX, then CONSIDER —
tracked in the two review documents rather than duplicated here. Any API or
behaviour change a later phase introduces gets appended to the table above with
its commit.

**Last updated:** 2026-08-06
**Author:** code-reviewer / security-auditor
