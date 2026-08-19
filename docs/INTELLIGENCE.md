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
only — matching `get_hr_company`, which already gates every `/hr/*` REST
endpoint on that role alone.

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
