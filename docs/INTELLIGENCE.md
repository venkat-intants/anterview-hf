# Intelligence layers

Two shared packages and how they plug into the platform. Read `CLAUDE.md` first
for the one-paragraph version; this is the operator/maintainer detail.

---

## 1. Role engine — `shared/intelligence/`

### The problem it solves

Every AI feature used to re-guess the job from a free-text `job_title`, and the
guess leaned software: a welder was asked about "systems", a nurse was scored on
a rubric shaped for a developer. Telling the model "follow the role" in prose
helped but was unverifiable, and left the scorer nothing to calibrate against.

### How a role is resolved

```
job_title + jd_text + required_skills + department + level
        │
        ├─ classify()      weighted keyword match → 1 of 18 families
        │                  title 6× · skills 3× · department 2× · JD 1×
        │                  below threshold → "generic" (honest, not a guess)
        │
        ├─ baseline_profile()   family → 4-6 weighted competencies,
        │                       each with weak/adequate/strong anchors
        │
        └─ derive_role_profile()  optional Gemini refinement for THIS posting
                                  ↓ any failure
                                  baseline stands, source="taxonomy"
```

`derive_role_profile` **never raises**. It runs on the live interview hot path,
so a role model that could throw would take the interview down with it.

### Who consumes it

| Consumer | Effect |
|---|---|
| `interview_core` worker | question plan weighted by competency, replacing the fixed `Q1 intro / Q2–Q6 technical / Q7–Q9 behavioural / Q10 wrap` |
| `interview_core` graph | `[ROLE MODEL]` block + per-turn competency chosen in code |
| `feedback_billing` scorer | per-role axis weights, role-specific rubric, per-competency breakdown |
| `feedback_billing` exam gen | per-competency question quota via the same allocator |

#### Question-bank generation (HR authoring, not an agent)

A question bank can be filled with AI drafts of either kind. Both endpoints live
in `services/data_gateway/app/routers/question_banks.py`, are `hr_manager` only,
take the company from the session, and call the **same** `feedback_billing`
generators the exam editor uses — there is no second prompt:

| Endpoint | Generator | Per-request bound |
|---|---|---|
| `POST /hr/question-banks/{bank_id}/questions/generate` | `/internal/generate-exam` (MCQ) | 1–20 questions |
| `POST /hr/question-banks/{bank_id}/questions/generate-coding` (added 2026-10-13) | `/internal/generate-coding` | 1–5 questions; the generator budgets `4096 + 2048·n` output tokens, so at most 14,336 |

Both return a **preview and save nothing**. `POST …/questions/bulk` stores what
HR keeps as `origin='ai_draft'` drafts, which then need the ordinary review:
approved by someone who did not write, edit or submit them. These are plain HR
endpoints — no tool in `shared/agents/` can reach them, and a unit test asserts
that.

What is true of the coding endpoint specifically:

- **Nothing is executed.** The reference solution and every expected output are
  model output that no code path runs at authoring time (the exam coding
  generator does not either). Each draft therefore carries
  `reference_solution_verified: false` in the preview and the tag
  `reference-unverified` (one constant, `question_banks.UNVERIFIED_TAG`).
  **The marker is stamped by the server, not carried by the browser:** every
  preview item has it (with or without a reference solution, because the
  expected outputs are the model's too), `POST …/questions/bulk` adds it to
  every `coding` item whatever `tags` the client sent, and `PATCH
  /hr/bank-questions/{id}` puts it back on an `ai_draft` coding question while
  no version in its lineage has ever been approved (once a version has been
  approved a later revision may drop it). It is a caution label, not a gate:
  submission and approval never look at it. `coding_questions` has no tags
  column, so the tag is **not** copied onto an exam's row when an approved
  question is added; the exam row keeps `source_bank_question_id`, which the
  exam editor can use to read the bank question's `tags`/`origin`.
- **Failure messages are fixed.** A failed generator call (unreachable,
  timeout, non-200, or a 200 whose body is not the expected JSON) answers 502
  with one sentence — "The AI question generator is not available right now.
  Please try again in a minute." — on the two bank routes and the two
  exam-editor generate routes. The origin, the start command and the upstream
  body excerpt are written to the server log only; a short developer hint
  (never the body) is appended only when `APP_ENV=development`.
- **Model output is re-validated in `data_gateway`**
  (`app/bank_coding_generation.py`): at least one sample and one hidden test
  case, no test with an empty expected output, no URL or credential-shaped
  string in the statement, the solution or the test data, the same length
  limits as hand authoring, and a duplicate of a question already in the bank is
  dropped. Unusable items come back in `dropped` with a reason each; only
  "nothing usable at all" is a 502.
- **No starter code is generated.** A coding question has one `starter_code`
  string and the candidate editor pre-fills it whichever language is chosen, so
  a single-language stub would be wrong for the others. Per-language starter
  code would need a schema change in the bank, the exam and the take path.
- **Role context is partial.** `job_title` reaches the allocator only for the
  `software_it` and `data_analytics` families (`_CODING_FAMILIES` in
  `feedback_billing/app/exam_generator.py`); for any other role the topic drives
  alone. A bank has no workflow round, so a round's own rubric is never used
  here.
- **Rate limits, fail-closed.** Per company: `BANK_GENERATE_CODING_PER_MINUTE`
  (default 4) and `BANK_GENERATE_MCQ_PER_MINUTE` (default 6), plus a shared
  daily cap `BANK_GENERATE_DAILY_PER_COMPANY` (default 200, a fixed 24-hour
  window from the window's first request) covering MCQ and coding together.
  They are charged only after body validation and the bank lookup, so a 422 or
  a 404 spends nothing. Unlike the other limiters in the service these answer
  **503** when Redis is unreachable instead of running unmetered, because each
  request is an LLM bill. The exam-editor generate routes
  (`EXAM_GENERATE_PER_MINUTE`, default 6, one counter for both) are fail-closed
  too and, since 2026-10-13, charged the same way: in the handler after the
  exam-ownership and rubric reads, so a 404 spends nothing. The database
  connection is released (`rollback`) before the model call on the two bank
  routes and on both exam-editor generate routes; none of the four touches the
  session afterwards (the output is a preview, nothing is saved).
  Limits have not been exercised against a real Redis.
- **Failure messages.** A failed generation answers 502 with one fixed
  sentence; the service origin, start command and upstream body are logged
  server-side only. The same holds for resume re-scoring and the "why this
  candidate matched" explanation (`scoring_client` / `embedding_client`). A
  short developer hint is appended only when `APP_ENV=development`.
- **Statement language** is EN / HI / TE, the same choice the MCQ generator
  offers. The quality of generated Hindi and Telugu statements is
  **unverified** — no real model call was made while building this.

### The frozen part

The four scorecard axes (`communication`, `technical`, `problem_solving`,
`confidence`) are **not** role-derived. They are persisted in
`scorecards.scores`, aggregated by the admin analytics SQL (`avg_problem_solving`
et al) and typed in the frontend. What the role engine changes is their
**weighting** and what each one *means* for a given role.

Blending is deliberate: `ROLE_WEIGHT_BLEND = 0.6` toward role-derived,
`MIN_AXIS_WEIGHT = 0.10` floor. Without the floor an axis could fall to 0.02 and
still render at full size on a chart, which misleads a human reader.

### Caching

`profile_id` is a SHA-256 of the normalised derivation inputs plus
`SCHEMA_VERSION`. Editing a JD naturally misses the cache; bumping the schema
version invalidates everything without flushing Redis. The id is recorded on
every scorecard, so any score traces back to the exact rubric that produced it —
the audit story for a government bid.

---

## 2. Agent layer — `shared/agents/`

### The invariant

**Agents cannot mutate anything.** Not by prompting — structurally:

```python
ToolEffect = Literal["read", "draft"]      # no "write" member
```

and `ToolRegistry.register` raises on anything else. When an agent wants
something to happen it emits a `Proposal`:

```
PROPOSAL : Interview invite — Asha K
  commit : POST /hr/interviews {"applicant_id": "a-1", "language": "hi"}
  risk   : Sends a real email to the candidate. This cannot be unsent.
```

That is inert data. `web/src/components/agent/ProposalCard.tsx` renders it; on
an explicit click the **frontend** fires the request with the signed-in user's
credentials against the normal authorised endpoint. A proposal cannot widen
anyone's authority — if the human could not do it manually, committing fails
identically.

`CommitSpec.path` must be relative (validated), so a proposal can never point
the commit button at another host.

### The second invariant — a console cannot read outside its remit

The write ban says what an agent may DO. This says what it may SEE, and it is
enforced the same way — in the type, at construction:

```python
ToolDataClass = Literal[
    "candidate_pii",       # a NAMED candidate: resume, transcript, axis scores
    "company_scoped",      # aggregates inside one company, nobody identifiable
    "company_staff",       # the company's own staff records
    "platform_aggregate",  # crosses tenants; aggregate-only, always
]

DATA_CLASS_ROLES = {
    "candidate_pii":      frozenset({"hr_manager"}),
    "company_scoped":     frozenset({"hr_manager", "super_admin"}),
    "company_staff":      frozenset({"super_admin"}),
    "platform_aggregate": frozenset({"platform_owner", "admin"}),
}
```

Every tool declares both a `data_class` and a non-empty `allowed_roles`;
`ToolSpec` rejects any pair the matrix does not permit. That check runs when the
tool is constructed, which is module import — so a mis-scoped tool fails the
service at startup rather than at the request that would have leaked.

Construction is not the only check, because a check that runs once is a check a
later line can undo: `ToolSpec` is frozen (`spec.allowed_roles = ...` raises),
`ToolRegistry.register` re-runs the same check when a tool becomes callable (a spec
built by `model_copy(update=...)` skips validators), and `DATA_CLASS_ROLES` and
`CITATION_MIN_ROLES` are read-only `MappingProxyType`s, so widening the matrix means
editing the literal in `shared/agents/schema.py`, not assigning to it. Unchanged:
running the tools on a read-only database transaction is NOT done — a tool handler
is trusted not to write, and that trust is the remaining gap.

The resulting per-console toolsets:

| Tool | Class | hr_manager | super_admin | platform_owner | admin |
|---|---|:--:|:--:|:--:|:--:|
| `list_applicants` | candidate_pii | ✅ | — | — | — |
| `get_applicant_detail` | candidate_pii | ✅ | — | — | — |
| `draft_interview_invites` | candidate_pii | ✅ | — | — | — |
| `draft_shortlist` | candidate_pii | ✅ | — | — | — |
| `get_funnel_analytics` | company_scoped | ✅ | ✅ | — | — |
| `get_exam_question_stats` | company_scoped | ✅ | ✅ | — | — |
| `get_role_model` | company_scoped | ✅ | ✅ | — | — |
| `get_company_overview` | company_staff | — | ✅ | — | — |
| `get_hr_workload` | company_staff | — | ✅ | — | — |
| `get_platform_overview` | platform_aggregate | — | — | ✅ | — |
| `get_score_distribution` | platform_aggregate | — | — | ✅ | ✅ |

`POST /agent/panel/{applicant_id}` returns resume + exam + coding + transcript
in one response, so it is candidate PII in all but name and is `hr_manager`
only — matching `get_hr_company`, which gates every `/hr/*` REST endpoint on
**two** conditions, not one: the caller must hold `hr_manager`, *and* their user
row must resolve to a company. An `hr_manager` with no `company_id` is refused
with a 403 rather than reaching a handler with an unscoped filter.

**A super admin is deliberately not a superset of an HR manager.** "More senior
therefore sees more" is the intuitive default and the reason this separation
would erode; `test_the_super_admin_console_is_not_a_superset_of_hr` exists to
make that erosion fail CI. The super admin trades candidate depth for company
breadth.

Two further gates back this up:

* `run_agent` refuses when `AgentSpec.role` and `ToolContext.role` disagree.
  The persona and the toolset are chosen independently and nothing else forced
  them to match.
* `_agent_context` reads `users.company_id` on **every** path. A cross-tenant
  console requires an account with no company, so a company user who also holds
  `admin` is refused rather than silently handed the un-scoped analytics tools.

Tests: `shared/agents/tests/test_access_matrix.py` (mechanism),
`services/data_gateway/tests/unit/test_agent_role_isolation.py` (wiring).

### Defence in depth against prompt injection

Everything interesting an agent reads is written by outsiders: resumes, JDs,
transcripts. Layers, most valuable first:

1. **Structural** — no write tool. A successful injection can make the agent say
   something wrong; it cannot make it act.
2. **Tenancy** — handlers filter on `ctx.company_id`, set by the router from the
   session. The query never had a company parameter to poison.
3. **Framing** — tool output is wrapped in `UNTRUSTED DATA` and the shared
   `SAFETY_CLAUSE` says so. Weakest layer, which is why it is not the one relied
   on.

`detect_injection()` **reports** steering text found in a resume rather than
stripping it — that a candidate tried it is a fact HR would want.

### The panel

Four specialists read one signal each, blind to one another, then a synthesizer
reports disagreements. Blindness matters: one agent shown all four signals
anchors on whichever it read first and rationalises the rest into agreement.

Contradiction detection is **deterministic** (`CONTRADICTION_THRESHOLD = 25`,
severe at `35`), not a model judgement — a hiring conversation may rest on it.

Resume-vs-exam and resume-vs-coding are deliberately **not** compared: a
self-authored claim differing from a measurement is the normal case, and
flagging it every time trains recruiters to ignore the panel.

Confidence is capped per signal type (`resume 0.45`, `exam 0.70`,
`interview 0.75`, `coding 0.85`) so a model cannot talk itself into treating a
resume like a measurement.

### Watchers

Deterministic SQL + arithmetic, run nightly at `WATCHERS_CRON_HOUR:30` UTC on
the same APScheduler instance as the DPDP retention job.

| Watcher | Fires when |
|---|---|
| `dpdp_deadlines` | pending erasure request within 48h — always `critical`, goes to platform owners |
| `stalled_applicants` | non-terminal stage > 10 days |
| `funnel_health` | ≥15 applicants and <10% reach interview |
| `exam_quality` | ≥8 attempts and ≤15% correct (probably broken) or ≥98% (separates nobody) |

Findings are deduplicated by content, not time (Redis marker, 8-day TTL), so a
nightly re-run does not re-notify about the same stalled candidates until the
set changes. Suppression **fails open** — a Redis outage costs a duplicate
notification, never a missed DPDP deadline.

---

## Operating it

### Env

| Var | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | — (required by the Space entrypoint) | powers derivation, copilots, panel |
| `AGENTS_ENABLED` | `true` | copilots + panel off without rotating the key |
| `WATCHERS_ENABLED` | `true` | nightly sweep |
| `WATCHERS_CRON_HOUR` | `2` | UTC hour; sweep runs at `:30` |

With no key: `/agent/status` reports `enabled: false`, the copilot button
self-hides, the panel runs its deterministic path, and the role engine uses
taxonomy baselines. Nothing breaks.

### Endpoints

```
GET  /agent/status                  availability + which console
POST /agent/chat                    {message, history[]} → reply + proposals
POST /agent/panel/{applicant_id}    specialist panel (HR roles)
POST /agent/watchers/run            manual sweep (platform_owner)
```

### Cost

Copilot budgets are per-console (`shared/agents/roster.py`): HR gets 7 steps /
14 tool calls, analytics 4 / 8. The panel is 5 calls per candidate and is
**user-triggered**, never on list render. Role derivation is cached per
process and per Redis key, and the taxonomy baseline is cached too — so a role
whose LLM derivation keeps failing does not burn a call per interview.

### Known gaps

- **No free-form candidate email tool.** There is no
  `POST /hr/applicants/{id}/email` endpoint, so a draft tool would render a
  button that 404s. Add the endpoint first.
- **Watchers use `target_job_title` as the funnel grouping key** — the
  denormalised applicant row carries no job id.
- **No feedback loop.** Nothing learns from hiring outcomes.
