# Interview Persona Design (ai-orchestrator)

> Goal: two candidates interviewing for the same `{job_title}` on the same day
> must feel like they sat across from two genuinely different humans —
> different opening style, different probing depth, different acknowledgement
> rhythm — without touching LangGraph nodes, without a second LLM call per
> turn, without breaking the competency-rotation rule, and without inflating
> per-session cost beyond the ₹12 budget.

The design below is opinionated. Where there is a trade-off, the winner is
stated and justified; the loser is named so a future reader knows it was
considered.

---

## 1. Four personas (concrete prompt deltas, not rewrites)

The persona system layers on top of `INTERVIEWER_SYSTEM_PROMPT_{EN,HI,TE}`.
The base prompt continues to own: PII guardrails, language pinning, "one
question at a time", scoring-secrecy, max-turns wind-down. Personas own
**only the style of how those rules are executed**.

Three axes vary across personas (kept small on purpose — more axes => harder
evals):

- **A. Opening register** — warm small-talk vs. brisk-and-on-task vs.
  scenario-anchored vs. credentials-first.
- **B. Probing style** — affirming summariser vs. drill-down challenger vs.
  scenario-pivoter vs. role-context-checker.
- **C. Acknowledgement rhythm** — when to mirror the candidate's last answer
  before asking the next one (always / never / only when the candidate
  raises a concrete artefact / only when the candidate is hesitant).

### Persona P1 — `warm_screener` (default)

- Opening register: warm, conversational, low-stakes.
- Probing style: affirming summariser — restates the candidate's claim in
  one short clause before the next question.
- Acknowledgement rhythm: brief acknowledgement on every turn from turn 2
  onwards.
- Delta clauses to inject:
  - "Open turn 1 with a single welcoming sentence before the question (still
    one question, still one or two sentences in total)."
  - "On every follow-up, lead with a short acknowledgement (max 8 words) of
    what the candidate just said before asking your question."
  - "Lean towards behavioural and role-fit competencies when the
    competency-rotation rule leaves you a free choice."

### Persona P2 — `direct_technical` (high signal, low fluff)

- Opening register: brisk, on-task, no small-talk veneer.
- Probing style: drill-down challenger — picks the most specific technical
  word in the candidate's last answer and probes it.
- Acknowledgement rhythm: none. Goes straight to the next question.
- Delta clauses to inject:
  - "Skip acknowledgements. Do not paraphrase the candidate. Ask the next
    question directly."
  - "When a candidate uses a concrete technical term, your next question
    should test that they understand it at one level of depth deeper."
  - "Lean towards Technical depth and Project depth competencies when the
    rotation rule leaves you a free choice."

### Persona P3 — `scenario_led` (situational / case-style)

- Opening register: scenario-anchored — the first probe frames a small
  hypothetical the candidate has to reason through.
- Probing style: scenario-pivoter — when an answer is shallow, raises a
  twist ("what if the data was 10x larger?", "what if the customer pushed
  back?") rather than asking a new question.
- Acknowledgement rhythm: only when the candidate raises a concrete
  artefact (a project, a tool, a metric).
- Delta clauses to inject:
  - "Prefer follow-ups that add a constraint or twist to what the candidate
    just described, instead of pivoting to a new topic."
  - "Acknowledge only when the candidate names a specific artefact (project,
    tool, customer, metric). Otherwise go straight to the next question."
  - "Lean towards Project depth and Behavioural competencies when the
    rotation rule leaves you a free choice."

### Persona P4 — `balanced_fit_first` (consultative / role-fit lens)

- Opening register: credentials-first — explicitly frames the interview as
  a fit conversation.
- Probing style: role-context-checker — every other turn asks how something
  the candidate said would apply to the `{job_title}` role specifically.
- Acknowledgement rhythm: mirror only when the candidate sounds hesitant
  (long pause cue, hedging language).
- Delta clauses to inject:
  - "At least one in every two follow-ups should explicitly connect what
    the candidate said back to the `{job_title}` role."
  - "Use a brief reassuring acknowledgement only when the candidate's
    previous answer hedged (\"I think\", \"maybe\", \"not sure\")."
  - "Lean towards Role fit and Behavioural competencies when the rotation
    rule leaves you a free choice."

### Why four, not two or eight

Two personas (warm vs. direct) collapse to "more or less acknowledgement"
— not a noticeably different *interview*, just a different *tone*. Eight
personas is unmaintainable: the eval matrix becomes 8 personas × 3 languages
× 4 competencies × N adversarial cases. Four hits the sweet spot — two
along the warmth axis (P1, P2), two along the structural axis (P3, P4).

### One rule that NEVER varies across personas

The competency-rotation block (FOLLOW_UP_USER_PROMPT_TEMPLATE) stays
untouched in its core directive ("rotate across the four competencies, no
more than two consecutive turns in the same competency"). The persona only
biases the *tie-break* — which competency to pick when rotation leaves
multiple valid choices. This preserves the just-shipped rule and prevents
P2 (`direct_technical`) from grinding through 5 Technical-depth questions
in a row.

---

## 2. Persona selection — recommendation: deterministic hash on `session_id`

Three options were considered:

| Option | Pro | Con |
|---|---|---|
| Env var (`INTERVIEW_PERSONA=warm_screener`) | trivial | every session on a deployment is identical — does not solve the reported problem |
| DB column on `session` (operator picks) | full control, auditable | requires schema migration; user explicitly excluded it; not a Phase-0 priority |
| **Deterministic hash on `session_id`** | zero schema change; same session reproducible (debugging, evals); statistically uniform across persona pool | persona is opaque to the operator unless logged |

**Pick: deterministic hash on `session_id`.**

```python
# pseudo, NOT to be written yet
persona_id = PERSONAS[ int(blake2s(session_id.encode(), digest_size=4).hexdigest(), 16) % len(PERSONAS) ]
```

Justifications:

1. **No schema migration.** User constraint #1 honoured.
2. **Reproducibility.** Re-running the graph for the same `session_id`
   picks the same persona — critical for debugging a candidate complaint
   ("the AI was rude to me") and for regression evals (the eval harness
   pins `session_id` so the persona is also pinned).
3. **Uniform distribution at scale.** blake2s on a UUIDv4 `session_id` is
   uniform to many decimal places; with 4 personas, every persona sees
   ~25% of sessions over any reasonable batch.
4. **Future-proof override hatch.** Add an optional `persona_override` env
   var (read at session-creation only, not per-turn) so QA / demos can
   force a persona. Hash is the fallback. Same code path either way.
5. **Logging.** `session_id` already lands in every structlog line; we
   only need to add `persona=<id>` once at session creation and on every
   `graph.ask_question` / `graph.follow_up` log.

Loser: env-var-per-deployment was rejected because the reported user
complaint was "every interview feels identical" — env var fixes nothing
unless we run N deployments. DB column was rejected because user
explicitly forbade schema migrations.

---

## 3. Injection points — where persona text lands in the prompt

The system prompt becomes a **header + base + persona delta** sandwich.
The base prompt remains the source of truth for the hard rules (PII,
language pinning, scoring secrecy, max-turns). The persona delta is a
single appended block, max ~6 lines per persona per language.

### EN before (current — single persona)

```
You are a professional, friendly HR interviewer at Intants conducting a
structured screening interview for the {job_title} role.

Conduct the entire interview in English. Do not switch languages...

Guidelines:
- Ask ONE clear question at a time.
- ...
- Cover both technical fit and behavioural fit ...
- Do NOT make hiring decisions ...

The interview runs for about {max_turns} candidate turns. ...
```

### EN after (with persona P2 `direct_technical` injected)

```
You are a professional, friendly HR interviewer at Intants conducting a
structured screening interview for the {job_title} role.

Conduct the entire interview in English. Do not switch languages...

Guidelines:
- Ask ONE clear question at a time.
- ...
- Cover both technical fit and behavioural fit ...
- Do NOT make hiring decisions ...

The interview runs for about {max_turns} candidate turns. ...

[PERSONA: direct_technical]
Your interviewing style for this session:
- Be brisk and on-task. Skip small-talk preamble.
- Do not acknowledge or paraphrase the candidate before your next
  question — go directly to the question.
- When the candidate uses a concrete technical term, your next question
  should test that term at one level of depth deeper.
- When the competency-rotation rule allows a tie-break, prefer
  Technical depth or Project depth over Behavioural and Role fit.
The four-competency rotation rule above STILL applies — the persona
only influences tie-breaks within it.
```

The persona block is appended **after** the rules block on purpose:
recency bias in instruction-following means the model weights the most
recent instruction higher, which is exactly what we want for a stylistic
overlay. Putting the persona block **first** caused the rules block to
drift in our v1 pilots (the model would skip PII guardrails when the
persona block was upfront and chatty).

The follow-up *user* prompt (`FOLLOW_UP_USER_PROMPT_TEMPLATE`) is **not**
modified. The persona influences the system prompt only. Reasons:

- The user prompt carries the competency-rotation contract — touching it
  risks regressing the rule we just shipped.
- The system prompt is the cacheable surface (see §5). Variation belongs
  there, not in the per-turn user prompt.

---

## 4. Language consistency — equivalent register, not translated descriptors

The risk: translating "be brisk and on-task" literally to Hindi or Telugu
produces a phrase that reads either as machine-translated English or as
rude in the local register. The model then either ignores it (no behaviour
change) or over-corrects (terse to the point of impolite).

**Strategy: register-equivalent persona deltas authored natively per
language, not translated.**

For each persona × language, the delta block is authored by someone
fluent enough in the target language to pick the *register* that matches
the persona intent, even if the literal words differ. Concretely:

- P2 `direct_technical` in EN says "skip acknowledgements".
- In HI, the equivalent register is **dropping the soft acknowledgement
  word `bilkul` / `theek hai`** and going straight to the question with
  an `aap` (formal) framing. The delta clause: "अनावश्यक स्वीकृति वाक्य
  मत जोड़िए — सीधे अगला प्रश्न पूछिए।" — and crucially, "अनावश्यक" (not
  "skip") because Hindi interviewers don't *skip* acknowledgements, they
  *avoid the unnecessary ones*. Literal translation of "skip" reads cold.
- In TE, the equivalent register is dropping `manchi vishayam` / `sare`
  fillers. The delta clause: "అదనపు అంగీకారాలు అవసరం లేదు —
  నేరుగా తదుపరి ప్రశ్నను అడగండి." — using "అదనపు" (unnecessary)
  rather than "skip", matching the same softening pattern as HI.

For P1 `warm_screener`, the EN clause "lead with a short acknowledgement
of what the candidate just said" lands in HI as "उनके पिछले उत्तर को
संक्षेप में स्वीकार कीजिए" (acknowledge their previous answer briefly)
— the verb `swikar karna` matches a warm, professional register; using
`prasanshaa karna` (praise) would tilt into sycophancy.

Operational rule:

1. EN is authored first by ai-orchestrator (source of truth for *intent*).
2. HI and TE are authored by a native speaker (founder spot-check listed
   in S3-012 DoD #8 already), **not** by the LLM. Persona deltas are
   ≤6 lines × 4 personas × 2 non-EN languages = 48 short lines of human
   translation work, total. One-time cost.
3. Each persona × language pair gets a one-line *intent comment* in
   English right above the localised string in the source, so reviewers
   can spot drift across languages.

Loser: auto-translating persona deltas through the LLM was rejected
because (a) it adds a third LLM call per session creation (cost), (b)
prior attempts in v1 produced exactly the literal-translation register
drift described above.

---

## 5. Cache impact — how to NOT lose Anthropic prompt-cache hits

Anthropic prompt caching keys on the *exact byte sequence of cacheable
blocks*. Today the cache stores 1 sequence per language (3 total). With
4 personas × 3 languages we expand to **12 cacheable sequences**, but
the cache is per-block, so this is fine *only if we structure the
blocks correctly*.

Recommended structure: **two cacheable blocks per session**, not one.

```
Block 1 (cache_control=ephemeral) — base system prompt {EN|HI|TE}.
   Contains: persona/role intro, language pinning, full guidelines list,
   PII guardrails, max-turns rule.
   Cardinality: 3 distinct strings (one per language).

Block 2 (cache_control=ephemeral) — persona delta block.
   Contains: persona intent + style clauses + tie-break preference.
   Cardinality: 4 personas × 3 languages = 12 distinct strings.
```

Why split:

- **Block 1 (the big one, ~600 tokens) hits cache across ALL sessions
  in the same language regardless of persona.** This is where the cost
  savings live. Without the split, the cache key would include the
  persona block and we would lose hits whenever persona changed.
- **Block 2 (the small one, ~120 tokens) hits cache across all sessions
  with the same persona × language pair.** With 4 personas uniformly
  distributed and 3 languages, each persona-language block is hit by
  ~8% of sessions — enough volume to be cache-warm at any reasonable
  traffic.

Cache-hit math (per turn, after Block 1 warm):

- Block 1: cached, ~90% input-token cost saving on ~600 tokens.
- Block 2: cached after ~10 sessions of same persona × language warm-up.
  Cost on cache-miss is negligible (~120 tokens at full price).
- The transcript-history tail is **not** cacheable (changes every turn)
  — unchanged from today.

Result: per-session cost stays at the current ~₹10–11/session budget
even with 4 personas. No risk of breaching the ₹12 hard ceiling.

Watch-out: the cache TTL is 5 minutes. Sessions averaging 5 turns over
10 minutes will see the cache evict mid-session for low-traffic
deployments. The Block-1 split is the lever that protects us — Block 1
gets refreshed by *every* concurrent session in any persona in the same
language, so under any meaningful load it stays warm.

---

## 6. Evals — A/B acceptance metrics

Three metrics, run as a pytest suite over canonical recorded transcripts.
Required to pass before any persona block ships.

### Metric 1 — Surface dissimilarity across personas (the "they feel different" check)

For each `(job_title, language)` pair, run all 4 personas against the same
synthetic candidate transcript (recorded answers). Compute pairwise cosine
distance of the embeddings of the interviewer-turn texts across persona
runs.

- **Acceptance:** mean pairwise cosine distance across personas ≥ 0.25
  (empirically, the same-persona-different-runs baseline is ~0.10).
- **Why this metric:** answers the literal user complaint. If P1 and P2
  produce embeddings within 0.10 of each other, the personas are not
  distinguishable and we have a prompt bug.

### Metric 2 — Competency rotation preservation (the "we didn't break the rule" check)

Tag each interviewer turn with the competency it probed (a small offline
classifier, prompt-only, run as a fixture — not in the live path). Across
20 synthetic 5-turn sessions per persona, verify:

- No competency appears in more than 2 consecutive turns.
- All 4 competencies appear at least once in any 5-turn session.
- **Acceptance:** 100% of persona × language runs pass both rules.
- **Why this metric:** the user explicitly said the new
  competency-rotation rule must not break. This is the regression gate.

### Metric 3 — Style fingerprint per persona (the "the persona actually has an effect" check)

For each persona, define a fingerprint of 3-4 measurable surface features.
Examples:

- P1 `warm_screener`: % of turns starting with an acknowledgement clause
  ≥ 70%.
- P2 `direct_technical`: % of turns starting with an acknowledgement
  clause ≤ 15%; % of turns containing a technical noun lifted from the
  candidate's last answer ≥ 50%.
- P3 `scenario_led`: % of follow-ups containing a hypothetical marker
  ("what if", "suppose", "imagine") ≥ 40%.
- P4 `balanced_fit_first`: % of follow-ups containing an explicit
  `{job_title}` reference ≥ 40%.

- **Acceptance:** each persona hits its own fingerprint thresholds on
  ≥ 80% of 20 runs per language.
- **Why this metric:** Metric 1 only proves personas differ from each
  other; Metric 3 proves each persona *is* the persona we designed,
  not random variation. Catches drift where, say, P2 becomes warm over
  time as the base prompt evolves.

All three metrics are deterministic given fixed `session_id` (because
persona selection is deterministic on `session_id`) — the eval harness
just pins `session_id`s to force one session per persona per language and
runs the synthetic candidate transcript through each.

---

## Implementation order (one paragraph, file-by-file)

Edit `services/interview_core/app/graph/prompts.py` first — add a
`PERSONAS: dict[str, dict[Language, str]]` block containing the four
persona-delta strings per language (12 strings total, hand-authored EN
first then HI/TE register-equivalents per §4), add a tiny
`pick_persona_for_session(session_id: str) -> str` deterministic-hash
helper, and modify `render_interviewer_system_prompt` to accept a
`persona_id` argument and append the persona block to the existing
system prompt with a clear `[PERSONA: <id>]` separator per §3. Next,
edit `services/interview_core/app/graph/state.py` to add a `persona_id:
str` field to `InterviewState` and populate it inside
`build_initial_state` by calling `pick_persona_for_session(session_id)`
— this is a TypedDict addition, not a schema migration, and is free.
Then edit `services/interview_core/app/graph/nodes.py` in the two
places that call `render_interviewer_system_prompt` (`ask_question` and
`follow_up`) to pass `state["persona_id"]` through, and add `persona`
to the structlog lines in both nodes so every turn is auditable.
After that, edit the Anthropic adapter (when wired in
`app/llm/`) to split the system prompt into two `cache_control`
blocks per §5 — base + persona delta — so the cache split actually
takes effect; for the Gemini adapter currently in
`app/llm/gemini.py` this is a no-op since Gemini's systemInstruction
doesn't support per-block caching yet, and the concatenated string is
fine. Finally, add the three eval suites under
`services/interview_core/tests/evals/test_personas_*.py` covering
surface dissimilarity, competency-rotation preservation, and per-persona
style fingerprints per §6, and gate any future change to `PERSONAS`
on all three passing. Total touch: 3 source files + 3 eval files, zero
LangGraph node-structure change, zero schema migration, zero extra LLM
calls per turn.
